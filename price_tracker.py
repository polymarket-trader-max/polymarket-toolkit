"""
价格追踪器 - 持续轮询 Polymarket 活跃市场，构建本地价格时序数据库

原理：
  每次运行将当前所有活跃市场的价格快照保存到 data/price_snapshots/ 下的 JSONL 文件
  文件格式：data/price_snapshots/YYYY-MM-DD.jsonl
  每行一条记录：{"ts": unix_timestamp, "id": market_id, "yes": price, "vol24h": ..., "liq": ...}

使用方式：
  python3 price_tracker.py              # 单次快照
  python3 price_tracker.py --daemon     # 每 15 分钟轮询（后台运行）
  python3 price_tracker.py --stats      # 统计已有数据量
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, date

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "price_snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

BASE_URL = "https://gamma-api.polymarket.com"


def _get(path: str, params: dict = None) -> list | dict:
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_all_active_markets(min_liquidity: float = 500) -> list[dict]:
    """抓取所有活跃 binary 市场，包含 token IDs。"""
    all_markets = []
    seen_ids = set()
    limit = 100

    tags = [None, "politics", "crypto", "finance", "tech", "sports", "geopolitics"]
    for tag in tags:
        params = {
            "active": "true",
            "closed": "false",
            "order": "liquidity",
            "ascending": "false",
            "limit": limit,
        }
        if tag:
            params["tag_slug"] = tag
        try:
            markets = _get("/markets", params)
        except Exception as e:
            print(f"  ⚠ 抓取 tag={tag} 失败: {e}", file=sys.stderr)
            continue

        for m in markets:
            mid = m.get("id")
            if mid in seen_ids:
                continue
            try:
                outcomes = json.loads(m.get("outcomes", "[]"))
                prices = json.loads(m.get("outcomePrices", "[]"))
                if len(outcomes) != 2 or len(prices) < 2:
                    continue
                liq = float(m.get("liquidity", 0))
                if liq < min_liquidity:
                    continue
                yes_price = float(prices[0])
                if yes_price <= 0 or yes_price >= 1:
                    continue

                seen_ids.add(mid)
                all_markets.append({
                    "id": mid,
                    "question": m.get("question", ""),
                    "slug": m.get("slug", ""),
                    "conditionId": m.get("conditionId", ""),
                    "clobTokenIds": m.get("clobTokenIds", "[]"),
                    "category": _infer_category(m),
                    "end_date": m.get("endDate", ""),
                    "yes_price": yes_price,
                    "no_price": float(prices[1]),
                    "liquidity": liq,
                    "volume": float(m.get("volume", 0)),
                    "volume_24h": float(m.get("volume24hr", 0)),
                    "volume_1w": float(m.get("volume1wk", 0)),
                    "spread": float(m.get("spread", 0.01)),
                    "day_change": float(m.get("oneDayPriceChange", 0)),
                    "week_change": float(m.get("oneWeekPriceChange", 0)),
                    "last_trade": float(m.get("lastTradePrice", yes_price)),
                    "competitive": float(m.get("competitive", 0)),
                    "enable_ob": bool(m.get("enableOrderBook", False)),
                })
            except Exception:
                continue

    return all_markets


def take_snapshot(verbose: bool = True) -> int:
    """拍摄一次快照，追加到今天的 JSONL 文件。返回保存的市场数。"""
    ts = int(time.time())
    today = date.today().isoformat()
    out_path = os.path.join(SNAPSHOT_DIR, f"{today}.jsonl")

    if verbose:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取活跃市场...", flush=True)

    markets = fetch_all_active_markets()

    count = 0
    with open(out_path, "a") as f:
        for m in markets:
            record = {
                "ts": ts,
                "id": m["id"],
                "q": m["question"][:80],
                "cat": m["category"],
                "slug": m["slug"],
                "yes": m["yes_price"],
                "liq": m["liquidity"],
                "vol24h": m["volume_24h"],
                "vol1w": m["volume_1w"],
                "spread": m["spread"],
                "day_chg": m["day_change"],
                "wk_chg": m["week_change"],
                "last": m["last_trade"],
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    if verbose:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ 保存 {count} 个市场 → {out_path}", flush=True)

    return count


def load_price_series(market_id: str, days_back: int = 30) -> list[dict]:
    """
    从本地 JSONL 文件加载指定市场的价格序列。
    返回按时间排序的 [{"ts": ..., "yes": ..., "liq": ..., "vol24h": ...}, ...]
    """
    cutoff = int(time.time()) - days_back * 86400
    series = []

    # 遍历数据目录
    try:
        files = sorted(os.listdir(SNAPSHOT_DIR))
    except Exception:
        return []

    for fname in files:
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(SNAPSHOT_DIR, fname)
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if rec.get("id") == market_id and rec.get("ts", 0) >= cutoff:
                            series.append(rec)
                    except Exception:
                        continue
        except Exception:
            continue

    series.sort(key=lambda x: x["ts"])
    return series


def load_all_latest_prices() -> dict[str, dict]:
    """
    返回所有市场的最新一条记录 {market_id -> record}
    用于快速访问当前价格。
    """
    latest = {}
    try:
        files = sorted(os.listdir(SNAPSHOT_DIR))[-3:]  # 只看最近3天文件
    except Exception:
        return {}

    for fname in files:
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(SNAPSHOT_DIR, fname)
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        mid = rec.get("id")
                        if mid:
                            # 保留最新的
                            if mid not in latest or rec["ts"] > latest[mid]["ts"]:
                                latest[mid] = rec
                    except Exception:
                        continue
        except Exception:
            continue

    return latest


def get_stats() -> dict:
    """统计本地价格数据库状态。"""
    try:
        files = [f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".jsonl")]
    except Exception:
        return {}

    total_records = 0
    market_ids = set()
    dates = []

    for fname in files:
        fpath = os.path.join(SNAPSHOT_DIR, fname)
        day_count = 0
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        market_ids.add(rec.get("id"))
                        day_count += 1
                        total_records += 1
                    except Exception:
                        continue
        except Exception:
            continue
        dates.append((fname, day_count))

    return {
        "total_records": total_records,
        "unique_markets": len(market_ids),
        "days": len(files),
        "files": dates[-5:],
    }


def _infer_category(m: dict) -> str:
    q = (m.get("question", "") + " " + m.get("description", "")).lower()
    slug = m.get("slug", "").lower()
    crypto_kw = ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "xrp", "coinbase", "defi"]
    politics_kw = ["president", "election", "senate", "congress", "democrat", "republican", "vote", "governor"]
    geo_kw = ["russia", "ukraine", "china", "taiwan", "iran", "war", "ceasefire", "nato", "israel", "gaza"]
    tech_kw = ["gpt", "claude", "gemini", "apple", "google", "microsoft", "openai", "anthropic"]
    finance_kw = ["fed", "rate", "gdp", "inflation", "s&p", "nasdaq", "stock"]
    scores = {
        "crypto": sum(1 for k in crypto_kw if k in q or k in slug),
        "politics": sum(1 for k in politics_kw if k in q or k in slug),
        "geopolitics": sum(1 for k in geo_kw if k in q or k in slug),
        "tech": sum(1 for k in tech_kw if k in q or k in slug),
        "finance": sum(1 for k in finance_kw if k in q or k in slug),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Polymarket 价格追踪器")
    parser.add_argument("--daemon", action="store_true", help="每15分钟循环采集")
    parser.add_argument("--stats", action="store_true", help="显示数据库统计")
    parser.add_argument("--interval", type=int, default=900, help="轮询间隔秒数（默认900=15分钟）")
    args = parser.parse_args()

    if args.stats:
        s = get_stats()
        print(f"\n📊 价格数据库统计")
        print(f"  总记录数: {s.get('total_records', 0):,}")
        print(f"  唯一市场: {s.get('unique_markets', 0):,}")
        print(f"  天数: {s.get('days', 0)}")
        print(f"  最近文件:")
        for fname, cnt in s.get('files', []):
            print(f"    {fname}: {cnt:,} 条")
        sys.exit(0)

    if args.daemon:
        print(f"🔄 守护模式：每 {args.interval}s 采集一次")
        print(f"📁 存储目录: {SNAPSHOT_DIR}")
        while True:
            try:
                take_snapshot()
            except Exception as e:
                print(f"  ⚠ 快照失败: {e}", file=sys.stderr)
            print(f"  💤 等待 {args.interval}s...", flush=True)
            time.sleep(args.interval)
    else:
        # 单次快照
        n = take_snapshot()
        print(f"\n📁 数据目录: {SNAPSHOT_DIR}")
        s = get_stats()
        print(f"📊 累计: {s.get('total_records', 0):,} 条 | {s.get('unique_markets', 0)} 个市场 | {s.get('days', 0)} 天")
