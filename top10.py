"""
TOP 10 PICKS — Geopolitical "by DATE" Events with NO Edge
Pipeline:
1. Scan Polymarket events API for geopolitical date-based events
2. Filter for active sub-markets with genuine uncertainty
3. Compare with Manifold Markets (Metaculus is dead, requires auth)
4. AI researches and forecasts each pick — focus on NO edge
"""

import time
import re
import json
import requests
from datetime import datetime, timezone
import polymarket_api as api
from config import ANTHROPIC_API_KEY
import prediction_store as pstore

HEADERS = {"User-Agent": "PolymarketBot/2.0"}

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

# Geopolitical keywords for filtering events
GEO_KEYWORDS = [
    "russia", "ukraine", "capture", "ceasefire", "war", "israel", "hamas",
    "iran", "china", "taiwan", "nato", "invasion", "troops", "missile",
    "sanctions", "tariff", "conflict", "military", "strike", "bomb",
    "nuclear", "regime", "coup", "annexe", "occupy", "attack",
    "hezbollah", "gaza", "crimea", "donbas", "kursk", "syria",
    "north korea", "kim jong", "trump", "putin", "xi jinping",
    "greenland", "panama", "strait of hormuz", "south china sea",
]

# Pattern to detect "by [DATE]" markets
DATE_PATTERN = re.compile(
    r'by\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d+',
    re.IGNORECASE
)

# Meme/garbage exclusions
EXCLUDED_KEYWORDS = [
    "jesus", "christ", "god ", "bible", "gta vi", "gta 6",
    "alien", "ufo", "flat earth", "zombie", "apocalypse",
    "santa claus", "bigfoot", "meme", "doge", "pepe",
    "celebrity death", "die before", "return before",
    "girlfriend", "boyfriend", "tiktok",
]


# ═══════════════════════════════════════════════════════════════════════
# STEP 1: Fetch geopolitical "by DATE" events from Polymarket
# ═══════════════════════════════════════════════════════════════════════

def _is_geo_event(title):
    """Check if event title is geopolitical."""
    t = title.lower()
    return any(kw in t for kw in GEO_KEYWORDS)


def _is_excluded(title):
    """Check if event is meme/garbage."""
    t = title.lower()
    return any(kw in t for kw in EXCLUDED_KEYWORDS)


def _has_date_markets(markets):
    """Check if event has 'by [DATE]' sub-markets."""
    for m in markets:
        q = m.get("question", "")
        if DATE_PATTERN.search(q):
            return True
    return False


def _parse_sub_market(raw_market):
    """Parse a sub-market from an event, check if active."""
    try:
        question = raw_market.get("question", "")
        end_date_str = raw_market.get("endDate") or raw_market.get("end_date_iso") or ""
        volume = float(raw_market.get("volume", 0) or 0)
        liquidity = float(raw_market.get("liquidity", 0) or 0)

        outcome_prices = raw_market.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            try:
                prices = json.loads(outcome_prices)
            except:
                prices = []
        else:
            prices = outcome_prices or []

        if len(prices) < 2:
            return None

        yes_price = float(prices[0])
        no_price = float(prices[1])

        # Calculate days left
        days_left = None
        is_expired = False
        now = datetime.now(timezone.utc)
        if end_date_str:
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                days_left = max(0, (end_dt - now).days)
                if end_dt <= now:
                    is_expired = True
            except:
                pass

        # Check closed/archived flags
        if raw_market.get("closed") in [True, "true"]:
            is_expired = True
        if raw_market.get("active") in [False, "false"]:
            is_expired = True

        # Markets at 0% or 100% are resolved
        if yes_price <= 0.02 or yes_price >= 0.98:
            is_expired = True

        slug = raw_market.get("slug", "")
        market_id = raw_market.get("id") or raw_market.get("conditionId", "")

        return {
            "id": market_id,
            "question": question,
            "yes_price": round(yes_price, 4),
            "no_price": round(no_price, 4),
            "volume": volume,
            "liquidity": liquidity,
            "end_date": end_date_str,
            "days_left": days_left,
            "is_expired": is_expired,
            "slug": slug,
            "url": f"https://polymarket.com/event/{slug}" if slug else "",
        }
    except Exception as e:
        print(f"[TOP10] Parse error: {e}")
        return None


