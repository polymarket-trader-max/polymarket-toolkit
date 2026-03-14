"""
压力测试 v2 - 含真实交易成本，内存优化版

交易成本：
- Polymarket 手续费：赢家利润的 2%
- 买入价差（spread）：平均 1.5%
- 最低下注：$5，最高单注 $200
"""

import sys, os, random, math, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import fetch_resolved_markets
from signals.rules import RulesBasedSignalGenerator
from backtest.engine import _is_junk_market


def run_stress(n_markets=600, bankroll=1000.0, max_kelly=0.05,
               n_sims=10, seed=42, fee=0.02, spread=0.015,
               label="", verbose=True):
    random.seed(seed)
    gen = RulesBasedSignalGenerator()

    # ── 加载并过滤市场 ───────────────────────────────────────────
    raw, offset = [], 0
    while len(raw) < n_markets:
        batch = fetch_resolved_markets(limit=100, offset=offset, min_volume=5000)
        if not batch: break
        raw.extend(batch)
        offset += 100

    valid = [m for m in raw
             if not _is_junk_market(m) and m.get("resolved_yes") is not None]

    if verbose:
        print(f"\n{'─'*55}")
        print(f"  {label or '压力测试'} | 市场:{len(valid)} | 蒙特卡洛:{n_sims}次")
        print(f"  手续费:{fee:.0%}  价差:{spread:.1%}  凯利上限:{max_kelly:.0%}")
        print(f"{'─'*55}")

    # ── 蒙特卡洛模拟 ─────────────────────────────────────────────
    bk = bankroll
    wins = losses = skipped = 0
    pnls, bets_placed = [], []
    cat_stats = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0.0})

    for m in valid:
        resolution = m["resolved_yes"]
        liq = m.get("liquidity", 0)
        vol = m.get("volume", 0)
        cat = m.get("category", "other")

        for _ in range(n_sims):
            sim_yes = random.uniform(0.10, 0.90)

            mkt = {
                "id": m["id"], "question": m["question"],
                "category": cat,
                "yes_price": sim_yes, "no_price": 1 - sim_yes,
                "liquidity": liq, "volume": vol,
                "volume_24h": vol * 0.05,
                "spread": spread, "competitive": 0.7,
                "end_date": m.get("end_date", ""),
                "description": m.get("description", ""),
            }

            sig = gen.generate(mkt)
            if sig is None or sig.direction == "SKIP":
                skipped += 1
                continue

            # 含价差的实际入场价
            if sig.direction == "YES":
                entry = min(0.97, sim_yes + spread / 2)
                won   = (resolution == True)
            else:
                entry = min(0.97, (1 - sim_yes) + spread / 2)
                won   = (resolution == False)

            # 仓位（凯利 + 流动性约束）
            kelly_f = min(sig.kelly_fraction, max_kelly)
            eff_bk  = bk / n_sims
            bet     = eff_bk * kelly_f
            bet     = max(5.0, min(bet, 200.0, liq * 0.03 if liq > 0 else bet))

            if entry <= 0.01:
                skipped += 1
                continue

            # P&L
            if won:
                gross = bet * (1.0 / entry - 1.0)
                pnl   = gross * (1 - fee)
            else:
                pnl   = -bet

            bk += pnl
            pnls.append(pnl / bet)   # 收益率
            bets_placed.append(bet)
            cat_stats[cat]["pnl"] += pnl
            if won:
                wins += 1
                cat_stats[cat]["w"] += 1
            else:
                losses += 1
                cat_stats[cat]["l"] += 1

    total = wins + losses
    win_rate = wins / max(1, total)
    roi = (bk - bankroll) / bankroll * 100

    # Sharpe（年化，假设每月约50笔 → 600/年）
    if len(pnls) > 1:
        mu  = sum(pnls) / len(pnls)
        std = math.sqrt(sum((r - mu)**2 for r in pnls) / (len(pnls) - 1))
        sharpe = (mu / std) * math.sqrt(600) if std > 0 else 0
    else:
        sharpe = 0

    # 最大回撤（简化：用最终 vs 起点）
    total_wagered = sum(bets_placed)
    yot = sum(pnls[i]*bets_placed[i] for i in range(len(pnls))) / max(1, total_wagered) * 100

    if verbose:
        print(f"  下注: {total} | 胜率: {win_rate:.1%} ({wins}W/{losses}L) | 跳过: {skipped}")
        print(f"  ROI: {roi:+.1f}%  Sharpe: {sharpe:.2f}  成交/ROI: {yot:+.2f}%")
        print(f"  最终资金: ${bk:,.2f}  (起始 ${bankroll:,.0f})")
        print(f"\n  类别明细:")
        for c, d in sorted(cat_stats.items(), key=lambda x: -abs(x[1]["pnl"])):
            t = d["w"] + d["l"]
            if t == 0: continue
            print(f"    {c:15s}: {t:3d}笔  胜率{d['w']/t:.0%}  P&L ${d['pnl']:+,.2f}")
        print(f"{'─'*55}")

    return {"total": total, "win_rate": win_rate, "roi": roi,
            "sharpe": sharpe, "final_bk": bk, "yot": yot}


if __name__ == "__main__":
    # 测试1：基准（含标准费率）
    r1 = run_stress(n_markets=600, bankroll=1000, max_kelly=0.05,
                    n_sims=10, seed=42, fee=0.02, spread=0.015,
                    label="基准测试（标准费率）")

    # 测试2：最差情况（高费率）
    r2 = run_stress(n_markets=600, bankroll=1000, max_kelly=0.05,
                    n_sims=10, seed=42, fee=0.03, spread=0.025,
                    label="最坏情况（高费率+大价差）")

    # 测试3：多种子稳定性
    print(f"\n{'─'*55}")
    print(f"  多种子稳定性验证（含真实成本）")
    print(f"{'─'*55}")
    seeds = [1, 7, 42, 99, 256]
    results = []
    for s in seeds:
        r = run_stress(n_markets=400, bankroll=1000, max_kelly=0.05,
                       n_sims=10, seed=s, fee=0.02, spread=0.015, verbose=False)
        results.append(r)
        print(f"  Seed={s:3d}: {r['total']:3d}笔  胜率{r['win_rate']:.1%}  "
              f"ROI{r['roi']:+.0f}%  Sharpe{r['sharpe']:.2f}")

    avg_wr  = sum(r["win_rate"] for r in results) / len(results)
    avg_roi = sum(r["roi"] for r in results) / len(results)
    avg_sh  = sum(r["sharpe"] for r in results) / len(results)
    print(f"\n  均值:      胜率{avg_wr:.1%}  ROI{avg_roi:+.0f}%  Sharpe{avg_sh:.2f}")
    print(f"{'─'*55}")

    # 总结判断
    print(f"\n🎯 含成本后模型评估:")
    if avg_roi > 30 and avg_wr > 0.62 and avg_sh > 1.0:
        print(f"  ✅ 达到预期 — 建议进入实盘小额测试阶段")
    elif avg_roi > 10 and avg_wr > 0.55:
        print(f"  🆗 基本可用 — 继续优化信号后可考虑实盘")
    else:
        print(f"  ⚠️  含成本后优势不足 — 需进一步优化")
