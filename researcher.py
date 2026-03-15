"""
MODULE 3 — Divergence Researcher
Compares Polymarket vs Manifold Markets.
Supports EVENTS with multiple date-based sub-markets.
NOTE: Metaculus API requires auth now (dead). Swift Centre returns CSS junk (dead).
"""

import requests
import re
import json
from datetime import datetime, timezone
from config import MIN_DIVERGENCE_EDGE, ANTHROPIC_API_KEY
import polymarket_api as api
import prediction_store as pstore

HEADERS = {"User-Agent": "PolymarketBot/2.0"}


def _search_manifold(query, limit=5):
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
                        "url": f"https://manifold.markets{m.get('url', '')}",
                        "source": "Manifold"
                    })
            return matches
    except Exception as e:
        print(f"[RESEARCHER/MANIF] {e}")
    return []


def _latest_headline(query: str) -> str:
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=HEADERS, timeout=10
        )
        if r.ok and "<item>" in r.text:
            match = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>",
                              r.text[r.text.find("<item>"):])
            if match:
                return (match.group(1) or match.group(2) or "").strip()
    except Exception:
        pass
    return ""


def _keywords(question: str) -> str:
    stop = {"will","the","a","an","by","in","of","to","be","on","at","for","or","and","is","that","its","before"}
    words = [w for w in re.sub(r"[^a-zA-Z0-9 ]", "", question).split()
             if w.lower() not in stop and len(w) > 2]
    return " ".join(words[:6])


def _fetch_event_markets(slug):
    """Fetch ALL sub-markets from a Polymarket event."""
    try:
        r = requests.get(f"{api.GAMMA_BASE}/events", params={"slug": slug},
                         headers=HEADERS, timeout=15)
        if r.ok:
            events = r.json()
            if isinstance(events, list) and events:
                event = events[0]
                event_title = event.get("title", "") or event.get("question", "")
                raw_markets = event.get("markets", [])

                parsed_markets = []
                now = datetime.now(timezone.utc)

                for raw_m in raw_markets:
                    p = api.parse_market(raw_m)
                    if not p:
                        continue

                    is_expired = False
                    end_str = p.get("end_date", "")
                    if end_str:
                        try:
                            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                            if end_dt <= now:
                                is_expired = True
                        except:
                            pass

                    yes = p["yes_price"]
                    if yes <= 0.02 or yes >= 0.98:
                        is_expired = True
                    if raw_m.get("closed") in [True, "true"]:
                        is_expired = True

                    p["_is_expired"] = is_expired
                    parsed_markets.append(p)

                return event_title, parsed_markets
    except Exception as e:
        print(f"[RESEARCHER] Event fetch error: {e}")
    return None, []


def _ai_event_analysis(event_title, markets_data):
    """AI analyzes all date sub-markets with NO edge focus."""
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        return ""

    market_text = ""
    for m in markets_data:
        status = "EXPIRED" if m.get("_is_expired") else "ACTIVE"
        market_text += (
            f"- {m['question'][:70]}\n"
            f"  YES: {m['yes_price']:.0%} | NO: {m['no_price']:.0%} | "
            f"Vol: ${m['volume']/1_000:.0f}K | Days: {m.get('days_left', '?')} | {status}\n"
        )

    accuracy_ctx = pstore.get_accuracy_context()
    accuracy_block = f"\n{accuracy_ctx}\n" if accuracy_ctx else ""

    # Get track record for this specific event type
    event_keywords = [w for w in re.sub(r'[^a-zA-Z ]', '', event_title).split() if len(w) > 3][:5]
    track_record = pstore.get_event_track_record(event_keywords) if event_keywords else ""
    track_block = f"\n{track_record}\n" if track_record else ""

    prompt = f"""You are a geopolitical prediction market analyst. Analyze this event with date-based sub-markets. Focus on finding NO edge — where YES is overpriced.
{accuracy_block}{track_block}
EVENT: {event_title}

SUB-MARKETS:
{market_text}

For each ACTIVE sub-market:
1. Your probability estimate (%)
2. Is YES overpriced or underpriced?
3. TRADE: BUY YES / BUY NO / HOLD with entry price
4. Key catalyst that could move this

Then:
PROBABILITY CURVE: What does the trend across dates tell us?
BEST NO BET: Which date has the best NO edge and why?
BEST VALUE: Which specific trade offers the highest ROI?
KEY RISK: What could invalidate the NO thesis?

Be decisive. Specific % forecasts. Max 400 words. Plain text. No markdown."""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=50
        )
        if r.ok:
            text = r.json().get("content", [{}])[0].get("text", "")
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'^#{1,3}\s*', '', text, flags=re.MULTILINE)
            return text.strip()
    except Exception as e:
        print(f"[RESEARCHER/AI] {e}")
    return ""


