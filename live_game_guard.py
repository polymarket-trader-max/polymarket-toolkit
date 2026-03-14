#!/usr/bin/env python3
"""
live_game_guard.py — 实时赛事止损守卫
每5分钟运行，专门监控体育/电竞持仓
在比赛结果明朗时抢在市场结算前退出，避免全额亏损

零Token消耗：纯Python + 免费API，不经过AI
"""
import json, os, re, math, sys, requests
from datetime import datetime, timedelta
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, MarketOrderArgs, OrderArgs, OrderType,
    BalanceAllowanceParams, AssetType,
)

# ─── Config ───────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)
client = ClobClient(
    "https://clob.polymarket.com", key=PRIVATE_KEY, chain_id=POLYGON,
    creds=creds, signature_type=1, funder=PROXY_WALLET,
)

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
LOG_FILE        = os.path.join(BASE_DIR, "trade_log.json")
ACTION_LOG_FILE = os.path.join(BASE_DIR, "action_log.json")
GUARD_LOG_DIR   = os.path.join(BASE_DIR, "logs")
os.makedirs(GUARD_LOG_DIR, exist_ok=True)

# ─── 赛事分类关键词 ──────────────────────────────────
SPORTS_KEYWORDS = [
    # NBA / 篮球
    r"\bvs\.?\b", r"\bnba\b", r"lakers|celtics|warriors|knicks|nets|bucks|suns|nuggets|76ers|sixers",
    r"cavaliers|magic|hornets|timberwolves|pacers|kings|jazz|heat|hawks|bulls|pistons|rockets",
    r"clippers|spurs|pelicans|grizzlies|blazers|raptors|wizards|thunder|mavericks|wolves",
    # 足球
    r"fc\b|win on 2026|premier league|la liga|serie a|bundesliga|ligue 1",
    r"arsenal|liverpool|barcelona|bayern|madrid|juventus|inter|milan|chelsea|manchester",
    r"galatasaray|lazio|bologna|lecce|brest|werder|lyon|atletico|atalanta|newcastle",
    r"both teams to score|btts|o/u \d",
    # 电竞
    r"\bbo[135]\b|counter-strike|cs2|valorant|dota\s*2|league of legends|\blol\b",
    r"esl pro|pgl |vct |emea master|lpl|lck|lec",
    r"\besports?\b|natus vincere|navi\b|g2\b|furia\b|mongol|heroic|spirit\b|fearx|blg\b|jdg\b",
    # 棒球 / 其他赛事
    r"baseball|mlb|nfl|nhl|panthers|cardinals|stanford|lightning|sabres",
    r"cricket|t20|world cup.*2026",
    # StarCraft
    r"starcraft|sc2\b",
]
SPORTS_PATTERN = re.compile("|".join(SPORTS_KEYWORDS), re.IGNORECASE)

# 排除：长期市场不算赛事
EXCLUDE_KEYWORDS = [
    r"by end of (march|april|may|june|july)",
    r"crude oil|bitcoin|btc|eth\b|nvidia|trump|iran|ceasefire|ukraine|russia",
    r"tate.*post|mrBeast|khamenei|spacex",
]
EXCLUDE_PATTERN = re.compile("|".join(EXCLUDE_KEYWORDS), re.IGNORECASE)

# ─── 止损阈值 ──────────────────────────────────────
# 体育/电竞市场，当价格跌到这些水平时说明比赛基本输了
EMERGENCY_EXIT_PRICE = 0.12   # YES价格低于此 → 紧急退出（保留~12%）
LOSING_BADLY_PRICE   = 0.20   # YES价格低于此 → 可能要输了，检查ESPN确认
ESPN_CONFIRM_EXIT    = 0.15   # ESPN确认劣势时的退出阈值（比纯价格宽松一点）

# ─── Helpers ────────────────────────────────────────

