#!/usr/bin/env python3
"""
edge_scanner.py — 多策略统一扫描器 v1
==============================================
策略来源: 学习Polymarket顶级盈利者的方法论

策略A: 电竞定价低效 (验证alpha, 83%WR)
策略B: 体育赔率对比 (Swisstony策略, 用ESPN赔率)
策略C: 系统性NO (70%市场resolve NO, 逆向alpha)
策略D: 结算狙击 (实时比分→买近确定结果)
策略E: 时间衰减 (高概率市场持有到期)

不做: BTC短期价格, Elon推文, O/U大小分, 让分盘
"""
import json, time, os, sys, requests
from datetime import datetime, timedelta

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

# ── 参数 ────────────────────────────────────────────
MIN_BALANCE    = 100.0
MAX_DAILY_SPEND = 40.0
MAX_TRADES_RUN = 3       # 每次运行最多下单数

# 黑名单 (永不触碰)
BLACKLIST = [
    "bitcoin up or down", "ethereum up or down", "up or down -",
    "price of bitcoin", "price of ethereum", "price of btc", "price of eth",
    "btc above", "btc below", "btc between", "elon musk", "tweets from",
    "o/u ", "over/under", "spread:", "both teams to score",
]

# ── 电竞关键词 ─────────────────────────────────────
ESPORTS_KW = [
    "counter-strike:", "cs2:", "valorant:", "dota 2:", "lol:",
    "league of legends:", "overwatch:", "rocket league:", "honor of kings:",
    "bo3", "bo5", "esports", "gaming vs", "esl ", "vct ", "pgl ", "blast ",
]

# ══════════════════════════════════════════════════════
#  策略B: ESPN 赔率对比 (Swisstony策略的免费版)
# ══════════════════════════════════════════════════════
def get_espn_odds(sport="basketball", league="nba"):
    """从ESPN获取比赛赔率，转为隐含概率"""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
    except:
        return []
    
    games = []
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {}).get("state", "")
        
        teams = {}
        for t in comp.get("competitors", []):
            name = t.get("team", {}).get("displayName", "")
            abbr = t.get("team", {}).get("abbreviation", "")
            ha = t.get("homeAway", "")
            score = t.get("score", "0")
            teams[ha] = {"name": name, "abbr": abbr, "score": int(score or 0)}
        
        odds = comp.get("odds", [])
        if odds:
            o = odds[0]
            home_ml = o.get("homeTeamOdds", {}).get("moneyLine")
            away_ml = o.get("awayTeamOdds", {}).get("moneyLine")
            
            if home_ml and away_ml:
                hml = int(home_ml)
                aml = int(away_ml)
                
                # Convert moneyline → implied probability
                hp = abs(hml)/(abs(hml)+100) if hml < 0 else 100/(hml+100)
                ap = abs(aml)/(abs(aml)+100) if aml < 0 else 100/(aml+100)
                
                # Remove vig (normalize)
                total = hp + ap
                hp_fair = hp / total
                ap_fair = ap / total
                
                home = teams.get("home", {})
                away = teams.get("away", {})
                
                games.append({
                    "home": home.get("name", ""),
                    "away": away.get("name", ""),
                    "home_abbr": home.get("abbr", ""),
                    "away_abbr": away.get("abbr", ""),
                    "home_prob": round(hp_fair, 3),
                    "away_prob": round(ap_fair, 3),
                    "status": status,
                    "home_score": home.get("score", 0),
                    "away_score": away.get("score", 0),
                })
        else:
            # No odds but maybe live score — useful for resolution sniping
            home = teams.get("home", {})
            away = teams.get("away", {})
            if status == "in":
                games.append({
                    "home": home.get("name", ""),
                    "away": away.get("name", ""),
                    "home_abbr": home.get("abbr", ""),
                    "away_abbr": away.get("abbr", ""),
                    "home_prob": None,
                    "away_prob": None,
                    "status": status,
                    "home_score": home.get("score", 0),
                    "away_score": away.get("score", 0),
                })
    return games


def match_poly_to_espn(poly_question, espn_games):
    """匹配Polymarket市场到ESPN比赛"""
    ql = poly_question.lower()
    for g in espn_games:
        # 检查队名匹配
        home_match = g["home_abbr"].lower() in ql or g["home"].lower().split()[-1].lower() in ql
        away_match = g["away_abbr"].lower() in ql or g["away"].lower().split()[-1].lower() in ql
        
        if home_match or away_match:
            # 判断Polymarket YES是哪支队
            # "Knicks vs. Jazz" → YES = Knicks (第一个队)
            # "Will Arsenal FC win" → YES = Arsenal
            is_home_yes = g["home"].lower().split()[-1].lower() in ql.split("vs")[0] if "vs" in ql else \
                          g["home"].lower().split()[-1].lower() in ql
            
            return g, is_home_yes
    return None, None


