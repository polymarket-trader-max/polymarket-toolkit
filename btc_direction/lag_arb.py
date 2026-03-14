"""
btc_direction/lag_arb.py — Binance Lag Arbitrage 核心引擎

策略：
  Polymarket 的 "Bitcoin Up or Down" 市场解析 Binance 1h K线涨跌。
  当K线进行中：BTC 已移动了 X%，用布朗运动模型计算真实概率，
  若比 Polymarket 价格高出 MIN_GAP，即为套利机会。

数学模型（布朗运动）：
  P(close ≥ open | elapsed=t, current_return=r) = N(d)
  d = r / (σ * √(T_remaining))
  σ = BTC 小时波动率（历史约 0.7%/h）
"""

import json
import math
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── 模型参数 ──────────────────────────────────────────────────────────
BTC_HOURLY_VOL  = 0.007     # BTC 每小时波动率 ~0.7%（可动态校准）
MIN_GAP_ALERT   = 0.04      # 4¢ 以上提醒
MIN_GAP_TRADE   = 0.06      # 6¢ 以上可交易
CANDLE_DURATION = 3600      # 1小时K线 = 3600秒
GAMMA_API       = "https://gamma-api.polymarket.com"
CLOB_API        = "https://clob.polymarket.com"
BINANCE_API     = "https://api.binance.com"


def _get(url: str, timeout: int = 8) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LagArb/1.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


# ── 布朗运动概率模型 ─────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """标准正态累积分布函数（数值近似）"""
    return (1 + math.erf(x / math.sqrt(2))) / 2


def prob_candle_up(
    current_return: float,
    elapsed_seconds: float,
    total_seconds: float = CANDLE_DURATION,
    hourly_vol: float = BTC_HOURLY_VOL,
) -> float:
    """
    给定K线已进行 elapsed_seconds，当前收益率为 current_return，
    返回K线最终收阳（close ≥ open）的概率。

    公式：P = N(d)
    d = r / (σ * √T_remaining_hours)
    """
    remaining_seconds = total_seconds - elapsed_seconds
    if remaining_seconds <= 0:
        # 已结束
        return 1.0 if current_return >= 0 else 0.0

    remaining_hours = remaining_seconds / 3600
    sigma = hourly_vol * math.sqrt(remaining_hours)

    if sigma == 0:
        return 1.0 if current_return >= 0 else 0.0

    d = current_return / sigma
    return _norm_cdf(d)


# ── Binance K线追踪 ──────────────────────────────────────────────────

@dataclass
class CandleState:
    open_price: float
    current_price: float
    open_time: datetime
    symbol: str = "BTCUSDT"

    @property
    def elapsed_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.open_time).total_seconds()

    @property
    def current_return(self) -> float:
        if self.open_price == 0:
            return 0.0
        return (self.current_price - self.open_price) / self.open_price

    @property
    def prob_up(self) -> float:
        return prob_candle_up(self.current_return, self.elapsed_seconds)


def get_current_candle(symbol: str = "BTCUSDT") -> Optional[CandleState]:
    """
    获取 Binance 当前进行中的 1h K线状态
    返回 CandleState 或 None
    """
    data = _get(f"{BINANCE_API}/api/v3/klines?symbol={symbol}&interval=1h&limit=2")
    if not data or len(data) < 1:
        return None

    current_kline = data[-1]  # 最后一根（进行中）
    open_price = float(current_kline[1])
    current_price = float(current_kline[4])
    open_time_ms = int(current_kline[0])
    open_time = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)

    return CandleState(
        open_price=open_price,
        current_price=current_price,
        open_time=open_time,
        symbol=symbol,
    )


# ── Polymarket 市场状态 ──────────────────────────────────────────────

@dataclass
class BTCDirectionMarket:
    market_id: str
    question: str
    token_up: str
    token_down: str
    event_start: datetime
    end_time: datetime
    poly_price_up: float      # Polymarket UP 的报价（ask，我们买的价格）
    poly_price_down: float
    liquidity: float
    accepting_orders: bool


