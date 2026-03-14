"""
实时市场扫描 v2 - 集成真实价格时序数据

改进：
- 使用 ComboSignal（规则 + 动量 + 流动性集成）
- 展示时序数据质量和动量特征
- 自动触发快照，积累历史数据
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import fetch_active_markets
from signals.combo import ComboSignalGenerator
from signals.momentum import MomentumSignalGenerator
from signals.price_series import build_market_features, features_summary
from price_tracker import take_snapshot, get_stats, load_all_latest_prices
from datetime import datetime, timezone


def scan_live(verbose: bool = True, auto_snapshot: bool = True) -> list[dict]:
    """
    扫描所有活跃市场，返回排序好的机会列表。
    
    auto_snapshot: 扫描前自动拍摄价格快照（积累历史数据）
    """
    if auto_snapshot:
        if verbose:
            print("  📸 采集价格快照...", end=" ", flush=True)
        try:
            n = take_snapshot(verbose=False)
            if verbose:
                s = get_stats()
                print(f"✓ {n} 市场 | 累计 {s.get('total_records',0):,} 条/{s.get('days',0)} 天")
        except Exception as e:
            if verbose:
                print(f"⚠ {e}")

    combo_gen = ComboSignalGenerator()
    opportunities = []

    tags = ["politics", "crypto", "geopolitics", "finance", "tech", None]
    seen = set()

    if verbose:
        print("  🔍 扫描市场...", end=" ", flush=True)

    all_markets = []
    for tag in tags:
        markets = fetch_active_markets(limit=80, tag=tag)
        for m in markets:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            all_markets.append(m)

    if verbose:
        print(f"✓ {len(all_markets)} 个唯一市场")

    # 加载本地价格缓存（用于快速判断有无历史数据）
    cached_latest = load_all_latest_prices()

    for m in all_markets:
        sig = combo_gen.generate(m)
        if sig and sig.direction != "SKIP" and sig.is_actionable:
            try:
                end = datetime.fromisoformat(m["end_date"].replace("Z", "+00:00"))
                days_left = max(0, (end - datetime.now(timezone.utc)).days)
            except Exception:
                days_left = 999

            # 加载时序特征
            feat = None
            try:
                feat = build_market_features(m["id"], days_back=7)
            except Exception:
                pass

            # 综合评分：边际 × 置信度 × 时间权重 × 数据质量加成
            data_bonus = 1.0
            if feat and feat.data_quality == "good":
                data_bonus = 1.20
            elif feat and feat.data_quality == "ok":
                data_bonus = 1.10

            score = sig.edge * sig.confidence * (1 / max(1, days_left / 30)) * data_bonus

            opportunities.append({
                "market": m,
                "signal": sig,
                "days_left": days_left,
                "score": score,
                "feat": feat,
                "has_history": m["id"] in cached_latest,
            })

    opportunities.sort(key=lambda x: -x["score"])

    if verbose:
        _print_report(opportunities, len(seen))

    return opportunities


def _print_report(opportunities: list[dict], n_scanned: int):
    from collections import Counter

    n_with_history = sum(1 for o in opportunities if o["has_history"])

    print(f"\n{'='*72}")
    print(f"  📡 Polymarket 实时机会扫描 v2  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  扫描: {n_scanned} 市场  |  机会: {len(opportunities)}  |  含历史数据: {n_with_history}")
    print(f"{'='*72}\n")

    if not opportunities:
        print("  暂无满足条件的机会\n")
        return

    for i, opp in enumerate(opportunities[:12], 1):
        m    = opp["market"]
        sig  = opp["signal"]
        dl   = opp["days_left"]
        yp   = m["yes_price"]
        liq  = m["liquidity"]
        feat = opp["feat"]

        direction_str = "做 NO  ↓" if sig.direction == "NO" else "做 YES ↑"
        bet_price     = (1 - yp) if sig.direction == "NO" else yp
        implied_odds  = 1 / bet_price if bet_price > 0 else 0

        # 数据质量徽章
        dq = "⬛"  # 无历史
        if feat:
            dq = {"none": "⬛", "sparse": "🟡", "ok": "🟠", "good": "🟢"}.get(
                feat.data_quality, "⬛"
            )

        print(
            f"  #{i:<2} {dq} [{sig.category.upper():12s}]  {direction_str}"
            f"  @ {bet_price:.3f}¢  ({implied_odds:.1f}x)"
        )
        print(f"       {m['question'][:68]}")
        print(
            f"       边际:{sig.edge:.1%}  置信:{sig.confidence:.0%}  "
            f"liq:${liq:,.0f}  {dl}天  Kelly:{sig.kelly_fraction:.1%}"
        )
        print(f"       {sig.reasoning[:85]}")

        # 时序信息（如果有）
        if feat and feat.data_quality in ("ok", "good"):
            fsum = features_summary(feat)
            print(f"       📈 {fsum}")

        print()

    # 汇总
    cats = Counter(o["signal"].category for o in opportunities)
    dirs = Counter(o["signal"].direction for o in opportunities)
    src  = Counter(o["signal"].source for o in opportunities)

    print(f"  类别: {dict(cats)}")
    print(f"  方向: YES={dirs['YES']}  NO={dirs['NO']}")
    print(f"  信号源: {dict(src)}")
    print()

    # 价格数据库状态
    s = get_stats()
    if s.get("total_records"):
        print(
            f"  📁 本地数据库: {s['total_records']:,} 条 | "
            f"{s['unique_markets']} 市场 | {s['days']} 天"
        )
        print(
            f"  💡 提示：每次运行会积累历史数据，"
            f"数据越多动量信号越准确 (🟢=好 🟠=中 🟡=少 ⬛=无)"
        )

    print(f"{'='*72}\n")


if __name__ == "__main__":
    opps = scan_live()
