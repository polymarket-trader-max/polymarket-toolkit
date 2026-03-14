"""
主回测入口脚本

用法：
    python run_backtest.py
    python run_backtest.py --markets 500 --verbose
    python run_backtest.py --category crypto
"""

import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import BacktestEngine


def main():
    parser = argparse.ArgumentParser(description="Polymarket 回测引擎")
    parser.add_argument("--markets", type=int, default=300, help="回测市场数量")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="起始资金")
    parser.add_argument("--min-volume", type=float, default=5000, help="最小市场成交量")
    parser.add_argument("--max-kelly", type=float, default=0.05, help="单注最大仓位比例")
    parser.add_argument("--min-edge", type=float, default=0.05, help="最小优势阈值")
    parser.add_argument("--category", type=str, default=None, help="过滤类别(crypto/politics/tech/geopolitics)")
    parser.add_argument("--verbose", action="store_true", default=True)
    parser.add_argument("--output", type=str, default=None, help="输出JSON文件路径")
    args = parser.parse_args()

    categories = [args.category] if args.category else None

    engine = BacktestEngine(
        starting_bankroll=args.bankroll,
        max_kelly_pct=args.max_kelly,
        n_simulations=5,
        seed=42,
    )

    print(f"\n🚀 开始回测... ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    result = engine.run(
        n_markets=args.markets,
        min_volume=args.min_volume,
        verbose=args.verbose,
    )

    summary = result.summary()

    # 按类别打印明细
    if "category_breakdown" in summary:
        print("\n📊 各类别表现：")
        for cat, stats in summary["category_breakdown"].items():
            print(f"  {cat:15s}: {stats['count']:3d}笔 | 胜率{stats['win_rate']:.0%} | P&L ${stats['pnl']:+,.2f}")

    # 打印最佳/最差交易
    if result.trades:
        sorted_trades = sorted(result.trades, key=lambda t: t["pnl"], reverse=True)
        print("\n🏆 最佳5笔交易：")
        for t in sorted_trades[:5]:
            print(f"  [{t['direction']}] {t['question'][:50]:50s} P&L: ${t['pnl']:+.2f} (边际:{t['edge']:.2f})")
        print("\n💀 最差5笔交易：")
        for t in sorted_trades[-5:]:
            print(f"  [{t['direction']}] {t['question'][:50]:50s} P&L: ${t['pnl']:+.2f} (边际:{t['edge']:.2f})")

    # 保存结果
    output_path = args.output
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"results/backtest_{ts}.json"
        )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 将 trades 也保存
    full_output = {
        "run_at": datetime.now().isoformat(),
        "config": {
            "markets": args.markets,
            "bankroll": args.bankroll,
            "max_kelly": args.max_kelly,
            "min_edge": args.min_edge,
            "category_filter": args.category,
        },
        "summary": summary,
        "trades": result.trades,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n💾 完整结果已保存至: {output_path}")

    # 最终评分
    roi = summary.get("roi_pct", 0)
    win_rate = summary.get("win_rate", 0)
    sharpe = summary.get("sharpe_ratio", 0)

    print(f"\n🎯 模型评分：")
    grade = _grade(roi, win_rate, sharpe, summary.get("total_bets", 0))
    print(f"  {grade}")
    print()


def _grade(roi: float, win_rate: float, sharpe: float, n_bets: int) -> str:
    if n_bets < 20:
        return "⚠️  样本量不足（<20笔），结果不可靠，需要更多数据"
    if roi > 20 and win_rate > 0.60 and sharpe > 1.5:
        return f"🌟 优秀 | ROI={roi:+.1f}% | 胜率={win_rate:.0%} | Sharpe={sharpe:.2f}"
    elif roi > 10 and win_rate > 0.55 and sharpe > 1.0:
        return f"✅ 良好 | ROI={roi:+.1f}% | 胜率={win_rate:.0%} | Sharpe={sharpe:.2f}"
    elif roi > 0 and win_rate > 0.50:
        return f"🆗 一般 | ROI={roi:+.1f}% | 胜率={win_rate:.0%} | Sharpe={sharpe:.2f} — 需要优化"
    elif roi > -10:
        return f"⚠️  不稳定 | ROI={roi:+.1f}% | 胜率={win_rate:.0%} — 模型需要改进"
    else:
        return f"❌ 失败 | ROI={roi:+.1f}% | 胜率={win_rate:.0%} — 重新设计信号"


if __name__ == "__main__":
    main()
