#!/usr/bin/env python3
"""
multi_edge_scanner.py — 多品类信息优势扫描器
三条腿：天气 + 加密日内价格 + Fed/宏观事件

核心逻辑：用权威数据源的概率 vs Polymarket定价，找错误定价
只在 edge >= MIN_EDGE 时下注，全部GTC限价单(Maker fee=0)
"""

import json, os, sys, time, math, requests, logging
from datetime import datetime, timezone, timedelta

# ============ 配置 ============
MIN_EDGE = 0.08           # 最小edge 8%
MIN_VOLUME_24H = 10000    # 最小24h成交量
BET_SIZE_WEATHER = 5      # 天气单注 $5
BET_SIZE_CRYPTO = 5       # 加密单注 $5
BET_SIZE_FED = 8          # Fed/宏观 $8 (高确定性)
MAX_DAILY_SPEND = 30      # 每日上限
MAX_DAILY_TRADES = 10
MIN_BALANCE = 10          # 最低余额保护
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "edge_state.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "logs", "multi_edge.log")

# Polymarket cities for weather
WEATHER_CITIES = {
    "Seoul":    {"lat": 37.5665, "lon": 126.978, "unit": "C"},
    "Shanghai": {"lat": 31.2304, "lon": 121.4737, "unit": "C"},
    "Dallas":   {"lat": 32.7767, "lon": -96.797, "unit": "F"},
}

