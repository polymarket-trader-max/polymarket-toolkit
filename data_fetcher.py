"""
Polymarket 数据抓取模块
使用 Gamma API (公开) 获取历史市场数据
"""

import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Optional


BASE_URL = "https://gamma-api.polymarket.com"


def _get(path: str, params: dict = None) -> dict | list:
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-model/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_resolved_markets(limit: int = 500, offset: int = 0, min_volume: float = 5000) -> list[dict]:
    """
    获取已解决的市场，用于回测。
    只取 Binary (Yes/No) 市场。
    """
    params = {
        "active": "false",
        "closed": "true",
        "order": "volume",
        "ascending": "false",
        "limit": limit,
        "offset": offset,
    }
    markets = _get("/markets", params)
    result = []
    for m in markets:
        try:
            vol = float(m.get("volume", 0))
            if vol < min_volume:
                continue
            outcomes = json.loads(m.get("outcomes", "[]"))
            prices = json.loads(m.get("outcomePrices", "[]"))
            if len(outcomes) != 2:
                continue  # 只要 Binary 市场
            result.append({
                "id": m["id"],
                "question": m["question"],
                "category": _infer_category(m),
                "start_date": m.get("startDate", ""),
                "end_date": m.get("endDate", ""),
                "volume": vol,
                "volume_1w": float(m.get("volume1wk", 0)),
                "volume_1m": float(m.get("volume1mo", 0)),
                "liquidity": float(m.get("liquidity", 0)),
                "final_yes_price": float(prices[0]) if prices else None,
                "resolved_yes": _infer_resolution(m),
                "description": m.get("description", ""),
                "slug": m.get("slug", ""),
                "condition_id": m.get("conditionId", ""),
                "clob_token_ids": m.get("clobTokenIds", "[]"),
                "enable_order_book": bool(m.get("enableOrderBook", False)),
            })
        except Exception:
            continue
    return result


def fetch_active_markets(limit: int = 100, tag: Optional[str] = None) -> list[dict]:
    """获取当前活跃市场，用于实盘信号生成。"""
    params = {
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false",
        "limit": limit,
    }
    if tag:
        params["tag_slug"] = tag

    markets = _get("/markets", params)
    result = []
    for m in markets:
        try:
            outcomes = json.loads(m.get("outcomes", "[]"))
            prices = json.loads(m.get("outcomePrices", "[]"))
            if len(outcomes) != 2:
                continue
            yes_price = float(prices[0])
            if yes_price <= 0 or yes_price >= 1:
                continue
            result.append({
                "id": m["id"],
                "question": m["question"],
                "category": _infer_category(m),
                "end_date": m.get("endDate", ""),
                "volume": float(m.get("volume", 0)),
                "volume_24h": float(m.get("volume24hr", 0)),
                "liquidity": float(m.get("liquidity", 0)),
                "yes_price": yes_price,
                "no_price": float(prices[1]) if len(prices) > 1 else 1 - yes_price,
                "spread": float(m.get("spread", 0)),
                "last_trade": float(m.get("lastTradePrice", yes_price)),
                "day_change": float(m.get("oneDayPriceChange", 0)),
                "week_change": float(m.get("oneWeekPriceChange", 0)),
                "competitive": float(m.get("competitive", 0)),
                "slug": m.get("slug", ""),
                "description": m.get("description", ""),
            })
        except Exception:
            continue
    return result


def fetch_market_history(condition_id: str, resolution_token_id: str) -> list[dict]:
    """获取市场价格历史（CLOB timeseries），用于分析价格走势。"""
    # Polymarket CLOB API
    url = f"https://clob.polymarket.com/prices-history"
    params = {
        "market": condition_id,
        "tokenID": resolution_token_id,
        "interval": "1d",
        "fidelity": 10,
    }
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"User-Agent": "polymarket-model/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("history", [])
    except Exception:
        return []


def _infer_category(m: dict) -> str:
    """从市场文本推断类别。"""
    q = (m.get("question", "") + " " + m.get("description", "")).lower()
    slug = m.get("slug", "").lower()
    tags_str = str(m.get("events", []))

    crypto_kw = ["bitcoin", "btc", "ethereum", "eth", "crypto", "defi", "solana", "xrp", "coinbase", "deepseek", "ai model", "openai", "anthropic"]
    politics_kw = ["president", "election", "senate", "congress", "democrat", "republican", "vote", "primary", "nominee", "governor", "minister", "parliament"]
    geo_kw = ["russia", "ukraine", "china", "taiwan", "iran", "war", "ceasefire", "nato", "sanctions", "military", "venezuela", "israel", "gaza"]
    tech_kw = ["release", "launch", "model", "gpt", "claude", "gemini", "apple", "google", "microsoft", "meta", "tesla", "spacex"]
    finance_kw = ["fed", "rate", "gdp", "recession", "inflation", "s&p", "nasdaq", "dow", "stock", "market"]

    scores = {
        "crypto": sum(1 for k in crypto_kw if k in q or k in slug),
        "politics": sum(1 for k in politics_kw if k in q or k in slug),
        "geopolitics": sum(1 for k in geo_kw if k in q or k in slug),
        "tech": sum(1 for k in tech_kw if k in q or k in slug),
        "finance": sum(1 for k in finance_kw if k in q or k in slug),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def _infer_resolution(m: dict) -> Optional[bool]:
    """
    尝试从已关闭市场推断最终解决结果。
    已解决的市场 outcomePrices 通常为 [1.0, 0.0] 或 [0.0, 1.0]。
    """
    try:
        prices = json.loads(m.get("outcomePrices", "[]"))
        if not prices:
            return None
        yes_price = float(prices[0])
        if yes_price >= 0.99:
            return True
        elif yes_price <= 0.01:
            return False
        else:
            return None  # 未完全解决或无效
    except Exception:
        return None


if __name__ == "__main__":
    print("=== 测试：抓取已解决市场 ===")
    resolved = fetch_resolved_markets(limit=50, min_volume=10000)
    print(f"获取到 {len(resolved)} 个已解决市场")
    for m in resolved[:5]:
        print(f"  [{m['category']}] {m['question'][:60]}... vol=${m['volume']:,.0f} resolved_yes={m['resolved_yes']}")

    print("\n=== 测试：抓取活跃市场 ===")
    active = fetch_active_markets(limit=20)
    print(f"获取到 {len(active)} 个活跃市场")
    for m in active[:5]:
        print(f"  [{m['category']}] {m['question'][:60]}... yes={m['yes_price']:.2f}")
