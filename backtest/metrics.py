"""
绩效指标计算模块
"""

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import BacktestResult


def compute_metrics(result: "BacktestResult") -> dict:
    if not result.trades:
        return {}

    # 提取数据
    pnls = [t["pnl"] for t in result.trades]
    bet_sizes = [t["bet_size"] for t in result.trades]
    edges = [t["edge"] for t in result.trades]
    confidences = [t["confidence"] for t in result.trades]
    equity = result.equity_curve

    # 收益率
    returns = []
    for t in result.trades:
        r = t["pnl"] / t["bet_size"] if t["bet_size"] > 0 else 0
        returns.append(r)

    # Sharpe Ratio（年化，假设每月50笔交易 = 600/年）
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.001
        annualization = math.sqrt(600)
        sharpe = (mean_r / std_r) * annualization if std_r > 0 else 0
    else:
        sharpe = 0

    # 最大回撤
    max_dd = 0
    peak = result.starting_bankroll
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd

    # Sortino (仅下行风险)
    down_returns = [r for r in returns if r < 0]
    if down_returns:
        down_var = sum(r**2 for r in down_returns) / len(down_returns)
        down_std = math.sqrt(down_var)
        mean_r = sum(returns) / len(returns)
        sortino = (mean_r / down_std) * math.sqrt(600) if down_std > 0 else 0
    else:
        sortino = float("inf")

    # 期望值验证（实际命中率 vs 模型预测概率）
    calibration_errors = []
    for t in result.trades:
        model_prob = t.get("confidence", 0.5)
        actual = 1.0 if t["won"] else 0.0
        calibration_errors.append(abs(model_prob - actual))
    avg_calibration_error = sum(calibration_errors) / len(calibration_errors) if calibration_errors else 0

    # 按类别分组统计
    by_category = {}
    for t in result.trades:
        cat = t.get("category", "other")
        if cat not in by_category:
            by_category[cat] = {"wins": 0, "losses": 0, "pnl": 0}
        if t["won"]:
            by_category[cat]["wins"] += 1
        else:
            by_category[cat]["losses"] += 1
        by_category[cat]["pnl"] += t["pnl"]

    cat_stats = {}
    for cat, d in by_category.items():
        total = d["wins"] + d["losses"]
        cat_stats[cat] = {
            "win_rate": d["wins"] / total if total else 0,
            "pnl": d["pnl"],
            "count": total,
        }

    return {
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3) if sortino != float("inf") else 999,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_return_per_bet": round(sum(returns) / len(returns) * 100, 2),
        "avg_edge": round(sum(edges) / len(edges), 4) if edges else 0,
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0,
        "calibration_error": round(avg_calibration_error, 4),
        "category_breakdown": cat_stats,
        "total_wagered": round(sum(bet_sizes), 2),
        "yield_on_turnover": round(sum(pnls) / sum(bet_sizes) * 100, 2) if sum(bet_sizes) > 0 else 0,
    }
