"""
MODULE 9 — ACLED Conflict Event Monitor
Ground truth for actual conflict events worldwide.
Requires free account at acleddata.com — set credentials in config.py.
Falls back to recent public data if no credentials.
"""

import requests
from datetime import datetime, timezone, timedelta
import hashlib
import telegram_client as tg

ACLED_API = "https://acleddata.com/api/acled/read.json"
HEADERS = {"User-Agent": "PolymarketBot/1.0"}

_alerted_hashes: set = set()

# Countries relevant to Polymarket geopolitical markets
WATCHED_COUNTRIES = [
    "Russia", "Ukraine", "Israel", "Palestine", "Iran",
    "China", "Taiwan", "North Korea", "Syria", "Lebanon",
    "Yemen", "Myanmar", "Sudan", "Libya", "Somalia",
    "Venezuela", "Haiti", "Pakistan", "Afghanistan"
]

# High-signal event types
HIGH_SIGNAL_EVENTS = [
    "Battles", "Explosions/Remote violence",
    "Violence against civilians", "Strategic developments"
]


def _get_acled_token() -> str:
    """Get ACLED OAuth token if credentials available."""
    try:
        from config import ACLED_EMAIL, ACLED_PASSWORD
        if not ACLED_EMAIL or ACLED_EMAIL == "YOUR_ACLED_EMAIL":
            return ""
        r = requests.post("https://acleddata.com/api/auth/oauth/token",
            data={
                "grant_type": "password",
                "username": ACLED_EMAIL,
                "password": ACLED_PASSWORD,
                "client_id": "acled"
            }, timeout=15)
        if r.ok:
            return r.json().get("access_token", "")
    except (ImportError, AttributeError):
        pass
    except Exception as e:
        print(f"[ACLED] Auth error: {e}")
    return ""


def _fetch_events(countries: list = None, days_back: int = 3, limit: int = 50) -> list:
    """Fetch conflict events from ACLED API."""
    token = _get_acled_token()
    if not token:
        print("[ACLED] No credentials — using public fallback")
        return _fetch_public_fallback()

    date_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    params = {
        "event_date": date_from,
        "limit": limit,
    }
    if countries:
        params["country"] = "|".join(countries)

    headers = {**HEADERS, "Authorization": f"Bearer {token}"}

    try:
        r = requests.get(ACLED_API, params=params, headers=headers, timeout=20)
        if r.ok:
            data = r.json()
            return data.get("data", [])
    except Exception as e:
        print(f"[ACLED] Fetch error: {e}")
    return []


def _fetch_public_fallback() -> list:
    """
    Fallback: use GDELT to get conflict event data when ACLED credentials not available.
    """
    try:
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": '("armed conflict" OR "military clash" OR bombing OR shelling OR airstrike)',
            "mode": "artlist",
            "maxrecords": 15,
            "timespan": "24h",
            "format": "json",
            "sort": "datedesc"
        }, headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            articles = data.get("articles", [])
            # Convert to ACLED-like format
            events = []
            for art in articles:
                events.append({
                    "event_type": "Conflict Report",
                    "country": art.get("sourcecountry", "Unknown"),
                    "location": "",
                    "fatalities": "",
                    "notes": art.get("title", ""),
                    "source": art.get("domain", ""),
                    "event_date": art.get("seendate", "")[:10] if art.get("seendate") else ""
                })
            return events
    except Exception as e:
        print(f"[ACLED] Fallback error: {e}")
    return []


def run_acled_check():
    """Check for new high-signal conflict events."""
    print("[ACLED] Checking conflict events...")
    events = _fetch_events(countries=WATCHED_COUNTRIES, days_back=2, limit=30)
    alerts_sent = 0

    for evt in events:
        event_type = evt.get("event_type", "")
        country = evt.get("country", "Unknown")
        location = evt.get("location", "")
        fatalities = evt.get("fatalities", 0)
        notes = evt.get("notes", "")[:150]
        date = evt.get("event_date", "")

        # Only alert on high-signal events
        if event_type not in HIGH_SIGNAL_EVENTS and "Conflict" not in event_type:
            continue

        # Skip low-fatality events unless strategic
        try:
            fat_count = int(fatalities) if fatalities else 0
        except (ValueError, TypeError):
            fat_count = 0

        if fat_count < 5 and "Strategic" not in event_type and "Conflict" not in event_type:
            continue

        # Dedup
        h = hashlib.md5(f"{date}:{country}:{notes[:50]}".encode()).hexdigest()
        if h in _alerted_hashes:
            continue
        _alerted_hashes.add(h)
        if len(_alerted_hashes) > 500:
            _alerted_hashes.clear()

        fat_str = f"💀 Fatalities: {fat_count}" if fat_count > 0 else ""

        tg.send(
            f"🔴 <b>ACLED — {event_type}</b>\n\n"
            f"📍 {country}" + (f" — {location}" if location else "") + "\n"
            f"📅 {date}\n"
            + (f"{fat_str}\n" if fat_str else "")
            + f"\n📝 {notes}\n\n"
            f"💡 Check Polymarket positions for {country}"
        )
        alerts_sent += 1
        print(f"[ACLED] Alert: {event_type} in {country}")

    print(f"[ACLED] Done. {alerts_sent} alerts sent.")
    return alerts_sent


def acled_briefing() -> str:
    """Build an ACLED conflict briefing for the /conflicts command."""
    events = _fetch_events(countries=WATCHED_COUNTRIES, days_back=3, limit=50)

    if not events:
        return "🔴 <b>ACLED CONFLICT BRIEFING</b>\n\nNo data available. Add ACLED credentials to config.py for full access."

    # Group by country
    by_country = {}
    for evt in events:
        country = evt.get("country", "Unknown")
        if country not in by_country:
            by_country[country] = []
        by_country[country].append(evt)

    lines = ["🔴 <b>ACLED CONFLICT BRIEFING (72h)</b>\n"]

    for country, evts in sorted(by_country.items(), key=lambda x: -len(x[1])):
        total_fat = 0
        event_types = set()
        for e in evts:
            try:
                total_fat += int(e.get("fatalities", 0) or 0)
            except (ValueError, TypeError):
                pass
            event_types.add(e.get("event_type", ""))

        lines.append(
            f"\n📍 <b>{country}</b> — {len(evts)} events"
            + (f", {total_fat} fatalities" if total_fat > 0 else "")
        )
        for et in list(event_types)[:3]:
            count = sum(1 for e in evts if e.get("event_type") == et)
            lines.append(f"  • {et}: {count}")

    lines.append(f"\n💡 Cross-reference with /scan for market impact")
    return "\n".join(lines)
