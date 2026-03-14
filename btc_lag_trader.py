"""
btc_lag_trader.py — Binance Lag Arbitrage 完整执行器

功能：
  1. 扫描当前进行中的 BTC 1h 方向市场
  2. 用布朗运动模型计算真实概率
  3. 若 gap > 6¢ 自动下单
  4. 同时扫描 95¢ 策略机会

用法：
  ./venv/bin/python3 btc_lag_trader.py           # 单次扫描报告
  ./venv/bin/python3 btc_lag_trader.py --watch   # 持续监控 (每15秒)
  ./venv/bin/python3 btc_lag_trader.py --trade   # 扫描+自动下单
  ./venv/bin/python3 btc_lag_trader.py --95c     # 仅扫描 95¢ 策略
"""

import sys, os, json, time, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
from datetime import datetime, timezone
from btc_direction.lag_arb import (
    get_current_candle, fetch_active_btc_direction_markets,
    find_opportunities, LagArbOpportunity, MIN_GAP_TRADE,
)
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, BalanceAllowanceParams, AssetType,
    MarketOrderArgs, OrderType,
)
import urllib.request

# ── 凭据 ────────────────────────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)

LAG_LOG = os.path.join(os.path.dirname(__file__), "btc_direction", "lag_trades.json")

# 风控
MAX_BET      = 8.0
MIN_BET      = 2.0
KELLY_FRAC   = 0.25
MAX_OPEN     = 3       # 最多同时3个 lag arb 仓位


def get_client():
    return ClobClient("https://clob.polymarket.com",
        key=PRIVATE_KEY, chain_id=POLYGON,
        creds=creds, signature_type=1, funder=PROXY_WALLET)


def get_balance(client):
    b = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(b["balance"]) / 1e6


def load_log():
    if os.path.exists(LAG_LOG):
        try:
            return json.load(open(LAG_LOG))
        except Exception:
            pass
    return {"trades": []}


