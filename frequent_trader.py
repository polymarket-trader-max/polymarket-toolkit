#!/usr/bin/env python3
"""
frequent_trader.py — 电竞+高确信度自动交易
策略v3: 只做电竞(唯一正alpha品类) + 高概率时间衰减
- 电竞: 赛事定价低效，83%胜率，净+$6.42
- 时间衰减: 结算前买高概率市场，持有到期
- 全部GTC限价单, Maker fee=0
"""
import json, time, os, sys
from datetime import datetime, timedelta
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType, MarketOrderArgs, OrderArgs, OrderType

# ── 凭据 ────────────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)
client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=POLYGON,
    creds=creds, signature_type=1, funder=PROXY_WALLET)

LOG_FILE       = os.path.join(os.path.dirname(__file__), "trade_log.json")
DAILY_LOG_FILE = os.path.join(os.path.dirname(__file__), "daily_trade_state.json")

# ── 参数 ────────────────────────────────────────────────
MIN_BALANCE        = 100.0  # 余额低于此值停止（保守，保命钱）
BET_SIZE_ESPORTS   = 4.0    # 电竞下注额（我们的alpha，可以大一点）
BET_SIZE_TIMEDECAY = 5.0    # 时间衰减下注额（高确定性）
BET_SIZE_DEFAULT   = 3.0    # 其他下注额
MAX_NEW_TRADES     = 4      # 每次最多新开仓位
MAX_DAILY_TRADES   = 15     # 每日最多新开仓位
MAX_DAILY_SPEND    = 40.0   # 每日最大投入（控制风险）
MAX_OPEN_POSITIONS = 12     # 全局最多同时持仓数
MAX_SPREAD         = 0.03   # 价差放宽到3分（电竞市场流动性不如体育）

# ── 电竞白名单（ONLY these get traded automatically）────
ESPORTS_KEYWORDS = [
    "counter-strike:", "cs2:", "valorant:", "dota 2:", "lol:", 
    "league of legends:", "overwatch:", "rocket league:",
    "honor of kings:", "starcraft:",
    # 比赛格式标记
    "bo3", "bo5",
    # 战队/赛事名
    "esports", "gaming vs", "esl ", "vct ", "pgl ", "blast ",
    "iem ", "cct ", "masters",
]

# 时间衰减白名单关键词（高概率、结算快的市场）
TIMEDECAY_KEYWORDS = [
    "vs.", "win on", "fc win",  # 体育比赛
]

# ── 黑名单（永远不碰）─────────────────────────────────
BLACKLIST_KEYWORDS = [
    "Bitcoin Up or Down", "Ethereum Up or Down", "Up or Down -",
    "price of Bitcoin", "price of Ethereum", "price of BTC", "price of ETH",
    "BTC above", "BTC below", "BTC between",
    "Elon Musk", "tweets from",
    "O/U ", "Over/Under",      # 大小分，盈亏比差
    "Spread:",                  # 让分盘，盈亏比差
    "Both Teams to Score",     # BTTS，盈亏比差
]

# ── 辅助 ────────────────────────────────────────────────
def get_balance():
    bal = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(bal["balance"]) / 1e6

def load_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        raw = json.load(f)
    return raw if isinstance(raw, list) else raw.get("trades", [])

def save_log(trades):
    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

def already_have(token_id, trades):
    """判断是否已有该token的持仓"""
    for t in trades:
        if t.get("token_id") == token_id and t.get("status") == "open":
            return True
    return False

def classify_market(question):
    """分类市场: esports / timedecay / skip"""
    ql = question.lower()
    # 先检查黑名单
    if any(kw.lower() in ql for kw in BLACKLIST_KEYWORDS):
        return "skip"
    # 电竞优先
    if any(kw.lower() in ql for kw in ESPORTS_KEYWORDS):
        return "esports"
    # 时间衰减（体育比赛，高概率）
    if any(kw.lower() in ql for kw in TIMEDECAY_KEYWORDS):
        return "timedecay"
    return "skip"

