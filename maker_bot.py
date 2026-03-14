#!/usr/bin/env python3
"""
maker_bot.py — 全自动做市机器人 v2

核心逻辑：
  1. 扫描高流动性、中间价位、有价差的市场
  2. 在 best_bid 上方 1 tick 挂 BUY 限价单（成为最优买方）
  3. 成交后，立刻在 best_ask 下方 1 tick 挂 SELL 限价单（成为最优卖方）
  4. 两侧都成交 → 赚取 spread（Maker fee = 0!）
  5. 价格移动太远 → 取消旧单，重新挂

用法：
  ./venv/bin/python3 maker_bot.py              # 完整循环：检查成交 → 挂新单
  ./venv/bin/python3 maker_bot.py --dry-run    # 只看计划不执行
  ./venv/bin/python3 maker_bot.py --status     # 查看当前所有maker状态
  ./venv/bin/python3 maker_bot.py --cancel-all # 取消所有挂单
"""

import sys, os, json, time, subprocess, argparse, math
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, BalanceAllowanceParams, AssetType,
    OrderArgs, OrderType, MarketOrderArgs,
)

# ── 凭据 ──────────────────────────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)

# ── 参数 ──────────────────────────────────────────────────────────────
BET_PER_ORDER      = 3.0     # 每笔挂单金额 (USDC)
MAX_ACTIVE_PAIRS   = 5       # 同时做市的市场对数量
TICK_SIZE          = 0.01    # Polymarket 最小价格单位
MIN_SPREAD_TICKS   = 2       # 最小价差(tick数)，低于这个不做市
MIN_VOLUME_24H     = 100000  # 最低24h成交量
MIN_PRICE          = 0.20    # 最低价格（太低的市场风险大）
MAX_PRICE          = 0.80    # 最高价格（太高利润空间小）
STALE_MINUTES      = 30      # 挂单超过这么久且价格偏移则取消
PRICE_DRIFT_TICKS  = 3       # 价格偏移超过这么多tick则重新挂单
MAX_INVENTORY       = 30.0   # 单个市场最大持仓 token 数（防过度暴露）
MAX_DAILY_MAKER_SPEND = 40.0 # maker每日最大投入

STATE_FILE = os.path.join(os.path.dirname(__file__), "maker_state.json")
ACTION_LOG = os.path.join(os.path.dirname(__file__), "action_log.json")

# ── 黑名单（与 frequent_trader 共享）─────────────────────────────────
BLACKLIST_KEYWORDS = [
    "Bitcoin Up or Down", "Ethereum Up or Down", "Up or Down -",
    "price of Bitcoin", "price of Ethereum", "price of BTC", "price of ETH",
    "BTC above", "BTC below", "BTC between",
    "Elon Musk", "tweets from",
]


def get_client():
    return ClobClient("https://clob.polymarket.com",
        key=PRIVATE_KEY, chain_id=POLYGON,
        creds=creds, signature_type=1, funder=PROXY_WALLET)


def get_balance(client):
    b = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(b["balance"]) / 1e6


def get_orderbook(client, token_id):
    """获取订单簿，返回 best_bid, best_ask, bid_depth, ask_depth"""
    try:
        book = client.get_order_book(token_id)
        bids = book.bids if hasattr(book, 'bids') else book.get('bids', [])
        asks = book.asks if hasattr(book, 'asks') else book.get('asks', [])

        best_bid = float(bids[0].price if hasattr(bids[0], 'price') else bids[0]['price']) if bids else 0.0
        best_ask = float(asks[0].price if hasattr(asks[0], 'price') else asks[0]['price']) if asks else 0.0

        # Depth: total size within 3 ticks of best
        bid_depth = 0
        for b in bids[:5]:
            p = float(b.price if hasattr(b, 'price') else b['price'])
            s = float(b.size if hasattr(b, 'size') else b['size'])
            if p >= best_bid - 0.03:
                bid_depth += s
        ask_depth = 0
        for a in asks[:5]:
            p = float(a.price if hasattr(a, 'price') else a['price'])
            s = float(a.size if hasattr(a, 'size') else a['size'])
            if p <= best_ask + 0.03:
                ask_depth += s

        return best_bid, best_ask, bid_depth, ask_depth
    except Exception as e:
        print(f"  ⚠️ 订单簿获取失败: {e}")
        return 0.0, 0.0, 0, 0