# ============ 日志 ============
os.makedirs(os.path.join(SCRIPT_DIR, "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("multi_edge")

# ============ CLOB客户端 ============
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams
from py_clob_client.order_builder.constants import BUY

PRIVATE_KEY = os.environ["POLYMARKET_PRIVATE_KEY"]
FUNDER = os.environ["POLYMARKET_PROXY_WALLET"]

def get_client():
    client = ClobClient(
        "https://clob.polymarket.com",
        key=PRIVATE_KEY,
        chain_id=137,
        signature_type=1,
        funder=FUNDER
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    return client

def get_balance(client):
    try:
        r = client.get_balance_allowance(BalanceAllowanceParams(asset_type="COLLATERAL"))
        return int(r.get("balance", 0)) / 1e6
    except:
        return 0

# ============ 状态管理 ============
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"trades_today": [], "daily_spend": 0, "last_date": "", "positions": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def reset_daily(state):
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["trades_today"] = []
        state["daily_spend"] = 0
        state["last_date"] = today
    return state

# ============ 天气模块 ============
def get_weather_forecast(city_name, city_info):
    """从Open-Meteo获取未来2天的每日最高温预报"""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": city_info["lat"],
            "longitude": city_info["lon"],
            "daily": "temperature_2m_max",
            "timezone": "auto",
            "forecast_days": 3
        }
        if city_info["unit"] == "F":
            params["temperature_unit"] = "fahrenheit"
        
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        
        dates = data["daily"]["time"]  # ['2026-03-14', '2026-03-15', ...]
        temps = data["daily"]["temperature_2m_max"]  # [15.2, 16.8, ...]
        
        forecasts = {}
        for d, t in zip(dates, temps):
            forecasts[d] = round(t, 1)
        
        log.info(f"🌡️ {city_name} 预报: {forecasts}")
        return forecasts
    except Exception as e:
        log.error(f"天气预报获取失败 {city_name}: {e}")
        return {}

def find_weather_markets(city_name, date_str):
    """在Polymarket找指定城市+日期的温度市场"""
    try:
        # 搜索活跃市场
        markets = []
        for offset in [0, 200]:
            r = requests.get(
                "https://gamma-api.polymarket.com/markets",
                params={"limit": 200, "offset": offset, "active": "true", "closed": "false"},
                timeout=15
            )
            markets.extend(r.json())
        
        # 过滤：城市名 + 日期
        # 日期格式：March 14, Mar 14 等
        from datetime import datetime as dt
        d = dt.strptime(date_str, "%Y-%m-%d")
        date_patterns = [
            d.strftime("March %d").replace(" 0", " "),  # "March 14"
            d.strftime("Mar %d").replace(" 0", " "),     # "Mar 14"
        ]
        
        results = []
        for m in markets:
            q = m.get("question", "")
            if city_name.lower() not in q.lower():
                continue
            if not any(dp.lower() in q.lower() for dp in date_patterns):
                continue
            if "temperature" in q.lower() or "°" in q:
                results.append(m)
        
        return results
    except Exception as e:
        log.error(f"天气市场搜索失败: {e}")
        return []

def parse_temp_from_question(question, unit):
    """从问题中解析温度目标"""
    import re
    q = question
    
    # 模式1: "be XX°C" 或 "be XX°F"
    # 模式2: "be between XX-YY°F"
    # 模式3: "XX°C or higher"
    # 模式4: "be XX°C on"
    
    # "between X-Y°F" or "between X and Y"
    m = re.search(r'between\s+(\d+)[°\s-]+(\d+)', q)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        return {"type": "range", "low": low, "high": high}
    
    # "XX or higher" / "XX or more"
    m = re.search(r'(\d+)\s*°?\s*(or higher|or more|or above|\+)', q)
    if m:
        return {"type": "above", "threshold": float(m.group(1))}
    
    # "XX or lower" / "XX or below"
    m = re.search(r'(\d+)\s*°?\s*(or lower|or below)', q)
    if m:
        return {"type": "below", "threshold": float(m.group(1))}
    
    # Exact: "be XX°C on"
    m = re.search(r'be\s+(\d+)\s*°', q)
    if m:
        return {"type": "exact", "value": float(m.group(1))}
    
    return None

def calc_weather_probability(forecast_temp, target, std_dev=2.0):
    """
    用正态分布计算天气概率
    1-2天预报标准差约 ±2°C (±3.6°F)
    """
    from math import erf, sqrt
    
    def norm_cdf(x, mu, sigma):
        return 0.5 * (1 + erf((x - mu) / (sigma * sqrt(2))))
    
    if target["type"] == "above":
        return 1 - norm_cdf(target["threshold"] - 0.5, forecast_temp, std_dev)
    elif target["type"] == "below":
        return norm_cdf(target["threshold"] + 0.5, forecast_temp, std_dev)
    elif target["type"] == "exact":
        return norm_cdf(target["value"] + 0.5, forecast_temp, std_dev) - norm_cdf(target["value"] - 0.5, forecast_temp, std_dev)
    elif target["type"] == "range":
        return norm_cdf(target["high"] + 0.5, forecast_temp, std_dev) - norm_cdf(target["low"] - 0.5, forecast_temp, std_dev)
    
    return 0.5

def scan_weather():
    """扫描天气市场机会"""
    opportunities = []
    
    # 扫描明天和后天
    today = datetime.now(timezone.utc)
    for days_ahead in [1, 2]:
        target_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        
        for city_name, city_info in WEATHER_CITIES.items():
            forecasts = get_weather_forecast(city_name, city_info)
            if target_date not in forecasts:
                continue
            
            forecast_temp = forecasts[target_date]
            std_dev = 2.0 if city_info["unit"] == "C" else 3.6  # °F has wider range
            
            markets = find_weather_markets(city_name, target_date)
            log.info(f"  {city_name} {target_date}: 预报={forecast_temp}°{city_info['unit']}, 找到{len(markets)}个市场")
            
            for m in markets:
                target = parse_temp_from_question(m["question"], city_info["unit"])
                if not target:
                    continue
                
                model_prob = calc_weather_probability(forecast_temp, target, std_dev)
                
                # 获取市场价格
                prices = json.loads(m.get("outcomePrices", "[]"))
                if len(prices) < 2:
                    continue
                yes_price = float(prices[0])
                no_price = float(prices[1])
                vol24h = float(m.get("volume24hr", 0) or 0)
                
                if vol24h < MIN_VOLUME_24H:
                    continue
                
                # 计算edge
                if model_prob > yes_price + MIN_EDGE:
                    edge = model_prob - yes_price
                    opportunities.append({
                        "type": "weather",
                        "side": "YES",
                        "question": m["question"],
                        "market_id": m.get("id"),
                        "token_id": json.loads(m.get("clobTokenIds", "[]"))[0],
                        "model_prob": round(model_prob, 3),
                        "market_price": yes_price,
                        "edge": round(edge, 3),
                        "bet_size": BET_SIZE_WEATHER,
                        "forecast": f"{forecast_temp}°{city_info['unit']}",
                        "target": target,
                        "vol24h": vol24h,
                    })
                elif (1 - model_prob) > no_price + MIN_EDGE:
                    edge = (1 - model_prob) - no_price
                    opportunities.append({
                        "type": "weather",
                        "side": "NO",
                        "question": m["question"],
                        "market_id": m.get("id"),
                        "token_id": json.loads(m.get("clobTokenIds", "[]"))[1],
                        "model_prob": round(1 - model_prob, 3),
                        "market_price": no_price,
                        "edge": round(edge, 3),
                        "bet_size": BET_SIZE_WEATHER,
                        "forecast": f"{forecast_temp}°{city_info['unit']}",
                        "target": target,
                        "vol24h": vol24h,
                    })
    
    return opportunities

# ============ 加密价格模块 ============
def get_btc_price():
    """获取BTC当前价格"""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5)
        return float(r.json()["price"])
    except:
        try:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=5)
            return float(r.json()["bitcoin"]["usd"])
        except:
            return None