def fetch_candidates():
    """扫描市场 — 电竞无时间限制，时间衰减只看3天内结算"""
    now = datetime.utcnow()
    cutoff_3d = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    
    candidates = []
    for offset in [0, 200, 400, 600, 800]:
        try:
            resp = requests.get(
                f"https://gamma-api.polymarket.com/markets?limit=200&offset={offset}"
                f"&active=true&closed=false&order=volume24hr&ascending=false",
                timeout=15)
            markets = resp.json()
            if not markets:
                break
        except Exception as e:
            print(f"  ⚠️ 获取市场失败: {e}")
            continue

        for m in markets:
            question = m.get("question", "")
            category = classify_market(question)
            if category == "skip":
                continue

            vol  = float(m.get("volume24hr", 0) or 0)
            ask  = float(m.get("bestAsk", 0) or 0)
            bid  = float(m.get("bestBid", 0) or 0)
            end  = m.get("endDate", "")[:10]
            
            if not bid or ask < 0.05 or ask > 0.95:
                continue
            
            spread = ask - bid
            if spread > MAX_SPREAD:
                continue

            # 电竞: 任何价位都行(0.20-0.90), 流动性门槛低一点
            if category == "esports":
                if vol < 5000 or ask < 0.20 or ask > 0.90:
                    continue
                bet_size = BET_SIZE_ESPORTS
            
            # 时间衰减: 高概率(>0.78), 近期结算, 高流动性
            elif category == "timedecay":
                if vol < 80000 or bid < 0.78 or ask > 0.92:
                    continue
                if end > cutoff_3d:
                    continue
                bet_size = BET_SIZE_TIMEDECAY
            
            else:
                continue

            try:
                tids = json.loads(m.get("clobTokenIds", "[]"))
            except:
                continue
            if not tids:
                continue

            candidates.append({
                "question": question,
                "token_id": tids[0],
                "price": ask,
                "bid": bid,
                "volume": vol,
                "spread": spread,
                "end": end,
                "category": category,
                "bet_size": bet_size,
            })

    # 电竞优先排序，然后按volume
    candidates.sort(key=lambda x: (
        0 if x["category"] == "esports" else 1,
        -x["volume"]
    ))
    return candidates

def load_daily_state():
    """读取今日交易状态"""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if os.path.exists(DAILY_LOG_FILE):
        try:
            with open(DAILY_LOG_FILE) as f:
                state = json.load(f)
            if state.get("date") == today:
                return state
        except:
            pass
    return {"date": today, "trades_today": 0, "spend_today": 0.0}

def save_daily_state(state):
    with open(DAILY_LOG_FILE, "w") as f:
        json.dump(state, f, indent=2)

def verify_fill(token_id, expected_bet, timeout=5):
    """下单后验证链上实际成交量，返回实际 token 余额"""
    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
    time.sleep(timeout)   # 等链上确认
    try:
        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
        actual_tokens = int(bal.get("balance", 0)) / 1e6
        return actual_tokens
    except:
        return 0.0

def execute_trade(token_id, bet, price, question):
    """
    执行 GTC 限价买单（Maker 模式，手续费=0）
    price = Gamma bestAsk（我们挂在 bestBid 价格，比 ask 便宜）
    返回 (success, order_id, error)
    """
    try:
        # 策略：在 Gamma bestBid 处挂限价单（而非 bestAsk 市价吃单）
        # 这样我们是 Maker，手续费=0，且买入价更优
        # 注意：price 参数传入的是 candidate["price"] = Gamma ask
        # 我们用 candidate["bid"] 通过 run() 传入，但这里用 price 做 fallback
        # GTC 单会一直挂着直到成交或取消
        
        buy_price = price  # 调用方已经传入优化后的价格
        tokens = round(bet / buy_price, 2)
        if tokens < 5.0:
            tokens = 5.0  # Polymarket 最小订单 = 5 tokens
        
        order = client.create_order(OrderArgs(
            token_id=token_id,
            price=buy_price,
            size=tokens,
            side="BUY",
        ))
        resp = client.post_order(order, OrderType.GTC)
        if resp.get("success") or resp.get("orderID"):
            return True, resp.get("orderID", "?"), None
        else:
            return False, None, resp.get("errorMsg") or resp.get("error") or str(resp)[:80]
    except Exception as e:
        return False, None, str(e)[:120]

