"""
MODULE 7 — GDELT Narrative Detection
Real-time geopolitical narrative monitoring via GDELT DOC 2.0 API.
Free, no API key needed. Updates every 15 min.
"""

import requests
import hashlib
import telegram_client as tg

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
HEADERS = {"User-Agent": "PolymarketBot/1.0"}

_alerted_hashes: set = set()

# Queries that map to Polymarket geopolitical themes
NARRATIVE_QUERIES = [
    {
        "label": "Military Conflict",
        "query": '("military strike" OR "military operation" OR "armed conflict" OR invasion OR "troops deployed")',
        "emoji": "⚔️"
    },
    {
        "label": "Nuclear Threat",
        "query": '("nuclear weapon" OR "nuclear test" OR "nuclear threat" OR "nuclear escalation")',
        "emoji": "☢️"
    },
    {
        "label": "Sanctions & Trade War",
        "query": '("new sanctions" OR "trade war" OR "economic sanctions" OR embargo OR tariff)',
        "emoji": "🚫"
    },
    {
        "label": "Regime Change / Coup",
        "query": '(coup OR "regime change" OR "government overthrown" OR "martial law" OR revolution)',
        "emoji": "🏴"
    },
    {
        "label": "Ceasefire & Peace",
        "query": '(ceasefire OR "peace deal" OR "peace agreement" OR "ceasefire agreement" OR truce)',
        "emoji": "🕊️"
    },
    {
        "label": "Election Crisis",
        "query": '("election fraud" OR "contested election" OR "election crisis" OR "election result")',
        "emoji": "🗳️"
    }
]


def _fetch_articles(query: str, timespan: str = "1h", max_records: int = 10) -> list:
    """Fetch articles from GDELT DOC 2.0 API."""
    try:
        r = requests.get(GDELT_API, params={
            "query": query,
            "mode": "artlist",
            "maxrecords": max_records,
            "timespan": timespan,
            "format": "json",
            "sort": "datedesc"
        }, headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data.get("articles", [])
    except Exception as e:
        print(f"[GDELT] API error: {e}")
    return []


def _is_high_signal(title: str) -> bool:
    """Filter for high-signal headlines only."""
    high_signal = [
        "breaking", "confirmed", "official", "emergency", "attack",
        "strike", "invasion", "sanctions", "nuclear", "ceasefire",
        "troops", "missile", "bomb", "war", "coup", "collapse",
        "unprecedented", "escalation", "retaliation", "deployed"
    ]
    title_lower = title.lower()
    return any(word in title_lower for word in high_signal)


def run_gdelt_check():
    """Check GDELT for breaking geopolitical narratives."""
    print(f"[GDELT] Scanning narratives...")
    alerts_sent = 0

    for nq in NARRATIVE_QUERIES:
        articles = _fetch_articles(nq["query"], timespan="1h", max_records=5)

        for art in articles:
            title = art.get("title", "").strip()
            url = art.get("url", "")
            domain = art.get("domain", "")

            if not title or not _is_high_signal(title):
                continue

            # Dedup
            h = hashlib.md5(title[:80].encode()).hexdigest()
            if h in _alerted_hashes:
                continue
            _alerted_hashes.add(h)

            # Keep set bounded
            if len(_alerted_hashes) > 500:
                _alerted_hashes.clear()

            tg.send(
                f"{nq['emoji']} <b>GDELT — {nq['label']}</b>\n\n"
                f"📰 <b>{title[:120]}</b>\n"
                f"🌐 Source: {domain}\n"
                f"🔗 <a href=\"{url}\">Read Article</a>\n\n"
                f"💡 Check if this impacts your Polymarket positions"
            )
            alerts_sent += 1
            print(f"[GDELT] Alert: {title[:60]}")

    print(f"[GDELT] Done. {alerts_sent} alerts sent.")
    return alerts_sent


def gdelt_briefing() -> str:
    """Build a narrative briefing for the /gdelt command."""
    lines = ["🌍 <b>GDELT NARRATIVE BRIEFING</b>\n"]

    for nq in NARRATIVE_QUERIES:
        articles = _fetch_articles(nq["query"], timespan="24h", max_records=3)
        if articles:
            lines.append(f"\n{nq['emoji']} <b>{nq['label']}</b>")
            for art in articles[:2]:
                title = art.get("title", "")[:90]
                domain = art.get("domain", "")
                lines.append(f"  • {title} ({domain})")
        else:
            lines.append(f"\n{nq['emoji']} <b>{nq['label']}</b> — quiet")

    lines.append(f"\n💡 Use /research <url> on any market affected")
    return "\n".join(lines)