def fetch_geo_date_events():
    """
    Fetch geopolitical events with 'by DATE' sub-markets.
    Returns list of events, each with their active sub-markets.
    """
    events = []
    seen_slugs = set()

    for page in range(4):
        try:
            r = requests.get(f"{api.GAMMA_BASE}/events", params={
                "limit": 100, "offset": page * 100,
                "active": "true", "closed": "false",
                "order": "volume", "ascending": "false"
            }, headers=HEADERS, timeout=15)

            if not r.ok:
                break

            raw_events = r.json()
            if not raw_events:
                break

            for event in raw_events:
                title = event.get("title") or event.get("question") or ""
                slug = event.get("slug", "")

                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                # Filter: must be geopolitical
                if not _is_geo_event(title):
                    continue

                # Filter: not garbage
                if _is_excluded(title):
                    continue

                raw_markets = event.get("markets", [])

                # Filter: must have multiple sub-markets with "by DATE"
                if len(raw_markets) < 2:
                    continue
                if not _has_date_markets(raw_markets):
                    continue

                # Parse all sub-markets
                parsed = []
                for rm in raw_markets:
                    p = _parse_sub_market(rm)
                    if p:
                        parsed.append(p)

                # Need at least 1 active sub-market
                active = [m for m in parsed if not m["is_expired"]]
                if not active:
                    continue

                # Total volume
                total_vol = sum(m["volume"] for m in parsed)

                events.append({
                    "title": title,
                    "slug": slug,
                    "url": f"https://polymarket.com/event/{slug}",
                    "total_volume": total_vol,
                    "all_markets": parsed,
                    "active_markets": active,
                    "expired_markets": [m for m in parsed if m["is_expired"]],
                })

        except Exception as e:
            print(f"[TOP10] Fetch events page {page}: {e}")
            break
        time.sleep(0.3)

    events.sort(key=lambda x: x.get("total_volume") or 0, reverse=True)
    print(f"[TOP10] Found {len(events)} geopolitical 'by DATE' events")
    return events


# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Manifold Markets comparison (replaces dead Metaculus)
# ═══════════════════════════════════════════════════════════════════════

def _search_manifold(query, limit=3):
    """Search Manifold Markets for matching predictions."""
    try:
        r = requests.get("https://api.manifold.markets/v0/search-markets", params={
            "term": query[:80],
            "limit": limit,
            "sort": "liquidity",
            "filter": "open"
        }, headers=HEADERS, timeout=10)
        if r.ok:
            markets = r.json()
            matches = []
            for m in markets:
                if m.get("outcomeType") == "BINARY":
                    prob = m.get("probability", 0)
                    liq = m.get("totalLiquidity", 0)
                    matches.append({
                        "title": m.get("question", ""),
                        "forecast": round(prob * 100, 1),
                        "liquidity": liq,
                        "url": m.get("url", ""),
                        "source": "Manifold"
                    })
            return matches
    except Exception as e:
        print(f"[TOP10/MANIF] {e}")
    return []


def get_forecaster_comparison(event_title):
    """Get Manifold Markets comparison for an event."""
    # Extract key search terms
    search = re.sub(r'by\.\.\.(\?)?$', '', event_title).strip()
    search = re.sub(r'\s+', ' ', search)[:80]

    manifold = _search_manifold(search, limit=3)

    return {
        "manifold": manifold,
        "has_data": len(manifold) > 0
    }


# ═══════════════════════════════════════════════════════════════════════
# STEP 3: Score events for TOP 10
# ═══════════════════════════════════════════════════════════════════════

def score_event(event, forecaster_data):
    """
    Score an event for TOP 10 ranking.
    Prioritizes: NO edge potential, uncertainty, volume, time sensitivity.
    """
    score = 0
    active = event["active_markets"]
    total_vol = event["total_volume"]

    # Volume score (0-20)
    if total_vol >= 10_000_000:
        score += 20
    elif total_vol >= 5_000_000:
        score += 16
    elif total_vol >= 1_000_000:
        score += 12
    elif total_vol >= 500_000:
        score += 8
    else:
        score += 4

    # NO edge potential (0-30) — MOST IMPORTANT
    # Look for sub-markets where YES is 55-90% (NO is cheap at 10-45%)
    best_no_edge = 0
    for m in active:
        yes = m["yes_price"]
        if 0.55 <= yes <= 0.90:
            # Sweet spot: YES is overpriced, NO is the value bet
            edge = (yes - 0.50) * 100  # Higher YES = more NO edge
            if edge > best_no_edge:
                best_no_edge = edge

    if best_no_edge >= 30:
        score += 30
    elif best_no_edge >= 20:
        score += 25
    elif best_no_edge >= 10:
        score += 18
    elif best_no_edge >= 5:
        score += 10
    else:
        score += 3

    # Uncertainty spread (0-20) — events with mix of uncertain markets
    uncertain_count = sum(1 for m in active if 0.20 <= m["yes_price"] <= 0.80)
    if uncertain_count >= 3:
        score += 20
    elif uncertain_count >= 2:
        score += 15
    elif uncertain_count >= 1:
        score += 10
    else:
        score += 3

    # Time sensitivity (0-15) — nearest deadline
    nearest_days = min((m.get("days_left") or 999 for m in active), default=999)
    if 3 <= nearest_days <= 14:
        score += 15  # Sweet spot
    elif 14 < nearest_days <= 30:
        score += 12
    elif 1 <= nearest_days < 3:
        score += 8
    elif 30 < nearest_days <= 60:
        score += 5
    else:
        score += 2

    # Forecaster divergence (0-15)
    if forecaster_data and forecaster_data["has_data"]:
        manifold = forecaster_data.get("manifold", [])
        if manifold:
            # Compare nearest active market with Manifold
            for m in active:
                poly_yes = m["yes_price"] * 100
                for f in manifold:
                    div = abs(poly_yes - f["forecast"])
                    if div >= 15:
                        score += 15
                        break
                    elif div >= 10:
                        score += 10
                        break
                    elif div >= 5:
                        score += 7
                        break
                if score > 0:
                    break

    return score


