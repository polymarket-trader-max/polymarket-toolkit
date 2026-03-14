#!/usr/bin/env python3
"""
spread_maker.py — Pure Market Making Strategy (No Prediction Needed)
=====================================================================
Core Logic: Place simultaneous bid/ask orders on high-liquidity markets, earning the spread.
- Buy YES@bid + Buy NO@(1-ask) → Total cost < $1 → Settlement pays $1 → Net profit = spread
- Maker fee = 0 on Polymarket, this is our structural edge
- No prediction required! YES and NO are complementary, sum = 1

Risk Management:
- One-sided fill → directional exposure → time-based stop loss
- Market regime change → spread disappears → don't refill
- Max exposure per market: configurable
"""
import json, time, os, sys
from datetime import datetime, timedelta
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, BalanceAllowanceParams, AssetType, OrderArgs, OrderType
)

# ── Credentials (from environment) ──────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)
client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=POLYGON,
    creds=creds, signature_type=1, funder=PROXY_WALLET)

# ── State Files ─────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(__file__), "maker_state.json")
ACTION_LOG = os.path.join(os.path.dirname(__file__), "action_log.json")

# ── Parameters ──────────────────────────────────────
MIN_BALANCE       = 40.0     # Minimum balance to operate
MIN_VOLUME_24H    = 25000    # Minimum 24h volume
MIN_SPREAD        = 0.015    # Minimum spread (1.5 cents)
MAX_SPREAD        = 0.15     # Maximum spread
MAX_EXPOSURE      = 10.0     # Max exposure per market
MAX_TOTAL_DEPLOY  = 65.0     # Total deployment cap
MAX_MARKETS       = 10       # Max simultaneous markets
ORDER_SIZE_TOKENS = 8        # Tokens per order
STALE_HOURS       = 4        # One-sided timeout hours
MIN_HOURS_TO_END  = 24       # Minimum hours to market end

# ── Blacklist ───────────────────────────────────────
BLACKLIST = [
    "bitcoin up or down", "ethereum up or down", "up or down",
    "elon musk", "tweets from", "andrew tate",
]


def get_balance():
    bal = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(bal["balance"]) / 1e6


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"active_pairs": [], "history": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def log_action(action, details):
    """Persist actions to action_log for statistics"""
    entry = {
        "time": datetime.now().isoformat(),
        "action": action,
        "question": details.get("question", ""),
        "details": details,
    }
    log = []
    if os.path.exists(ACTION_LOG):
        log = json.load(open(ACTION_LOG))
    log.append(entry)
    json.dump(log, open(ACTION_LOG, "w"), indent=2, ensure_ascii=False)


def find_best_markets():
    """Find markets best suited for market making: high liquidity + suitable spread + far from settlement"""
    now = datetime.utcnow()
    min_end = (now + timedelta(hours=MIN_HOURS_TO_END)).strftime("%Y-%m-%dT%H:%M")
    
    candidates = []
    
    for offset in [0, 200, 400]:
        try:
            r = requests.get(
                f"https://gamma-api.polymarket.com/markets?limit=200&offset={offset}"
                f"&active=true&closed=false&order=volume24hr&ascending=false",
                timeout=15)
            markets = r.json()
            if not markets:
                break
        except:
            continue
        
        for m in markets:
            q = m.get("question", "")
            ql = q.lower()
            
            if any(kw in ql for kw in BLACKLIST):
                continue
            
            vol = float(m.get("volume24hr", 0) or 0)
            bid = float(m.get("bestBid", 0) or 0)
            ask = float(m.get("bestAsk", 0) or 0)
            end = m.get("endDate", "")
            
            if vol < MIN_VOLUME_24H:
                continue
            if not bid or not ask or bid <= 0 or ask <= 0:
                continue
            
            spread = ask - bid
            if spread < MIN_SPREAD or spread > MAX_SPREAD:
                continue
            
            if end and end[:16] < min_end:
                continue
            
            mid = (bid + ask) / 2
            if mid < 0.15 or mid > 0.85:
                continue
            
            try:
                tids = json.loads(m.get("clobTokenIds", "[]"))
            except:
                continue
            if len(tids) < 2:
                continue
            
            profit_per_pair = spread
            capital_per_pair = bid + (1 - ask)
            roi_pct = (profit_per_pair / capital_per_pair) * 100
            
            candidates.append({
                "question": q,
                "yes_token": tids[0],
                "no_token": tids[1],
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "volume": vol,
                "end": end[:10] if end else "?",
                "roi_pct": roi_pct,
                "capital_needed": capital_per_pair,
            })
    
    candidates.sort(key=lambda x: x["volume"] * x["roi_pct"], reverse=True)
    return candidates[:10]


