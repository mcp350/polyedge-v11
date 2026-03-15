"""
MODULE 11 — UN Security Council Monitor
Emergency sessions = major geopolitical events incoming.
Monitors UN press releases and UNSC meeting schedules.
No API key needed — RSS/web scraping.
"""

import requests
import re
import hashlib
import telegram_client as tg

HEADERS = {"User-Agent": "PolymarketBot/1.0"}

_alerted_hashes: set = set()

# UN RSS feeds
UN_FEEDS = [
    {
        "name": "UN News — Peace & Security",
        "url": "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml",
        "emoji": "🇺🇳"
    },
    {
        "name": "UN Security Council Press",
        "url": "https://press.un.org/en/un-press-releases/security-council/rss.xml",
        "emoji": "🏛️"
    },
    {
        "name": "UN News — Law & Crime Prevention",
        "url": "https://news.un.org/feed/subscribe/en/news/topic/law-and-crime-prevention/feed/rss.xml",
        "emoji": "⚖️"
    }
]

# High-signal keywords for UNSC activity
EMERGENCY_KEYWORDS = [
    "emergency session", "emergency meeting", "urgent meeting",
    "emergency briefing", "resolution", "veto", "condemns",
    "demands", "sanctions", "peacekeeping", "ceasefire",
    "humanitarian crisis", "threat to peace", "chapter vii",
    "military action", "deployment", "intervention",
    "nuclear", "chemical weapons", "biological weapons",
    "genocide", "war crimes", "crimes against humanity"
]

# Ultra-high signal — instant alert
CRITICAL_KEYWORDS = [
    "emergency session", "emergency meeting", "chapter vii",
    "nuclear", "genocide", "chemical weapons", "veto"
]


def _parse_un_rss(url: str) -> list:
    """Parse UN RSS feed."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return []

        items = []
        for match in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            item_xml = match.group(1)

            title_match = re.search(
                r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>",
                item_xml
            )
            link_match = re.search(r"<link>(.*?)</link>", item_xml)
            desc_match = re.search(
                r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>",
                item_xml, re.DOTALL
            )

            title = ""
            if title_match:
                title = (title_match.group(1) or title_match.group(2) or "").strip()
            link = link_match.group(1).strip() if link_match else ""
            desc = ""
            if desc_match:
                desc = (desc_match.group(1) or desc_match.group(2) or "").strip()
                desc = re.sub(r"<[^>]+>", "", desc)[:200]

            if title:
                items.append({"title": title, "link": link, "description": desc})

        return items[:15]
    except Exception as e:
        print(f"[UNSC] RSS error: {e}")
        return []


def _signal_level(title: str, description: str = "") -> str:
    """Determine signal level: critical, high, or low."""
    text = (title + " " + description).lower()

    if any(kw in text for kw in CRITICAL_KEYWORDS):
        return "critical"
    if any(kw in text for kw in EMERGENCY_KEYWORDS):
        return "high"
    return "low"


def run_unsc_check():
    """Monitor UNSC for emergency sessions and significant resolutions."""
    print("[UNSC] Checking UN Security Council activity...")
    alerts_sent = 0

    for feed in UN_FEEDS:
        items = _parse_un_rss(feed["url"])

        for item in items:
            title = item["title"]
            link = item["link"]
            desc = item.get("description", "")

            level = _signal_level(title, desc)
            if level == "low":
                continue

            # Dedup
            h = hashlib.md5(title[:80].encode()).hexdigest()
            if h in _alerted_hashes:
                continue
            _alerted_hashes.add(h)

            if len(_alerted_hashes) > 300:
                _alerted_hashes.clear()

            if level == "critical":
                emoji = "🚨"
                label = "CRITICAL — EMERGENCY SESSION"
            else:
                emoji = "🏛️"
                label = "UNSC ACTIVITY"

            msg = (
                f"{emoji} <b>{label}</b>\n\n"
                f"📰 <b>{title[:120]}</b>\n"
            )
            if desc:
                msg += f"\n{desc[:150]}\n"
            if link:
                msg += f"\n🔗 <a href=\"{link}\">Read Full</a>\n"
            msg += f"\n💡 Emergency UNSC sessions often precede major market moves"

            tg.send(msg)
            alerts_sent += 1
            print(f"[UNSC] {'🚨 CRITICAL' if level == 'critical' else 'Alert'}: {title[:60]}")

    print(f"[UNSC] Done. {alerts_sent} alerts sent.")
    return alerts_sent


def unsc_briefing() -> str:
    """Build a UNSC activity briefing for the /unsc command."""
    lines = ["🇺🇳 <b>UN SECURITY COUNCIL BRIEFING</b>\n"]

    for feed in UN_FEEDS:
        items = _parse_un_rss(feed["url"])
        if not items:
            lines.append(f"\n{feed['emoji']} <b>{feed['name']}</b> — no data")
            continue

        lines.append(f"\n{feed['emoji']} <b>{feed['name']}</b>")
        for item in items[:3]:
            level = _signal_level(item["title"], item.get("description", ""))
            marker = "🚨" if level == "critical" else ("⚠️" if level == "high" else "  ")
            lines.append(f"  {marker} {item['title'][:80]}")

    lines.append(f"\n🚨 = emergency/critical | ⚠️ = significant")
    return "\n".join(lines)
