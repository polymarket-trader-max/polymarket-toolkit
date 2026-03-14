#!/usr/bin/env python3
"""
cross_arb_scanner.py — 跨市场逻辑套利扫描器

原理：找 Polymarket 上逻辑上互相约束的市场对，
发现价格违反概率公理时（P(A) > P(B) 但 A 是 B 的子集），下注套利。

典型模式：
  1. 时间嵌套：P(event by Month 1) ≤ P(event by Year end)
  2. 粒度嵌套：P(candidate X wins) ≤ P(party wins)
  3. 子集关系：P(specific outcome) ≤ P(broader outcome)

用法：
  ./venv/bin/python3 cross_arb_scanner.py
"""

import json, re, subprocess, time
from datetime import datetime, timezone
from difflib import SequenceMatcher


def fetch_markets(limit=200, offset=0):
    url = (f"https://gamma-api.polymarket.com/markets"
           f"?limit={limit}&offset={offset}&active=true&closed=false"
           f"&order=volume24hr&ascending=false")
    r = subprocess.run(f'curl -s "{url}"', shell=True,
                       capture_output=True, text=True, timeout=10)
    try:
        return json.loads(r.stdout) if r.stdout else []
    except Exception:
        return []


def get_price(m):
    """返回 YES 的 ask 价（买入价），过滤无效值"""
    try:
        p = float(m.get("bestAsk") or m.get("lastTradePrice") or 0)
        return p if 0.02 <= p <= 0.98 else 0.0  # 过滤已结算/无效市场
    except Exception:
        return 0.0


def get_bid(m):
    """返回 YES 的 bid 价（卖出价）"""
    try:
        p = float(m.get("bestBid") or 0)
        return p if 0.02 <= p <= 0.98 else 0.0
    except Exception:
        return 0.0