def place_maker_pair(market, state):
    """Place simultaneous YES buy and NO buy orders on a market"""
    q = market["question"]
    yes_tid = market["yes_token"]
    no_tid = market["no_token"]
    
    yes_price = market["bid"]
    yes_tokens = ORDER_SIZE_TOKENS
    
    no_price = round(1 - market["ask"], 3)
    no_tokens = ORDER_SIZE_TOKENS
    
    total_cost = yes_price * yes_tokens + no_price * no_tokens
    if total_cost > MAX_EXPOSURE:
        scale = MAX_EXPOSURE / total_cost
        yes_tokens = max(5, int(yes_tokens * scale))
        no_tokens = max(5, int(no_tokens * scale))
        total_cost = yes_price * yes_tokens + no_price * no_tokens
    
    expected_profit = market["spread"] * min(yes_tokens, no_tokens)
    
    print(f"\n  📊 Market Making: {q[:55]}")
    print(f"     YES: BUY {yes_tokens}@{yes_price} | NO: BUY {no_tokens}@{no_price}")
    print(f"     Cost≈${total_cost:.2f} | Profit if both fill≈${expected_profit:.2f} ({market['roi_pct']:.1f}%)")
    
    yes_oid = None
    no_oid = None
    
    try:
        order = client.create_order(OrderArgs(
            token_id=yes_tid, price=yes_price, size=yes_tokens, side="BUY"))
        resp = client.post_order(order, OrderType.GTC)
        if resp.get("success") or resp.get("orderID"):
            yes_oid = resp.get("orderID", "?")
            print(f"     ✅ YES order: {yes_oid[:25]}")
        else:
            print(f"     ❌ YES order failed: {resp}")
            return None
    except Exception as e:
        print(f"     ❌ YES order error: {e}")
        return None
    
    time.sleep(0.5)
    
    try:
        order = client.create_order(OrderArgs(
            token_id=no_tid, price=no_price, size=no_tokens, side="BUY"))
        resp = client.post_order(order, OrderType.GTC)
        if resp.get("success") or resp.get("orderID"):
            no_oid = resp.get("orderID", "?")
            print(f"     ✅ NO order: {no_oid[:25]}")
        else:
            print(f"     ❌ NO order failed: {resp}")
            try:
                client.cancel(order_id=yes_oid)
                print(f"     🔄 Cancelled YES order")
            except:
                pass
            return None
    except Exception as e:
        print(f"     ❌ NO order error: {e}")
        try:
            client.cancel(order_id=yes_oid)
        except:
            pass
        return None
    
    pair = {
        "question": q,
        "yes_token": yes_tid,
        "no_token": no_tid,
        "yes_oid": yes_oid,
        "no_oid": no_oid,
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_tokens": yes_tokens,
        "no_tokens": no_tokens,
        "spread": market["spread"],
        "expected_profit": expected_profit,
        "total_cost": total_cost,
        "time": datetime.now().isoformat(),
        "status": "active",
        "yes_filled": False,
        "no_filled": False,
    }
    
    return pair


