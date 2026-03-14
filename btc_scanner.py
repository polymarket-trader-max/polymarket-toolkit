#!/usr/bin/env python3
"""
btc_scanner.py — BTC 方向交易顶层入口

用法：
  ./venv/bin/python3 btc_scanner.py             # 扫描信号+机会，不下单
  ./venv/bin/python3 btc_scanner.py --trade     # 扫描并自动下单
  ./venv/bin/python3 btc_scanner.py --positions # 查看当前持仓
  ./venv/bin/python3 btc_scanner.py --signal    # 仅看信号
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from btc_direction.signals import compute_signal
from btc_direction.scanner import evaluate_opportunities
from btc_direction.trader import run as trade_run, show_positions


def main():
    parser = argparse.ArgumentParser(description="BTC 方向信号扫描器")
    parser.add_argument("--trade",     action="store_true", help="自动执行下单")
    parser.add_argument("--positions", action="store_true", help="查看持仓")
    parser.add_argument("--signal",    action="store_true", help="仅计算信号")
    parser.add_argument("--dry-run",   action="store_true", help="模拟下单")
    args = parser.parse_args()

    if args.positions:
        show_positions()
        return

    if args.signal:
        compute_signal(verbose=True)
        return

    if args.trade or args.dry_run:
        trade_run(dry_run=args.dry_run or not args.trade)
        return

    # 默认：扫描信号 + 机会（不下单）
    sig = compute_signal(verbose=True)
    opps = evaluate_opportunities(sig, balance=70.0, verbose=True)

    if opps:
        print(f"✅ 找到 {len(opps)} 个机会，运行 --trade 执行下单")
    else:
        print("📭 当前无满足条件机会")


if __name__ == "__main__":
    main()
