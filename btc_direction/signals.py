"""
btc_direction/signals.py — BTC 1小时K线方向信号引擎

目标：预测 Binance BTC/USDT 特定1小时K线是涨（UP）还是跌（DOWN）
数据来源：Binance 公开 API（无需密钥）
"""

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


def _get(url: str, timeout: int = 10) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BTCSignal/1.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


# ── 数据获取 ────────────────────────────────────────────────────────

def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 48) -> list[dict]:
    """获取 K 线数据，返回解析后的列表"""
    data = _get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    if not data:
        return []
    return [
        {
            "open_time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": k[6],
            "quote_volume": float(k[7]),
            "trades": int(k[8]),
            "taker_buy_volume": float(k[9]),   # 主动买入量
            "taker_sell_volume": float(k[5]) - float(k[9]),  # 主动卖出量
        }
        for k in data
    ]


def get_orderbook_imbalance(symbol: str = "BTCUSDT", depth: int = 20, samples: int = 3) -> float:
    """
    订单簿不平衡度 [-1, +1]，取多次采样平均以降低噪音
    > 0: 买压强 → 价格偏上
    < 0: 卖压强 → 价格偏下
    """
    readings = []
    for _ in range(samples):
        data = _get(f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={depth}")
        if data:
            bid_vol = sum(float(b[1]) for b in data.get("bids", []))
            ask_vol = sum(float(a[1]) for a in data.get("asks", []))
            total = bid_vol + ask_vol
            if total > 0:
                readings.append((bid_vol - ask_vol) / total)
        if samples > 1:
            time.sleep(0.5)
    return sum(readings) / len(readings) if readings else 0.0


def get_funding_rate(symbol: str = "BTCUSDT") -> Optional[float]:
    """
    资金费率（期货）
    负 = 空头付费 = 多头被保护 → 倾向下跌修正
    正 = 多头付费 = 多头过重 → 倾向下跌修正
    注意：极端正 = 多头拥挤 = 可能反转向下
    """
    data = _get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
    if not data:
        return None
    try:
        return float(data["lastFundingRate"])
    except Exception:
        return None


def get_ma_signals(symbol: str = "BTCUSDT") -> dict:
    """
    获取日线 MA50 / MA200，返回均线值和价格偏离度
    > 0: 价格高于均线（多头结构）
    < 0: 价格低于均线（空头结构）
    """
    data = _get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=210")
    if not data or len(data) < 50:
        return {}
    closes = [float(k[4]) for k in data]
    current = closes[-1]
    ma50  = sum(closes[-50:])  / 50
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    dev50  = (current - ma50)  / ma50  * 100
    dev200 = (current - ma200) / ma200 * 100 if ma200 else None
    return {
        "ma50":   ma50,
        "ma200":  ma200,
        "dev50":  dev50,    # % 偏离，负 = 低于均线
        "dev200": dev200,
    }


def get_spot_futures_spread(symbol: str = "BTCUSDT") -> Optional[float]:
    """
    期现价差（%）= (期货价 - 现货价) / 现货价 * 100
    正 = 期货溢价 → 市场预期上涨
    负 = 期货折价 → 市场预期下跌
    """
    spot = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
    futures = _get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}")
    if not spot or not futures:
        return None
    try:
        spot_px = float(spot["price"])
        fut_px = float(futures["price"])
        return (fut_px - spot_px) / spot_px * 100
    except Exception:
        return None


