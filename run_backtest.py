# ============================================================
# 🔒 POLYMARKET TOOLKIT PRO — Preview Stub
# Full version: https://gumroad.com/l/polymarket-toolkit-pro
# This file shows the API surface. Upgrade to Pro for the
# complete implementation (~123 lines of battle-tested code).
# ============================================================
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
    raise NotImplementedError(
        "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
    )


def _grade(roi: float, win_rate: float, sharpe: float, n_bets: int) -> str:
    raise NotImplementedError(
        "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
    )


if __name__ == "__main__":
    print("🔒 Polymarket Toolkit Pro")
    print("This module requires a Pro license.")
    print("Get it at: https://gumroad.com/l/polymarket-toolkit-pro")
    print()
    print("Free modules available: edge_scanner, live_scanner, monitor_positions,")
    print("price_tracker, market_classifier, scorer, data_fetcher")