def check_fills(state):
    """Check fill status of existing market making pairs"""
    for pair in state.get("active_pairs", []):
        if pair["status"] != "active":
            continue
        
        if not pair.get("yes_filled"):
            try:
                bal = client.get_balance_allowance(
                    params=BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL, token_id=pair["yes_token"]))
                yes_bal = int(bal.get("balance", 0)) / 1e6
                if yes_bal >= pair["yes_tokens"] * 0.8:
                    pair["yes_filled"] = True
                    pair["yes_filled_time"] = datetime.now().isoformat()
                    print(f"  ✅ YES filled: {pair['question'][:45]} ({yes_bal:.1f} tokens)")
            except:
                pass
        
        if not pair.get("no_filled"):
            try:
                bal = client.get_balance_allowance(
                    params=BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL, token_id=pair["no_token"]))
                no_bal = int(bal.get("balance", 0)) / 1e6
                if no_bal >= pair["no_tokens"] * 0.8:
                    pair["no_filled"] = True
                    pair["no_filled_time"] = datetime.now().isoformat()
                    print(f"  ✅ NO filled: {pair['question'][:45]} ({no_bal:.1f} tokens)")
            except:
                pass
        
        if pair.get("yes_filled") and pair.get("no_filled"):
            pair["status"] = "both_filled"
            profit = pair["spread"] * min(pair["yes_tokens"], pair["no_tokens"])
            print(f"  🎉 Both sides filled! {pair['question'][:40]} | Locked profit≈${profit:.2f}")
            log_action("MAKER_BOTH_FILLED", {
                "question": pair["question"],
                "pnl": profit,
                "spread": pair["spread"],
            })
        
        elif pair.get("yes_filled") or pair.get("no_filled"):
            filled_side = "yes" if pair.get("yes_filled") else "no"
            filled_time = pair.get(f"{filled_side}_filled_time", pair["time"])
            elapsed = (datetime.now() - datetime.fromisoformat(filled_time)).total_seconds() / 3600
            
            if elapsed > STALE_HOURS:
                unfilled_side = "no" if filled_side == "yes" else "yes"
                unfilled_oid = pair.get(f"{unfilled_side}_oid")
                
                try:
                    client.cancel(order_id=unfilled_oid)
                    print(f"  ⏰ Timeout: cancelled {unfilled_side} order | {pair['question'][:40]}")
                except:
                    pass
                
                pair["status"] = "partial_timeout"
                log_action("MAKER_PARTIAL_TIMEOUT", {
                    "question": pair["question"],
                    "filled_side": filled_side,
                    "elapsed_hours": round(elapsed, 1),
                })


def cleanup_old_pairs(state):
    """Clean up completed/timed-out pairs"""
    active = []
    for pair in state.get("active_pairs", []):
        if pair["status"] == "active":
            active.append(pair)
        else:
            state.setdefault("history", []).append(pair)
    state["active_pairs"] = active


def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  📊 Spread Maker  {now}")
    print(f"{'='*60}")
    
    balance = get_balance()
    print(f"  💰 Balance: ${balance:.2f}")
    
    if balance < MIN_BALANCE:
        print(f"  ⛔ Balance < ${MIN_BALANCE}, pausing market making")
        return {"status": "low_balance", "balance": balance}
    
    state = load_state()
    
    active_count = len([p for p in state.get("active_pairs", []) if p["status"] == "active"])
    print(f"  📋 Active pairs: {active_count}/{MAX_MARKETS}")
    
    if state.get("active_pairs"):
        print(f"\n  🔍 Checking fill status...")
        check_fills(state)
        cleanup_old_pairs(state)
    
    total_deployed = sum(
        p.get("total_cost", 0) 
        for p in state.get("active_pairs", []) 
        if p["status"] == "active"
    )
    
    active_count = len([p for p in state.get("active_pairs", []) if p["status"] == "active"])
    
    if active_count < MAX_MARKETS and total_deployed < MAX_TOTAL_DEPLOY:
        print(f"\n  🔍 Scanning market making opportunities...")
        markets = find_best_markets()
        print(f"  📊 Found {len(markets)} candidate markets")
        
        active_tokens = set()
        for p in state.get("active_pairs", []):
            if p["status"] == "active":
                active_tokens.add(p["yes_token"])
                active_tokens.add(p["no_token"])
        
        for m in markets:
            if active_count >= MAX_MARKETS:
                break
            if total_deployed + m["capital_needed"] * ORDER_SIZE_TOKENS > MAX_TOTAL_DEPLOY:
                break
            if m["yes_token"] in active_tokens:
                continue
            
            print(f"\n  Candidate: {m['question'][:55]}")
            print(f"    bid={m['bid']} ask={m['ask']} spread={m['spread']:.3f} vol=${m['volume']:,.0f} roi={m['roi_pct']:.1f}%")
            
            pair = place_maker_pair(m, state)
            if pair:
                state.setdefault("active_pairs", []).append(pair)
                active_count += 1
                total_deployed += pair["total_cost"]
    
    save_state(state)
    
    active_pairs = [p for p in state.get("active_pairs", []) if p["status"] == "active"]
    both_filled = [p for p in state.get("history", []) if p.get("status") == "both_filled"]
    
    print(f"\n  {'─'*55}")
    print(f"  Active pairs: {len(active_pairs)} | Completed: {len(both_filled)}")
    print(f"  Total deployed: ${total_deployed:.2f} | Balance: ${balance:.2f}")
    
    return {
        "active_pairs": len(active_pairs),
        "total_deployed": total_deployed,
        "balance": balance,
    }


if __name__ == "__main__":
    result = run()
    print(f"\nResult: {json.dumps(result)}")
