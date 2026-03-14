#!/usr/bin/env python3
"""
持仓监控 + 自动止盈止损
每次运行检查所有开仓，触发条件时自动平仓
"""
import json, os, requests
from datetime import datetime
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderArgs, OrderType, BalanceAllowanceParams, AssetType

PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)
client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=POLYGON,
                    creds=creds, signature_type=1, funder=PROXY_WALLET)

LOG_FILE        = os.path.join(os.path.dirname(__file__), "trade_log.json")
ACTION_LOG_FILE = os.path.join(os.path.dirname(__file__), "action_log.json")

def log_action(action_type, question, details):
    """将止盈止损等关键执行动作持久化到 action_log.json，不依赖消息推送"""
    entry = {
        "time": datetime.now().isoformat(),
        "action": action_type,
        "question": question,
        "details": details,
    }
    logs = []
    if os.path.exists(ACTION_LOG_FILE):
        try:
            with open(ACTION_LOG_FILE) as f:
                logs = json.load(f)
        except:
            logs = []
    logs.append(entry)
    with open(ACTION_LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

def get_current_price(token_id):
    """获取当前 bid/ask（使用 requests，超时 4s）"""
    try:
        r = requests.get(
            f"https://clob.polymarket.com/price?token_id={token_id}&side=sell",
            timeout=4)
        sell = float(r.json().get("price", 0))
        r2 = requests.get(
            f"https://clob.polymarket.com/price?token_id={token_id}&side=buy",
            timeout=4)
        buy = float(r2.json().get("price", 0))
        return sell, buy
    except:
        return None, None

def get_actual_token_balance(token_id):
    """查询链上实际 conditional token 余额（÷1e6 得 token 数）"""
    try:
        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
        return int(bal.get("balance", 0)) / 1e6
    except:
        return 0.0

def close_position(token_id, tokens_held, direction="BUY", bet_usdc=None, exit_price=None):
    """平仓：卖出 YES/NO tokens
    优先 GTC 限价卖单 (Maker fee=0)，失败则 fallback FOK 市价单
    - SELL amount = 实际链上 token 余额（避免超额卖出报错）
    - 若余额几乎为零，说明该仓位未成交，跳过
    """
    try:
        actual = get_actual_token_balance(token_id)
        if actual < 0.01:
            return {"error": f"链上token余额极低({actual:.4f})，仓位可能未成交"}
        sell_amount = round(actual * 0.999, 6)

        # 优先: GTC 限价卖单 @ 当前 bid（Maker fee=0）
        if exit_price and exit_price > 0.02:
            try:
                order = client.create_order(OrderArgs(
                    token_id=token_id,
                    price=round(exit_price, 2),
                    size=sell_amount,
                    side="SELL",
                ))
                resp = client.post_order(order, OrderType.GTC)
                if resp.get("success") or resp.get("orderID"):
                    resp["order_type"] = "GTC_LIMIT"
                    return resp
            except Exception:
                pass  # GTC 失败，用 FOK

        # Fallback: FOK 市价卖单
        order = client.create_market_order(MarketOrderArgs(
            token_id=token_id,
            amount=sell_amount,
            side="SELL",
        ))
        resp = client.post_order(order, OrderType.FOK)
        return resp
    except Exception as e:
        return {"error": str(e)}

def get_balance():
    bal = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(bal["balance"]) / 1e6

def run_monitor():
    print(f"\n{'='*60}")
    print(f"  📊 持仓监控  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    raw = json.load(open(LOG_FILE))
    log = raw if isinstance(raw, list) else raw.get("trades", [])
    balance = get_balance()
    print(f"  💰 可用余额: ${balance:.4f}\n")

    # ── Step 0: 检查 pending_fill (GTC限价单) 是否已成交 ──
    pending_fills = [t for t in log if t.get("status") == "pending_fill"]
    if pending_fills:
        print(f"  📋 检查 {len(pending_fills)} 笔挂单...")
        for t in pending_fills:
            tid = t.get("token_id", "")
            q = t.get("question", "")[:45]
            oid = t.get("order_id", "")
            
            # 方法1: 检查链上 token 余额
            actual = get_actual_token_balance(tid)
            if actual > 0.01:
                t["status"] = "open"
                t["tokens"] = str(round(actual, 4))
                print(f"    ✅ 已成交: {q} | {actual:.4f} tokens")
            else:
                # 方法2: 检查订单状态
                try:
                    order_info = client.get_order(oid)
                    order_status = order_info.get("status", "") if isinstance(order_info, dict) else getattr(order_info, "status", "")
                    size_matched = float(order_info.get("size_matched", 0) if isinstance(order_info, dict) else getattr(order_info, "size_matched", 0))
                    
                    if order_status == "MATCHED" or size_matched > 0:
                        t["status"] = "open"
                        t["tokens"] = str(round(size_matched, 4)) if size_matched > 0 else t.get("tokens", "0")
                        print(f"    ✅ 已成交: {q} | {size_matched:.4f} tokens")
                    elif order_status in ("CANCELLED", "EXPIRED"):
                        t["status"] = "closed"
                        t["close_reason"] = "order_cancelled"
                        t["pnl_usd"] = 0  # 未成交=无损失（GTC被取消退回USDC）
                        print(f"    ❌ 已取消: {q}")
                    else:
                        # 仍在挂单中，检查是否过期（>4小时未成交则取消）
                        placed_time = t.get("time", "")
                        if placed_time:
                            from datetime import timedelta
                            try:
                                placed_dt = datetime.fromisoformat(placed_time)
                                age_hours = (datetime.now() - placed_dt).total_seconds() / 3600
                                if age_hours > 4:
                                    # 超时，取消挂单
                                    try:
                                        client.cancel_orders([oid])
                                        t["status"] = "closed"
                                        t["close_reason"] = "stale_order_cancelled"
                                        t["pnl_usd"] = 0
                                        print(f"    ⏰ 超时取消({age_hours:.1f}h): {q}")
                                    except:
                                        print(f"    ⏰ 取消失败: {q}")
                                else:
                                    print(f"    ⏳ 等待中({age_hours:.1f}h): {q} @ {t.get('price','?')}")
                            except:
                                print(f"    ⏳ 等待中: {q}")
                        else:
                            print(f"    ⏳ 等待中: {q}")
                except Exception as e:
                    print(f"    ⚠️ 查询失败: {q} | {str(e)[:60]}")
        
        # Save updated statuses
        json.dump(log, open(LOG_FILE, "w"), indent=2, ensure_ascii=False)
        print()

    open_trades = [t for t in log if t.get("status") == "open"]
    if not open_trades:
        print("  📭 无开仓\n")
        return {"balance": balance, "open": 0, "actions": []}

    actions = []
    for t in open_trades:
        q = t.get("question", "")[:55]
        entry = float(t.get("price", 0))
        tp    = float(t.get("take_profit", 1))
        sl    = float(t.get("stop_loss", 0))
        bet   = float(t.get("bet_usdc", 0))
        tid   = t.get("token_id", "")
        tokens = t.get("tokens") or t.get("tokens_received", "0")

        bid, ask = get_current_price(tid)
        if bid is None:
            print(f"  ⚠️  {q[:40]}... 无法获取价格")
            continue

        # 当前价格（以 bid 为准，即立刻卖出可得）
        current = bid
        pnl_pct = (current - entry) / entry * 100
        pnl_usd = (current - entry) * float(tokens) if tokens else 0

        status_icon = "🟢" if pnl_pct > 0 else "🔴"
        print(f"  {status_icon} {q}")
        print(f"     入场:{entry:.3f} | 现价:{current:.3f} | 盈亏:{pnl_pct:+.1f}% (${pnl_usd:+.2f})")
        print(f"     止盈:{tp:.3f} | 止损:{sl:.3f} | 持有:{tokens} tokens")

        action = None
        reason = None

        if current >= tp:
            action = "TAKE_PROFIT"
            reason = f"达到止盈 {tp:.3f}"
        elif current <= sl:
            action = "STOP_LOSS"
            reason = f"触发止损 {sl:.3f}"

        if action:
            print(f"     ⚡ {action}: {reason} → 平仓")

            # ── 预检：链上token余额为0 → 市场已结算，直接标记关闭 ──
            actual_balance = get_actual_token_balance(tid)
            if actual_balance < 0.01:
                # 检查是否市场已结算（price=0 或 orderbook 404）
                is_total_loss = (current <= 0.01)
                is_resolved_win = (current >= 0.95)
                if is_total_loss:
                    # token归零 = 全损
                    t["status"] = "closed"
                    t["exit_price"] = 0
                    t["exit_time"] = datetime.now().isoformat()
                    t["pnl_usd"] = round(-bet, 4)
                    t["usdc_back"] = 0
                    t["close_reason"] = "market_resolved_loss"
                    print(f"     🪦 市场已结算(归零) | token余额=0 | 亏损 ${-bet:.2f}")
                    actions.append({"trade": q, "action": "RESOLVED_LOSS", "pnl": -bet})
                    log_action("RESOLVED_LOSS", q, {"exit_price": 0, "pnl": -bet, "reason": "token_balance_zero_price_zero"})
                elif is_resolved_win:
                    # 价格≈1但token余额0 → 可能已自动赎回（市场结算赢了）
                    estimated_pnl = float(tokens) - bet if tokens else -bet
                    t["status"] = "closed"
                    t["exit_price"] = 1.0
                    t["exit_time"] = datetime.now().isoformat()
                    t["pnl_usd"] = round(estimated_pnl, 4)
                    t["usdc_back"] = round(float(tokens), 4) if tokens else 0
                    t["close_reason"] = "market_resolved_win"
                    print(f"     🏆 市场已结算(胜利) | token余额=0(已赎回) | 盈亏 ${estimated_pnl:+.2f}")
                    actions.append({"trade": q, "action": "RESOLVED_WIN", "pnl": estimated_pnl})
                    log_action("RESOLVED_WIN", q, {"exit_price": 1.0, "pnl": estimated_pnl, "reason": "token_redeemed"})
                else:
                    # token余额=0但价格非极端 → 可能未成交或已手动处理
                    t["status"] = "closed"
                    t["exit_price"] = current
                    t["exit_time"] = datetime.now().isoformat()
                    t["pnl_usd"] = round(-bet, 4)
                    t["usdc_back"] = 0
                    t["close_reason"] = "no_token_balance"
                    print(f"     ⚠️ 无token余额(价格{current:.3f}) | 标记关闭 | 亏损 ${-bet:.2f}")
                    actions.append({"trade": q, "action": "CLOSED_NO_TOKEN", "pnl": -bet})
                    log_action("CLOSED_NO_TOKEN", q, {"exit_price": current, "pnl": -bet, "reason": "no_token_balance"})
                continue  # 不再尝试平仓，直接跳到下一个

            # ── 正常平仓流程（优先 GTC Maker 卖单）──────────────
            resp = close_position(tid, tokens, bet_usdc=bet, exit_price=current)
            if resp.get("success"):
                # 优先用 takingAmount（USDC实收），fallback 用 exit_price * tokens 估算
                raw_taking = resp.get("takingAmount", 0) or 0
                try:
                    usdc_back = float(raw_taking)
                    if usdc_back > 1000:       # wei格式，转换
                        usdc_back = usdc_back / 1e6
                except (ValueError, TypeError):
                    usdc_back = 0
                # fallback：若 takingAmount 为0，用现价 × tokens 估算（保守取 bid）
                if usdc_back < 0.001:
                    try:
                        usdc_back = current * float(tokens)
                    except:
                        usdc_back = 0
                pnl_final = usdc_back - bet
                t["status"] = "closed"
                t["exit_price"] = current
                t["exit_time"] = datetime.now().isoformat()
                t["pnl_usd"] = round(pnl_final, 4)
                t["usdc_back"] = round(usdc_back, 4)
                t["close_reason"] = action
                print(f"     ✅ 平仓成功 | 回收 ${usdc_back:.2f} | 净盈亏 ${pnl_final:+.2f}")
                actions.append({"trade": q, "action": action, "pnl": pnl_final})
                log_action(action, q, {"exit_price": current, "usdc_back": usdc_back, "pnl": pnl_final, "success": True})
            else:
                err_str = str(resp)
                # ── 如果是 orderbook 404 或 no match → 市场不存在了，直接关闭 ──
                if "404" in err_str or "no match" in err_str.lower() or "No orderbook" in err_str:
                    t["status"] = "closed"
                    t["exit_price"] = current
                    t["exit_time"] = datetime.now().isoformat()
                    t["pnl_usd"] = round(-bet, 4)
                    t["usdc_back"] = 0
                    t["close_reason"] = "market_closed_no_orderbook"
                    print(f"     🪦 市场已关闭(无orderbook) | 标记关闭 | 亏损 ${-bet:.2f}")
                    actions.append({"trade": q, "action": "MARKET_CLOSED", "pnl": -bet})
                    log_action("MARKET_CLOSED", q, {"exit_price": current, "pnl": -bet, "error": err_str[:100]})
                elif "couldn't be fully filled" in err_str.lower() or "fok" in err_str.lower():
                    # FOK 未完全成交 — 流动性不足，下次再试
                    print(f"     ⚠️ FOK未成交(流动性不足)，下次重试")
                    log_action(action + "_FAILED", q, {"exit_price": current, "error": "FOK_no_fill"})
                else:
                    print(f"     ❌ 平仓失败: {resp}")
                    log_action(action + "_FAILED", q, {"exit_price": current, "error": err_str[:100]})
        print()

    # 更新日志（统一用 list 格式）
    json.dump(log, open(LOG_FILE, "w"), indent=2, ensure_ascii=False)

    # 汇总：已实现盈亏优先用 usdc_back 字段（修复版），fallback 用 exit_price*tokens 重算
    total_open = len([t for t in log if t.get("status") == "open"])
    closed_pnl = 0.0
    for t in log:
        if t.get("status") not in ("closed", "settled"):
            continue
        bet_t   = float(t.get("bet_usdc", 0) or 0)
        ep      = float(t.get("exit_price", 0) or 0)
        tokens_t = float(t.get("tokens") or t.get("tokens_received") or 0)
        # 优先用明确记录的 usdc_back
        if t.get("usdc_back") is not None:
            closed_pnl += float(t["usdc_back"]) - bet_t
        elif ep > 0 and tokens_t > 0:
            # 重算：exit_price × tokens - bet
            closed_pnl += ep * tokens_t - bet_t
        else:
            # 兜底：直接用 pnl_usd（可能不准，但聊胜于无）
            closed_pnl += float(t.get("pnl_usd", 0) or 0)
    print(f"  {'─'*55}")
    print(f"  开仓: {total_open} | 已实现盈亏(重算): ${closed_pnl:+.2f} | 余额: ${balance:.4f}")
    print(f"  ⚠️  以 Polymarket 官网 P&L 为准，脚本数字仅供参考")
    print()
    return {"balance": balance, "open": total_open, "actions": actions, "closed_pnl": round(closed_pnl, 4)}

if __name__ == "__main__":
    result = run_monitor()
    print("结果:", json.dumps(result, ensure_ascii=False))
