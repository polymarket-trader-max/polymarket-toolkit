#!/usr/bin/env python3
"""
Polymarket 实盘交易脚本 v2
通过 market_slug 匹配 CLOB token_id，针对小额资金优化
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, BalanceAllowanceParams, AssetType,
    MarketOrderArgs, OrderType
)
from live_scanner import scan_live
from datetime import datetime

# ── 凭据 ─────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)
client = ClobClient(
    "https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=POLYGON,
    creds=creds, signature_type=1, funder=PROXY_WALLET
)

# ── 仓位参数 ──────────────────────────────────────
MAX_BET    = 5.0
MIN_BET    = 2.0
MAX_TRADES = 3
KELLY_FRAC = 0.25
LOG_FILE   = "trade_log.json"

# ── 辅助函数 ──────────────────────────────────────
def get_balance():
    bal = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(bal["balance"]) / 1e6

def kelly_size(edge, price, balance):
    if price <= 0 or price >= 1: return 0
    b = (1 / price) - 1
    p = price + edge
    q = 1 - p
    f = (b * p - q) / b
    bet = balance * KELLY_FRAC * f
    return round(max(MIN_BET, min(MAX_BET, bet)), 2)

def load_log():
    if os.path.exists(LOG_FILE):
        return json.load(open(LOG_FILE))
    return {"trades": [], "total_bet": 0}

def save_log(data):
    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def build_clob_index():
    """拉取所有 CLOB 市场，建立 slug → tokens 索引"""
    index = {}
    cursor = ""
    pages = 0
    print("  📡 加载 CLOB 市场索引...", end=" ", flush=True)
    while pages < 20:  # 最多 20 页
        resp = client.get_markets(next_cursor=cursor)
        data = resp.get("data", [])
        for m in data:
            slug = m.get("market_slug", "")
            if slug:
                index[slug] = m.get("tokens", [])
        next_cur = resp.get("next_cursor", "LTE=")
        if next_cur == "LTE=" or not data:
            break
        cursor = next_cur
        pages += 1
    print(f"✓ {len(index)} 个市场")
    return index

def find_token_id(clob_index, market_slug, direction):
    """从 CLOB 索引获取 YES/NO token_id"""
    tokens = clob_index.get(market_slug, [])
    if not tokens:
        return None
    for t in tokens:
        outcome = t.get("outcome", "").upper()
        if direction == "YES" and outcome == "YES":
            return t["token_id"]
        if direction == "NO" and outcome == "NO":
            return t["token_id"]
    # 二选一市场：第0个=YES，第1个=NO
    if direction == "YES" and len(tokens) >= 1:
        return tokens[0]["token_id"]
    if direction == "NO" and len(tokens) >= 2:
        return tokens[1]["token_id"]
    return None

# ── 主逻辑 ────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"  🤖 Polymarket 自动交易 v2  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    balance = get_balance()
    print(f"💰 当前余额: ${balance:.4f} USDC")

    log = load_log()
    open_trades = [t for t in log["trades"] if t.get("status") == "open"]
    print(f"📋 持仓: {len(open_trades)}/{MAX_TRADES}")

    if len(open_trades) >= MAX_TRADES:
        print("⚠️  已达最大持仓数")
        return
    if balance < MIN_BET:
        print(f"⚠️  余额不足 ${MIN_BET}")
        return

    # 扫描机会
    print("\n🔍 扫描实时机会（含快照）...")
    opps = scan_live(verbose=True, auto_snapshot=True)

    if not opps:
        print("📭 暂无机会")
        return

    # 过滤已交易市场
    traded_ids = {t["market_id"] for t in log["trades"]}
    opps = [o for o in opps if o["market"]["id"] not in traded_ids]

    slots   = MAX_TRADES - len(open_trades)
    targets = opps[:slots]

    # 建立 CLOB 索引
    clob_index = build_clob_index()

    print(f"\n🎯 准备执行 {len(targets)} 笔交易：\n")
    placed = 0

    for i, opp in enumerate(targets, 1):
        m    = opp["market"]
        sig  = opp["signal"]
        slug = m.get("slug", "")
        yp   = m["yes_price"]

        direction = sig.direction
        price     = (1 - yp) if direction == "NO" else yp
        bet       = kelly_size(sig.edge, price, balance)

        print(f"  #{i} [{sig.category.upper()}] 做{direction} @ ${price:.3f} → 下注 ${bet}")
        print(f"      {m['question'][:65]}")
        print(f"      slug: {slug}")
        print(f"      边际:{sig.edge:.1%}  置信:{sig.confidence:.0%}  赔率:{1/price:.1f}x")

        # 获取 token_id
        token_id = find_token_id(clob_index, slug, direction)

        if not token_id:
            print(f"      ⚠️  CLOB 未找到该市场 (slug={slug})，跳过\n")
            log["trades"].append({
                "market_id": m["id"], "question": m["question"],
                "direction": direction, "price": price, "bet_usdc": bet,
                "status": "skipped", "reason": "no_clob_market",
                "time": datetime.now().isoformat()
            })
            continue

        print(f"      token_id: {token_id[:20]}...")
        print(f"      ⏳ 提交市价单...")

        try:
            order = client.create_market_order(MarketOrderArgs(
                token_id=token_id,
                amount=bet,
            ))
            resp = client.post_order(order, OrderType.FOK)

            if resp.get("success") or resp.get("orderID"):
                oid = resp.get("orderID", "?")
                print(f"      ✅ 成功！订单ID: {oid}")
                balance -= bet
                placed += 1
                log["trades"].append({
                    "market_id": m["id"], "question": m["question"],
                    "direction": direction, "price": price, "bet_usdc": bet,
                    "token_id": token_id, "order_id": oid,
                    "status": "open", "time": datetime.now().isoformat(),
                    "category": sig.category, "edge": sig.edge,
                    "days_left": opp["days_left"]
                })
                log["total_bet"] = log.get("total_bet", 0) + bet
            else:
                err = resp.get("errorMsg") or resp.get("error") or str(resp)[:120]
                print(f"      ❌ 下单失败: {err}")
                log["trades"].append({
                    "market_id": m["id"], "question": m["question"],
                    "direction": direction, "price": price, "bet_usdc": bet,
                    "status": "failed", "error": err,
                    "time": datetime.now().isoformat()
                })
        except Exception as e:
            print(f"      ❌ 异常: {e}")
            log["trades"].append({
                "market_id": m["id"], "question": m["question"],
                "direction": direction, "price": price, "bet_usdc": bet,
                "status": "error", "error": str(e)[:200],
                "time": datetime.now().isoformat()
            })
        print()

    save_log(log)

    balance_after = get_balance()
    print(f"\n{'='*60}")
    print(f"  📊 本次下单: {placed} 笔")
    print(f"  💰 剩余余额: ${balance_after:.4f} USDC")
    print(f"  📈 累计下注: ${log['total_bet']:.2f}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
