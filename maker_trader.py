#!/usr/bin/env python3
"""
maker_trader.py — Maker 挂单系统

原理：不用市价单（吃spread），而是挂 Limit 单在买卖中间，
    等对方来"吃"我们的单，省下价差，并赚取 Maker 返佣。

典型用法：
    bid=0.48, ask=0.52
    我们挂 @ 0.495 → 比市价便宜 0.5%，比 ask 少出 1.5%

用法：
  ./venv/bin/python3 maker_trader.py           # 扫描 + 显示挂单计划
  ./venv/bin/python3 maker_trader.py --place   # 实际挂单
  ./venv/bin/python3 maker_trader.py --cancel  # 取消所有未成交单
  ./venv/bin/python3 maker_trader.py --orders  # 查看当前挂单
"""

import sys, os, json, time, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timezone
from market_classifier import classify, score_for_maker, label
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

MAKER_LOG = os.path.join(os.path.dirname(__file__), "maker_orders.json")

# ── 参数 ──────────────────────────────────────────────────────────────
BET_PER_ORDER  = 2.0      # 每笔挂单金额
MAX_ORDERS     = 3        # 同时最多挂单数
SPREAD_CAPTURE = 0.40     # 进入买卖价差的比例（0.40 = 在中间偏买方40%处）
MIN_SPREAD     = 0.02     # 最小价差要求（小于这个不值得挂）
MIN_VOLUME     = 50000    # 最低24h成交量


def get_client():
    return ClobClient("https://clob.polymarket.com",
        key=PRIVATE_KEY, chain_id=POLYGON,
        creds=creds, signature_type=1, funder=PROXY_WALLET)


def get_balance(client):
    b = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(b["balance"]) / 1e6


def get_orderbook(token_id):
    """获取订单簿，返回最优 bid/ask"""
    r = subprocess.run(
        f'curl -s "https://clob.polymarket.com/book?token_id={token_id}"',
        shell=True, capture_output=True, text=True, timeout=5)
    try:
        book = json.loads(r.stdout)
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 0.0
        return best_bid, best_ask
    except Exception:
        return 0.0, 0.0


def calc_maker_price(bid, ask, direction="BUY"):
    """
    计算挂单价格：在 bid-ask 中间偏向我们的方向
    BUY  → 挂在 bid + (ask-bid) * SPREAD_CAPTURE（略高于 bid，等卖方来接）
    SELL → 挂在 ask - (ask-bid) * SPREAD_CAPTURE（略低于 ask，等买方来接）
    """
    spread = ask - bid
    if direction == "BUY":
        return round(bid + spread * SPREAD_CAPTURE, 3)
    else:
        return round(ask - spread * SPREAD_CAPTURE, 3)


def fetch_top_markets():
    """从 Gamma API 获取高流动性市场"""
    r = subprocess.run(
        'curl -s "https://gamma-api.polymarket.com/markets'
        '?limit=200&active=true&closed=false&order=volume24hr&ascending=false"',
        shell=True, capture_output=True, text=True, timeout=10)
    try:
        markets = json.loads(r.stdout) if r.stdout else []
        result = []
        for m in markets:
            vol   = float(m.get("volume24hr") or 0)
            bid   = float(m.get("bestBid") or 0)
            ask   = float(m.get("bestAsk") or 0)
            ids   = json.loads(m.get("clobTokenIds") or "[]")
            if vol < MIN_VOLUME or not ids:
                continue
            spread = ask - bid
            if spread < MIN_SPREAD or bid <= 0.05 or ask >= 0.95:
                continue

            # 计算剩余天数
            days_left = 999
            end = m.get("endDate", "")
            if end:
                try:
                    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    days_left = max(0, (end_dt - datetime.now(timezone.utc)).days)
                except Exception:
                    pass

            # 市场类型分类 + 做市评分
            m["_days_left"] = days_left
            maker_sc  = score_for_maker(m)
            mtype_inf = classify(m.get("question", ""), days_left=days_left)
            mtype_ico = label(m.get("question", ""), days_left=days_left)

            result.append({
                "question": m.get("question", ""),
                "bid": bid, "ask": ask, "spread": spread,
                "volume_24h": vol,
                "token_id_yes": ids[0],
                "days_left": days_left,
                "market_type": mtype_inf["type"],
                "market_icon": mtype_ico,
                "market_priority": mtype_inf["priority"],
                "maker_score": maker_sc,
                "type_notes": mtype_inf["notes"],
            })
        # 按综合做市评分排序（类型+流动性+spread，而非单纯成交量）
        result.sort(key=lambda x: x["maker_score"], reverse=True)
        return result
    except Exception as e:
        print(f"获取市场失败: {e}")
        return []


def load_maker_log():
    try:
        return json.load(open(MAKER_LOG))
    except Exception:
        return {"orders": []}