# ══════════════════════════════════════════════════════
#  策略C: 系统性NO
# ══════════════════════════════════════════════════════
def evaluate_no_opportunity(question, yes_price, end_date):
    """评估是否值得做NO"""
    ql = question.lower()
    
    # 高价值NO目标 (基于领域知识判断)
    no_targets = [
        # (关键词, NO理由, 最大YES价格阈值)
        ("crude oil", "油价从$116跌到$83，继续飙升可能性降低", 0.55),
        ("trump announces end", "战争正在升级，短期停战极不可能", 0.55),
        ("iranian regime fall by march", "政权3周内倒台概率极低", 0.10),
        ("us forces enter iran", "地面入侵风险低", 0.15),
        ("ceasefire by march 15", "4天内停火不现实", 0.10),
        ("ceasefire by april 30", "一个月内停火可能但不确定", 0.55),
    ]
    
    for kw, reason, max_yes in no_targets:
        if kw in ql and yes_price <= max_yes:
            return True, reason
    
    return False, None


# ══════════════════════════════════════════════════════
#  策略D: 结算狙击 (实时比分)
# ══════════════════════════════════════════════════════
def check_resolution_snipe(espn_games, poly_markets):
    """找正在进行中、大比分领先的比赛"""
    opportunities = []
    
    for game in espn_games:
        if game["status"] != "in":
            continue
        
        diff = game["home_score"] - game["away_score"]
        # 领先15+分的NBA比赛，赢面很大
        if abs(diff) >= 15:
            leader = "home" if diff > 0 else "away"
            leader_name = game["home"] if leader == "home" else game["away"]
            
            # 找对应的Polymarket市场
            for q, info in poly_markets.items():
                matched, is_home_yes = match_poly_to_espn(q, [game])
                if matched:
                    # 判断YES方是否是领先方
                    yes_is_leader = (is_home_yes and leader == "home") or \
                                   (not is_home_yes and leader == "away")
                    
                    if yes_is_leader and info["ask"] < 0.96:
                        opportunities.append({
                            "question": q,
                            "ask": info["ask"],
                            "bid": info["bid"],
                            "reason": f"{leader_name} 领先 {abs(diff)} 分",
                            "strategy": "resolution_snipe",
                            "token_id": info.get("token_id"),
                        })
    
    return opportunities