def get_eth_price():
    """获取ETH当前价格"""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", timeout=5)
        return float(r.json()["price"])
    except:
        return None

def get_crypto_volatility(symbol="BTCUSDT", hours=168):
    """
    计算近期波动率(年化) — 用7天数据，取max(历史vol, 1.5×历史vol)作为保守估计
    战时环境需要更大的vol buffer
    """
    try:
        r = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit={hours}", timeout=10)
        klines = r.json()
        returns = []
        for i in range(1, len(klines)):
            c1 = float(klines[i-1][4])
            c2 = float(klines[i][4])
            returns.append(math.log(c2/c1))
        
        if not returns:
            return 0.8  # default high vol
        
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r)**2 for r in returns) / len(returns)
        std = var ** 0.5
        hist_vol = std * math.sqrt(365 * 24)
        
        # 保守估计: 用1.5倍历史vol，至少60%
        conservative_vol = max(hist_vol * 1.5, 0.60)
        log.info(f"  {symbol} hist_vol={hist_vol:.1%} → conservative={conservative_vol:.1%}")
        return conservative_vol
    except:
        return 0.8

def black_scholes_binary(S, K, T, sigma, option_type="above"):
    """
    二元期权定价(数字期权) — 到期日价格
    S = 当前价格, K = 行权价, T = 到期时间(年), sigma = 波动率
    用于: "Will BTC be above $X on date Y?" 类市场
    """
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    
    from math import log, sqrt, erf
    
    def norm_cdf(x):
        return 0.5 * (1 + erf(x / sqrt(2)))
    
    d2 = (log(S / K) + (-0.5 * sigma**2) * T) / (sigma * sqrt(T))
    
    if option_type == "above":
        return norm_cdf(d2)
    else:
        return norm_cdf(-d2)

def barrier_touch_prob(S, K, T, sigma):
    """
    一触即发(one-touch barrier)定价
    P(S_t >= K for some t in [0,T]) — 连续监控
    用于: "Will BTC reach $X in March?" 类市场
    
    公式(GBM, drift μ = -σ²/2 for risk-neutral with r=0):
    P = Φ(a) + (K/S)^(2μ/σ²) × Φ(b)
    其中 a = (log(S/K) + μT)/(σ√T), b = (log(S/K) - μT)/(σ√T)
    """
    if T <= 0 or sigma <= 0:
        return 1.0 if S >= K else 0.0
    if S >= K:
        return 1.0  # 已经触碰
    
    from math import log, sqrt, erf, exp
    
    def norm_cdf(x):
        return 0.5 * (1 + erf(x / sqrt(2)))
    
    mu = -0.5 * sigma**2  # risk-neutral drift with r=0
    sqrt_T = sqrt(T)
    log_SK = log(S / K)
    
    a = (log_SK + mu * T) / (sigma * sqrt_T)
    b = (log_SK - mu * T) / (sigma * sqrt_T)
    
    try:
        barrier_term = exp(2 * mu * log_SK / (sigma**2))
    except OverflowError:
        barrier_term = 0
    
    prob = norm_cdf(a) + barrier_term * norm_cdf(b)
    return min(max(prob, 0), 1)

