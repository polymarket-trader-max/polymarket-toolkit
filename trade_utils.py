#!/usr/bin/env python3
"""
trade_utils.py — 公用工具函数
安全写入trade_log，避免格式混乱
"""
import json, os

LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")

def load_log():
    """安全读取 trade_log.json，统一返回 list"""
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        raw = json.load(f)
    return raw if isinstance(raw, list) else raw.get("trades", [])

def save_log(trades: list):
    """安全写入 list 格式的 trade log"""
    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

def append_trade(trade: dict):
    """追加一条交易记录"""
    trades = load_log()
    trades.append(trade)
    save_log(trades)
    return len(trades)

def update_trade_status(order_id: str, **updates):
    """根据 order_id 更新记录状态"""
    trades = load_log()
    for t in trades:
        if t.get("order_id") == order_id:
            t.update(updates)
    save_log(trades)