# ══════════════════════════════════════════════════════
#  主扫描
# ══════════════════════════════════════════════════════
def scan():
    now = datetime.utcnow()
    cutoff_3d = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    
    print(f"\n{'='*60}")
    print(f"  🔍 多策略扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    # 余额检查
    bal = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    balance = int(bal["balance"]) / 1e6
    print(f"  💰 余额: ${balance:.2f}")
    
    if balance < MIN_BALANCE:
        print(f"  ⛔ 余额 < ${MIN_BALANCE}，停止")
        return {"placed": 0, "skipped": "low_balance"}
    
    # 加载已有持仓
    trades = []
    if os.path.exists(LOG_FILE):
        trades = json.load(open(LOG_FILE))
        if not isinstance(trades, list):
            trades = trades.get("trades", [])
    
    open_tokens = {t["token_id"] for t in trades if t.get("status") in ("open", "pending_fill")}
    
    # ── 获取ESPN赔率 ──────────────────────────────────
    print(f"\n  📡 获取ESPN赔率...")
    espn_nba = get_espn_odds("basketball", "nba")
    espn_nhl = get_espn_odds("hockey", "nhl")
    espn_soccer = get_espn_odds("soccer", "eng.1")  # Premier League
    espn_all = espn_nba + espn_nhl + espn_soccer
    print(f"     NBA: {len(espn_nba)} | NHL: {len(espn_nhl)} | Soccer: {len(espn_soccer)}")
    
    # ── 获取Polymarket市场 ────────────────────────────
    all_markets = []
    poly_map = {}  # question → market info for matching
    
    for offset in [0, 200, 400, 600, 800]:
        try:
            r = requests.get(
                f"https://gamma-api.polymarket.com/markets?limit=200&offset={offset}"
                f"&active=true&closed=false&order=volume24hr&ascending=false", timeout=15)
            markets = r.json()
            if not markets: break
        except:
            continue
        all_markets.extend(markets)
    
    print(f"  📊 Polymarket活跃市场: {len(all_markets)}")
    
    # ── 扫描所有策略 ──────────────────────────────────
    opportunities = []
    
    for m in all_markets:
        q = m.get("question", "")
        ql = q.lower()
        bid = float(m.get("bestBid", 0) or 0)
        ask = float(m.get("bestAsk", 0) or 0)
        vol = float(m.get("volume24hr", 0) or 0)
        end = m.get("endDate", "")[:10]
        spread = ask - bid if bid > 0 else 99
        
        if not bid or ask <= 0.01: continue
        if any(kw in ql for kw in BLACKLIST): continue
        
        tids = []
        try:
            tids = json.loads(m.get("clobTokenIds", "[]"))
        except: continue
        if not tids: continue
        
        tid = tids[0]
        if tid in open_tokens: continue
        
        poly_map[q] = {"bid": bid, "ask": ask, "vol": vol, "token_id": tid}
        
        # ── 策略A: 电竞 ──
        if any(kw in ql for kw in ESPORTS_KW):
            if vol >= 5000 and 0.20 <= ask <= 0.90 and spread <= 0.03:
                opportunities.append({
                    "question": q, "token_id": tid,
                    "bid": bid, "ask": ask, "vol": vol,
                    "end": end, "spread": spread,
                    "strategy": "esports",
                    "bet": 4.0,
                    "score": vol / 10000,  # 按流动性排分
                })
        
        # ── 策略B: ESPN赔率对比 ──
        matched_game, is_home_yes = match_poly_to_espn(q, espn_all)
        if matched_game and matched_game.get("home_prob"):
            espn_prob = matched_game["home_prob"] if is_home_yes else matched_game["away_prob"]
            poly_prob = ask  # 我们的买入成本
            edge = espn_prob - poly_prob  # 正 = Polymarket便宜
            
            if edge >= 0.03 and vol >= 50000 and spread <= 0.02:
                opportunities.append({
                    "question": q, "token_id": tid,
                    "bid": bid, "ask": ask, "vol": vol,
                    "end": end, "spread": spread,
                    "strategy": "espn_value",
                    "bet": 5.0,
                    "espn_prob": espn_prob,
                    "edge": edge,
                    "score": edge * 100 + vol / 100000,
                })
        
        # ── 策略C: 系统性NO ──
        is_no, reason = evaluate_no_opportunity(q, bid, end)
        if is_no and vol >= 50000 and spread <= 0.03:
            no_ask = 1 - bid  # NO的买入成本
            if no_ask <= 0.70:  # 不买太贵的NO
                opportunities.append({
                    "question": q, "token_id": tids[1] if len(tids) > 1 else tid,
                    "bid": 1 - ask, "ask": no_ask, "vol": vol,
                    "end": end, "spread": spread,
                    "strategy": "systematic_no",
                    "bet": 3.0,
                    "reason": reason,
                    "score": (1 - no_ask) * 10,  # NO越便宜分越高
                })
        
        # ── 策略E: 时间衰减 ──
        if bid >= 0.80 and ask <= 0.92 and vol >= 80000 and spread <= 0.02:
            if end and end <= cutoff_3d:
                profit_pct = (1 - ask) / ask * 100
                opportunities.append({
                    "question": q, "token_id": tid,
                    "bid": bid, "ask": ask, "vol": vol,
                    "end": end, "spread": spread,
                    "strategy": "time_decay",
                    "bet": 5.0,
                    "profit_pct": profit_pct,
                    "score": profit_pct + vol / 100000,
                })
    
    # ── 策略D: 结算狙击(实时比赛) ──
    snipe_ops = check_resolution_snipe(espn_all, poly_map)
    for s in snipe_ops:
        s["bet"] = 5.0
        s["score"] = 50  # 高优先级
    opportunities.extend(snipe_ops)
    
    # ── 排序并显示 ────────────────────────────────────
    opportunities.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    strat_icons = {
        "esports": "🎮", "espn_value": "📊", "systematic_no": "🔴",
        "time_decay": "⏰", "resolution_snipe": "🎯",
    }
    
    print(f"\n  找到 {len(opportunities)} 个机会:")
    for o in opportunities[:10]:
        icon = strat_icons.get(o["strategy"], "❓")
        extra = ""
        if o["strategy"] == "espn_value":
            extra = f" | ESPN={o['espn_prob']:.0%} edge={o['edge']:.0%}"
        elif o["strategy"] == "systematic_no":
            extra = f" | NO理由: {o.get('reason','')[:30]}"
        elif o["strategy"] == "time_decay":
            extra = f" | 持有到期+{o.get('profit_pct',0):.0f}%"
        elif o["strategy"] == "resolution_snipe":
            extra = f" | {o.get('reason','')}"
        
        print(f"    {icon} [{o['strategy']:15s}] ask={o['ask']:.2f} vol=${o.get('vol',0):,.0f}{extra}")
        print(f"       {o['question'][:65]}")
    
    # ── 执行交易 ──────────────────────────────────────
    placed = 0
    daily_spend = 0
    new_entries = []
    
    for o in opportunities:
        if placed >= MAX_TRADES_RUN:
            break
        if daily_spend + o["bet"] > MAX_DAILY_SPEND:
            continue
        if o["token_id"] in open_tokens:
            continue
        
        maker_price = o["bid"]
        if maker_price < 0.02: continue
        
        tokens = round(o["bet"] / maker_price, 2)
        if tokens < 5:
            tokens = 5.0
            o["bet"] = round(tokens * maker_price, 2)
        
        if balance - o["bet"] < MIN_BALANCE:
            continue
        
        icon = strat_icons.get(o["strategy"], "")
        print(f"\n  {icon} 执行: {o['question'][:55]}")
        print(f"     策略={o['strategy']} | BUY {tokens}@{maker_price} | ${o['bet']}")
        
        try:
            order = client.create_order(OrderArgs(
                token_id=o["token_id"],
                price=maker_price,
                size=tokens,
                side="BUY",
            ))
            resp = client.post_order(order, OrderType.GTC)
            
            if resp.get("success") or resp.get("orderID"):
                oid = resp.get("orderID", "?")
                
                # 检查即时成交
                time.sleep(2)
                try:
                    actual_bal = client.get_balance_allowance(
                        params=BalanceAllowanceParams(
                            asset_type=AssetType.CONDITIONAL, token_id=o["token_id"]))
                    actual = int(actual_bal.get("balance", 0)) / 1e6
                except:
                    actual = 0
                
                fill_status = "open" if actual > 0.5 else "pending_fill"
                status_icon = "✅" if fill_status == "open" else "📋"
                print(f"     {status_icon} {fill_status} | order={oid[:30]}...")
                
                # TP/SL
                if maker_price >= 0.80:
                    tp = round(min(maker_price + 0.10, 0.97), 3)
                    sl = round(max(maker_price - 0.12, 0.40), 3)
                elif maker_price >= 0.60:
                    tp = round(min(maker_price + 0.18, 0.95), 3)
                    sl = round(max(maker_price - 0.15, 0.30), 3)
                else:
                    tp = round(min(maker_price + 0.25, 0.90), 3)
                    sl = round(max(maker_price - 0.20, 0.10), 3)
                
                entry = {
                    "question": o["question"], "direction": "BUY",
                    "price": maker_price, "bet_usdc": o["bet"],
                    "token_id": o["token_id"], "order_id": oid,
                    "tokens": str(round(actual if actual > 0.5 else tokens, 4)),
                    "take_profit": tp, "stop_loss": sl,
                    "rationale": f"{o['strategy']}: score={o.get('score',0):.1f}",
                    "status": fill_status,
                    "time": datetime.now().isoformat(),
                    "strategy": o["strategy"],
                }
                new_entries.append(entry)
                open_tokens.add(o["token_id"])
                balance -= o["bet"]
                daily_spend += o["bet"]
                placed += 1
            else:
                print(f"     ❌ 失败: {resp}")
        except Exception as e:
            print(f"     ❌ 错误: {str(e)[:80]}")
        
        time.sleep(0.5)
    
    if new_entries:
        trades.extend(new_entries)
        json.dump(trades, open(LOG_FILE, "w"), indent=2, ensure_ascii=False)
    
    print(f"\n  {'─'*55}")
    print(f"  本轮: {placed} 笔 | 花费: ${daily_spend:.2f} | 余额: ${balance:.2f}")
    
    return {"placed": placed, "opportunities": len(opportunities), "balance": balance}


if __name__ == "__main__":
    result = scan()
    print(f"\n结果: {json.dumps(result, ensure_ascii=False)}")