def scan_crypto_price():
    """扫描加密货币价格市场"""
    opportunities = []
    
    btc_price = get_btc_price()
    eth_price = get_eth_price()
    if not btc_price:
        log.warning("无法获取BTC价格")
        return []
    
    btc_vol = get_crypto_volatility("BTCUSDT")
    eth_vol = get_crypto_volatility("ETHUSDT") if eth_price else 0.8
    
    log.info(f"₿ BTC=${btc_price:,.0f} vol={btc_vol:.1%} | ETH=${eth_price:,.0f} vol={eth_vol:.1%}")
    
    # 获取活跃的BTC/ETH价格市场
    markets = []
    for offset in [0, 200]:
        try:
            r = requests.get(
                "https://gamma-api.polymarket.com/markets",
                params={"limit": 200, "offset": offset, "active": "true", "closed": "false",
                         "order": "volume24hr", "ascending": "false"},
                timeout=15
            )
            markets.extend(r.json())
        except:
            break
    
    import re
    now = datetime.now(timezone.utc)
    
    for m in markets:
        q = m.get("question", "")
        ql = q.lower()
        
        # 只处理 BTC/ETH 价格市场
        if "bitcoin" in ql or "btc" in ql:
            asset_price = btc_price
            vol = btc_vol
        elif "ethereum" in ql or "eth" in ql:
            asset_price = eth_price
            vol = eth_vol
        else:
            continue
        
        if not any(kw in ql for kw in ["above", "below", "reach", "hit", "dip"]):
            continue
        
        vol24h = float(m.get("volume24hr", 0) or 0)
        if vol24h < MIN_VOLUME_24H:
            continue
        
        # 解析行权价
        price_match = re.search(r'\$(\d[\d,]*)', q)
        if not price_match:
            continue
        strike = float(price_match.group(1).replace(",", ""))
        
        # 解析到期时间
        end_date = m.get("endDate", "")
        if not end_date:
            continue
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except:
            continue
        
        T = (end_dt - now).total_seconds() / (365.25 * 24 * 3600)
        if T <= 0 or T > 0.1:  # 只做36天内的
            continue
        
        # 区分市场类型
        is_reach = any(kw in ql for kw in ["reach", "hit"])  # barrier/touch
        is_above = any(kw in ql for kw in ["above"])          # point-in-time
        is_below = any(kw in ql for kw in ["below", "dip"])
        
        if is_reach:
            # Barrier option: 任何时刻触碰
            if strike > asset_price:
                model_prob = barrier_touch_prob(asset_price, strike, T, vol)
            else:
                model_prob = 1.0  # 已经在strike之上
        elif is_above:
            model_prob = black_scholes_binary(asset_price, strike, T, vol, "above")
        elif is_below:
            model_prob = black_scholes_binary(asset_price, strike, T, vol, "below")
        else:
            continue
        
        # 市场价格
        prices = json.loads(m.get("outcomePrices", "[]"))
        if len(prices) < 2:
            continue
        yes_price = float(prices[0])
        no_price = float(prices[1])
        
        # 计算edge (YES = above/reach成功, NO = 不成功)
        if model_prob > yes_price + MIN_EDGE:
            opportunities.append({
                "type": "crypto_price",
                "side": "YES",
                "question": q[:80],
                "market_id": m.get("id"),
                "token_id": json.loads(m.get("clobTokenIds", "[]"))[0],
                "model_prob": round(model_prob, 3),
                "market_price": yes_price,
                "edge": round(model_prob - yes_price, 3),
                "bet_size": BET_SIZE_CRYPTO,
                "spot_price": asset_price,
                "strike": strike,
                "T_days": round(T * 365.25, 1),
                "vol": round(vol, 3),
                "vol24h": vol24h,
                "market_type": "barrier" if is_reach else "european",
            })
        elif (1 - model_prob) > no_price + MIN_EDGE:
            opportunities.append({
                "type": "crypto_price",
                "side": "NO",
                "question": q[:80],
                "market_id": m.get("id"),
                "token_id": json.loads(m.get("clobTokenIds", "[]"))[1],
                "model_prob": round(1 - model_prob, 3),
                "market_price": no_price,
                "edge": round((1 - model_prob) - no_price, 3),
                "bet_size": BET_SIZE_CRYPTO,
                "spot_price": asset_price,
                "strike": strike,
                "T_days": round(T * 365.25, 1),
                "vol": round(vol, 3),
                "vol24h": vol24h,
                "market_type": "barrier" if is_reach else "european",
            })
    
    return opportunities

