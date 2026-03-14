#!/usr/bin/env python3
"""
market_classifier.py — 市场类型识别 + 机会优先级打分

根据 ChatGPT 案例分析的 8 类最赚钱市场类型，
对每个 Polymarket 市场打分，指导 maker_trader 选择目标。

优先级：
  P1 - 跨市场套利（无风险）
  P2 - 加密价格（BTC lag优势）
  P3 - 时间窗口误定价（time decay arb）
  P4 - 体育市场（丰富数据对比）
  P5 - 宏观经济（Fed等）
  P6 - Breaking News（新闻速度）
  P7 - 大选（资金量大才适合）
  P8 - ETF/监管（解读优势）
"""

import re

# ── 关键词库 ──────────────────────────────────────────────────────────

CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "binance", "bnb", "coinbase", "defi", "nft", "altcoin", "satoshi",
    "halving", "etf", "blockchain", "xrp", "ripple",
]

ELECTION_KEYWORDS = [
    "election", "president", "vote", "senator", "congress", "democrat",
    "republican", "primary", "ballot", "candidate", "governor", "mayor",
    "win", "seat", "poll", "party", "campaign",
    "trump", "biden", "harris", "macron", "modi", "xi jinping",
]

MACRO_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "gdp", "recession", "unemployment", "fomc",
    "bps", "basis point", "monetary", "fiscal", "bond yield",
    "ecb", "boe", "bank of japan", "central bank",
]

SPORTS_KEYWORDS = [
    "super bowl", "nfl", "nba", "fifa", "world cup", "champion",
    "win the", "beat", "defeat", "score", "match", "game",
    "soccer", "football", "basketball", "baseball", "hockey",
    "tennis", "golf", "f1", "formula", "nascar", "ufc", "mma",
    "league", "playoff", "championship", "cup", "open",
    "o/u", "over/under", "spread",
]

BREAKING_NEWS_KEYWORDS = [
    "resign", "arrest", "war", "strike", "attack", "bomb",
    "assassin", "coup", "collapse", "crisis", "sanction",
    "iran", "russia", "ukraine", "israel", "china", "taiwan",
    "ceasefire", "peace", "invasion", "troops", "missile",
    "nuclear", "chemical", "drone",
]

ETF_REGULATORY_KEYWORDS = [
    "etf", "sec", "approval", "regulation", "ban", "legal",
    "lawsuit", "court", "rule", "compliance", "license",
    "regulatory", "approved", "rejected",
]

TIME_WINDOW_WORDS = [
    "by end of", "before", "by march", "by april", "by may",
    "by june", "by july", "by august", "by september", "by october",
    "by november", "by december", "in 2025", "in 2026", "in 2027",
    "by q1", "by q2", "by q3", "by q4", "within", "this month",
    "this year", "this week", "this quarter",
]


# ── 市场类型检测 ───────────────────────────────────────────────────────