def fetch_maker_candidates():
    """从 Gamma API 获取适合做市的市场"""
    r = subprocess.run(
        'curl -s "https://gamma-api.polymarket.com/markets'
        '?limit=150&active=true&closed=false&order=volume24hr&ascending=false"',
        shell=True, capture_output=True, text=True, timeout=10)
    try:
        markets = json.loads(r.stdout) if r.stdout else []
    except:
        return []

    candidates = []
    for m in markets:
        vol = float(m.get("volume24hr") or 0)
        bid = float(m.get("bestBid") or 0)
        ask = float(m.get("bestAsk") or 0)
        ids = json.loads(m.get("clobTokenIds") or "[]")
        q   = m.get("question", "")

        if vol < MIN_VOLUME_24H or not ids:
            continue
        if bid < MIN_PRICE or ask > MAX_PRICE:
            continue

        spread = ask - bid
        spread_ticks = round(spread / TICK_SIZE)
        if spread_ticks < MIN_SPREAD_TICKS:
            continue

        # 黑名单检查
        if any(kw.lower() in q.lower() for kw in BLACKLIST_KEYWORDS):
            continue

        # 到期时间
        days_left = 999
        end = m.get("endDate", "")
        if end:
            try:
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                days_left = max(0, (end_dt - datetime.now(timezone.utc)).days)
            except:
                pass

        # 做市评分：优先高成交量 + 适中价差 + 中间价位
        center_score = 1.0 - abs(0.5 - (bid + ask) / 2) * 2  # 越接近0.5越好
        vol_score = min(1.0, math.log10(max(vol, 1)) / 6.5)
        spread_score = min(1.0, spread_ticks / 8)  # 4-8 tick spread 最佳
        if spread_ticks > 10:
            spread_score *= 0.7  # 太大的spread可能流动性差

        score = vol_score * 0.4 + spread_score * 0.35 + center_score * 0.25

        candidates.append({
            "question": q,
            "token_id_yes": ids[0],
            "token_id_no": ids[1] if len(ids) > 1 else None,
            "condition_id": m.get("conditionId", ""),
            "bid": bid, "ask": ask,
            "spread": spread,
            "spread_ticks": spread_ticks,
            "volume_24h": vol,
            "days_left": days_left,
            "score": round(score, 3),
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "active_pairs": [],   # 当前做市对
            "pending_exits": [],  # 等待卖出的仓位
            "completed": [],      # 已完成的做市循环
            "daily_spend": 0,
            "daily_date": "",
            "total_pnl": 0,
        }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def log_action(action, question, details):
    """写入共享 action_log"""
    try:
        with open(ACTION_LOG) as f:
            logs = json.load(f)
    except:
        logs = []

    logs.append({
        "time": datetime.now().isoformat(),
        "action": f"MAKER_{action}",
        "question": question,
        "details": details,
    })

    with open(ACTION_LOG, "w") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)


def place_limit_order(client, token_id, price, size_usdc, side="BUY"):
    """挂 GTC 限价单"""
    try:
        if side == "BUY":
            tokens = round(size_usdc / price, 2)
        else:
            tokens = size_usdc  # SELL side: size_usdc is actually token count

        order = client.create_order(OrderArgs(
            token_id=token_id,
            price=price,
            size=tokens,
            side=side,
        ))
        resp = client.post_order(order, OrderType.GTC)
        return resp
    except Exception as e:
        return {"error": str(e), "success": False}