# ============ Fed/宏观模块 ============
def scan_fed():
    """扫描FOMC利率市场 — 用CME FedWatch逻辑"""
    opportunities = []
    
    # March FOMC meeting: 3/17-18, 2026
    # 当前市场共识: no change ~99.5%
    # 我们用 CME futures implied概率作为参考
    # 由于CME FedWatch需要JS渲染，先用硬编码的共识概率
    # TODO: 自动获取CME FedWatch数据
    
    FED_CONSENSUS = {
        "no_change_march": 0.995,  # 几乎确定不变
        "cut_25_march": 0.003,
        "cut_50_march": 0.001,
        "hike_25_march": 0.001,
    }
    
    # 找FOMC市场
    try:
        markets = []
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 200, "active": "true", "closed": "false",
                     "order": "volume24hr", "ascending": "false"},
            timeout=15
        )
        markets = r.json()
    except:
        return []
    
    fed_markets = [m for m in markets if any(kw in m.get("question","").lower() 
                   for kw in ["fed ", "fomc", "interest rate"])]
    
    for m in fed_markets:
        q = m.get("question", "")
        ql = q.lower()
        vol24h = float(m.get("volume24hr", 0) or 0)
        
        if vol24h < MIN_VOLUME_24H:
            continue
        
        prices = json.loads(m.get("outcomePrices", "[]"))
        if len(prices) < 2:
            continue
        yes_price = float(prices[0])
        no_price = float(prices[1])
        
        # 匹配共识概率
        model_prob_yes = None
        if "no change" in ql and "march" in ql:
            model_prob_yes = FED_CONSENSUS["no_change_march"]
        elif "decrease" in ql and "50" in ql and "march" in ql:
            model_prob_yes = FED_CONSENSUS["cut_50_march"]
        elif "decrease" in ql and "25 bps" in ql and "march" in ql:
            model_prob_yes = FED_CONSENSUS["cut_25_march"]
        elif "increase" in ql and "march" in ql:
            model_prob_yes = FED_CONSENSUS["hike_25_march"]
        
        if model_prob_yes is None:
            continue
        
        # 计算edge
        if model_prob_yes > yes_price + MIN_EDGE:
            opportunities.append({
                "type": "fed",
                "side": "YES",
                "question": q[:80],
                "market_id": m.get("id"),
                "token_id": json.loads(m.get("clobTokenIds", "[]"))[0],
                "model_prob": round(model_prob_yes, 4),
                "market_price": yes_price,
                "edge": round(model_prob_yes - yes_price, 4),
                "bet_size": BET_SIZE_FED,
                "vol24h": vol24h,
            })
        elif (1 - model_prob_yes) > no_price + MIN_EDGE:
            opportunities.append({
                "type": "fed",
                "side": "NO",
                "question": q[:80],
                "market_id": m.get("id"),
                "token_id": json.loads(m.get("clobTokenIds", "[]"))[1],
                "model_prob": round(1 - model_prob_yes, 4),
                "market_price": no_price,
                "edge": round((1 - model_prob_yes) - no_price, 4),
                "bet_size": BET_SIZE_FED,
                "vol24h": vol24h,
            })
    
    return opportunities

