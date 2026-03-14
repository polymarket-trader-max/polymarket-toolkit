#!/usr/bin/env python3
"""
time_decay_scanner.py — 时间窗口误定价扫描器

核心逻辑：
  预测市场里，很多人忽略时间概率衰减。
  一个事件"在X天内发生"的概率，随着时间推移会系统性地：
    - 如果接近到期且还未发生 → 概率应快速下降（但市场往往滞后）
    - 如果多个时间窗口定价不一致 → 存在套利空间

策略1: 时间衰减空头
  - 找快到期（<14天）但价格仍然虚高的事件
  - 如果事件没有显著进展，市场应该下跌
  - 做 NO（买 NO token）

策略2: 时间嵌套套利（真正的无风险）
  - P(event by April) > P(event by June) → 逻辑错误，做空 April
  - 只在 P(短期) > P(长期) 时才进场（不同于 cross_arb 的配对交易）

用法：
  ./venv/bin/python3 time_decay_scanner.py
"""

import subprocess, json, math, time
from datetime import datetime, timezone


GAMMA = "https://gamma-api.polymarket.com"


def fetch_markets(limit=200, offset=0):
    url = (f"{GAMMA}/markets?limit={limit}&offset={offset}"
           f"&active=true&closed=false&order=volume24hr&ascending=false")
    r = subprocess.run(f'curl -s "{url}"', shell=True,
                       capture_output=True, text=True, timeout=10)
    try:
        return json.loads(r.stdout) if r.stdout else []
    except Exception:
        return []


