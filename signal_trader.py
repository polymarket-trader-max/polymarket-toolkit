#!/usr/bin/env python3
"""
signal_trader.py — 消息面驱动的智能交易
=============================================
核心思路：不盲扫，只在有信息优势时交易
信号源：
  1. 油价实时数据 → 原油相关市场（量化概率建模）
  2. ESPN实时比分 → 体育市场（比分领先→概率领先）
  3. 新闻关键词 → 地缘市场（快速反应）

与旧系统的区别：
  - 旧系统：扫800个市场，keyword匹配，盲买 → 9W/21L
  - 新系统：只在有明确信号时交易，每笔有推理依据
  - 止盈止损不等结算，活用

风控：
  - 单笔$2-3，信号强度高时最多$4
  - 每日最多$15（vs旧的$40）
  - edge要求>=8%（vs旧的3%）
"""
import json, time, os, sys, math
from datetime import datetime, timedelta
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, BalanceAllowanceParams, AssetType, OrderArgs, OrderType
)

# ── 凭据 ────────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)
client = ClobClient("https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=POLYGON,
    creds=creds, signature_type=1, funder=PROXY_WALLET)

LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")
ACTION_LOG = os.path.join(os.path.dirname(__file__), "action_log.json")
DAILY_STATE = os.path.join(os.path.dirname(__file__), "signal_daily_state.json")

# ── 参数 ────────────────────────────────────────────
MIN_BALANCE      = 50.0     # 降低门槛，做市占用多但信号交易要继续
MIN_EDGE         = 0.08     # 至少8%的edge才交易
BET_SMALL        = 2.0      # 普通信号
BET_MEDIUM       = 3.0      # 强信号
BET_LARGE        = 4.0      # 极强信号（>=15% edge）
MAX_DAILY_TRADES = 5
MAX_DAILY_SPEND  = 15.0
MAX_OPEN_SIGNAL  = 6        # 信号仓最多6个


def get_balance():
    bal = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    return int(bal["balance"]) / 1e6


def load_trades():
    if not os.path.exists(LOG_FILE):
        return []
    data = json.load(open(LOG_FILE))
    return data if isinstance(data, list) else data.get("trades", [])


def save_trades(trades):
    json.dump(trades, open(LOG_FILE, "w"), indent=2, ensure_ascii=False)


def load_daily():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if os.path.exists(DAILY_STATE):
        state = json.load(open(DAILY_STATE))
        if state.get("date") == today:
            return state
    return {"date": today, "trades": 0, "spend": 0.0}


def save_daily(state):
    json.dump(state, open(DAILY_STATE, "w"), indent=2)


