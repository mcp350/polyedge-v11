"""
MODULE 10 — Al Jazeera + RFE/RL + Non-Western Media RSS
Fills Western media blind spots with alternative news sources.
No API key needed — pure RSS feeds.
"""

import requests
import re
import hashlib
import telegram_client as tg

HEADERS = {"User-Agent": "PolymarketBot/1.0"}

_alerted_hashes: set = set()

# RSS feeds that cover blind spots Western media misses
RSS_FEEDS = [
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "emoji": "🟢",
        "focus": "Middle East, Global South"
    },
    {
        "name": "RFE/RL",
        "url": "https://www.rferl.org/api/z-pqpiev-qpp",
        "emoji": "🔵",
        "focus": "Russia, Central Asia, Eastern Europe"
    },
    {
        "name": "Al Jazeera English (Politics)",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "emoji": "🟢",
        "focus": "Global politics"
    },
    {
        "name": "TASS (English)",
        "url": "https://tass.com/rss/v2.xml",
        "emoji": "🟤",
        "focus": "Russian perspective"
    },
    {
        "name": "Xinhua",
        "url": "http://www.xinhuanet.com/english/rss/worldrss.xml",
        "emoji": "🔴",
        "focus": "Chinese perspective"
    }
]

# Keywords that signal market-moving events
TRIGGER_KEYWORDS = [
    "breaking", "attack", "strike", "invasion", "ceasefire", "troops",
    "missile", "nuclear", "sanctions", "war", "bomb", "military",
    "emergency", "coup", "collapse", "killed", "explosion", "airstrike",
    "drone", "blockade", "escalation", "retaliation", "offensive",
    "withdrawal", "peace deal", "agreement", "summit", "tariff",
    "crypto", "bitcoin", "regulation", "sec", "ban"
]


def _parse_rss(url: str) -> list:
    """Parse an RSS feed and return list of {title, link, pubDate}."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if not r.ok:
            return []

        items = []
        # Simple XML parsing without lxml
        for item_match in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            item_xml = item_match.group(1)

            title_match = re.search(
                r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>",
                item_xml
            )
            link_match = re.search(r"<link>(.*?)</link>", item_xml)

            title = ""
            if title_match:
                title = (title_match.group(1) or title_match.group(2) or "").strip()
            link = link_match.group(1).strip() if link_match else ""

            if title:
                items.append({"title": title, "link": link})

        return items[:10]
    except Exception as e:
        print(f"[RSS] Error fetching {url}: {e}")
        return []


def _is_market_relevant(title: str) -> bool:
    """Check if headline could move prediction markets."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in TRIGGER_KEYWORDS)


def run_rss_check():
    """Check all non-Western RSS feeds for market-moving headlines."""
    print("[RSS] Checking alternative news sources...")
    alerts_sent = 0

    for feed in RSS_FEEDS:
        items = _parse_rss(feed["url"])

        for item in items:
            title = item["title"]
            link = item["link"]

            if not _is_market_relevant(title):
                continue

            # Dedup
            h = hashlib.md5(title[:80].encode()).hexdigest()
            if h in _alerted_hashes:
                continue
            _alerted_hashes.add(h)

            if len(_alerted_hashes) > 500:
                _alerted_hashes.clear()

            tg.send(
                f"{feed['emoji']} <b>{feed['name']}</b> — {feed['focus']}\n\n"
                f"📰 <b>{title[:120]}</b>\n"
                + (f"🔗 <a href=\"{link}\">Read</a>\n" if link else "")
                + f"\n💡 Western media may not cover this yet"
            )
            alerts_sent += 1
            print(f"[RSS] Alert from {feed['name']}: {title[:60]}")

    print(f"[RSS] Done. {alerts_sent} alerts sent.")
    return alerts_sent


def rss_briefing() -> str:
    """Build a non-Western media briefing for the /intel command."""
    lines = ["📡 <b>ALTERNATIVE MEDIA INTEL</b>\n"]

    for feed in RSS_FEEDS:
        items = _parse_rss(feed["url"])
        if not items:
            lines.append(f"\n{feed['emoji']} <b>{feed['name']}</b> — unavailable")
            continue

        lines.append(f"\n{feed['emoji']} <b>{feed['name']}</b> ({feed['focus']})")
        for item in items[:3]:
            title = item["title"][:80]
            relevance = "🔥" if _is_market_relevant(item["title"]) else "  "
            lines.append(f"  {relevance} {title}")

    lines.append(f"\n🔥 = potential market impact")
    return "\n".join(lines)