def similarity(a, b):
    """字符串相似度 0-1"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ── 时间嵌套检测 ────────────────────────────────────────────────────

MONTH_PATTERNS = [
    r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|'
    r'july|jul|august|aug|september|sep|october|oct|november|nov|december|dec)\b',
    r'\bq[1-4]\b', r'\b(h1|h2)\b',
    r'\b(month|week|day)\b',
    r'\b(2025|2026|2027)\b',
]

def extract_time_scope(question):
    """从问题中提取时间范围关键词"""
    q = question.lower()
    tokens = []
    for pat in MONTH_PATTERNS:
        m = re.search(pat, q)
        if m:
            tokens.append(m.group())
    return tokens


def strip_time(question):
    """移除时间词后的核心问题"""
    q = question.lower()
    q = re.sub(r'\b(by|before|in|during|until|end of)\s+\w+', '', q)
    q = re.sub(r'\b\d{4}\b', '', q)
    q = re.sub(r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|'
               r'july|jul|august|aug|september|sep|october|oct|november|nov|'
               r'december|dec|q[1-4]|h1|h2)\b', '', q)
    q = re.sub(r'\s+', ' ', q).strip()
    return q


# ── 主扫描逻辑 ────────────────────────────────────────────────────

def find_time_nested_pairs(markets):
    """
    找时间嵌套套利：
    P(X by March) > P(X by 2026) → 卖 March YES，买 2026 YES
    """
    opportunities = []

    # 按核心主题分组
    groups = {}
    for m in markets:
        q = m.get("question", "")
        core = strip_time(q)
        if len(core) < 10:
            continue
        key = core[:40]
        if key not in groups:
            groups[key] = []
        groups[key].append(m)

    for key, group in groups.items():
        if len(group) < 2:
            continue
        # 在同组内找价格矛盾
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                m1, m2 = group[i], group[j]
                q1, q2 = m1.get("question", ""), m2.get("question", "")

                # 相似度检查
                sim = similarity(q1, q2)
                if sim < 0.55:
                    continue

                p1 = get_price(m1)
                p2 = get_price(m2)

                if p1 <= 0 or p2 <= 0:
                    continue

                bid1 = get_bid(m1)
                bid2 = get_bid(m2)

                vol1 = float(m1.get("volume24hr") or 0)
                vol2 = float(m2.get("volume24hr") or 0)

                if min(vol1, vol2) < 1000:
                    continue  # 流动性太低

                # 找 p1 > p2（违反子集关系）
                gap = p1 - p2
                if gap > 0.05:  # 至少 5% 价差才有意义
                    # 套利：卖 m1 YES（bid1），买 m2 YES（ask p2）
                    # 理论利润 = bid1 - p2（每 token）
                    arb_profit = bid1 - p2
                    if arb_profit > 0.03:
                        opportunities.append({
                            "type": "TIME_NESTED",
                            "q1": q1,
                            "q2": q2,
                            "p1": p1,
                            "p2": p2,
                            "gap": gap,
                            "arb_profit": arb_profit,
                            "action": f"卖 YES @ {bid1:.3f}（{q1[:45]}）\n         买 YES @ {p2:.3f}（{q2[:45]}）",
                            "vol1": vol1,
                            "vol2": vol2,
                            "sim": sim,
                            "token_sell": json.loads(m1.get("clobTokenIds") or "[]"),
                            "token_buy":  json.loads(m2.get("clobTokenIds") or "[]"),
                        })

    return opportunities


def find_complementary_mispricing(markets):
    """
    找互补事件定价错误：
    P(A) + P(B) + P(C) ≠ 1.0（对于互斥穷举事件集）
    如果总和 < 0.95，可以全买；如果 > 1.05，可以全卖
    """
    opportunities = []

    # 找相同 slug 前缀的市场（通常是同一个 event 的多个 outcome）
    by_event = {}
    for m in markets:
        slug = m.get("slug", "")
        # 取 slug 的前半部分作为 event key
        parts = slug.rsplit("-", 1)
        if len(parts) == 2:
            event_key = parts[0]
        else:
            event_key = slug
        if event_key not in by_event:
            by_event[event_key] = []
        by_event[event_key].append(m)

    for key, group in by_event.items():
        if len(group) < 3:  # 至少 3 个 outcome
            continue
        prices = [get_price(m) for m in group]
        prices = [p for p in prices if p > 0]
        if len(prices) < 3:
            continue

        total = sum(prices)
        vol_min = min(float(m.get("volume24hr") or 0) for m in group)
        if vol_min < 5000:
            continue

        if total < 0.90:
            profit = 1.0 - total
            opportunities.append({
                "type": "COMPLEMENT_UNDERPRICED",
                "key": key,
                "total_prob": total,
                "profit_per_token": profit,
                "markets": [m.get("question", "")[:50] for m in group],
                "action": f"买入所有 outcome，总价 {total:.3f}，理论利润 {profit:.3f}/token",
            })
        elif total > 1.10:
            profit = total - 1.0
            opportunities.append({
                "type": "COMPLEMENT_OVERPRICED",
                "key": key,
                "total_prob": total,
                "profit_per_token": profit,
                "markets": [m.get("question", "")[:50] for m in group],
                "action": f"卖出所有 outcome，总价 {total:.3f}，理论利润 {profit:.3f}/token",
            })

    return opportunities


def scan(verbose=True):
    if verbose:
        print(f"\n{'='*65}")
        print(f"  🔀 跨市场套利扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  策略：逻辑嵌套矛盾 + 互补事件定价错误")
        print(f"{'='*65}\n")

    all_markets = []
    for offset in [0, 200, 400]:
        batch = fetch_markets(limit=200, offset=offset)
        if not batch:
            break
        all_markets.extend(batch)
        time.sleep(0.3)

    if verbose:
        print(f"  扫描 {len(all_markets)} 个活跃市场...\n")

    opps = []

    # 1. 时间嵌套套利
    time_opps = find_time_nested_pairs(all_markets)
    opps.extend(time_opps)

    # 2. 互补事件定价
    comp_opps = find_complementary_mispricing(all_markets)
    opps.extend(comp_opps)

    # 按利润排序
    opps.sort(key=lambda x: x.get("arb_profit", x.get("profit_per_token", 0)), reverse=True)

    if verbose:
        if opps:
            print(f"  发现 {len(opps)} 个套利机会：\n")
            for i, o in enumerate(opps[:8], 1):
                print(f"  #{i} 类型: {o['type']}")
                print(f"      操作: {o.get('action', o.get('markets', '?'))}")
                profit = o.get('arb_profit', o.get('profit_per_token', 0))
                sim_str = f"{o['sim']:.2f}" if isinstance(o.get('sim'), float) else '-'
                print(f"      理论利润: {profit:.3f}/token  相似度: {sim_str}")
                print()
        else:
            print("  📭 当前无套利机会\n")

    return opps


if __name__ == "__main__":
    opps = scan(verbose=True)
    print(f"结果: {len(opps)} 个机会")