def log_action(action, details):
    entry = {"time": datetime.now().isoformat(), "action": action, **details}
    log = []
    if os.path.exists(ACTION_LOG):
        log = json.load(open(ACTION_LOG))
    log.append(entry)
    json.dump(log, open(ACTION_LOG, "w"), indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════
#  信号1: 油价 → 原油市场
# ══════════════════════════════════════════════════════
def get_oil_price():
    """获取实时WTI/Brent油价"""
    try:
        # 用Yahoo Finance API
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/CL=F?interval=1d&range=5d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        prices = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        current = [p for p in prices if p is not None][-1]
        return current
    except:
        pass
    
    # Fallback: Brent
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1d&range=5d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        prices = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        current = [p for p in prices if p is not None][-1]
        return current
    except:
        return None


def oil_hit_probability(current_price, target, days_left, direction="high"):
    """
    布朗运动模型：估算油价在days_left天内触及target的概率
    假设日波动率约3%（战时更高）
    """
    if days_left <= 0:
        if direction == "high":
            return 1.0 if current_price >= target else 0.0
        else:
            return 1.0 if current_price <= target else 0.0
    
    daily_vol = 0.05  # 战时约5%日波动率（伊朗战争+霍尔木兹关闭）
    
    # Log-normal模型
    log_ratio = math.log(target / current_price)
    sigma = daily_vol * math.sqrt(days_left)
    
    if direction == "high":
        # P(max > target) ≈ 2 * P(S_T > target) for GBM with no drift
        # 简化用 2*N(-d) where d = log(K/S) / (sigma)
        d = log_ratio / sigma
        # 标准正态CDF近似
        prob = 2 * norm_cdf(-d)
        return min(prob, 0.99)
    else:
        # P(min < target)
        d = -log_ratio / sigma
        prob = 2 * norm_cdf(-d)
        return min(prob, 0.99)


def norm_cdf(x):
    """标准正态CDF的近似（Abramowitz and Stegun）"""
    if x >= 0:
        t = 1.0 / (1.0 + 0.2316419 * x)
        d = 0.3989422804 * math.exp(-x * x / 2)
        p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
        return 1.0 - p
    else:
        return 1.0 - norm_cdf(-x)


def scan_oil_signals():
    """扫描油价相关的Polymarket市场，找edge"""
    oil_price = get_oil_price()
    if not oil_price:
        print("  ⚠️ 无法获取油价")
        return []
    
    print(f"  🛢️ 当前油价: ${oil_price:.2f}")
    
    now = datetime.utcnow()
    signals = []
    
    # 搜索Polymarket上的原油市场（直接关键词搜索更可靠）
    oil_markets = []
    try:
        for offset in [0, 200, 400]:
            r = requests.get(
                f"https://gamma-api.polymarket.com/markets?limit=200&offset={offset}"
                f"&active=true&closed=false&order=volume24hr&ascending=false",
                timeout=15)
            batch = r.json()
            if not batch:
                break
            oil_markets.extend([m for m in batch if "crude oil" in m.get("question", "").lower()])
    except:
        return []
    
    print(f"  📊 找到 {len(oil_markets)} 个原油市场")
    
    for m in oil_markets:
        q = m.get("question", "")
        ql = q.lower()
        if "crude oil" not in ql:
            continue
        
        bid = float(m.get("bestBid", 0) or 0)
        ask = float(m.get("bestAsk", 0) or 0)
        vol = float(m.get("volume24hr", 0) or 0)
        end = m.get("endDate", "")
        
        if not bid or not ask or vol < 20000:
            continue
        
        try:
            tids = json.loads(m.get("clobTokenIds", "[]"))
        except:
            continue
        if len(tids) < 2:
            continue
        
        # 解析目标价格和方向
        target = None
        direction = "high"
        
        # "hit (HIGH) $105" or "hit (LOW) $80"
        import re
        high_match = re.search(r'hit.*?(?:HIGH|high).*?\$(\d+)', q)
        low_match = re.search(r'hit.*?(?:LOW|low).*?\$(\d+)', q)
        
        if high_match:
            target = float(high_match.group(1))
            direction = "high"
        elif low_match:
            target = float(low_match.group(1))
            direction = "low"
        else:
            continue
        
        # 计算剩余天数
        if end:
            try:
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00").split("+")[0])
                days_left = max(0, (end_dt - now).total_seconds() / 86400)
            except:
                continue
        else:
            continue
        
        if days_left < 1:
            continue
        
        # 计算模型概率
        model_prob = oil_hit_probability(oil_price, target, days_left, direction)
        market_prob = ask  # 市场隐含概率（买入价）
        
        edge = model_prob - market_prob
        
        print(f"  📊 {q[:55]}")
        print(f"     油价${oil_price:.0f} → 目标${target} ({direction}) | {days_left:.0f}天")
        print(f"     模型概率: {model_prob:.1%} | 市场价: {market_prob:.1%} | Edge: {edge:+.1%}")
        
        if abs(edge) >= MIN_EDGE:
            if edge > 0:
                # 模型说YES概率更高 → 买YES
                signals.append({
                    "question": q,
                    "token_id": tids[0],
                    "side": "YES",
                    "price": bid,  # maker price
                    "edge": edge,
                    "model_prob": model_prob,
                    "market_prob": market_prob,
                    "reasoning": f"油价${oil_price:.0f}, 布朗运动模型{model_prob:.0%} vs 市场{market_prob:.0%}, edge={edge:+.0%}",
                    "source": "oil_model",
                })
            else:
                # 模型说YES概率更低 → 买NO
                no_edge = abs(edge)
                no_price = round(1 - ask, 3)
                signals.append({
                    "question": q,
                    "token_id": tids[1],
                    "side": "NO",
                    "price": no_price,
                    "edge": no_edge,
                    "model_prob": 1 - model_prob,
                    "market_prob": 1 - market_prob,
                    "reasoning": f"油价${oil_price:.0f}, 模型NO={1-model_prob:.0%} vs 市场NO={1-market_prob:.0%}, edge={no_edge:+.0%}",
                    "source": "oil_model",
                })
    
    return signals


# ══════════════════════════════════════════════════════
#  信号2: ESPN实时比分 → 体育市场
# ══════════════════════════════════════════════════════
def get_espn_live(sport="basketball", league="nba"):
    """获取ESPN实时比分"""
    try:
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard",
            timeout=10)
        return r.json().get("events", [])
    except:
        return []


