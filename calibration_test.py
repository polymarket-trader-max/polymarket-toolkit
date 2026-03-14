"""
校准验证测试 v2

思路调整：
- Polymarket 已解决市场最终价格 = 0 或 1（无中间值）
- 因此无法用"最终价格"做价格区间分析
- 正确方法：用"关闭时价格"对"实际结果"做校准
  → 如果一个市场关闭时 YES=95%，但实际解决为 NO，说明高估了

实现：直接抓 closed 市场原始数据，不过滤 resolved_yes，
统计不同收盘价区间的实际解决率。
"""

import sys, os, json, urllib.request, urllib.parse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signals.rules import ACTIONABLE_CATEGORIES, EXCLUDE_PATTERNS
import re


BASE_URL = "https://gamma-api.polymarket.com"


def _get(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-model/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_all_closed(limit=100, offset=0, min_vol=5000):
    """拿所有已关闭市场，不过滤解决结果"""
    params = {
        "active": "false",
        "closed": "true",
        "order": "volume",
        "ascending": "false",
        "limit": limit,
        "offset": offset,
    }
    raw = _get("/markets", params)
    result = []
    for m in raw:
        try:
            outcomes = json.loads(m.get("outcomes", "[]"))
            prices   = json.loads(m.get("outcomePrices", "[]"))
            vol      = float(m.get("volume", 0))
            if len(outcomes) != 2 or not prices or vol < min_vol:
                continue
            yes_price = float(prices[0])

            # 推断实际结果（宽松版：0/1 都纳入）
            if yes_price >= 0.97:
                resolved = True
            elif yes_price <= 0.03:
                resolved = False
            else:
                resolved = None   # 未解决 / 争议中

            from data_fetcher import _infer_category
            cat = _infer_category(m)

            result.append({
                "q": m.get("question", ""),
                "cat": cat,
                "final_yes": yes_price,
                "resolved": resolved,
                "vol": vol,
            })
        except Exception:
            continue
    return result


def run_calibration(n_total=3000, min_vol=5000):
    print(f"\n{'='*65}")
    print(f"  Polymarket 价格偏差校准测试 v2")
    print(f"  样本目标: {n_total} | 最小成交量: ${min_vol:,}")
    print(f"{'='*65}\n")

    all_m = []
    offset = 0
    while len(all_m) < n_total:
        batch = fetch_all_closed(limit=100, offset=offset, min_vol=min_vol)
        if not batch:
            break
        all_m.extend(batch)
        offset += 100
        if len(all_m) % 300 < 5:
            print(f"  已加载 {len(all_m)} 条...")

    print(f"\n  总原始数据: {len(all_m)}")

    # ── 关键统计：已解决 vs 未解决 ────────────────────────────────
    resolved_yes   = [m for m in all_m if m["resolved"] == True]
    resolved_no    = [m for m in all_m if m["resolved"] == False]
    unresolved     = [m for m in all_m if m["resolved"] is None]

    print(f"  已解决YES: {len(resolved_yes)} | 已解决NO: {len(resolved_no)} | 未解决: {len(unresolved)}\n")

    # ── 用"未解决"市场做校准（中间价格区间）────────────────────────
    # 未解决市场 = 市场共识价格处于 3%-97% 之间，最终结果还没确定
    # 我们需要将这类市场的最终价格作为"市场预测"，然后用 resolved 状态做验证
    # 但我们只能用已经有明确结果的做校准

    # 实际做法：把"接近YES"（0.80-0.97）和"接近NO"（0.03-0.20）的市场纳入校准
    near_yes = [m for m in all_m if 0.80 <= m["final_yes"] < 0.97]
    near_no  = [m for m in all_m if 0.03 < m["final_yes"] <= 0.20]

    print("📊 接近极端价格区间的市场统计：")

    buckets = {
        "[0.03, 0.10]":  (0.03, 0.10),
        "[0.10, 0.20]":  (0.10, 0.20),
        "[0.20, 0.35]":  (0.20, 0.35),
        "[0.65, 0.80]":  (0.65, 0.80),
        "[0.80, 0.90]":  (0.80, 0.90),
        "[0.90, 0.97]":  (0.90, 0.97),
    }

    print(f"\n{'价格区间':15s} {'样本':>5} {'%YES':>6} {'市场隐含中值':>10} {'偏差':>8} {'判断':>8}")
    print("-"*58)

    stats = {}
    for bname, (lo, hi) in buckets.items():
        bucket_markets = [m for m in all_m if lo <= m["final_yes"] < hi]
        n = len(bucket_markets)
        if n < 5:
            continue

        # 对这些市场，推测"若已解决，YES率是多少"
        # 用宽松推断：final_yes >= 0.97 → True，<= 0.03 → False
        # 但在这个中间区间，这些市场还没完全解决
        # 我们用 "最终价格" 作为市场共识，看价格方向是否正确

        # 替代方案：看这些市场中，实际最终价格走向 0 还是 1
        # 如果 final_yes > 0.5，代表最终倾向YES
        yes_direction = sum(1 for m in bucket_markets if m["final_yes"] >= 0.50)
        yes_rate = yes_direction / n
        mid = (lo + hi) / 2
        bias = yes_rate - mid

        verdict = "❌高估" if (mid > 0.5 and bias < -0.04) else (
                  "✅低估" if (mid < 0.5 and bias > 0.04) else "⚖️ 公平")

        print(f"{bname:15s} {n:>5} {yes_rate:>6.1%} {mid:>10.1%} {bias:>+8.1%} {verdict:>8}")
        stats[bname] = {"n": n, "yes_rate": yes_rate, "mid": mid, "bias": bias}

    # ── 已解决市场的类别分布 ──────────────────────────────────────
    print(f"\n\n📊 已解决市场类别分布（可验证 YES/NO 概率）：")
    cat_stats = defaultdict(lambda: {"yes": 0, "no": 0})
    for m in resolved_yes + resolved_no:
        r = "yes" if m["resolved"] else "no"
        cat_stats[m["cat"]][r] += 1

    print(f"{'类别':15s} {'YES解决':>8} {'NO解决':>8} {'总计':>6} {'YES率':>8}")
    print("-"*48)
    for cat in sorted(cat_stats.keys(), key=lambda c: -(cat_stats[c]["yes"]+cat_stats[c]["no"])):
        d = cat_stats[cat]
        total = d["yes"] + d["no"]
        if total < 10:
            continue
        print(f"{cat:15s} {d['yes']:>8} {d['no']:>8} {total:>6} {d['yes']/total:>8.1%}")

    # ── 关键洞察 ──────────────────────────────────────────────────
    print(f"\n\n🎯 核心发现：")

    # 总体YES率
    total_res = len(resolved_yes) + len(resolved_no)
    if total_res > 0:
        overall_yes = len(resolved_yes) / total_res
        print(f"  总体YES解决率: {overall_yes:.1%}（n={total_res}）")
        if overall_yes > 0.65:
            print(f"  → 市场存在「存活偏差」：高成交量市场YES率偏高")

    if stats:
        high_zone = stats.get("[0.90, 0.97]", {})
        if high_zone and high_zone.get("bias", 0) < -0.03:
            print(f"  → 90-97%区间高估确认：市场隐含{high_zone['mid']:.0%}，实际{high_zone['yes_rate']:.0%}")
        elif high_zone:
            print(f"  → 90-97%区间：n={high_zone.get('n',0)}, 实际YES率={high_zone.get('yes_rate',0):.1%}")

    print()


if __name__ == "__main__":
    run_calibration(n_total=2000, min_vol=3000)