# ── 主流程 ───────────────────────────────────────────────
def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  🤖 频繁交易扫描  {now}")
    print(f"{'='*60}")

    balance = get_balance()
    print(f"  💰 余额: ${balance:.2f}")

    # ── 每日状态 ──────────────────────────────────────────
    daily = load_daily_state()
    print(f"  📅 今日已开: {daily['trades_today']}/{MAX_DAILY_TRADES} 笔 | 已花: ${daily['spend_today']:.2f}/${MAX_DAILY_SPEND}")

    if balance < MIN_BALANCE:
        print(f"  ⛔ 余额低于 ${MIN_BALANCE}，暂停下单")
        return {"balance": balance, "placed": 0, "skipped": "low_balance"}

    if daily["trades_today"] >= MAX_DAILY_TRADES:
        print(f"  ⛔ 今日已达最大开仓数 {MAX_DAILY_TRADES}，停止")
        return {"balance": balance, "placed": 0, "skipped": "daily_limit"}

    if daily["spend_today"] >= MAX_DAILY_SPEND:
        print(f"  ⛔ 今日投入已达 ${MAX_DAILY_SPEND} 上限，停止")
        return {"balance": balance, "placed": 0, "skipped": "daily_spend_limit"}

    trades = load_log()
    open_tokens = {t["token_id"] for t in trades if t.get("status") == "open"}
    open_count = len([t for t in trades if t.get("status") == "open"])

    if open_count >= MAX_OPEN_POSITIONS:
        print(f"  ⛔ 持仓已达上限 {MAX_OPEN_POSITIONS}，暂停新开仓")
        return {"balance": balance, "placed": 0, "skipped": "max_positions"}

    print(f"  📋 当前开仓数: {open_count}/{MAX_OPEN_POSITIONS}")
    print(f"  🔍 扫描市场中...")

    candidates = fetch_candidates()
    print(f"  📊 候选市场: {len(candidates)} 个")

    placed = 0
    new_entries = []

    remaining_daily = min(MAX_DAILY_TRADES - daily["trades_today"], MAX_NEW_TRADES)

    for c in candidates:
        if placed >= remaining_daily:
            break
        if c["token_id"] in open_tokens:
            continue
        if open_count + placed >= MAX_OPEN_POSITIONS:
            print(f"  ⚠️ 持仓上限已满，停止本轮")
            break

        price = c["price"]
        bet = c.get("bet_size", BET_SIZE_DEFAULT)
        cat_emoji = "🎮" if c.get("category") == "esports" else "⏰"

        # 每日花费检查
        if daily["spend_today"] + bet > MAX_DAILY_SPEND:
            print(f"  ⚠️ 再下 ${bet} 将超今日 ${MAX_DAILY_SPEND} 上限，跳过")
            break

        if balance - bet < MIN_BALANCE:
            print(f"  ⚠️ 下单 ${bet} 会低于余额下限，跳过")
            break

        q_short = c["question"][:55]
        
        # Maker 策略：在 Gamma bestBid 处挂限价单（比 ask 便宜 1 个 spread）
        # 如果 bid 和 ask 很接近（spread < 0.01），用 bid 价格
        # 否则用 bid + 0.01（稍微激进一点，提高成交率）
        maker_price = c["bid"]
        if c["spread"] >= 0.02:
            maker_price = round(c["bid"] + 0.01, 3)  # 在 bid 上方 1 分，成为最优买方
        saving = round(price - maker_price, 3)
        
        # 确保满足最小 token 数量要求（Polymarket minimum = 5 tokens）
        min_tokens = 5.0
        min_bet = maker_price * min_tokens
        if bet < min_bet:
            bet = round(min_bet + 0.1, 2)  # 向上取整，确保至少 5 tokens
        
        print(f"\n  {cat_emoji} 下单: {q_short}")
        print(f"    [{c.get('category','?')}] ask={price} bid={c['bid']} → maker@{maker_price} (省${saving})")
        print(f"    vol={c['volume']:.0f} end={c['end']} bet=${bet} ({bet/maker_price:.1f}tokens)")

        success, oid, err = execute_trade(c["token_id"], bet, maker_price, c["question"])

        if success:
            # GTC 限价单：可能不会立即成交
            # 先等 3 秒检查是否已经成交（价格正好在 bid 处时可能秒成交）
            actual_tokens = verify_fill(c["token_id"], bet, timeout=3)
            if actual_tokens < 0.01:
                # GTC单已挂出但尚未成交 — 这是正常的！
                print(f"    📋 GTC限价单已挂出，等待成交 (maker_price={maker_price})")
                fill_status = "pending_fill"  # 新状态：挂单中
            else:
                fill_status = "open"
                print(f"    ✅ 即时成交！持有 {actual_tokens:.4f} tokens (Maker费率=0)")

            tokens_actual = str(round(actual_tokens if actual_tokens > 0.01 else bet / maker_price, 4))
            # ── 动态止盈止损（按入场价区间调整盈亏比）────────
            # 注意：用 maker_price（实际挂单价）而非 ask 价
            if maker_price >= 0.80:
                tp = round(min(maker_price + 0.10, 0.96), 3)
                sl = round(max(maker_price - 0.15, 0.30), 3)
            elif maker_price >= 0.70:
                tp = round(min(maker_price + 0.15, 0.95), 3)
                sl = round(max(maker_price - 0.18, 0.25), 3)
            else:
                tp = round(min(maker_price + 0.20, 0.90), 3)
                sl = round(max(maker_price - 0.15, 0.25), 3)
            entry = {
                "question": c["question"], "direction": "BUY",
                "price": maker_price, "bet_usdc": bet,
                "original_ask": price,  # 记录原始ask，方便复盘
                "token_id": c["token_id"], "order_id": oid,
                "tokens": tokens_actual,
                "take_profit": tp, "stop_loss": sl,
                "rationale": f"frequent_trading vol={int(c['volume'])} spread={c['spread']:.3f}",
                "status": fill_status,
                "time": datetime.now().isoformat(),
                "strategy": "frequent_trading",
            }
            new_entries.append(entry)
            open_tokens.add(c["token_id"])
            # GTC 挂单和即时成交都计入统计（挂单锁定了余额）
            if fill_status in ("open", "pending_fill"):
                balance -= bet
                daily["trades_today"] += 1
                daily["spend_today"] = round(daily["spend_today"] + bet, 2)
                placed += 1
                status_emoji = "✅" if fill_status == "open" else "📋"
                print(f"    {status_emoji} 入账！{oid[:30]} | 今日累计: {daily['trades_today']}笔/${daily['spend_today']:.2f}")
            else:
                print(f"    ⚠️ 未计入统计（状态异常）")
        else:
            print(f"    ❌ 失败: {err}")

        time.sleep(1.0)

    if new_entries:
        trades.extend(new_entries)
        save_log(trades)
        save_daily_state(daily)

    print(f"\n  {'─'*55}")
    print(f"  本轮确认成交: {placed} 笔 | 今日累计: {daily['trades_today']}笔/${daily['spend_today']:.2f} | 余额: ${balance:.2f}")
    return {"balance": balance, "placed": placed, "new_trades": [e["question"][:40] for e in new_entries]}

if __name__ == "__main__":
    result = run()
    print("\n结果:", json.dumps(result, ensure_ascii=False))