def research_market(market_ref: str) -> str:
    """
    Research a market or event.
    If event URL: shows ALL date sub-markets with AI analysis.
    If single market: divergence research with Manifold.
    """
    m = None
    is_event = False
    event_title = ""
    all_event_markets = []

    if "polymarket.com" in market_ref:
        slug = market_ref.rstrip("/").split("/")[-1]

        if "/event/" in market_ref:
            event_title, all_event_markets = _fetch_event_markets(slug)
            if all_event_markets:
                is_event = True

        if not is_event:
            raw = api.get_market_by_slug(slug)
            if raw:
                m = api.parse_market(raw)

    if not m and not is_event:
        raw = api.get_market_by_id(market_ref) or api.get_market_by_slug(market_ref)
        if raw:
            m = api.parse_market(raw)

    if not m and not is_event:
        return "❌ Market not found. Send a Polymarket URL or market ID."

    # Event with multiple sub-markets
    if is_event and len(all_event_markets) > 1:
        return _research_event(event_title, all_event_markets)

    # Single market or event with 1 market
    if not m and is_event and all_event_markets:
        active = [mk for mk in all_event_markets if not mk.get("_is_expired")]
        m = active[0] if active else all_event_markets[0]

    if not m:
        return "❌ Could not parse market data."

    return _research_single(m)


def _research_event(event_title, markets):
    """Research event with multiple date sub-markets."""
    active_markets = sorted(
        [m for m in markets if not m.get("_is_expired")],
        key=lambda m: datetime.fromisoformat(m.get("end_date", "2099-01-01").replace("Z", "+00:00"))
            if m.get("end_date") else datetime(2099, 1, 1, tzinfo=timezone.utc)
    )
    expired_markets = [m for m in markets if m.get("_is_expired")]

    kw = _keywords(event_title)

    lines = []
    lines.append(f"🔬 <b>EVENT RESEARCH</b>\n")
    lines.append(f"📌 <b>{event_title[:80]}</b>")
    lines.append(f"📊 {len(active_markets)} active | {len(expired_markets)} expired")

    total_vol = sum(m["volume"] for m in markets)
    vol_str = f"${total_vol/1_000_000:.1f}M" if total_vol >= 1_000_000 else f"${total_vol/1_000:.0f}K"
    lines.append(f"💰 Total volume: {vol_str}")
    lines.append("")

    # Active sub-markets
    lines.append(f"{'━' * 30}")
    lines.append(f"📅 <b>ACTIVE DATE MARKETS</b>")
    lines.append("")

    for i, m in enumerate(active_markets):
        yes = m["yes_price"]
        no = m["no_price"]
        vol = m["volume"]
        days = m.get("days_left", "?")

        vol_str = f"${vol/1_000:.0f}K" if vol < 1_000_000 else f"${vol/1_000_000:.1f}M"

        if yes >= 0.70:
            signal = "🟢 LIKELY"
        elif yes >= 0.40:
            signal = "🟡 UNCERTAIN"
        else:
            signal = "🔴 UNLIKELY"

        # NO edge indicator
        no_edge = ""
        if 0.55 <= yes <= 0.92:
            roi = ((1.0 / no) - 1) * 100 if no > 0 else 0
            no_edge = f"\n   💎 <b>NO edge:</b> Buy NO at {no:.0%} → {roi:.0f}% ROI if NO wins"

        lines.append(f"<b>{i+1}. {m['question'][:70]}</b>")
        lines.append(f"   {signal} — YES: <b>{yes:.0%}</b> | NO: <b>{no:.0%}</b>")
        lines.append(f"   Vol: {vol_str} | ⏳ {days} days left{no_edge}")
        lines.append("")

    # Expired
    if expired_markets:
        lines.append(f"{'━' * 30}")
        lines.append(f"⏹ <b>EXPIRED/RESOLVED</b>")
        for m in expired_markets[:4]:
            q = m["question"][:50]
            yes = m["yes_price"]
            result = "YES ✅" if yes >= 0.95 else "NO ❌" if yes <= 0.05 else f"{yes:.0%}"
            lines.append(f"  ⏹ {q} — {result}")
        lines.append("")

    # Manifold comparison
    lines.append(f"{'━' * 30}")
    lines.append(f"<b>Manifold Markets:</b>")
    manifold = _search_manifold(kw, limit=3)
    if manifold:
        for match in manifold[:3]:
            lines.append(f"  📊 <b>{match['forecast']}%</b> — {match['title'][:55]}")
    else:
        lines.append("  📊 No matching Manifold markets found")

    # News
    headline = _latest_headline(kw)
    if headline:
        lines.append(f"\n📰 Latest: {headline[:100]}")

    # AI analysis
    print("[RESEARCHER] Running AI event analysis...")
    ai_text = _ai_event_analysis(event_title, markets)
    if ai_text:
        lines.append(f"\n{'━' * 30}")
        lines.append(f"🧠 <b>AI ANALYSIS</b>")
        lines.append("")
        lines.append(ai_text)

    # Log predictions
    try:
        manifold_avg = None
        if manifold:
            manifold_avg = sum(m["forecast"] for m in manifold) / len(manifold) / 100
        for m in active_markets:
            m["_event_title"] = event_title
            m["_manifold_yes"] = manifold_avg
            if not m.get("id"):
                m["id"] = m.get("slug", f"research_{m['question'][:20]}")
        if ai_text:
            pstore.parse_ai_predictions(ai_text, active_markets, source="researcher")
    except Exception as e:
        print(f"[RESEARCHER] Prediction logging error: {e}")

    # Trade links
    lines.append(f"\n{'━' * 30}")
    lines.append(f"🔗 <b>TRADE LINKS</b>")
    for m in active_markets[:6]:
        q = m["question"][:45] + ("..." if len(m["question"]) > 45 else "")
        url = m.get("url", "")
        if url:
            lines.append(f'  📅 <a href="{url}">{q}</a> (NO@{m["no_price"]:.0%})')

    return "\n".join(lines)