def save_maker_log(data):
    json.dump(data, open(MAKER_LOG, "w"), indent=2, ensure_ascii=False)


def place_limit_order(client, token_id, price, size_usdc, side="BUY"):
    """
    挂限价单
    size_usdc = 你愿意花多少 USDC（BUY 时）
    price = 你愿意支付的单价
    tokens = size_usdc / price
    """
    try:
        tokens = round(size_usdc / price, 4)
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


def show_plan(markets, top_n=5):
    print(f"\n{'='*65}")
    print(f"  📋 Maker 挂单计划  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*65}\n")
    print(f"  找到 {len(markets)} 个符合条件的高流动性市场\n")
    print(f"  策略：在 bid-ask 中间 {SPREAD_CAPTURE*100:.0f}% 处挂单\n")

    for i, m in enumerate(markets[:top_n], 1):
        maker_price = calc_maker_price(m["bid"], m["ask"], "BUY")
        tokens = BET_PER_ORDER / maker_price
        saving_vs_market = (m["ask"] - maker_price) * tokens
        spread_pct = m["spread"] / m["ask"] * 100
        icon = m.get("market_icon", "❓")
        mtype = m.get("market_type", "")
        score = m.get("maker_score", 0)
        notes = m.get("type_notes", "")
        print(f"  #{i} {icon} [{mtype}] 评分={score:.2f} | {m['question'][:55]}")
        print(f"      bid={m['bid']:.3f} | ask={m['ask']:.3f} | spread={spread_pct:.1f}% | {m['days_left']}天")
        print(f"      🎯 挂单价: {maker_price:.3f} | 省 ${saving_vs_market:.3f} | 成交量: ${m['volume_24h']:,.0f}/24h")
        if notes:
            print(f"      💡 {notes}")
        print()


def run_place(client, markets, top_n=3):
    log = load_maker_log()
    placed = 0
    balance = get_balance(client)
    print(f"余额: ${balance:.2f}\n")

    for m in markets[:top_n * 2]:  # 多扫一些，跳过已有的
        if placed >= top_n:
            break

        token_id = m["token_id_yes"]
        # 实时获取最新 bid/ask
        bid, ask = get_orderbook(token_id)
        if bid <= 0 or ask <= 0:
            continue
        spread = ask - bid
        if spread < MIN_SPREAD:
            continue

        maker_price = calc_maker_price(bid, ask, "BUY")
        tokens = BET_PER_ORDER / maker_price

        print(f"挂单: {m['question'][:50]}")
        print(f"  bid={bid:.3f} ask={ask:.3f} → 挂 @ {maker_price:.3f} ({tokens:.2f} tokens)")

        resp = place_limit_order(client, token_id, maker_price, BET_PER_ORDER)
        if resp.get("success"):
            order_id = resp.get("orderID", "")
            print(f"  ✅ 成功！order_id={order_id[:20]}...")
            log["orders"].append({
                "question": m["question"],
                "token_id": token_id,
                "price": maker_price,
                "size_usdc": BET_PER_ORDER,
                "tokens": tokens,
                "order_id": order_id,
                "status": "open",
                "time": datetime.now().isoformat(),
            })
            placed += 1
        else:
            print(f"  ❌ 失败: {resp.get('error', resp)}")
        time.sleep(0.5)

    save_maker_log(log)
    print(f"\n共挂 {placed} 笔限价单")


def show_orders(client):
    """查看当前未成交限价单"""
    try:
        orders = client.get_orders() or []
        if not orders:
            print("无未成交订单")
            return
        print(f"未成交订单: {len(orders)} 笔")
        for o in orders:
            print(f"  {o}")
    except Exception as e:
        print(f"查询失败: {e}")


def cancel_all(client):
    """取消所有未成交单"""
    try:
        orders = client.get_orders() or []
        order_ids = [o.get("id") or o.get("orderID") for o in orders if o.get("id") or o.get("orderID")]
        if order_ids:
            client.cancel_orders(order_ids)
            print(f"已取消 {len(order_ids)} 笔订单")
        else:
            print("无可取消订单")
    except Exception as e:
        print(f"取消失败: {e}")


# ── 主入口 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Maker 挂单系统")
    parser.add_argument("--place",  action="store_true", help="实际挂单")
    parser.add_argument("--cancel", action="store_true", help="取消所有挂单")
    parser.add_argument("--orders", action="store_true", help="查看挂单")
    args = parser.parse_args()

    client = get_client()

    if args.orders:
        show_orders(client)
    elif args.cancel:
        cancel_all(client)
    elif args.place:
        markets = fetch_top_markets()
        if markets:
            run_place(client, markets, top_n=MAX_ORDERS)
        else:
            print("无符合条件市场")
    else:
        # 默认：显示计划，不下单
        markets = fetch_top_markets()
        show_plan(markets, top_n=5)
        print(f"  💡 运行 --place 开始挂单")
