"""
btc_direction/scanner.py — 扫描 Polymarket BTC 方向市场并评估机会

核心逻辑：
1. 获取所有活跃 "Bitcoin Up or Down" 市场
2. 过滤掉即将开始（<30min）或已结束的市场
3. 计算 BTC 信号
4. 配对信号与市场，找到有边际的机会
"""

import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from .signals import compute_signal, BTCSignalResult

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# 入场时机配置
MIN_MINUTES_BEFORE_START = 30     # 开盘前至少30分钟才入场（避免噪音）
MAX_HOURS_BEFORE_START   = 24     # 最早提前24小时入场（小时级别趋势预测）
MIN_EDGE = 0.04                   # 最低边际 4%
MAKER_FEE = 0.000                 # Polymarket 市价 taker fee（通常0）
MARKET_SPREAD = 0.02              # 买ask需多付2%的价差成本


def _get(url: str, timeout: int = 12) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BTCScanner/1.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None


def fetch_btc_direction_markets() -> list[dict]:
    """获取所有活跃有流动性的 BTC 方向市场"""
    data = _get(
        f"{GAMMA_API}/markets?active=true&closed=false&limit=200&order=liquidity&ascending=false"
    )
    if not data:
        return []

    now = datetime.now(timezone.utc)
    results = []

    for m in data:
        if "bitcoin up or down" not in m.get("question", "").lower():
            continue
        if float(m.get("liquidity", 0)) < 100:
            continue
        if not m.get("acceptingOrders", False):
            continue

        # 解析关键时间
        event_start_str = m.get("eventStartTime") or m.get("startDate")
        end_str = m.get("endDate")

        try:
            event_start = datetime.fromisoformat(event_start_str.replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        except Exception:
            continue

        # 过滤：只取未开始的（eventStartTime 在未来）
        if event_start <= now:
            continue  # 已经在计时中的，跳过（进场太晚可能没优势）

        minutes_to_start = (event_start - now).total_seconds() / 60
        hours_to_start = minutes_to_start / 60

        if minutes_to_start < MIN_MINUTES_BEFORE_START:
            continue  # 太近了，不入场
        if hours_to_start > MAX_HOURS_BEFORE_START:
            continue  # 太远了

        # 解析 token IDs
        token_ids = json.loads(m.get("clobTokenIds", "[]"))
        if len(token_ids) < 2:
            continue

        results.append({
            "market_id": m["id"],
            "question": m["question"],
            "slug": m.get("slug", ""),
            "event_start": event_start,
            "end_time": end_time,
            "minutes_to_start": round(minutes_to_start),
            "liquidity": float(m.get("liquidity", 0)),
            "liquidity_clob": float(m.get("liquidityClob", 0)),
            "token_up": token_ids[0],
            "token_down": token_ids[1],
            "best_bid": float(m.get("bestBid") or 0.49),
            "best_ask": float(m.get("bestAsk") or 0.51),
            "outcome_prices": m.get("outcomePrices"),
        })

    return results


@dataclass
class BTCOpportunity:
    market: dict
    signal: BTCSignalResult
    bet_direction: str         # "UP" or "DOWN"
    token_id: str
    entry_price: float         # 0.51 for taker
    edge: float                # net edge after spread
    kelly_fraction: float
    bet_usdc: float            # suggested bet size
    score: float               # 综合评分 0-100


def evaluate_opportunities(
    signal: Optional[BTCSignalResult] = None,
    balance: float = 0,
    max_bet: float = 5.0,
    min_bet: float = 2.0,
    kelly_frac: float = 0.25,
    verbose: bool = True,
) -> list[BTCOpportunity]:
    """扫描市场并返回有信号的机会"""

    if signal is None:
        signal = compute_signal(verbose=verbose)

    if signal.direction == "NEUTRAL":
        if verbose:
            print(f"  📊 信号中性 (score={signal.score:+.3f})，无机会")
        return []

    markets = fetch_btc_direction_markets()
    if not markets:
        if verbose:
            print("  📭 无活跃 BTC 方向市场")
        return []

    opportunities = []

    for mkt in markets:
        # 确定买哪个token
        if signal.direction == "UP":
            token_id = mkt["token_up"]
            entry_price = mkt["best_ask"]  # 买 UP 需要付 ask
        else:
            token_id = mkt["token_down"]
            # DOWN token 通常是 1 - ask_up
            entry_price = 1 - mkt["best_bid"]

        # 确保入场价在合理范围
        entry_price = max(0.45, min(0.55, entry_price))

        # 净边际 = 信号边际 - 价差成本
        raw_edge = signal.edge
        spread_cost = abs(entry_price - 0.50)
        net_edge = raw_edge - spread_cost

        if net_edge < MIN_EDGE:
            continue

        # Kelly 仓位
        b = (1 / entry_price) - 1   # 赔率
        p = entry_price + net_edge   # 估算胜率
        q = 1 - p
        kelly = (b * p - q) / b if b > 0 else 0
        kelly = max(0, kelly)

        if balance > 0:
            bet = balance * kelly_frac * kelly
            bet = round(max(min_bet, min(max_bet, bet)), 2)
        else:
            bet = min_bet

        # 综合评分
        score = (
            min(100, net_edge * 800) * 0.4 +        # 边际贡献 40%
            signal.confidence * 100 * 0.3 +          # 置信度 30%
            min(100, mkt["liquidity_clob"] / 100) * 0.2 +  # 流动性 20%
            min(100, mkt["minutes_to_start"] / 240 * 100) * 0.1  # 时间余量 10%
        )

        opportunities.append(BTCOpportunity(
            market=mkt,
            signal=signal,
            bet_direction=signal.direction,
            token_id=token_id,
            entry_price=entry_price,
            edge=net_edge,
            kelly_fraction=kelly,
            bet_usdc=bet,
            score=score,
        ))

    # 按评分排序
    opportunities.sort(key=lambda x: x.score, reverse=True)

    if verbose:
        print(f"\n  找到 {len(markets)} 个市场，{len(opportunities)} 个有信号机会")
        for opp in opportunities[:3]:
            mkt = opp.market
            print(f"\n  ⚡ [{opp.score:.0f}分] {mkt['question']}")
            print(f"     方向: {opp.bet_direction} @ {opp.entry_price:.3f}")
            print(f"     净边际: {opp.edge:.1%}  置信: {opp.signal.confidence:.0%}")
            print(f"     建议下注: ${opp.bet_usdc}")
            print(f"     距开盘: {mkt['minutes_to_start']} 分钟")

    return opportunities


if __name__ == "__main__":
    sig = compute_signal(verbose=True)
    opps = evaluate_opportunities(sig, balance=65.0, verbose=True)
    if not opps:
        print("当前无满足条件的机会")