def estimate_win_prob_nba(home_score, away_score, period, time_remaining_min):
    """
    估算NBA胜率（基于比分差和剩余时间）
    经验公式：每分钟约值2.5分的波动
    """
    diff = home_score - away_score  # 正=主队领先
    
    # 总剩余分钟
    total_min = (4 - period) * 12 + time_remaining_min
    if total_min <= 0:
        return 0.99 if diff > 0 else (0.50 if diff == 0 else 0.01)
    
    # 简化模型：正态分布
    # 最终分差 ≈ 当前分差 + N(0, sigma)
    # sigma ≈ 2.5 * sqrt(remaining_minutes)（经验值）
    sigma = 2.5 * math.sqrt(total_min)
    if sigma == 0:
        return 0.99 if diff > 0 else 0.01
    
    # P(主队赢) = P(final_diff > 0) = Phi(diff / sigma)
    prob = norm_cdf(diff / sigma)
    return max(0.01, min(0.99, prob))


def scan_espn_signals():
    """扫描ESPN实时比分，找Polymarket套利"""
    signals = []
    
    # NBA
    events = get_espn_live("basketball", "nba")
    if not events:
        return signals
    
    live_games = []
    for event in events:
        comp = event.get("competitions", [{}])[0]
        status = comp.get("status", {})
        state = status.get("type", {}).get("state", "")
        
        if state != "in":  # 只看进行中的
            continue
        
        teams = {}
        for t in comp.get("competitors", []):
            ha = t.get("homeAway", "")
            teams[ha] = {
                "name": t.get("team", {}).get("displayName", ""),
                "abbr": t.get("team", {}).get("abbreviation", ""),
                "score": int(t.get("score", 0) or 0),
            }
        
        period = status.get("period", 1)
        clock = status.get("displayClock", "12:00")
        try:
            parts = clock.split(":")
            mins_left = int(parts[0]) + float(parts[1]) / 60 if len(parts) == 2 else float(parts[0])
        except:
            mins_left = 6.0
        
        home = teams.get("home", {})
        away = teams.get("away", {})
        
        if not home.get("name") or not away.get("name"):
            continue
        
        home_prob = estimate_win_prob_nba(
            home["score"], away["score"], period, mins_left)
        
        diff = home["score"] - away["score"]
        total_min = (4 - period) * 12 + mins_left
        
        print(f"  🏀 {away['name']} {away['score']} @ {home['name']} {home['score']} | Q{period} {clock}")
        print(f"     主队胜率: {home_prob:.0%} | 分差: {diff:+d} | 剩余{total_min:.0f}min")
        
        live_games.append({
            "home": home, "away": away,
            "home_prob": home_prob, "away_prob": 1 - home_prob,
            "period": period, "diff": diff, "total_min": total_min,
        })
    
    if not live_games:
        return signals
    
    # 获取Polymarket体育市场
    try:
        r = requests.get(
            "https://gamma-api.polymarket.com/markets?limit=200&active=true&closed=false"
            "&order=volume24hr&ascending=false",
            timeout=15)
        all_markets = r.json()
    except:
        return signals
    
    for m in all_markets:
        q = m.get("question", "")
        ql = q.lower()
        bid = float(m.get("bestBid", 0) or 0)
        ask = float(m.get("bestAsk", 0) or 0)
        vol = float(m.get("volume24hr", 0) or 0)
        
        if not bid or not ask or vol < 50000:
            continue
        
        # 跳过非单场比赛结果
        if any(kw in ql for kw in ["o/u ", "over/under", "spread:", "both teams",
                "win the", "championship", "finals", "conference", "league",
                "premier", "champions", "world cup", "mvp", "award"]):
            continue
        
        try:
            tids = json.loads(m.get("clobTokenIds", "[]"))
        except:
            continue
        if len(tids) < 2:
            continue
        
        # 匹配ESPN比赛
        for game in live_games:
            home_name = game["home"]["name"].lower()
            away_name = game["away"]["name"].lower()
            home_last = home_name.split()[-1]
            away_last = away_name.split()[-1]
            
            # Polymarket格式："Cavaliers vs. Magic" → YES=Cavaliers
            if home_last not in ql and away_last not in ql:
                continue
            
            # 判断YES是哪支队
            # "X vs. Y" → YES = X (第一个名字)
            parts = ql.split("vs")
            if len(parts) >= 2:
                yes_part = parts[0]
                is_home_yes = home_last in yes_part
            else:
                is_home_yes = home_last in ql
            
            yes_prob = game["home_prob"] if is_home_yes else game["away_prob"]
            market_yes = ask
            edge = yes_prob - market_yes
            
            print(f"  📊 Poly: {q[:50]} | ask={ask:.3f}")
            print(f"     ESPN概率: {yes_prob:.0%} | Edge: {edge:+.0%}")
            
            if edge >= MIN_EDGE and game["total_min"] < 20:
                # 只在比赛接近尾声时套利（剩余<20分钟）
                signals.append({
                    "question": q,
                    "token_id": tids[0],
                    "side": "YES",
                    "price": bid,
                    "edge": edge,
                    "model_prob": yes_prob,
                    "market_prob": market_yes,
                    "reasoning": f"ESPN: {game['away']['abbr']} {game['away']['score']}@{game['home']['abbr']} {game['home']['score']} Q{game['period']}, 模型{yes_prob:.0%} vs 市场{market_yes:.0%}",
                    "source": "espn_live",
                })
            elif -edge >= MIN_EDGE and game["total_min"] < 20:
                # YES方大幅高估 → 买NO
                no_edge = abs(edge)
                signals.append({
                    "question": q,
                    "token_id": tids[1],
                    "side": "NO",
                    "price": round(1 - ask, 3),
                    "edge": no_edge,
                    "model_prob": 1 - yes_prob,
                    "market_prob": 1 - market_yes,
                    "reasoning": f"ESPN: 对手大幅领先, NO概率{1-yes_prob:.0%} vs 市场{1-market_yes:.0%}",
                    "source": "espn_live",
                })
    
    return signals