# ── 信号计算 ────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> float:
    """计算 RSI"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def momentum_score(klines: list[dict], periods: list[int] = [1, 3, 6, 12]) -> float:
    """
    多周期动量信号 [-1, +1]
    基于最近 N 根K线的收益率，短周期权重更高
    """
    if len(klines) < max(periods) + 1:
        return 0.0
    closes = [k["close"] for k in klines]
    current = closes[-1]
    weights = [1 / (p ** 0.5) for p in periods]  # 短周期权重更高
    total_weight = sum(weights)
    score = 0.0
    for p, w in zip(periods, weights):
        if len(closes) > p:
            ret = (current - closes[-p - 1]) / closes[-p - 1]
            # 标准化：假设 2% 以上是强信号
            normalized = max(-1, min(1, ret / 0.02))
            score += normalized * w
    return score / total_weight


def volume_flow_score(klines: list[dict], lookback: int = 6) -> float:
    """
    成交量方向流 [-1, +1]
    基于主动买入量 vs 主动卖出量
    """
    recent = klines[-lookback:]
    total_buy = sum(k["taker_buy_volume"] for k in recent)
    total_sell = sum(k["taker_sell_volume"] for k in recent)
    total = total_buy + total_sell
    if total == 0:
        return 0.0
    return (total_buy - total_sell) / total


def candle_pattern_score(klines: list[dict], lookback: int = 3) -> float:
    """
    K线形态得分 [-1, +1]
    - 阳线（收>开）多 → 看涨
    - 连续创新高 → 看涨
    """
    recent = klines[-lookback:]
    score = 0.0
    for k in recent:
        body = k["close"] - k["open"]
        total_range = k["high"] - k["low"]
        if total_range == 0:
            continue
        body_ratio = body / total_range  # +1 = 全阳线, -1 = 全阴线
        score += body_ratio
    return max(-1, min(1, score / lookback))


# ── 综合信号 ────────────────────────────────────────────────────────

@dataclass
class BTCSignalResult:
    # 综合得分 [-1=强DOWN, +1=强UP]
    score: float
    direction: str          # "UP" or "DOWN" or "NEUTRAL"
    confidence: float       # 0-1
    edge: float             # 估算边际优势

    # 各分项信号
    momentum: float = 0.0
    volume_flow: float = 0.0
    orderbook: float = 0.0
    funding_rate: float = 0.0
    spot_futures_spread: float = 0.0
    rsi_value: float = 50.0
    rsi_signal: float = 0.0
    candle_pattern: float = 0.0

    # 市场数据快照
    btc_price: float = 0.0
    notes: list = field(default_factory=list)


# 信号权重配置
SIGNAL_WEIGHTS = {
    "momentum":           0.30,
    "volume_flow":        0.20,
    "orderbook":          0.20,
    "candle_pattern":     0.15,
    "rsi_signal":         0.10,
    "funding_signal":     0.05,
}

# 边际校准系数（基于历史数据调整，初始保守值）
EDGE_SCALE = 0.08   # 信号1.0 → 最大8%边际


def compute_signal(verbose: bool = False) -> BTCSignalResult:
    """
    计算综合 BTC 方向信号。
    """
    notes = []

    # ── 获取数据 ──────────────────────────────────────────────
    klines_1h = get_klines("BTCUSDT", "1h", 48)
    klines_15m = get_klines("BTCUSDT", "15m", 32)
    ob_imbalance = get_orderbook_imbalance("BTCUSDT", 20)
    funding = get_funding_rate("BTCUSDT")
    sf_spread = get_spot_futures_spread("BTCUSDT")
    ma_data = get_ma_signals("BTCUSDT")

    btc_price = klines_1h[-1]["close"] if klines_1h else 0.0

    # ── 各信号 ────────────────────────────────────────────────

    # 1. 多周期动量（1h K线：1、3、6、12 根）
    mom_1h = momentum_score(klines_1h, [1, 3, 6, 12])
    # 15分钟动量（权重较小，捕捉短线）
    mom_15m = momentum_score(klines_15m, [1, 4, 8]) * 0.5
    momentum = (mom_1h * 0.7 + mom_15m * 0.3)

    # 2. 成交量流向（1h最近6根）
    vol_flow = volume_flow_score(klines_1h, 6)

    # 3. 订单簿不平衡（实时）
    ob_score = ob_imbalance  # 已是 [-1, +1]

    # 4. K线形态（1h最近3根）
    candle = candle_pattern_score(klines_1h, 3)

    # 5. RSI 信号（逆势/超买超卖）
    closes_1h = [k["close"] for k in klines_1h]
    rsi_val = rsi(closes_1h, 14)
    if rsi_val > 70:
        rsi_sig = -0.5 * ((rsi_val - 70) / 30)  # 超买 → 轻微看空
        notes.append(f"RSI超买 {rsi_val:.0f}")
    elif rsi_val < 30:
        rsi_sig = 0.5 * ((30 - rsi_val) / 30)   # 超卖 → 轻微看多
        notes.append(f"RSI超卖 {rsi_val:.0f}")
    else:
        rsi_sig = 0.0

    # 6. 资金费率信号
    if funding is not None:
        # 极端正 = 多头拥挤 = 看空；极端负 = 空头拥挤 = 看多
        funding_signal = -max(-1, min(1, funding / 0.0005))
        if abs(funding) > 0.0003:
            direction_word = "空头拥挤" if funding < 0 else "多头拥挤"
            notes.append(f"资金费率{direction_word} {funding:.5f}")
    else:
        funding_signal = 0.0

    # ── 加权综合 ──────────────────────────────────────────────
    components = {
        "momentum":       momentum,
        "volume_flow":    vol_flow,
        "orderbook":      ob_score,
        "candle_pattern": candle,
        "rsi_signal":     rsi_sig,
        "funding_signal": funding_signal,
    }

    score = sum(SIGNAL_WEIGHTS[k] * v for k, v in components.items())
    score = max(-1.0, min(1.0, score))

    # ── 决策 ──────────────────────────────────────────────────
    THRESHOLD = 0.12  # 最低信号强度才下注

    if score >= THRESHOLD:
        direction = "UP"
    elif score <= -THRESHOLD:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    confidence = min(1.0, abs(score) / 0.5)   # 0.5以上视为高置信
    edge = abs(score) * EDGE_SCALE              # 估算边际

    # 期现价差辅助信息
    if sf_spread is not None:
        if sf_spread > 0.1:
            notes.append(f"期货溢价 {sf_spread:.2f}% → 偏多")
        elif sf_spread < -0.1:
            notes.append(f"期货折价 {sf_spread:.2f}% → 偏空")

    # 均线结构（宏观背景，不纳入评分但加入 notes）
    if ma_data:
        dev50  = ma_data.get("dev50")
        dev200 = ma_data.get("dev200")
        ma50   = ma_data.get("ma50")
        ma200  = ma_data.get("ma200")
        if dev50 is not None:
            arrow = "↑" if dev50 > 0 else "↓"
            notes.append(f"MA50=${ma50:,.0f} 偏离{dev50:+.1f}% {arrow}")
        if dev200 is not None:
            arrow = "↑" if dev200 > 0 else "↓"
            notes.append(f"MA200=${ma200:,.0f} 偏离{dev200:+.1f}% {arrow}")

    if verbose:
        print(f"\n{'='*50}")
        print(f"  🔭 BTC方向信号  BTC=${btc_price:,.0f}")
        print(f"{'='*50}")
        print(f"  动量信号:    {momentum:+.3f}  (权重30%)")
        print(f"  成交量流向:  {vol_flow:+.3f}  (权重20%)")
        print(f"  订单簿:      {ob_score:+.3f}  (权重20%)")
        print(f"  K线形态:     {candle:+.3f}  (权重15%)")
        print(f"  RSI({rsi_val:.0f}):      {rsi_sig:+.3f}  (权重10%)")
        print(f"  资金费率:    {funding_signal:+.3f}  (权重5%)")
        print(f"  ────────────────────────────")
        print(f"  综合得分:  {score:+.3f}")
        print(f"  方向:      {direction}")
        print(f"  置信度:    {confidence:.0%}")
        print(f"  估算边际:  {edge:.1%}")
        if notes:
            print(f"  备注:      {' | '.join(notes)}")
        print(f"{'='*50}\n")

    return BTCSignalResult(
        score=score,
        direction=direction,
        confidence=confidence,
        edge=edge,
        momentum=momentum,
        volume_flow=vol_flow,
        orderbook=ob_score,
        funding_rate=funding if funding is not None else 0.0,
        spot_futures_spread=sf_spread if sf_spread is not None else 0.0,
        rsi_value=rsi_val,
        rsi_signal=rsi_sig,
        candle_pattern=candle,
        btc_price=btc_price,
        notes=notes,
    )


if __name__ == "__main__":
    compute_signal(verbose=True)