def get_days_left(end_str):
    if not end_str:
        return 999
    try:
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        delta = (end - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(0, delta)
    except Exception:
        return 999


def decay_probability(current_price, days_left, total_days=365):
    """
    时间衰减调整：
    如果事件还没发生，随着到期时间临近，真实概率应该下降。
    用简单线性衰减估算：
      adjusted = current_price * (days_left / total_days) ^ 0.5
    这是一个保守估算，实际衰减可能更快。
    """
    if days_left <= 0:
        return 0.0
    ratio = min(1.0, days_left / max(total_days, days_left + 1))
    return current_price * (ratio ** 0.3)


def strip_time_words(q):
    """去掉时间词，提取核心问题"""
    import re
    q2 = q.lower()
    # 去掉 "by March", "before April", "in 2026" 等
    q2 = re.sub(r'\b(by|before|in|during|until|end of|after)\s+\w+', '', q2)
    q2 = re.sub(r'\b\d{4}\b', '', q2)
    q2 = re.sub(r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b', '', q2)
    q2 = re.sub(r'\b(q1|q2|q3|q4|h1|h2)\b', '', q2)
    q2 = re.sub(r'\s+', ' ', q2).strip()
    return q2


def scan_time_decay(markets):
    """策略1: 临近到期但价格虚高"""
    opps = []
    for m in markets:
        q    = m.get("question", "")
        ask  = float(m.get("bestAsk") or 0)
        bid  = float(m.get("bestBid") or 0)
        vol  = float(m.get("volume24hr") or 0)
        end  = m.get("endDate", "")
        days = get_days_left(end)

        if vol < 30000 or ask <= 0 or bid <= 0:
            continue
        if ask < 0.08 or ask > 0.85:
            continue  # 极端市场跳过
        if days > 30 or days < 1:
            continue  # 只看14-30天内到期

        # 计算时间调整后的真实概率
        # 用市场整体存续天数估算
        total_days = 90  # 假设大多数事件有90天窗口
        true_p = decay_probability(ask, days, total_days)

        # 如果市场价比时间调整后的概率高很多 → 做NO
        gap = ask - true_p
        if gap > 0.10:  # 至少10%的定价误差
            no_price = 1 - bid  # NO 的买入价
            opps.append({
                "type": "TIME_DECAY_SHORT",
                "question": q,
                "days_left": round(days, 1),
                "ask_yes": ask,
                "true_p": round(true_p, 3),
                "gap": round(gap, 3),
                "action": f"买 NO @ {no_price:.3f}",
                "no_price": no_price,
                "vol_24h": vol,
                "token_ids": json.loads(m.get("clobTokenIds") or "[]"),
            })

    opps.sort(key=lambda x: x["gap"], reverse=True)
    return opps


def scan_true_arb(markets):
    """
    策略2: 真正的时间嵌套套利
    找 P(短期) > P(长期) 的逻辑矛盾（真正无风险）
    """
    from difflib import SequenceMatcher

    opps = []

    # 按核心问题分组
    groups = {}
    for m in markets:
        q    = m.get("question", "")
        ask  = float(m.get("bestAsk") or 0)
        bid  = float(m.get("bestBid") or 0)
        vol  = float(m.get("volume24hr") or 0)
        end  = m.get("endDate", "")
        days = get_days_left(end)

        if vol < 20000 or ask <= 0 or bid <= 0:
            continue
        if ask < 0.03 or ask > 0.97:
            continue

        core = strip_time_words(q)[:50]
        if core not in groups:
            groups[core] = []
        groups[core].append({
            "question": q, "ask": ask, "bid": bid,
            "vol": vol, "days": days,
            "token_ids": json.loads(m.get("clobTokenIds") or "[]"),
        })

    # 在同组内找 P(短期) > P(长期) 的矛盾
    for core, group in groups.items():
        if len(group) < 2:
            continue

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]

                # 确认两个问题确实相似
                sim = SequenceMatcher(None,
                    a["question"].lower(), b["question"].lower()).ratio()
                if sim < 0.60:
                    continue

                # 谁的时间窗口更短？（days_left更小的是短期）
                if a["days"] < b["days"]:
                    short, long_ = a, b
                else:
                    short, long_ = b, a

                # 真正的逻辑矛盾：P(短期) > P(长期)
                # 短期事件概率不应高于长期
                if short["ask"] > long_["ask"] + 0.05:
                    # 真正套利：
                    # 卖 短期 YES（buy 短期 NO）
                    # 买 长期 YES
                    arb_profit = short["bid"] - long_["ask"]
                    if arb_profit > 0:  # 净利润 > 0 才是真套利
                        opps.append({
                            "type": "TRUE_ARB",
                            "short_q": short["question"][:55],
                            "long_q":  long_["question"][:55],
                            "short_p": short["ask"],
                            "long_p":  long_["ask"],
                            "short_days": round(short["days"], 0),
                            "long_days":  round(long_["days"], 0),
                            "arb_profit": round(arb_profit, 3),
                            "action": (
                                f"买 短期NO @ {1-short['bid']:.3f}  +  "
                                f"买 长期YES @ {long_['ask']:.3f}  "
                                f"= 净利润 {arb_profit:.3f}/token"
                            ),
                            "sim": round(sim, 2),
                        })

    opps.sort(key=lambda x: x["arb_profit"], reverse=True)
    return opps


def scan(verbose=True):
    if verbose:
        print(f"\n{'='*65}")
        print(f"  ⏱️  时间窗口套利扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  策略1: 时间衰减空头（快到期但价格虚高）")
        print(f"  策略2: 时间嵌套真套利（P(短期) > P(长期) 逻辑矛盾）")
        print(f"{'='*65}\n")

    all_markets = []
    for offset in [0, 200, 400]:
        batch = fetch_markets(200, offset)
        if not batch:
            break
        all_markets.extend(batch)
        time.sleep(0.3)

    if verbose:
        print(f"  扫描 {len(all_markets)} 个市场\n")

    decay_opps = scan_time_decay(all_markets)
    arb_opps   = scan_true_arb(all_markets)

    if verbose:
        if decay_opps:
            print(f"  📉 策略1 — 时间衰减空头 ({len(decay_opps)} 个)\n")
            for o in decay_opps[:5]:
                print(f"  {o['question'][:60]}")
                print(f"    市场={o['ask_yes']:.3f}  真实={o['true_p']:.3f}  "
                      f"误定价={o['gap']:.3f}  剩{o['days_left']}天")
                print(f"    操作: {o['action']}  成交量=${o['vol_24h']:,.0f}/24h")
                print()
        else:
            print("  策略1: 暂无时间衰减机会\n")

        if arb_opps:
            print(f"  ✅ 策略2 — 真时间嵌套套利 ({len(arb_opps)} 个)\n")
            for o in arb_opps[:3]:
                print(f"  净利润: {o['arb_profit']:.3f}/token  相似度: {o['sim']}")
                print(f"    SHORT: {o['short_q']} ({o['short_days']:.0f}天) @ {o['short_p']:.3f}")
                print(f"    LONG:  {o['long_q']} ({o['long_days']:.0f}天) @ {o['long_p']:.3f}")
                print(f"    操作: {o['action']}")
                print()
        else:
            print("  策略2: 暂无真套利机会（市场定价合理）\n")

    return {"decay": decay_opps, "arb": arb_opps}


if __name__ == "__main__":
    results = scan(verbose=True)
    print(f"结果: 衰减空头 {len(results['decay'])} 个 | 真套利 {len(results['arb'])} 个")
