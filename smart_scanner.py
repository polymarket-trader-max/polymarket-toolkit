#!/usr/bin/env python3
"""
概率重定价扫描器 - 核心逻辑：
找流动性充足、价差窄、近期有概率重定价催化剂的市场
不赌事件结果，交易「市场对未来看法的变化」
"""
import subprocess, json, time
from datetime import datetime, timezone

def fetch_markets(limit=200, offset=0, tag=None):
    url = f"https://gamma-api.polymarket.com/markets?limit={limit}&offset={offset}&active=true&closed=false&order=volume24hr&ascending=false"
    if tag:
        url += f"&tag={tag}"
    r = subprocess.run(f'curl -s "{url}"', shell=True, capture_output=True, text=True)
    return json.loads(r.stdout) if r.stdout else []

def get_clob_spread(token_id):
    """获取买卖价差（越小越好）"""
    r = subprocess.run(
        f'curl -s "https://clob.polymarket.com/book?token_id={token_id}"',
        shell=True, capture_output=True, text=True, timeout=5)
    try:
        book = json.loads(r.stdout)
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        if bids and asks:
            best_bid = float(bids[0]['price'])
            best_ask = float(asks[0]['price'])
            return best_bid, best_ask
    except:
        pass
    return None, None

def analyze_opportunity(m):
    """
    评估市场，返回交易机会或 None
    策略：
    1. 流动性 > $5000, 24h 成交 > $500
    2. 价差 < 5%
    3. 价格在 0.1-0.9 之间（有双向空间）
    4. 近期有价格移动（说明市场在重定价）
    """
    try:
        volume   = float(m.get('volume24hr', 0) or 0)
        liq      = float(m.get('liquidity', 0) or 0)
        yes_p    = float(m.get('bestAsk', 0) or 0)   # ask = 你买YES的价
        bid_p    = float(m.get('bestBid', 0) or 0)   # bid = 你卖YES的价
        last     = float(m.get('lastTradePrice', 0) or 0)

        if volume < 300 or liq < 2000:
            return None
        if yes_p < 0.08 or yes_p > 0.92:   # 太极端的事件跳过
            return None
        if yes_p == 0 or bid_p == 0:
            return None

        spread_pct = (yes_p - bid_p) / yes_p
        if spread_pct > 0.06:   # 价差 > 6% 跳过
            return None

        # 近期价格变动（Gamma API 提供 1day change）
        change_1d = float(m.get('oneDayPriceChange') or 0)

        # 方向判断：找被推得过低或过高的概率
        # 如果 24h 跌了 >10% 但流动性仍高 → 可能超跌，做 YES
        # 如果 24h 涨了 >10% 但已接近顶部 → 可能超涨，做 NO
        direction = None
        edge = 0
        reason = ""

        if change_1d < -0.08 and yes_p < 0.55:
            direction = "YES"
            edge = abs(change_1d) * 0.4
            reason = f"超跌回弹 24h-{abs(change_1d):.0%} liq=${liq:.0f}"

        elif change_1d > 0.08 and yes_p > 0.55:
            direction = "NO"
            edge = change_1d * 0.4
            reason = f"超涨回调 24h+{change_1d:.0%} liq=${liq:.0f}"

        elif 0.40 < yes_p < 0.60 and spread_pct < 0.03:
            # 50/50 附近，高流动性，价差极小 → 做市获利
            direction = "YES" if bid_p < 0.50 else "NO"
            edge = 0.03
            reason = f"均衡点套利 spread={spread_pct:.1%} liq=${liq:.0f}"

        if not direction or edge < 0.05:   # 巨鲸标准：edge ≥ 5% 才交易
            return None

        # 1/4 Kelly 仓位
        price = yes_p if direction == "YES" else (1 - bid_p)
        b = (1 / price) - 1
        p = price + edge
        q = 1 - p
        kelly = (b * p - q) / b if b > 0 else 0
        bet = min(5.0, max(2.0, round(15.0 * 0.25 * kelly, 2)))  # 基于$15余额

        days_left = 999
        end = m.get('endDate') or m.get('endDateIso') or ''
        if end:
            try:
                ed = datetime.fromisoformat(end.replace('Z','+00:00'))
                days_left = max(0, (ed - datetime.now(timezone.utc)).days)
            except:
                pass

        return {
            'question':   m['question'],
            'slug':       m.get('slug',''),
            'direction':  direction,
            'price':      price,
            'bid':        bid_p,
            'ask':        yes_p,
            'spread_pct': spread_pct,
            'edge':       edge,
            'bet':        bet,
            'volume_24h': volume,
            'liquidity':  liq,
            'change_1d':  change_1d,
            'days_left':  days_left,
            'reason':     reason,
            'clob_token_ids': json.loads(m.get('clobTokenIds') or '[]'),
            'last_price': last,
        }
    except Exception as e:
        return None

def scan_smart(verbose=True):
    if verbose:
        print(f"\n{'='*65}")
        print(f"  🧠 概率重定价扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  策略：流动性好 + 价差窄 + 近期重定价催化剂")
        print(f"{'='*65}\n")

    all_markets = []
    for offset in [0, 200, 400]:
        batch = fetch_markets(limit=200, offset=offset)
        if not batch:
            break
        all_markets.extend(batch)
        time.sleep(0.3)

    if verbose:
        print(f"  扫描 {len(all_markets)} 个活跃市场...")

    opps = []
    for m in all_markets:
        opp = analyze_opportunity(m)
        if opp:
            opps.append(opp)

    # 按 edge × volume 排序
    opps.sort(key=lambda x: x['edge'] * x['volume_24h'], reverse=True)

    if verbose:
        if opps:
            print(f"  发现 {len(opps)} 个机会：\n")
            for i, o in enumerate(opps[:5], 1):
                print(f"  #{i} 做{o['direction']} @ ${o['price']:.3f} → 下注 ${o['bet']}")
                print(f"      {o['question'][:68]}")
                print(f"      边际:{o['edge']:.1%}  价差:{o['spread_pct']:.1%}  24h成交:${o['volume_24h']:.0f}  剩:{o['days_left']}天")
                print(f"      💡 {o['reason']}\n")
        else:
            print("  暂无符合条件的机会\n")

    return opps[:5]

if __name__ == '__main__':
    scan_smart()