def _research_single(parsed):
    """Research a single market with Manifold comparison."""
    q         = parsed["question"]
    yes_p     = parsed["yes_price"]
    no_p      = parsed["no_price"]
    vol       = parsed["volume"]
    days      = parsed.get("days_left", "?")
    url       = parsed["url"]
    kw        = _keywords(q)

    lines = [
        f"🔬 <b>DIVERGENCE RESEARCH</b>\n",
        f"📌 <b>{q[:80]}</b>",
        f"YES: <b>{yes_p:.0%}</b>  |  NO: <b>{no_p:.0%}</b>",
        f"Vol: ${vol/1000:.0f}K  |  {days} days left\n",
        "<b>Forecaster Comparison:</b>"
    ]

    forecaster_probs = []

    # Manifold Markets
    manifold = _search_manifold(kw, limit=3)
    if manifold:
        for match in manifold[:3]:
            lines.append(f"  📊 Manifold: <b>{match['forecast']}%</b> YES")
            lines.append(f"     ↳ {match['title'][:55]}")
            forecaster_probs.append(match["forecast"])
    else:
        lines.append("  📊 Manifold: no matching markets found")

    # News
    headline = _latest_headline(kw)
    if headline:
        lines.append(f"\n📰 Latest: {headline[:100]}")

    # Divergence
    if forecaster_probs:
        avg       = sum(forecaster_probs) / len(forecaster_probs)
        mkt_yes   = yes_p * 100
        div       = mkt_yes - avg
        abs_div   = abs(div)

        lines.append(f"\n<b>Divergence:</b>")
        lines.append(f"  Manifold avg: <b>{avg:.0f}%</b>  |  Polymarket: <b>{mkt_yes:.0f}%</b>")
        lines.append(f"  Gap: <b>{'+'if div>0 else ''}{div:.0f} points</b> "
                     f"({'Poly overprices YES' if div > 0 else 'Poly underprices YES'})")

        if abs_div >= 15:
            if div > 0:
                lines.append(f"\n⚡ <b>NO EDGE — {abs_div:.0f}pt gap</b>")
                roi = ((1.0 / no_p) - 1) * 100 if no_p > 0 else 0
                lines.append(f"  Buy NO at {no_p:.0%} → {roi:.0f}% ROI if NO wins")
            else:
                lines.append(f"\n⚡ <b>YES EDGE — {abs_div:.0f}pt gap</b>")
        elif abs_div >= MIN_DIVERGENCE_EDGE:
            lines.append(f"\n✅ <b>MODERATE EDGE</b> — {abs_div:.0f}pt gap")
        else:
            lines.append(f"\n⚪ LOW EDGE — {abs_div:.0f}pt gap")
    else:
        lines.append("\n⚪ No forecaster data. Manual analysis needed.")

    # Log prediction for single market
    try:
        manifold_avg = None
        if forecaster_probs:
            manifold_avg = sum(forecaster_probs) / len(forecaster_probs) / 100

        recommendation = "HOLD"
        if forecaster_probs:
            avg = sum(forecaster_probs) / len(forecaster_probs)
            div = (yes_p * 100) - avg
            if div >= 15:
                recommendation = "BUY_NO"
            elif div <= -15:
                recommendation = "BUY_YES"

        pstore.log_prediction(
            market_id=parsed["id"],
            question=q,
            source="researcher",
            poly_yes=yes_p,
            poly_no=no_p,
            ai_forecast_yes=manifold_avg if manifold_avg else yes_p,
            ai_recommendation=recommendation,
            manifold_yes=manifold_avg,
            end_date=parsed.get("end_date", ""),
            volume=vol,
            days_left=days if isinstance(days, int) else None,
        )
    except Exception as e:
        print(f"[RESEARCHER] Prediction logging error: {e}")

    lines.append(f"\n🔗 <a href=\"{url}\">Open Market</a>")
    lines.append(f"To track: /add {parsed['id']} [entry_price] [size_usd]")
    return "\n".join(lines)
