# ============================================================
# 🔒 POLYMARKET TOOLKIT PRO — Preview Stub
# Full version: https://gumroad.com/l/polymarket-toolkit-pro
# This file shows the API surface. Upgrade to Pro for the
# complete implementation (~99 lines of battle-tested code).
# ============================================================
"""
backtest/metrics.py — 回测性能指标

计算：Sharpe ratio, 最大回撤, 胜率, 平均盈亏比,
      连续亏损次数, 资金曲线, 风险调整收益等。
"""

_PRO_URL = "https://gumroad.com/l/polymarket-toolkit-pro"


def compute_metrics(result) -> dict:
    """
    计算完整回测指标

    返回：
        {
            'total_trades': int,
            'win_rate': float,
            'total_pnl': float,
            'roi_pct': float,
            'sharpe_ratio': float,
            'max_drawdown': float,
            'avg_win': float,
            'avg_loss': float,
            'profit_factor': float,
            'max_consecutive_losses': int,
            'equity_curve': list[float],
        }
    """
    raise NotImplementedError(f"🔒 PRO feature. Get full version at: {_PRO_URL}")


if __name__ == "__main__":
    print("🔒 Polymarket Toolkit Pro — Performance Metrics")
    print("This module requires a Pro license.")
    print(f"Get it at: {_PRO_URL}")
    print()
    print("Free modules available: edge_scanner, live_scanner, monitor_positions,")
    print("price_tracker, market_classifier, scorer, data_fetcher")