def select_top10(events):
    """Score all events and select TOP 10."""
    scored = []

    for i, ev in enumerate(events[:25]):
        print(f"[TOP10] Scoring {i+1}/{min(25, len(events))}: {ev['title'][:50]}...")
        forecaster = get_forecaster_comparison(ev["title"])
        s = score_event(ev, forecaster)
        scored.append({
            "event": ev,
            "score": s,
            "forecasters": forecaster
        })
        time.sleep(0.3)

    scored.sort(key=lambda x: x.get("score") or 0, reverse=True)
    print(f"[TOP10] Selected top 10 from {len(scored)} scored events")
    return scored[:10]


# ═══════════════════════════════════════════════════════════════════════
# STEP 4: AI Research + Forecast
# ═══════════════════════════════════════════════════════════════════════

def _ai_research_top10(top10_data):
    """AI analyzes all 10 events and makes forecasts with NO edge focus."""
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        return ""

    picks_text = ""
    for i, item in enumerate(top10_data):
        ev = item["event"]
        f = item["forecasters"]
        vol_str = f"${ev['total_volume']/1_000_000:.1f}M" if ev["total_volume"] >= 1_000_000 else f"${ev['total_volume']/1_000:.0f}K"

        picks_text += f"\n--- EVENT #{i+1} (Score: {item['score']}) ---\n"
        picks_text += f"Event: {ev['title']}\n"
        picks_text += f"Volume: {vol_str}\n"

        # Active sub-markets
        picks_text += "Active sub-markets:\n"
        for m in ev["active_markets"]:
            picks_text += f"  - {m['question'][:60]}: YES {m['yes_price']:.0%} / NO {m['no_price']:.0%} ({m.get('days_left', '?')}d left)\n"

        # Expired
        if ev["expired_markets"]:
            picks_text += f"Expired/resolved: {len(ev['expired_markets'])} markets\n"
            for m in ev["expired_markets"][:2]:
                picks_text += f"  - {m['question'][:50]}: YES {m['yes_price']:.0%} (resolved)\n"

        # Forecaster comparison
        for match in f.get("manifold", [])[:2]:
            picks_text += f"  Manifold: {match['forecast']}% — \"{match['title'][:50]}\"\n"

    # Get past accuracy context for self-improving prompts
    accuracy_ctx = pstore.get_accuracy_context()
    accuracy_block = f"\n\n{accuracy_ctx}\nUse this to calibrate your confidence levels.\n" if accuracy_ctx else ""

    prompt = f"""You are an elite geopolitical prediction market analyst specializing in finding NO edge bets — events where Polymarket overprices YES and buying NO is the smart play.
{accuracy_block}
Analyze these TOP 10 geopolitical events with date-based sub-markets:

{picks_text}

For EACH event, analyze ALL active date sub-markets and provide:

[NUMBER]. [EVENT NAME (short)]
PROBABILITY CURVE: [how odds change across dates]
BEST NO BET: [which specific date sub-market has the best NO edge]
NO ENTRY: Buy NO at [price] on [specific market]
AI FORECAST: [your % prediction vs Polymarket]
CATALYST: [what real-world event could make NO win]
ROI IF NO WINS: [calculated return]

After all 10:

TOP 3 NO BETS: [ranked by edge, with specific entry prices]
HIGHEST CONVICTION: [which NO bet has the strongest case]
WARNING: [any event where YES might actually be underpriced]

RULES:
- Focus on NO edge — where markets overestimate YES probability
- Be DECISIVE with specific % forecasts
- Calculate ROI for NO bets (buy NO at X cents, wins at $1 = Y% return)
- Consider real geopolitical intelligence and timing
- If past accuracy data is provided above, LEARN from it: repeat winning patterns, avoid losing patterns
- If calibration biases are noted, actively correct for them in your forecasts
- Max 900 words. Plain text only. No markdown. No disclaimers."""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1800,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )
        if r.ok:
            text = r.json().get("content", [{}])[0].get("text", "")
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'^#{1,3}\s*', '', text, flags=re.MULTILINE)
            return text.strip()
        else:
            print(f"[TOP10/AI] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[TOP10/AI] {e}")
    return ""


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY
# ═══════════════════════════════════════════════════════════════════════