# ============ 执行交易 ============
def place_trade(client, opp, state):
    """下GTC限价单"""
    try:
        token_id = opp["token_id"]
        price = round(opp["market_price"] + 0.01, 2)  # 比市场价高1分买入
        if price >= 0.99:
            price = 0.99
        if price <= 0.01:
            price = 0.01
        
        size = opp["bet_size"] / price
        size = round(size, 1)
        if size < 5:
            size = 5
        
        cost = price * size
        
        log.info(f"📝 下单: {opp['side']} {opp['question'][:50]}")
        log.info(f"   price={price} size={size} cost=${cost:.2f} edge={opp['edge']:.1%}")
        
        order = client.create_and_post_order(
            OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY,
            )
        )
        
        log.info(f"   ✅ 订单已提交: {order}")
        
        state["trades_today"].append({
            "time": datetime.now().isoformat(),
            "type": opp["type"],
            "side": opp["side"],
            "question": opp["question"][:60],
            "price": price,
            "size": size,
            "cost": cost,
            "edge": opp["edge"],
            "model_prob": opp["model_prob"],
        })
        state["daily_spend"] += cost
        save_state(state)
        
        return True
    except Exception as e:
        log.error(f"   ❌ 下单失败: {e}")
        return False

# ============ 主循环 ============
def main():
    log.info("=" * 60)
    log.info("🔍 Multi-Edge Scanner 启动")
    log.info("=" * 60)
    
    client = get_client()
    balance = get_balance(client)
    log.info(f"💰 USDC余额: ${balance:.2f}")
    
    if balance < MIN_BALANCE:
        log.warning(f"⚠️ 余额不足 ${MIN_BALANCE}，仅扫描不下单")
    
    state = load_state()
    state = reset_daily(state)
    
    remaining_budget = MAX_DAILY_SPEND - state["daily_spend"]
    remaining_trades = MAX_DAILY_TRADES - len(state["trades_today"])
    log.info(f"📊 今日已花: ${state['daily_spend']:.2f}/{MAX_DAILY_SPEND} | 已交易: {len(state['trades_today'])}/{MAX_DAILY_TRADES}")
    
    if remaining_budget <= 0 or remaining_trades <= 0:
        log.info("📛 今日配额已满")
        return
    
    # 三路扫描
    all_opps = []
    
    log.info("\n--- 🌡️ 天气市场扫描 ---")
    weather_opps = scan_weather()
    all_opps.extend(weather_opps)
    log.info(f"天气机会: {len(weather_opps)}")
    
    log.info("\n--- ₿ 加密价格扫描 ---")
    crypto_opps = scan_crypto_price()
    all_opps.extend(crypto_opps)
    log.info(f"加密机会: {len(crypto_opps)}")
    
    log.info("\n--- 🏦 Fed/宏观扫描 ---")
    fed_opps = scan_fed()
    all_opps.extend(fed_opps)
    log.info(f"Fed机会: {len(fed_opps)}")
    
    # 按edge排序
    all_opps.sort(key=lambda x: x["edge"], reverse=True)
    
    log.info(f"\n{'='*60}")
    log.info(f"📋 总机会: {len(all_opps)}")
    for i, opp in enumerate(all_opps):
        log.info(f"  #{i+1} [{opp['type']}] {opp['side']} | edge={opp['edge']:.1%} | model={opp['model_prob']:.1%} vs market={opp['market_price']:.1%}")
        log.info(f"       {opp['question'][:65]}")
    
    # 执行最佳机会
    if balance < MIN_BALANCE:
        log.info("💤 余额不足，不执行交易")
        return
    
    executed = 0
    for opp in all_opps:
        if remaining_budget < opp["bet_size"]:
            log.info(f"💰 预算剩余 ${remaining_budget:.2f}，不够 ${opp['bet_size']}")
            break
        if remaining_trades <= 0:
            break
        
        # 检查是否已对该市场下过注
        already_traded = any(
            t.get("question", "")[:40] == opp["question"][:40]
            for t in state["trades_today"]
        )
        if already_traded:
            log.info(f"⏭️ 跳过(已交易): {opp['question'][:40]}")
            continue
        
        if place_trade(client, opp, state):
            executed += 1
            remaining_budget -= opp["bet_size"]
            remaining_trades -= 1
            time.sleep(1)  # rate limit
    
    log.info(f"\n✅ 本轮执行: {executed} 笔交易")
    log.info(f"💰 今日总花费: ${state['daily_spend']:.2f}")
    save_state(state)

if __name__ == "__main__":
    main()