def log_action(action_type, question, details):
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
    try:
        r = requests.get(
            f"https://clob.polymarket.com/price?token_id={token_id}&side=sell", timeout=5)
        sell = float(r.json().get("price", 0))
        r2 = requests.get(
            f"https://clob.polymarket.com/price?token_id={token_id}&side=buy", timeout=5)
        buy = float(r2.json().get("price", 0))
        return sell, buy
    except:
        return None, None


def get_actual_token_balance(token_id):
    try:
        bal = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
        return int(bal.get("balance", 0)) / 1e6
    except:
        return 0.0


def close_position_urgent(token_id, tokens_str, current_price, bet_usdc):
    """紧急平仓：FOK市价单优先（速度 > 手续费）"""
    try:
        actual = get_actual_token_balance(token_id)
        if actual < 0.01:
            return {"error": "no_tokens", "actual": actual}
        sell_amount = round(actual * 0.999, 6)

        # 紧急情况：直接 FOK 市价单（确保成交）
        order = client.create_market_order(MarketOrderArgs(
            token_id=token_id,
            amount=sell_amount,
            side="SELL",
        ))
        resp = client.post_order(order, OrderType.FOK)

        if resp.get("success") or resp.get("orderID"):
            raw_taking = resp.get("takingAmount", 0) or 0
            try:
                usdc_back = float(raw_taking)
                if usdc_back > 1000:
                    usdc_back = usdc_back / 1e6
            except:
                usdc_back = 0
            if usdc_back < 0.001:
                usdc_back = current_price * actual
            resp["usdc_back"] = usdc_back
            resp["pnl"] = usdc_back - bet_usdc
            resp["success"] = True
            return resp

        # FOK 失败，尝试 GTC 限价卖
        if current_price and current_price > 0.02:
            try:
                order2 = client.create_order(OrderArgs(
                    token_id=token_id,
                    price=round(current_price, 2),
                    size=sell_amount,
                    side="SELL",
                ))
                resp2 = client.post_order(order2, OrderType.GTC)
                if resp2.get("success") or resp2.get("orderID"):
                    resp2["order_type"] = "GTC_LIMIT"
                    resp2["usdc_back"] = current_price * actual
                    resp2["pnl"] = current_price * actual - bet_usdc
                    resp2["success"] = True
                    return resp2
            except:
                pass

        return resp
    except Exception as e:
        return {"error": str(e)}


# ─── ESPN Live Scores ──────────────────────────────

def get_espn_live(sport="basketball", league="nba"):
    try:
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard",
            timeout=10)
        return r.json().get("events", [])
    except:
        return []