def fetch_active_btc_direction_markets() -> list[BTCDirectionMarket]:
    """获取当前进行中或即将开始的 BTC 方向市场"""
    # 同时按成交量和流动性各取200个，确保不漏掉 Up/Down 市场
    data_vol = _get(f"{GAMMA_API}/markets?active=true&closed=false&limit=200&order=volume24hr&ascending=false") or []
    data_liq = _get(f"{GAMMA_API}/markets?active=true&closed=false&limit=200&order=liquidity&ascending=false") or []
    seen = set()
    data = []
    for m in data_vol + data_liq:
        mid = m.get("id") or m.get("slug")
        if mid and mid not in seen:
            seen.add(mid)
            data.append(m)
    if not data:
        return []

    now = datetime.now(timezone.utc)
    results = []

    for m in data:
        if "bitcoin up or down" not in m.get("question", "").lower():
            continue
        if not m.get("acceptingOrders", False):
            continue
        if float(m.get("liquidity", 0)) < 100:
            continue

        token_ids_raw = m.get("clobTokenIds", "[]")
        token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
        if len(token_ids) < 2:
            continue

        event_start_str = m.get("eventStartTime") or m.get("startDate")
        end_str = m.get("endDate")
        try:
            event_start = datetime.fromisoformat(event_start_str.replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        except Exception:
            continue

        # 只关注：K线已开始 OR 将在2小时内开始
        hours_to_start = (event_start - now).total_seconds() / 3600
        if hours_to_start > 2:
            continue  # 太早，先不关注
        if end_time < now:
            continue  # 已结束

        # 获取 Polymarket 实时价格（UP token 的 ask）
        up_ask = float(m.get("bestAsk") or 0.51)
        down_ask = 1 - float(m.get("bestBid") or 0.49)  # DOWN 的 ask ≈ 1 - UP bid

        results.append(BTCDirectionMarket(
            market_id=m["id"],
            question=m.get("question", ""),
            token_up=token_ids[0],
            token_down=token_ids[1],
            event_start=event_start,
            end_time=end_time,
            poly_price_up=up_ask,
            poly_price_down=down_ask,
            liquidity=float(m.get("liquidityClob", m.get("liquidity", 0))),
            accepting_orders=m.get("acceptingOrders", False),
        ))

    return results


# ── 套利机会检测 ─────────────────────────────────────────────────────

@dataclass
class LagArbOpportunity:
    market: BTCDirectionMarket
    candle: CandleState
    theoretical_prob_up: float
    poly_price: float           # 我们要买的价格
    direction: str              # "UP" or "DOWN"
    token_id: str
    gap: float                  # 理论概率 - 市场价格（正数=市场低估）
    elapsed_min: float
    remaining_min: float
    candle_return_pct: float
    notes: list = field(default_factory=list)


def find_opportunities(
    candle: Optional[CandleState] = None,
    markets: Optional[list] = None,
    verbose: bool = True,
) -> list[LagArbOpportunity]:
    """
    核心函数：找出所有 Lag Arb 机会
    """
    if candle is None:
        candle = get_current_candle()
    if candle is None:
        if verbose:
            print("  ⚠️ 无法获取 Binance K线数据")
        return []

    if markets is None:
        markets = fetch_active_btc_direction_markets()

    now = datetime.now(timezone.utc)
    opportunities = []

    for mkt in markets:
        # 判断这个市场对应的是哪根K线
        # eventStartTime = K线开盘时间
        candle_start = mkt.event_start
        candle_end = mkt.end_time

        # 检查当前 Binance K线是否就是这个市场对应的K线
        if not (candle_start <= now <= candle_end):
            # K线还没开始，用预测信号（基于当前趋势）
            if candle_start > now:
                elapsed = 0
                prob_up = candle.prob_up  # 用当前K线状态预估
            else:
                continue
        else:
            # K线正在进行，用精确计算
            elapsed = (now - candle_start).total_seconds()
            prob_up = prob_candle_up(candle.current_return, elapsed)

        elapsed_min = elapsed / 60
        remaining_min = (candle_end - now).total_seconds() / 60

        # 确定套利方向
        # UP 套利：理论 prob_up > poly_price_up + MIN_GAP_ALERT
        gap_up = prob_up - mkt.poly_price_up
        gap_down = (1 - prob_up) - mkt.poly_price_down

        best_gap = max(gap_up, gap_down)
        if best_gap < MIN_GAP_ALERT:
            continue

        if gap_up >= gap_down:
            direction = "UP"
            token_id = mkt.token_up
            poly_price = mkt.poly_price_up
            gap = gap_up
        else:
            direction = "DOWN"
            token_id = mkt.token_down
            poly_price = mkt.poly_price_down
            gap = gap_down

        notes = []
        ret_pct = candle.current_return * 100
        if abs(ret_pct) > 0.5:
            notes.append(f"K线已移动 {ret_pct:+.2f}%")
        if elapsed_min > 30:
            notes.append(f"K线已过 {elapsed_min:.0f}分钟")
        if remaining_min < 10:
            notes.append(f"⚠️ 剩余仅 {remaining_min:.0f}分钟")

        opportunities.append(LagArbOpportunity(
            market=mkt,
            candle=candle,
            theoretical_prob_up=prob_up,
            poly_price=poly_price,
            direction=direction,
            token_id=token_id,
            gap=gap,
            elapsed_min=elapsed_min,
            remaining_min=remaining_min,
            candle_return_pct=ret_pct,
            notes=notes,
        ))

    opportunities.sort(key=lambda x: x.gap, reverse=True)

    if verbose:
        candle_ret = candle.current_return * 100
        elapsed_m = candle.elapsed_seconds / 60
        print(f"\n{'='*55}")
        print(f"  ⚡ Lag Arb 扫描  {datetime.now().strftime('%H:%M:%S')}")
        print(f"  BTC: ${candle.current_price:,.0f}  K线开盘: ${candle.open_price:,.0f}")
        print(f"  K线收益: {candle_ret:+.3f}%  已过: {elapsed_m:.1f}分钟")
        print(f"  理论收阳概率: {candle.prob_up:.1%}")
        print(f"  监控市场: {len(markets)} 个  套利机会: {len(opportunities)} 个")

        for opp in opportunities:
            marker = "🔥" if opp.gap >= MIN_GAP_TRADE else "👀"
            print(f"\n  {marker} {opp.direction}  gap={opp.gap:.3f}")
            print(f"     {opp.market.question[:55]}")
            print(f"     理论: {opp.theoretical_prob_up:.1%}  市场: {opp.poly_price:.3f}")
            print(f"     剩余: {opp.remaining_min:.0f}min  K线: {opp.candle_return_pct:+.2f}%")
            if opp.notes:
                print(f"     📝 {' | '.join(opp.notes)}")
        print(f"{'='*55}\n")

    return opportunities


# ── 持续监控（带休眠） ───────────────────────────────────────────────

def monitor_loop(
    interval_seconds: int = 15,
    max_iterations: int = 240,   # 最多跑1小时
    on_opportunity=None,          # 回调函数
    verbose: bool = True,
) -> None:
    """
    持续监控循环，每 interval_seconds 检查一次
    on_opportunity: 发现机会时的回调 (opportunity) -> bool (True=已处理)
    """
    print(f"  🔄 启动 Lag Arb 监控 (间隔 {interval_seconds}s, 最多 {max_iterations} 次)")
    handled = set()  # 已处理的市场 ID，避免重复下单

    for i in range(max_iterations):
        candle = get_current_candle()
        markets = fetch_active_btc_direction_markets()
        opps = find_opportunities(candle, markets, verbose=verbose)

        for opp in opps:
            if opp.gap < MIN_GAP_TRADE:
                continue
            mkt_id = opp.market.market_id
            if mkt_id in handled:
                continue
            if on_opportunity:
                handled_flag = on_opportunity(opp)
                if handled_flag:
                    handled.add(mkt_id)

        time.sleep(interval_seconds)


if __name__ == "__main__":
    # 测试：单次扫描
    candle = get_current_candle()
    markets = fetch_active_btc_direction_markets()

    print(f"当前 BTC K线：开盘 ${candle.open_price:,.2f}  现价 ${candle.current_price:,.2f}")
    print(f"K线收益：{candle.current_return*100:+.3f}%  已过 {candle.elapsed_seconds/60:.1f}分钟")
    print(f"理论收阳概率：{candle.prob_up:.1%}\n")
    print(f"监控市场数量：{len(markets)}")
    for m in markets:
        print(f"  {m.question} | UP ask={m.poly_price_up} | start={m.event_start.strftime('%H:%M UTC')}")

    opps = find_opportunities(candle, markets, verbose=True)