# ══════════════════════════════════════════════════════
#  执行交易
# ══════════════════════════════════════════════════════
def execute_signals(signals, trades, daily):
    """执行筛选后的信号"""
    balance = get_balance()
    open_tokens = {t["token_id"] for t in trades if t.get("status") in ("open", "pending_fill")}
    signal_count = len([t for t in trades if t.get("strategy") == "signal_trader" and t.get("status") == "open"])
    
    # 同信号源集中度限制（防止全仓一个方向）
    MAX_SAME_SOURCE = 3
    source_count = {}
    for t in trades:
        if t.get("status") == "open":
            src = t.get("signal_source", t.get("strategy", ""))
            source_count[src] = source_count.get(src, 0) + 1
    
    placed = 0
    
    # 按edge排序，最强信号优先
    signals.sort(key=lambda x: x.get("edge", 0), reverse=True)
    
    for sig in signals:
        if placed >= MAX_DAILY_TRADES - daily["trades"]:
            break
        if signal_count + placed >= MAX_OPEN_SIGNAL:
            break
        if sig["token_id"] in open_tokens:
            print(f"  ⏭️ 已持仓，跳过: {sig['question'][:40]}")
            continue
        
        # 同源集中度检查
        src = sig.get("source", "")
        if source_count.get(src, 0) >= MAX_SAME_SOURCE:
            print(f"  ⏭️ {src}已有{MAX_SAME_SOURCE}仓，跳过集中风险: {sig['question'][:35]}")
            continue
        
        # 根据edge强度决定下注大小
        edge = sig["edge"]
        if edge >= 0.15:
            bet = BET_LARGE
        elif edge >= 0.10:
            bet = BET_MEDIUM
        else:
            bet = BET_SMALL
        
        if daily["spend"] + bet > MAX_DAILY_SPEND:
            break
        if balance - bet < MIN_BALANCE:
            break
        
        price = sig["price"]
        if price < 0.02:
            continue
        
        tokens = round(bet / price, 2)
        if tokens < 5:
            tokens = 5.0
            bet = round(tokens * price, 2)
        
        print(f"\n  🎯 执行信号: {sig['question'][:50]}")
        print(f"     {sig['side']} @ {price} | ${bet} | edge={edge:.0%}")
        print(f"     理由: {sig['reasoning'][:80]}")
        
        try:
            order = client.create_order(OrderArgs(
                token_id=sig["token_id"],
                price=price,
                size=tokens,
                side="BUY",
            ))
            resp = client.post_order(order, OrderType.GTC)
            
            if resp.get("success") or resp.get("orderID"):
                oid = resp.get("orderID", "?")
                
                # 动态止盈止损（紧凑，不等结算）
                if price >= 0.75:
                    tp = round(min(price + 0.08, 0.96), 3)
                    sl = round(max(price - 0.10, 0.30), 3)
                elif price >= 0.50:
                    tp = round(min(price + 0.12, 0.95), 3)
                    sl = round(max(price - 0.12, 0.20), 3)
                else:
                    tp = round(min(price + 0.15, 0.85), 3)
                    sl = round(max(price - 0.10, 0.05), 3)
                
                entry = {
                    "question": sig["question"],
                    "direction": "BUY",
                    "price": price,
                    "bet_usdc": bet,
                    "token_id": sig["token_id"],
                    "order_id": oid,
                    "tokens": str(tokens),
                    "take_profit": tp,
                    "stop_loss": sl,
                    "rationale": f"signal_trader/{sig['source']}: {sig['reasoning'][:100]}",
                    "status": "open",
                    "time": datetime.now().isoformat(),
                    "strategy": "signal_trader",
                    "signal_source": sig["source"],
                    "edge_at_entry": round(edge, 3),
                    "model_prob": round(sig["model_prob"], 3),
                }
                trades.append(entry)
                open_tokens.add(sig["token_id"])
                balance -= bet
                daily["trades"] += 1
                source_count[src] = source_count.get(src, 0) + 1
                daily["spend"] = round(daily["spend"] + bet, 2)
                placed += 1
                
                print(f"     ✅ 下单成功: {oid[:25]} | TP={tp} SL={sl}")
                
                log_action("SIGNAL_TRADE", {
                    "question": sig["question"],
                    "side": sig["side"],
                    "price": price,
                    "bet": bet,
                    "edge": round(edge, 3),
                    "source": sig["source"],
                    "reasoning": sig["reasoning"][:100],
                })
            else:
                print(f"     ❌ 失败: {resp}")
        except Exception as e:
            print(f"     ❌ 错误: {str(e)[:80]}")
        
        time.sleep(0.5)
    
    return placed