def run_top10() -> str:
    """
    Full TOP 10 pipeline for geopolitical date events with NO edge.
    """
    print("[TOP10] ═══ TOP 10 GEO DATE EVENTS — NO EDGE ═══")
    start = time.time()

    # Step 1: Fetch geo date events
    events = fetch_geo_date_events()
    if not events:
        return "No active geopolitical date events found."

    # Step 2+3: Score with forecaster comparison, select top 10
    top10 = select_top10(events)
    if not top10:
        return "Could not score events."

    # Step 4: AI research and forecast
    print("[TOP10] Running AI research on top 10...")
    ai_text = _ai_research_top10(top10)

    elapsed = round(time.time() - start, 1)
    now = datetime.now(timezone.utc)

    # Log predictions for tracking
    try:
        all_markets_for_logging = []
        for item in top10:
            ev = item["event"]
            forecasters = item.get("forecasters", {})
            manifold = forecasters.get("manifold", [])
            manifold_avg = None
            if manifold:
                manifold_avg = sum(m["forecast"] for m in manifold) / len(manifold) / 100

            for m in ev["active_markets"]:
                m["_event_title"] = ev["title"]
                m["_manifold_yes"] = manifold_avg
                if not m.get("id"):
                    m["id"] = m.get("slug", f"unknown_{m['question'][:20]}")
            all_markets_for_logging.extend(ev["active_markets"])

        if ai_text and all_markets_for_logging:
            pstore.parse_ai_predictions(ai_text, all_markets_for_logging, source="top10")
            print(f"[TOP10] Logged {len(all_markets_for_logging)} market predictions")
    except Exception as e:
        print(f"[TOP10] Prediction logging error: {e}")

    # Build output
    lines = []
    lines.append(f"🏆 <b>TOP 10 GEO EVENTS — NO EDGE</b>")
    lines.append(f"📅 Date-based events | {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"{'━' * 30}")
    lines.append("")

    if ai_text:
        lines.append(ai_text)
    else:
        # Fallback
        for i, item in enumerate(top10):
            ev = item["event"]
            vol_str = f"${ev['total_volume']/1_000_000:.1f}M" if ev["total_volume"] >= 1_000_000 else f"${ev['total_volume']/1_000:.0f}K"
            lines.append(f"\n{i+1}. {ev['title'][:60]}")
            lines.append(f"   Vol: {vol_str} | {len(ev['active_markets'])} active dates")
            for m in ev["active_markets"][:3]:
                signal = "🟢" if m["yes_price"] >= 0.60 else "🟡" if m["yes_price"] >= 0.35 else "🔴"
                lines.append(f"   {signal} {m['question'][:50]} — YES:{m['yes_price']:.0%} NO:{m['no_price']:.0%}")

    # Event links
    lines.append("")
    lines.append(f"{'━' * 30}")
    lines.append(f"🔗 <b>EVENT LINKS</b>")
    medals = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, item in enumerate(top10):
        ev = item["event"]
        t = ev["title"][:45] + ("..." if len(ev["title"]) > 45 else "")
        active = ev["active_markets"]
        best_no = max(active, key=lambda m: m["no_price"]) if active else None
        no_tag = f" (NO@{best_no['no_price']:.0%})" if best_no and best_no["no_price"] >= 0.10 else ""
        lines.append(f'{medals[i]} <a href="{ev["url"]}">{t}</a>{no_tag}')

    lines.append("")
    lines.append(f"{'━' * 30}")
    lines.append(f"📊 Scanned {len(events)} events | {elapsed}s")
    lines.append(f"🔬 Sources: Polymarket + Manifold + Claude AI")
    lines.append(f"💡 /research [event_url] for deep dive on any event")

    return "\n".join(lines)