def classify(question: str, end_date: str = "", days_left: int = 999) -> dict:
    """
    返回 {
        type: str,           # 主类型
        subtypes: list,      # 次类型
        priority: int,       # 1=最高
        maker_score: float,  # 做市价值 0-1
        arb_score: float,    # 套利价值 0-1
        notes: str           # 说明
    }
    """
    q = question.lower()

    # 关键词匹配
    is_crypto   = any(kw in q for kw in CRYPTO_KEYWORDS)
    is_election = any(kw in q for kw in ELECTION_KEYWORDS)
    is_macro    = any(kw in q for kw in MACRO_KEYWORDS)
    is_sports   = any(kw in q for kw in SPORTS_KEYWORDS)
    is_news     = any(kw in q for kw in BREAKING_NEWS_KEYWORDS)
    is_etf      = any(kw in q for kw in ETF_REGULATORY_KEYWORDS)
    has_time    = any(kw in q for kw in TIME_WINDOW_WORDS)

    # 选举优先级高于 sports（避免 "win the election" 被误判为 sports）
    if is_election and ("election" in q or "president" in q or "vote" in q):
        is_sports = False

    # 时间窗口分析
    time_arb_bonus = 0.0
    notes = []

    if has_time and days_left < 30:
        time_arb_bonus = 0.15
        notes.append(f"⏱️ {days_left}天窗口 — 时间衰减套利")
    elif has_time and days_left < 90:
        time_arb_bonus = 0.08
        notes.append(f"📅 {days_left}天中期窗口")

    # 类型 + 优先级
    subtypes = []
    if is_crypto:   subtypes.append("crypto")
    if is_election: subtypes.append("election")
    if is_macro:    subtypes.append("macro")
    if is_sports:   subtypes.append("sports")
    if is_news:     subtypes.append("news")
    if is_etf:      subtypes.append("etf_reg")
    if has_time:    subtypes.append("time_window")

    # 主类型 + 优先级逻辑
    if is_crypto and is_etf:
        mtype, priority = "crypto_etf", 2
        maker_score, arb_score = 0.7, 0.8
        notes.append("ETF监管事件 + 加密价格双重机会")
    elif is_crypto:
        mtype, priority = "crypto", 2
        maker_score, arb_score = 0.75, 0.70
        notes.append("BTC Lag Arb适用")
    elif is_sports:
        mtype, priority = "sports", 4
        maker_score, arb_score = 0.70, 0.65
        notes.append("传统赔率可对比")
    elif is_macro:
        mtype, priority = "macro", 5
        maker_score, arb_score = 0.65, 0.60
        notes.append("宏观模型可套利")
    elif is_news:
        mtype, priority = "breaking_news", 6
        maker_score, arb_score = 0.50, 0.55
        notes.append("新闻速度优势")
    elif is_election:
        mtype, priority = "election", 7
        maker_score, arb_score = 0.80, 0.85  # 高流动性高spread
        notes.append("高流动性但需大资金")
    elif is_etf:
        mtype, priority = "etf_reg", 8
        maker_score, arb_score = 0.60, 0.65
        notes.append("监管解读套利")
    else:
        mtype, priority = "other", 9
        maker_score, arb_score = 0.40, 0.30

    arb_score = min(1.0, arb_score + time_arb_bonus)

    return {
        "type": mtype,
        "subtypes": subtypes,
        "priority": priority,
        "maker_score": round(maker_score, 2),
        "arb_score": round(arb_score, 2),
        "notes": " | ".join(notes) if notes else "",
    }


def score_for_maker(market: dict) -> float:
    """
    综合评分：越高越适合 maker 挂单
    = 类型分 × 流动性分 × spread分 × 时间分
    """
    q         = market.get("question", "")
    bid       = float(market.get("bestBid") or 0)
    ask       = float(market.get("bestAsk") or 0)
    vol       = float(market.get("volume24hr") or 0)
    days_left = market.get("_days_left", 999)

    if ask <= 0 or bid <= 0:
        return 0.0

    spread = ask - bid
    spread_score = min(1.0, spread / 0.10)    # spread 10¢ = 满分，越大越好

    # 流动性分（log scale）
    import math
    liq_score = min(1.0, math.log10(max(vol, 1)) / 6)  # $1M vol = 0.83分

    # 价格在中间好（避免0.95+或0.05-的极端市场）
    center_score = 1.0 - abs(0.5 - ask) * 2
    center_score = max(0, center_score)

    # 类型分
    info = classify(q, days_left=days_left)
    type_score = info["maker_score"]

    total = type_score * 0.3 + liq_score * 0.3 + spread_score * 0.25 + center_score * 0.15
    return round(total, 3)


def label(question: str, days_left: int = 999) -> str:
    """返回简短类型标签"""
    info = classify(question, days_left=days_left)
    icons = {
        "crypto": "₿",
        "crypto_etf": "₿📋",
        "election": "🗳️",
        "macro": "🏦",
        "sports": "⚽",
        "breaking_news": "📰",
        "etf_reg": "📋",
        "other": "❓",
    }
    return icons.get(info["type"], "❓")


# ── 简单测试 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Will BTC reach $100k by June 2026?", 90),
        ("Will the Fed cut rates by 50bps in March?", 20),
        ("Super Bowl winner: Chiefs?", 7),
        ("Will Trump win the 2028 election?", 730),
        ("Will Iran regime fall by June 30?", 114),
        ("Will the SEC approve a Bitcoin spot ETF?", 180),
        ("Will George Russell be the 2026 F1 Champion?", 270),
    ]

    print("\n市场类型分类测试\n" + "="*60)
    for q, days in tests:
        info = classify(q, days_left=days)
        icon = label(q, days)
        print(f"{icon} [{info['type']}] P{info['priority']}  maker={info['maker_score']}  arb={info['arb_score']}")
        print(f"   {q[:55]}")
        if info['notes']:
            print(f"   {info['notes']}")
        print()