def parse_espn_games():
    """获取所有进行中的ESPN赛事，返回 {team_name_lower: game_info}"""
    games = {}
    sport_leagues = [
        ("basketball", "nba"),
        ("soccer", "eng.1"),   # Premier League
        ("soccer", "ger.1"),   # Bundesliga
        ("soccer", "ita.1"),   # Serie A
        ("soccer", "fra.1"),   # Ligue 1
        ("soccer", "esp.1"),   # La Liga
        ("soccer", "uefa.champions"),
        ("soccer", "uefa.europa"),
        ("baseball", "mlb"),
        ("hockey", "nhl"),
    ]

    for sport, league in sport_leagues:
        events = get_espn_live(sport, league)
        for event in events:
            comp = event.get("competitions", [{}])[0]
            status = comp.get("status", {})
            state = status.get("type", {}).get("state", "")

            teams_data = {}
            for t in comp.get("competitors", []):
                ha = t.get("homeAway", "")
                teams_data[ha] = {
                    "name": t.get("team", {}).get("displayName", ""),
                    "abbr": t.get("team", {}).get("abbreviation", ""),
                    "score": int(t.get("score", 0) or 0),
                }

            home = teams_data.get("home", {})
            away = teams_data.get("away", {})
            period = status.get("period", 1)
            clock = status.get("displayClock", "")

            game_info = {
                "sport": sport,
                "league": league,
                "state": state,  # pre / in / post
                "home": home,
                "away": away,
                "period": period,
                "clock": clock,
                "status_detail": status.get("type", {}).get("detail", ""),
            }

            # 计算胜率（NBA专用）
            if sport == "basketball" and state == "in":
                try:
                    parts = clock.split(":")
                    mins_left = int(parts[0]) + float(parts[1]) / 60 if len(parts) == 2 else float(parts[0])
                except:
                    mins_left = 6.0
                total_min = max(0, (4 - period) * 12 + mins_left)
                diff = home["score"] - away["score"]
                if total_min > 0:
                    sigma = 2.5 * math.sqrt(total_min)
                    # norm_cdf approximation
                    z = diff / sigma if sigma > 0 else 0
                    home_prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))
                else:
                    home_prob = 0.99 if diff > 0 else (0.50 if diff == 0 else 0.01)
                game_info["home_win_prob"] = max(0.01, min(0.99, home_prob))
                game_info["away_win_prob"] = 1 - game_info["home_win_prob"]
                game_info["total_min_left"] = total_min

            # 足球：简化胜率
            if sport == "soccer" and state == "in":
                diff = home["score"] - away["score"]
                try:
                    mins_played = int(re.search(r"(\d+)", clock).group(1)) if clock else 45
                except:
                    mins_played = 45
                mins_remaining = max(0, 90 - mins_played)
                game_info["mins_remaining"] = mins_remaining
                # 简化：领先方大概率赢，尤其是最后20分钟
                if mins_remaining < 20 and abs(diff) >= 2:
                    game_info["home_win_prob"] = 0.95 if diff > 0 else 0.05
                    game_info["away_win_prob"] = 1 - game_info["home_win_prob"]

            # 索引：用多种名称变体方便匹配
            for team in [home, away]:
                name = team.get("name", "")
                abbr = team.get("abbr", "")
                if name:
                    games[name.lower()] = game_info
                    # 去掉 "FC" 等后缀
                    short = re.sub(r"\b(fc|sc|cf|afc)\b", "", name, flags=re.I).strip()
                    if short:
                        games[short.lower()] = game_info
                if abbr:
                    games[abbr.lower()] = game_info

    return games


def match_game(question, espn_games):
    """尝试将持仓的 question 与 ESPN 赛事匹配"""
    ql = question.lower()
    best_match = None
    best_len = 0

    for team_key, game_info in espn_games.items():
        if len(team_key) >= 3 and team_key in ql:
            if len(team_key) > best_len:
                best_match = game_info
                best_len = len(team_key)

    return best_match


def is_sports_position(question):
    """判断是否为体育/电竞持仓"""
    if EXCLUDE_PATTERN.search(question):
        return False
    return bool(SPORTS_PATTERN.search(question))


# ─── Main Guard Logic ──────────────────────────────