def check_order_status(client, order_id):
    """检查订单是否成交"""
    try:
        order = client.get_order(order_id)
        if isinstance(order, dict):
            return order
        return vars(order) if hasattr(order, '__dict__') else {"status": "unknown"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def cancel_order(client, order_id):
    """取消单个订单"""
    try:
        client.cancel_orders([order_id])
        return True
    except:
        return False


# ── 核心循环 ──────────────────────────────────────────────────────────

def run_cycle(client, state, dry_run=False):
    """做市主循环"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 重置每日计数
    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_spend"] = 0

    balance = get_balance(client)
    print(f"\n{'='*60}")
    print(f"  🏪 Maker Bot  {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"  余额: ${balance:.2f} | 今日投入: ${state['daily_spend']:.2f}/{MAX_DAILY_MAKER_SPEND}")
    print(f"  活跃做市: {len(state['active_pairs'])} | 待卖出: {len(state['pending_exits'])}")
    print(f"{'='*60}")

    # ── Step 1: 检查 pending_exits（已买入，等待卖出） ──
    print(f"\n📤 检查待卖出仓位...")
    exits_to_remove = []
    for i, pe in enumerate(state.get("pending_exits", [])):
        order_id = pe.get("sell_order_id")
        if order_id:
            status = check_order_status(client, order_id)
            order_status = status.get("status", status.get("order_status", "unknown"))
            size_matched = float(status.get("size_matched", 0) or 0)
            original_size = float(status.get("original_size", pe.get("tokens", 0)) or 0)

            if size_matched > 0 and (order_status == "MATCHED" or size_matched >= original_size * 0.95):
                # 卖出成交！
                buy_cost = pe["buy_cost"]
                sell_revenue = size_matched * pe["sell_price"]
                pnl = sell_revenue - buy_cost
                print(f"  ✅ 做市完成: {pe['question'][:45]}")
                print(f"     买入 ${buy_cost:.2f} @ {pe['buy_price']} → 卖出 ${sell_revenue:.2f} @ {pe['sell_price']}")
                print(f"     利润: ${pnl:+.3f}")

                log_action("COMPLETE", pe["question"], {
                    "buy_price": pe["buy_price"],
                    "sell_price": pe["sell_price"],
                    "tokens": pe.get("tokens", 0),
                    "buy_cost": buy_cost,
                    "sell_revenue": round(sell_revenue, 4),
                    "pnl": round(pnl, 4),
                })
                state["total_pnl"] = round(state.get("total_pnl", 0) + pnl, 4)
                exits_to_remove.append(i)

            elif order_status in ("CANCELLED", "EXPIRED"):
                print(f"  ⚠️ 卖出单被取消: {pe['question'][:45]}")
                # 重新挂卖单
                token_id = pe["token_id"]
                bid, ask, _, _ = get_orderbook(client, token_id)
                if ask > 0 and not dry_run:
                    new_sell_price = round(ask - TICK_SIZE, 3)
                    if new_sell_price > pe["buy_price"]:  # 确保有利润
                        tokens = pe.get("tokens", 0)
                        resp = place_limit_order(client, token_id, new_sell_price, tokens, "SELL")
                        if resp.get("success") or resp.get("orderID"):
                            pe["sell_order_id"] = resp.get("orderID", "")
                            pe["sell_price"] = new_sell_price
                            print(f"     重新挂卖 @ {new_sell_price}")
                        else:
                            print(f"     重挂失败: {resp}")
            else:
                # 检查价格是否偏移太多
                bid, ask, _, _ = get_orderbook(client, pe["token_id"])
                if ask > 0:
                    optimal_sell = round(ask - TICK_SIZE, 3)
                    drift = abs(optimal_sell - pe["sell_price"])
                    if drift >= PRICE_DRIFT_TICKS * TICK_SIZE and optimal_sell > pe["buy_price"]:
                        if not dry_run:
                            cancel_order(client, order_id)
                            tokens = pe.get("tokens", 0)
                            resp = place_limit_order(client, pe["token_id"], optimal_sell, tokens, "SELL")
                            if resp.get("success") or resp.get("orderID"):
                                pe["sell_order_id"] = resp.get("orderID", "")
                                pe["sell_price"] = optimal_sell
                                print(f"  🔄 调整卖价: {pe['question'][:40]} → {optimal_sell}")
                        else:
                            print(f"  🔄 [dry] 需调整卖价: {pe['question'][:40]} {pe['sell_price']}→{optimal_sell}")
                    else:
                        print(f"  ⏳ 等待成交: {pe['question'][:45]} @ {pe['sell_price']}")
        else:
            # 没有卖单，需要挂卖
            token_id = pe["token_id"]
            bid, ask, _, _ = get_orderbook(client, token_id)
            if ask > 0:
                sell_price = round(ask - TICK_SIZE, 3)
                if sell_price > pe["buy_price"]:
                    tokens = pe.get("tokens", 0)
                    if not dry_run:
                        resp = place_limit_order(client, token_id, sell_price, tokens, "SELL")
                        if resp.get("success") or resp.get("orderID"):
                            pe["sell_order_id"] = resp.get("orderID", "")
                            pe["sell_price"] = sell_price
                            print(f"  📤 挂卖单: {pe['question'][:45]} @ {sell_price}")
                    else:
                        print(f"  📤 [dry] 挂卖: {pe['question'][:45]} @ {sell_price}")

    # Remove completed exits (reverse order to preserve indices)
    for i in sorted(exits_to_remove, reverse=True):
        completed = state["pending_exits"].pop(i)
        state.setdefault("completed", []).append(completed)

    # ── Step 2: 检查活跃买单是否成交 ──
    print(f"\n📥 检查活跃买单...")
    pairs_to_remove = []
    for i, pair in enumerate(state.get("active_pairs", [])):
        order_id = pair.get("buy_order_id")
        if not order_id:
            pairs_to_remove.append(i)
            continue

        status = check_order_status(client, order_id)
        order_status = status.get("status", status.get("order_status", "unknown"))
        size_matched = float(status.get("size_matched", 0) or 0)
        original_size = float(status.get("original_size", 0) or 0)

        if size_matched > 0 and (order_status == "MATCHED" or
            (original_size > 0 and size_matched >= original_size * 0.95)):
            # 买入成交！移入 pending_exits
            tokens = size_matched
            buy_cost = tokens * pair["buy_price"]
            print(f"  ✅ 买入成交: {pair['question'][:45]}")
            print(f"     {tokens:.2f} tokens @ {pair['buy_price']}")

            # 立即挂卖单
            token_id = pair["token_id"]
            bid, ask, _, _ = get_orderbook(client, token_id)
            sell_price = round(ask - TICK_SIZE, 3) if ask > pair["buy_price"] + TICK_SIZE else round(pair["buy_price"] + 2 * TICK_SIZE, 3)

            sell_order_id = ""
            if not dry_run:
                resp = place_limit_order(client, token_id, sell_price, tokens, "SELL")
                if resp.get("success") or resp.get("orderID"):
                    sell_order_id = resp.get("orderID", "")
                    print(f"     📤 卖单挂出 @ {sell_price} (spread利润: ${(sell_price - pair['buy_price']) * tokens:.3f})")
                else:
                    print(f"     ⚠️ 卖单失败: {resp}")

            state["pending_exits"].append({
                "question": pair["question"],
                "token_id": token_id,
                "buy_price": pair["buy_price"],
                "buy_cost": round(buy_cost, 4),
                "tokens": round(tokens, 4),
                "sell_price": sell_price,
                "sell_order_id": sell_order_id,
                "buy_time": pair.get("time", ""),
                "fill_time": datetime.now().isoformat(),
            })

            log_action("BUY_FILLED", pair["question"], {
                "buy_price": pair["buy_price"],
                "tokens": round(tokens, 4),
                "cost": round(buy_cost, 4),
                "sell_target": sell_price,
            })
            pairs_to_remove.append(i)

        elif order_status in ("CANCELLED", "EXPIRED"):
            print(f"  ❌ 买单取消/过期: {pair['question'][:45]}")
            pairs_to_remove.append(i)

        else:
            # 检查是否过期或价格偏移
            placed_time = pair.get("time", "")
            if placed_time:
                try:
                    placed_dt = datetime.fromisoformat(placed_time)
                    age_min = (now - placed_dt).total_seconds() / 60
                except:
                    age_min = 0
            else:
                age_min = 0

            bid, ask, _, _ = get_orderbook(client, pair["token_id"])
            if bid > 0:
                optimal_buy = round(bid + TICK_SIZE, 3)
                drift = abs(optimal_buy - pair["buy_price"])

                if drift >= PRICE_DRIFT_TICKS * TICK_SIZE or age_min > STALE_MINUTES:
                    if not dry_run:
                        cancel_order(client, order_id)
                        print(f"  🔄 取消旧买单: {pair['question'][:40]} (偏移{drift:.3f}, {age_min:.0f}分钟)")
                    else:
                        print(f"  🔄 [dry] 需取消: {pair['question'][:40]} (偏移{drift:.3f})")
                    pairs_to_remove.append(i)
                else:
                    print(f"  ⏳ 等待: {pair['question'][:45]} @ {pair['buy_price']} ({age_min:.0f}min)")

    for i in sorted(pairs_to_remove, reverse=True):
        state["active_pairs"].pop(i)

    # ── Step 3: 挂新买单 ──
    slots = MAX_ACTIVE_PAIRS - len(state["active_pairs"])
    remaining_budget = MAX_DAILY_MAKER_SPEND - state["daily_spend"]

    if slots <= 0:
        print(f"\n📊 做市位已满 ({MAX_ACTIVE_PAIRS}个)")
    elif remaining_budget < BET_PER_ORDER:
        print(f"\n💰 今日maker预算用尽 (${state['daily_spend']:.2f}/{MAX_DAILY_MAKER_SPEND})")
    elif balance < BET_PER_ORDER + 10:
        print(f"\n💰 余额不足 (${balance:.2f})")
    else:
        print(f"\n🔍 扫描做市机会 (空位: {slots})...")
        candidates = fetch_maker_candidates()

        # 排除已在做市的市场
        active_tokens = set(p["token_id"] for p in state["active_pairs"])
        exit_tokens = set(p["token_id"] for p in state["pending_exits"])
        used_tokens = active_tokens | exit_tokens

        placed = 0
        for cand in candidates:
            if placed >= slots or remaining_budget < BET_PER_ORDER:
                break

            token_id = cand["token_id_yes"]
            if token_id in used_tokens:
                continue

            # 获取实时订单簿
            bid, ask, bid_depth, ask_depth = get_orderbook(client, token_id)
            if bid <= 0 or ask <= 0:
                continue

            spread_ticks = round((ask - bid) / TICK_SIZE)
            if spread_ticks < MIN_SPREAD_TICKS:
                continue

            # 我们的买入价：best_bid + 1 tick（成为最优买方）
            buy_price = round(bid + TICK_SIZE, 3)
            # 预期卖出价：best_ask - 1 tick
            expected_sell = round(ask - TICK_SIZE, 3)
            # 预期利润
            tokens = BET_PER_ORDER / buy_price
            expected_profit = (expected_sell - buy_price) * tokens
            expected_pct = (expected_sell - buy_price) / buy_price * 100

            if expected_profit < 0.05:  # 至少赚 $0.05
                continue

            print(f"\n  #{placed+1} {cand['question'][:50]}")
            print(f"      book: bid={bid:.3f} ask={ask:.3f} spread={spread_ticks}ticks")
            print(f"      🎯 买@{buy_price} → 卖@{expected_sell} = +${expected_profit:.3f} ({expected_pct:.1f}%)")
            print(f"      vol: ${cand['volume_24h']:,.0f}/24h | score: {cand['score']}")

            if not dry_run:
                resp = place_limit_order(client, token_id, buy_price, BET_PER_ORDER, "BUY")
                if resp.get("success") or resp.get("orderID"):
                    order_id = resp.get("orderID", "")
                    print(f"      ✅ 买单挂出! order={order_id[:20]}...")

                    state["active_pairs"].append({
                        "question": cand["question"],
                        "token_id": token_id,
                        "buy_price": buy_price,
                        "buy_usdc": BET_PER_ORDER,
                        "expected_sell": expected_sell,
                        "expected_profit": round(expected_profit, 4),
                        "buy_order_id": order_id,
                        "time": datetime.now().isoformat(),
                    })
                    state["daily_spend"] = round(state["daily_spend"] + BET_PER_ORDER, 2)
                    remaining_budget -= BET_PER_ORDER
                    placed += 1
                else:
                    print(f"      ❌ 失败: {resp.get('error', resp)}")
            else:
                print(f"      [dry-run] 不执行")
                placed += 1

            time.sleep(0.3)

        if placed == 0 and slots > 0:
            print("  没有找到合适的做市机会")

    # ── Summary ──
    total_pending_value = sum(pe.get("buy_cost", 0) for pe in state["pending_exits"])
    total_active_value = sum(p.get("buy_usdc", 0) for p in state["active_pairs"])
    print(f"\n{'─'*60}")
    print(f"  📊 汇总:")
    print(f"  活跃买单: {len(state['active_pairs'])} (${total_active_value:.2f})")
    print(f"  待卖出:   {len(state['pending_exits'])} (${total_pending_value:.2f})")
    print(f"  累计做市利润: ${state.get('total_pnl', 0):+.4f}")
    print(f"  今日投入: ${state['daily_spend']:.2f}/{MAX_DAILY_MAKER_SPEND}")
    print(f"{'─'*60}\n")

    save_state(state)
    return state


def show_status(state):
    """显示当前做市状态"""
    print(f"\n{'='*60}")
    print(f"  🏪 Maker Bot 状态")
    print(f"{'='*60}")

    print(f"\n  📥 活跃买单 ({len(state.get('active_pairs', []))}):")
    for p in state.get("active_pairs", []):
        print(f"    {p['question'][:50]}")
        print(f"      买@{p['buy_price']} → 目标卖@{p.get('expected_sell','?')} | 预期利润${p.get('expected_profit',0):.3f}")

    print(f"\n  📤 待卖出 ({len(state.get('pending_exits', []))}):")
    for pe in state.get("pending_exits", []):
        spread_profit = (pe.get("sell_price", 0) - pe.get("buy_price", 0)) * pe.get("tokens", 0)
        print(f"    {pe['question'][:50]}")
        print(f"      买@{pe['buy_price']} → 卖@{pe.get('sell_price','?')} | {pe.get('tokens',0):.2f}tokens | 利润${spread_profit:.3f}")

    print(f"\n  📈 累计做市利润: ${state.get('total_pnl', 0):+.4f}")
    print(f"  🔄 已完成循环: {len(state.get('completed', []))}")
    print(f"  💰 今日投入: ${state.get('daily_spend', 0):.2f}/{MAX_DAILY_MAKER_SPEND}")
    print(f"{'='*60}\n")


def cancel_all_orders(client, state):
    """取消所有maker相关挂单"""
    cancelled = 0
    # Cancel buy orders
    for p in state.get("active_pairs", []):
        oid = p.get("buy_order_id")
        if oid and cancel_order(client, oid):
            cancelled += 1
    # Cancel sell orders
    for pe in state.get("pending_exits", []):
        oid = pe.get("sell_order_id")
        if oid and cancel_order(client, oid):
            cancelled += 1
    state["active_pairs"] = []
    # Keep pending_exits since we still hold the tokens
    save_state(state)
    print(f"已取消 {cancelled} 笔挂单")


# ── 主入口 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Maker Bot v2")
    parser.add_argument("--dry-run", action="store_true", help="只看计划不执行")
    parser.add_argument("--status", action="store_true", help="查看当前状态")
    parser.add_argument("--cancel-all", action="store_true", help="取消所有挂单")
    args = parser.parse_args()

    state = load_state()

    if args.status:
        show_status(state)
    elif args.cancel_all:
        client = get_client()
        cancel_all_orders(client, state)
    else:
        client = get_client()
        run_cycle(client, state, dry_run=args.dry_run)