# ══════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════
def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  🎯 Signal Trader  {now}")
    print(f"{'='*60}")
    
    balance = get_balance()
    print(f"  💰 余额: ${balance:.2f}")
    
    if balance < MIN_BALANCE:
        print(f"  ⛔ 余额 < ${MIN_BALANCE}")
        return {"status": "low_balance"}
    
    daily = load_daily()
    print(f"  📅 今日: {daily['trades']}/{MAX_DAILY_TRADES}笔 | ${daily['spend']:.1f}/${MAX_DAILY_SPEND}")
    
    if daily["trades"] >= MAX_DAILY_TRADES:
        print(f"  ⛔ 今日交易已满")
        return {"status": "daily_limit"}
    
    trades = load_trades()
    
    # 收集所有信号
    all_signals = []
    
    print(f"\n  {'─'*50}")
    print(f"  🛢️ 油价信号扫描")
    print(f"  {'─'*50}")
    oil_signals = scan_oil_signals()
    all_signals.extend(oil_signals)
    print(f"  → 油价信号: {len(oil_signals)} 个")
    
    print(f"\n  {'─'*50}")
    print(f"  🏀 ESPN实时比分扫描")
    print(f"  {'─'*50}")
    espn_signals = scan_espn_signals()
    all_signals.extend(espn_signals)
    print(f"  → ESPN信号: {len(espn_signals)} 个")
    
    # 过滤
    valid_signals = [s for s in all_signals if s.get("edge", 0) >= MIN_EDGE]
    print(f"\n  📊 有效信号(edge>={MIN_EDGE:.0%}): {len(valid_signals)}/{len(all_signals)}")
    
    if valid_signals:
        placed = execute_signals(valid_signals, trades, daily)
        save_trades(trades)
        save_daily(daily)
        print(f"\n  本轮下单: {placed}笔")
    else:
        placed = 0
        print(f"  💤 无有效信号")
    
    print(f"\n  {'═'*55}")
    return {
        "signals_found": len(all_signals),
        "valid_signals": len(valid_signals),
        "placed": placed,
        "balance": balance,
    }


if __name__ == "__main__":
    result = run()
    print(f"\n结果: {json.dumps(result)}")