def run_guard():
    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"  🛡️  Live Game Guard  {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # 1. Load open positions
    try:
        raw = json.load(open(LOG_FILE))
        log = raw if isinstance(raw, list) else raw.get("trades", [])
    except Exception as e:
        print(f"  ❌ 无法读取 trade_log: {e}")
        return {"error": str(e)}

    open_trades = [t for t in log if t.get("status") == "open"]
    sports_trades = [t for t in open_trades if is_sports_position(t.get("question", ""))]

    if not sports_trades:
        print("  📭 无体育/电竞持仓，跳过")
        return {"status": "no_sports_positions", "checked": 0}

    print(f"  📋 体育/电竞持仓: {len(sports_trades)}/{len(open_trades)}\n")

    # 2. Fetch ESPN live scores
    print("  📡 获取 ESPN 实时比分...")
    espn_games = parse_espn_games()
    live_count = len(set(id(v) for v in espn_games.values()))
    print(f"  → 找到 {live_count} 场赛事数据\n")

    # 3. Check each sports position
    actions = []
    modified = False

    for t in sports_trades:
        q = t.get("question", "")[:60]
        entry = float(t.get("price", 0))
        bet = float(t.get("bet_usdc", 0))
        tid = t.get("token_id", "")
        tokens = t.get("tokens") or t.get("tokens_received", "0")
        sl = float(t.get("stop_loss", 0))

        # Get current price
        bid, ask = get_current_price(tid)
        if bid is None:
            print(f"  ⚠️  {q} — 无法获取价格")
            continue

        current = bid
        pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0

        # Try to match with ESPN
        game = match_game(t.get("question", ""), espn_games)
        game_state = game.get("state", "unknown") if game else "no_match"

        # ─── Decision Logic ───────────────────────────

        should_exit = False
        exit_reason = ""

        # Case 1: 市场已结算（价格极低/极高）→ 紧急退出
        if current <= 0.02:
            # 价格几乎为零，市场可能已结算
            print(f"  🪦 {q}")
            print(f"     价格={current:.3f} ≈ 0 → 市场可能已结算，跳过（等monitor处理）")
            continue

        # Case 2: 价格低于紧急阈值 → 不管ESPN数据，直接退出
        if current <= EMERGENCY_EXIT_PRICE and current < entry * 0.5:
            should_exit = True
            exit_reason = f"🚨 紧急退出: 价格{current:.3f} < {EMERGENCY_EXIT_PRICE}（比赛基本输了）"

        # Case 3: ESPN 确认比赛进行中，且我们大幅落后
        elif game and game_state == "in":
            home = game.get("home", {})
            away = game.get("away", {})
            sport = game.get("sport", "")

            # NBA：用胜率模型判断
            if sport == "basketball" and "home_win_prob" in game:
                # 判断我们买的是哪队
                # 简化：如果question包含home队名 → 我们买home胜
                home_name = home.get("name", "").lower()
                away_name = away.get("name", "").lower()
                ql = t.get("question", "").lower()

                our_prob = None
                if home_name and home_name[:5] in ql:
                    our_prob = game["home_win_prob"]
                elif away_name and away_name[:5] in ql:
                    our_prob = game["away_win_prob"]

                total_min = game.get("total_min_left", 48)

                if our_prob is not None:
                    print(f"  🏀 {q}")
                    print(f"     {away['abbr']} {away['score']} @ {home['abbr']} {home['score']} | Q{game['period']} {game['clock']}")
                    print(f"     我方胜率: {our_prob:.0%} | 现价: {current:.3f} | 入场: {entry:.3f}")

                    # 最后8分钟，胜率<15% → 退出
                    if total_min <= 8 and our_prob < 0.15:
                        should_exit = True
                        exit_reason = f"🏀 NBA最后{total_min:.0f}min 胜率仅{our_prob:.0%}，抢跑退出"
                    # 最后4分钟，胜率<25% → 退出
                    elif total_min <= 4 and our_prob < 0.25:
                        should_exit = True
                        exit_reason = f"🏀 NBA最后{total_min:.0f}min 胜率{our_prob:.0%}，紧急退出"
                else:
                    print(f"  🏀 {q} — ESPN有数据但无法匹配队伍方向")

            # 足球：最后20分钟落后2球
            elif sport == "soccer":
                mins_rem = game.get("mins_remaining", 90)
                home_prob = game.get("home_win_prob")
                if home_prob and home_prob < 0.10 and mins_rem < 15:
                    should_exit = True
                    exit_reason = f"⚽ 足球最后{mins_rem}min 大比分落后"

        # Case 4: ESPN确认比赛已结束 (post)
        elif game and game_state == "post":
            if current < entry * 0.5:
                should_exit = True
                exit_reason = f"🏁 比赛已结束，价格{current:.3f}远低于入场{entry:.3f}，抢在结算前退出"

        # Case 5: 电竞 — 无ESPN数据，纯靠价格判断
        elif not game and current <= LOSING_BADLY_PRICE and current < entry * 0.4:
            # 电竞市场价格跌到0.20以下，说明比赛大概率输了
            should_exit = True
            exit_reason = f"🎮 电竞: 价格{current:.3f}暴跌（无实时比分，按价格止损）"

        # ─── Execute Exit ─────────────────────────────
        if should_exit:
            print(f"  ⚡ {exit_reason}")
            print(f"     → 紧急平仓 {q}")

            resp = close_position_urgent(tid, tokens, current, bet)

            if resp.get("success"):
                usdc_back = resp.get("usdc_back", 0)
                pnl = resp.get("pnl", usdc_back - bet)
                t["status"] = "closed"
                t["exit_price"] = current
                t["exit_time"] = now.isoformat()
                t["pnl_usd"] = round(pnl, 4)
                t["usdc_back"] = round(usdc_back, 4)
                t["close_reason"] = "live_guard_exit"
                modified = True
                saved_pct = (1 - abs(pnl) / bet) * 100 if bet > 0 else 0
                print(f"     ✅ 平仓成功 | 回收 ${usdc_back:.2f} | 亏损 ${pnl:+.2f} | 挽救 {saved_pct:.0f}%")
                actions.append({"trade": q, "action": "LIVE_GUARD_EXIT", "pnl": pnl, "reason": exit_reason})
                log_action("LIVE_GUARD_EXIT", t.get("question", ""), {
                    "exit_price": current, "usdc_back": usdc_back,
                    "pnl": pnl, "reason": exit_reason, "success": True,
                })
            else:
                err = str(resp)[:100]
                print(f"     ❌ 平仓失败: {err}")
                # 如果是 no match / 404 → 市场已结算
                if "no match" in err.lower() or "404" in err:
                    t["status"] = "closed"
                    t["exit_price"] = current
                    t["exit_time"] = now.isoformat()
                    t["pnl_usd"] = round(-bet, 4)
                    t["usdc_back"] = 0
                    t["close_reason"] = "live_guard_market_closed"
                    modified = True
                    print(f"     🪦 市场已结算，记录全损 ${-bet:.2f}")
                    log_action("LIVE_GUARD_MARKET_CLOSED", t.get("question", ""), {
                        "exit_price": current, "pnl": -bet, "error": err,
                    })
                else:
                    log_action("LIVE_GUARD_EXIT_FAILED", t.get("question", ""), {
                        "exit_price": current, "error": err,
                    })
        else:
            if not (game and game_state in ("in", "post")):
                status = "⏳ 赛前/无数据"
            elif game_state == "in":
                status = "🟢 比赛中，暂安全"
            else:
                status = f"📊 状态: {game_state}"
            print(f"  {status} | {q} | 价格:{current:.3f} ({pnl_pct:+.1f}%)")

    # 4. Save if modified
    if modified:
        json.dump(log, open(LOG_FILE, "w"), indent=2, ensure_ascii=False)
        print(f"\n  💾 已更新 trade_log.json")

    print(f"\n  {'─'*55}")
    print(f"  体育/电竞持仓: {len(sports_trades)} | 紧急退出: {len(actions)}")
    if actions:
        total_saved = sum(a["pnl"] for a in actions)
        print(f"  退出详情: {len(actions)} 笔 | 净P&L: ${total_saved:+.2f}")
    print(f"{'='*60}\n")

    return {"checked": len(sports_trades), "exits": len(actions), "actions": actions}


if __name__ == "__main__":
    # 写日志到文件
    log_file = os.path.join(GUARD_LOG_DIR, f"guard_{datetime.now().strftime('%Y-%m-%d_%H%M')}.log")
    
    # 同时输出到 stdout 和日志文件
    import io

    class TeeWriter:
        def __init__(self, *writers):
            self.writers = writers
        def write(self, s):
            for w in self.writers:
                w.write(s)
                w.flush()
        def flush(self):
            for w in self.writers:
                w.flush()

    log_fh = open(log_file, "w")
    sys.stdout = TeeWriter(sys.__stdout__, log_fh)

    try:
        result = run_guard()
    except Exception as e:
        print(f"\n❌ Guard 异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        log_fh.close()
        sys.stdout = sys.__stdout__

    # 清理7天前的日志
    cutoff = datetime.now() - timedelta(days=7)
    for f in os.listdir(GUARD_LOG_DIR):
        if f.startswith("guard_"):
            fpath = os.path.join(GUARD_LOG_DIR, f)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime < cutoff:
                    os.remove(fpath)
            except:
                pass