def save_log(data):
    os.makedirs(os.path.dirname(LAG_LOG), exist_ok=True)
    with open(LAG_LOG, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def kelly_bet(gap: float, price: float, balance: float) -> float:
    """Kelly 仓位计算"""
    if price <= 0 or price >= 1:
        return MIN_BET
    b = (1 / price) - 1
    p = price + gap      # 估算胜率
    q = 1 - p
    f = max(0, (b * p - q) / b)
    bet = balance * KELLY_FRAC * f
    return round(max(MIN_BET, min(MAX_BET, bet)), 2)


# ── 执行下单 ────────────────────────────────────────────────────────

def execute_lag_arb(opp: LagArbOpportunity, client, balance: float, dry_run: bool = False) -> bool:
    """执行 Lag Arb 下单"""
    bet = kelly_bet(opp.gap, opp.poly_price, balance)

    print(f"\n  🔥 执行 Lag Arb:")
    print(f"     {opp.market.question}")
    print(f"     方向: {opp.direction}  价格: {opp.poly_price:.3f}  gap: {opp.gap:.3f}")
    print(f"     K线进度: {opp.elapsed_min:.1f}min/{opp.remaining_min:.1f}min剩")
    print(f"     理论: {opp.theoretical_prob_up:.1%}  下注: ${bet}")

    if dry_run:
        print(f"     🔶 [模拟] 未实际下单")
        return True

    try:
        order = client.create_market_order(MarketOrderArgs(
            token_id=opp.token_id,
            amount=bet,
            side="BUY",
            price=opp.poly_price,
        ))
        resp = client.post_order(order, OrderType.FOK)

        if resp.get("success") or resp.get("orderID"):
            oid = resp.get("orderID", "?")
            taking = resp.get("takingAmount", "?")
            print(f"     ✅ 成交！订单: {oid[:20]} 获得: {taking} tokens")

            log = load_log()
            log["trades"].append({
                "type": "lag_arb",
                "market_id": opp.market.market_id,
                "question": opp.market.question,
                "direction": opp.direction,
                "token_id": opp.token_id,
                "price": opp.poly_price,
                "gap": opp.gap,
                "theoretical_prob": opp.theoretical_prob_up,
                "candle_return_pct": opp.candle_return_pct,
                "elapsed_min": opp.elapsed_min,
                "bet_usdc": bet,
                "order_id": oid,
                "tokens_received": taking,
                "time": datetime.now(timezone.utc).isoformat(),
                "status": "open",
                "event_end": str(opp.market.end_time),
            })
            save_log(log)
            return True
        else:
            err = resp.get("errorMsg") or str(resp)[:100]
            print(f"     ❌ 失败: {err}")
            return False
    except Exception as e:
        print(f"     ❌ 异常: {e}")
        return False


# ── 95¢ 策略 ────────────────────────────────────────────────────────

def scan_95c_strategy(min_price: float = 0.93, verbose: bool = True) -> list[dict]:
    """
    扫描所有价格 ≥ min_price 的高确定性市场（95¢ 策略）
    这些市场几乎必然解析为 YES，买入等待 1.00 结算
    """
    import json
    def _get(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Bot/1.0"})
            r = urllib.request.urlopen(req, timeout=12)
            return json.loads(r.read())
        except Exception:
            return None

    data = _get(
        "https://gamma-api.polymarket.com/markets?active=true&closed=false"
        "&limit=200&order=volume&ascending=false"
    )
    if not data:
        return []

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    results = []

    for m in data:
        if not m.get("acceptingOrders", False):
            continue
        liq = float(m.get("liquidity", 0))
        if liq < 500:
            continue

        prices_raw = m.get("outcomePrices")
        if not prices_raw:
            continue
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except Exception:
            continue

        # YES ≥ 93¢ 或 NO ≥ 93¢（其中一个接近确定）
        best_price = max(yes_price, no_price)
        if best_price < min_price:
            continue

        direction = "YES" if yes_price >= no_price else "NO"
        price = yes_price if direction == "YES" else no_price
        expected_return = (1 - price) / price * 100

        # 解析到期时间
        end_str = m.get("endDate", "")
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            days_left = (end_dt - now).days
        except Exception:
            days_left = -1

        token_ids = m.get("clobTokenIds", "[]")
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)

        results.append({
            "question": m.get("question", ""),
            "direction": direction,
            "price": price,
            "expected_return_pct": round(expected_return, 1),
            "days_left": days_left,
            "liquidity": liq,
            "volume": float(m.get("volume", 0)),
            "token_id": token_ids[0] if direction == "YES" and token_ids else (
                token_ids[1] if len(token_ids) > 1 else ""
            ),
            "market_id": m["id"],
        })

    results.sort(key=lambda x: x["price"], reverse=True)

    if verbose and results:
        print(f"\n{'='*55}")
        print(f"  💎 95¢ 策略扫描  发现 {len(results)} 个高确定性市场")
        print(f"{'='*55}")
        for r in results[:10]:
            profit_str = f"+{r['expected_return_pct']:.1f}%"
            print(f"\n  [{r['direction']} @ {r['price']:.3f} → {profit_str}] {r['days_left']}天剩")
            print(f"  {r['question'][:70]}")
            print(f"  liq=${r['liquidity']:.0f}  vol=${r['volume']:.0f}")
        print(f"{'='*55}\n")

    return results


# ── 主入口 ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch",   action="store_true", help="持续监控 Lag Arb (每15秒)")
    parser.add_argument("--trade",   action="store_true", help="自动下单")
    parser.add_argument("--dry-run", action="store_true", help="模拟下单")
    parser.add_argument("--95c",     dest="ninetyfive", action="store_true", help="仅扫描 95¢ 策略")
    parser.add_argument("--interval", type=int, default=15, help="监控间隔秒数")
    args = parser.parse_args()

    if args.ninetyfive:
        scan_95c_strategy(verbose=True)
        return

    if args.watch or args.trade:
        client = get_client() if args.trade else None
        handled = set()

        print(f"\n  🔄 Lag Arb 实时监控 | 间隔 {args.interval}s")
        print(f"  {'模拟模式' if args.dry_run else '实盘模式' if args.trade else '仅监控'}\n")

        iteration = 0
        while True:
            iteration += 1
            candle = get_current_candle()
            markets = fetch_active_btc_direction_markets()
            opps = find_opportunities(candle, markets, verbose=True)

            if args.trade or args.dry_run:
                balance = get_balance(client) if client else 70.0
                log = load_log()
                open_count = sum(1 for t in log["trades"] if t.get("status") == "open")

                for opp in opps:
                    if opp.gap < MIN_GAP_TRADE:
                        continue
                    if opp.market.market_id in handled:
                        continue
                    if open_count >= MAX_OPEN:
                        print(f"  ⚠️ 已达最大仓位 {MAX_OPEN}")
                        break
                    ok = execute_lag_arb(opp, client, balance, dry_run=args.dry_run)
                    if ok:
                        handled.add(opp.market.market_id)
                        open_count += 1

            # 同时跑一次 95¢ 扫描（每小时一次）
            if iteration % (3600 // args.interval) == 1:
                scan_95c_strategy(min_price=0.94, verbose=True)

            time.sleep(args.interval)

    else:
        # 单次扫描
        print(f"\n{'='*55}")
        print(f"  ⚡ Lag Arb 单次扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*55}")

        candle = get_current_candle()
        markets = fetch_active_btc_direction_markets()
        opps = find_opportunities(candle, markets, verbose=True)

        print(f"\n  --- 95¢ 策略 ---")
        results_95c = scan_95c_strategy(min_price=0.94, verbose=True)

        if opps:
            print(f"✅ Lag Arb: {len(opps)} 个机会 (运行 --trade 执行)")
        else:
            print(f"📭 Lag Arb: 无套利机会（当前K线进度或市场价差不足）")

        if results_95c:
            print(f"✅ 95¢策略: {len(results_95c)} 个高确定性机会")


if __name__ == "__main__":
    main()
