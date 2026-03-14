"""
news_monitor.py — Polymarket 持仓消息面监控 v1

监控来源：
  - BBC 中东新闻 RSS
  - Al Jazeera RSS
  - OilPrice RSS
  - BTC 实时价格（CoinGecko）
  - 油价（oilprice.com 爬虫）
  - 状态存储在 news_monitor_state.json

关键词过滤：与持仓相关的事件/人物
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
import re

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

# ── 配置 ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "news_monitor_state.json"

RSS_FEEDS = [
    {
        "name": "BBC 中东",
        "url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
        "category": "geopolitics",
    },
    {
        "name": "BBC 全球",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "category": "geopolitics",
    },
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "category": "geopolitics",
    },
    {
        "name": "OilPrice",
        "url": "https://oilprice.com/rss/main",
        "category": "oil",
    },
]

# 关键词 → 对应持仓影响
KEYWORD_MAP = {
    # 伊朗相关 → 影响所有伊朗仓位
    "iran": "🇮🇷 伊朗",
    "khamenei": "👤 Khamenei",
    "tehran": "🇮🇷 德黑兰",
    "irgc": "⚔️ IRGC",
    "hormuz": "🛢️ 霍尔木兹",
    "hezbollah": "⚔️ 真主党",
    "beirut": "🇱🇧 贝鲁特",
    "israel": "🇮🇱 以色列",
    "idf": "🇮🇱 IDF",
    # 美国政治
    "trump": "🇺🇸 Trump",
    "musk": "🔵 Musk",
    "rubio": "🇺🇸 Rubio",
    "hegseth": "🇺🇸 Hegseth",
    "pentagon": "🇺🇸 Pentagon",
    # 油价
    "crude oil": "🛢️ 原油",
    "brent": "🛢️ Brent",
    "opec": "🛢️ OPEC",
    "oil price": "🛢️ 油价",
    "petroleum": "🛢️ 石油",
    "ceasefire": "☮️ 停火",
    "surrender": "🏳️ 投降",
    "regime change": "💥 政权更迭",
    "nuclear": "☢️ 核",
    "strait": "🚢 海峡",
}

# 持仓影响评估
POSITION_IMPACT = {
    "iran": ["伊朗政权2027倒台 YES", "3月31日倒台 NO", "美伊停火 YES"],
    "khamenei": ["Khamenei接班 YES", "伊朗政权2027倒台 YES"],
    "tehran": ["伊朗政权2027倒台 YES", "3月31日倒台 NO"],
    "irgc": ["伊朗政权2027倒台 YES"],
    "hormuz": ["原油$100+ NO ⚠️"],
    "crude oil": ["原油$100+ NO ⚠️"],
    "brent": ["原油$100+ NO ⚠️"],
    "oil price": ["原油$100+ NO ⚠️"],
    "opec": ["原油$100+ NO ⚠️"],
    "ceasefire": ["美伊停火 YES", "3月31日倒台 NO"],
    "surrender": ["伊朗政权2027倒台 YES", "3月31日倒台 NO", "美伊停火 YES"],
    "trump": ["所有持仓（消息驱动）"],
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen_ids": {}, "last_run": None, "last_oil_price": None, "last_btc_price": None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def fetch_url(url: str, timeout: int = 10) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"})
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return None


def parse_rss_feedparser(url: str) -> list[dict]:
    """使用 feedparser 解析 RSS"""
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:20]:
            items.append({
                "id": getattr(entry, "id", entry.get("link", "")),
                "title": getattr(entry, "title", ""),
                "summary": getattr(entry, "summary", ""),
                "link": getattr(entry, "link", ""),
                "published": getattr(entry, "published", ""),
            })
        return items
    except Exception:
        return []


def parse_rss_manual(content: str) -> list[dict]:
    """手动解析 RSS XML（feedparser 备用）"""
    items = []
    # 找所有 <item> 或 <entry>
    for block in re.findall(r"<(?:item|entry)>(.*?)</(?:item|entry)>", content, re.DOTALL):
        def extract(tag):
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.DOTALL)
            return m.group(1).strip() if m else ""

        title = extract("title")
        link = extract("link") or re.search(r"<link[^>]*/?>|<link>(.*?)</link>", block)
        summary = extract("description") or extract("summary")
        guid = extract("guid") or extract("id") or title
        published = extract("pubDate") or extract("published") or extract("updated")

        if isinstance(link, str):
            pass
        elif link:
            link = link.group(1) if link.lastindex else ""

        if title:
            items.append({
                "id": str(guid),
                "title": title,
                "summary": summary[:300],
                "link": link if isinstance(link, str) else "",
                "published": published,
            })
    return items[:20]


def get_news_items(feed: dict) -> list[dict]:
    content = fetch_url(feed["url"])
    if not content:
        return []
    if HAS_FEEDPARSER:
        items = parse_rss_feedparser(feed["url"])
    else:
        items = parse_rss_manual(content)
    return items


def find_matching_keywords(text: str) -> list[tuple[str, str]]:
    """返回匹配的 (keyword, label) 列表"""
    text_lower = text.lower()
    matches = []
    for keyword, label in KEYWORD_MAP.items():
        if keyword in text_lower:
            matches.append((keyword, label))
    return matches


def get_btc_price() -> float | None:
    data = fetch_url("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
    if data:
        try:
            return json.loads(data)["bitcoin"]["usd"]
        except Exception:
            pass
    return None


def get_oil_price() -> str | None:
    """从 oilprice.com 获取 Brent/WTI 价格"""
    content = fetch_url("https://oilprice.com/oil-price-charts/", timeout=12)
    if not content:
        return None
    # 搜索价格模式
    m = re.search(r"Brent[^$]*\$\s*([\d.]+)", content)
    if m:
        return f"Brent ${m.group(1)}"
    # 备选
    m = re.search(r"(\d{2,3}\.\d{1,2})", content)
    if m:
        val = float(m.group(1))
        if 60 < val < 200:
            return f"~${val}"
    return None


def run_monitor(verbose: bool = True, only_new: bool = True) -> dict:
    """
    运行新闻监控。
    Returns: {
        "new_items": [...],
        "btc_price": float,
        "oil_price": str,
        "alerts": [...],  # 高影响新闻
    }
    """
    state = load_state()
    seen_ids = state.get("seen_ids", {})

    new_items = []
    all_alerts = []

    if verbose:
        print(f"\n{'='*60}")
        print(f"  📡 消息面监控  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")

    # ── 抓取各 RSS ─────────────────────────────────────────────
    for feed in RSS_FEEDS:
        items = get_news_items(feed)
        feed_new = []

        for item in items:
            item_id = item["id"]
            if only_new and item_id in seen_ids.get(feed["name"], []):
                continue

            text = f"{item['title']} {item['summary']}"
            matches = find_matching_keywords(text)

            if matches:
                entry = {
                    "source": feed["name"],
                    "title": item["title"],
                    "link": item["link"],
                    "published": item["published"],
                    "keywords": [label for _, label in matches],
                    "positions_affected": list(set(
                        pos
                        for kw, _ in matches
                        for pos in POSITION_IMPACT.get(kw, [])
                    )),
                }
                feed_new.append(entry)
                new_items.append(entry)

                # 高优先级关键词
                high_priority = {"khamenei", "trump", "surrender", "hormuz", "ceasefire", "nuclear"}
                if any(kw in high_priority for kw, _ in matches):
                    all_alerts.append(entry)

            # 标记为已见
            seen_ids.setdefault(feed["name"], [])
            if item_id not in seen_ids[feed["name"]]:
                seen_ids[feed["name"]].append(item_id)
            # 只保留最近100条
            seen_ids[feed["name"]] = seen_ids[feed["name"]][-100:]

        if verbose and feed_new:
            print(f"\n  📰 {feed['name']} ({len(feed_new)} 条新消息)")
            for item in feed_new[:5]:
                kw_str = " ".join(item["keywords"][:3])
                print(f"     [{kw_str}] {item['title'][:80]}")
                if item.get("positions_affected"):
                    print(f"       → 影响: {', '.join(item['positions_affected'][:3])}")

    # ── 价格 ───────────────────────────────────────────────────
    btc_price = get_btc_price()
    oil_price = get_oil_price()

    if verbose:
        print(f"\n  💰 BTC: ${btc_price:,.0f}" if btc_price else "\n  💰 BTC: 获取失败")
        prev_btc = state.get("last_btc_price")
        if prev_btc and btc_price:
            delta = btc_price - prev_btc
            pct = delta / prev_btc * 100
            print(f"     变化: {'+' if delta > 0 else ''}{delta:,.0f} ({pct:+.1f}%) vs 上次检查")

        if oil_price:
            print(f"  🛢️  油价: {oil_price}")
            prev_oil = state.get("last_oil_price")
            if prev_oil:
                print(f"     上次: {prev_oil}")
        else:
            print("  🛢️  油价: 获取失败")

    # ── 汇总 ───────────────────────────────────────────────────
    if verbose:
        print(f"\n  ───────────────────────────────────────────────")
        print(f"  新闻: {len(new_items)} 条新消息 | 高优先: {len(all_alerts)} 条")
        if not new_items:
            print("  ✅ 无新增相关消息")
        print(f"{'='*60}\n")

    # ── 更新状态 ───────────────────────────────────────────────
    state["seen_ids"] = seen_ids
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    if btc_price:
        state["last_btc_price"] = btc_price
    if oil_price:
        state["last_oil_price"] = oil_price
    save_state(state)

    return {
        "new_items": new_items,
        "btc_price": btc_price,
        "oil_price": oil_price,
        "alerts": all_alerts,
    }


if __name__ == "__main__":
    import sys
    only_new = "--all" not in sys.argv  # --all 显示所有，否则只显示新增
    result = run_monitor(verbose=True, only_new=only_new)
    if result["alerts"]:
        print(f"🚨 {len(result['alerts'])} 条高优先级消息需要关注！")
