"""
MODULE 1 — War Room Scanner (v3)
HIGH-RISK / HIGH-VELOCITY geopolitical edge finder.
Focused ONLY on: wars, ceasefires, military ops, regime changes, geopolitical flashpoints.
Cross-references GDELT, Kalshi, RSS intel for mispriced markets.
Optimized for SHORT timeframe, HIGH conviction, AGGRESSIVE trading.
"""

import time
import re
import hashlib
import requests
from datetime import datetime, timezone
import polymarket_api as api
import portfolio_store as store
import telegram_client as tg
from config import (
    MIN_VOLUME, MIN_DAYS_LEFT, MAX_DAYS_LEFT,
    NO_PRICE_MIN, NO_PRICE_MAX, GEOPOLITICAL_KEYWORDS,
    ANTHROPIC_API_KEY
)

# ═══════════════════════════════════════════════════════════════════════
# KEYWORD ENGINE — Tight focus on war/conflict/geopolitical flashpoints
# ═══════════════════════════════════════════════════════════════════════

# Tier 1: Direct war/conflict terms (highest relevance)
WAR_DIRECT = [
    "war", "ceasefire", "invasion", "invade", "airstrike", "missile",
    "strike", "bomb", "troops", "military", "drone strike", "offensive",
    "casualties", "artillery", "frontline", "combat", "armed conflict",
    "hostilities", "deployment", "battleground", "shelling", "blockade",
    "siege", "occupation", "withdraw", "retreat", "surrender",
    "peace deal", "peace talks", "armistice", "truce", "escalation",
    "retaliation", "counterattack", "proxy war", "insurgent",
    "assassination", "regime change", "overthrow", "martial law"
]

# Tier 2: Active conflict zones and actors
CONFLICT_ACTORS = [
    "russia", "ukraine", "china", "taiwan", "israel", "iran", "gaza",
    "hamas", "hezbollah", "north korea", "syria", "yemen", "houthi",
    "nato", "wagner", "idf", "irgc", "kremlin", "pentagon",
    "south china sea", "crimea", "donbas", "kherson", "zaporizhzhia",
    "west bank", "rafah", "golan", "strait of hormuz", "red sea",
    "korean peninsula"
]

# Tier 3: Geopolitical power moves & flashpoints
GEO_FLASHPOINTS = [
    "nuclear", "sanctions", "regime", "collapse", "annex", "occupy",
    "coup", "embargo", "tariff", "greenland", "panama", "arctic",
    "cyber attack", "espionage", "separatist", "territorial",
    "diplomatic crisis", "expel ambassador", "recall ambassador",
    "break relations", "ultimatum", "mobilization", "conscription",
    "no-fly zone", "red line", "chemical weapons", "biological weapons",
    "election interference", "election", "president", "prime minister",
    "summit", "treaty", "alliance", "referendum", "political crisis",
    "government", "opposition", "coalition", "diplomat"
]

ALL_GEO_KEYWORDS = list(set(
    WAR_DIRECT + CONFLICT_ACTORS + GEO_FLASHPOINTS + GEOPOLITICAL_KEYWORDS
))

# High-signal words for scoring GDELT headlines
BREAKING_SIGNALS = [
    "breaking", "confirmed", "official", "attack", "strike",
    "emergency", "escalation", "invasion", "ceasefire", "collapse",
    "nuclear", "sanctions", "retaliation", "deployed", "killed",
    "troops", "missile", "bombing", "offensive", "withdrawn",
    "peace deal", "surrender", "assassination", "overthrow",
    "ultimatum", "mobilization", "breakthrough", "deal reached",
    "agreement signed", "talks collapse", "threat", "warning",
    "alert", "defcon", "intercepted", "shot down", "sunk",
    "captured", "liberated", "advanced", "retreated"
]

# Entity map for GDELT queries (expanded)
ENTITY_MAP = {
    "russia": "Russia", "ukraine": "Ukraine", "china": "China",
    "taiwan": "Taiwan", "israel": "Israel", "iran": "Iran",
    "gaza": "Gaza", "hamas": "Hamas", "north korea": "North Korea",
    "syria": "Syria", "yemen": "Yemen", "nato": "NATO",
    "trump": "Trump", "biden": "Biden", "putin": "Putin",
    "zelensky": "Zelensky", "xi": "Xi Jinping",
    "greenland": "Greenland", "panama": "Panama Canal",
    "hezbollah": "Hezbollah", "houthi": "Houthi",
    "nuclear": "nuclear", "ceasefire": "ceasefire",
    "tariff": "tariff", "sanctions": "sanctions",
    "crimea": "Crimea", "donbas": "Donbas", "rafah": "Rafah",
    "golan": "Golan", "idf": "IDF", "irgc": "IRGC",
    "wagner": "Wagner", "pentagon": "Pentagon",
    "south china sea": "South China Sea", "red sea": "Red Sea",
    "strait of hormuz": "Strait of Hormuz",
    "election": "election", "president": "president",
    "peace": "peace", "war": "war", "invasion": "invasion",
    "coup": "coup", "regime": "regime"
}


# ═══════════════════════════════════════════════════════════════════════
# EXCLUSION LIST — Hard block on sports, entertainment, crypto, etc.
# ═══════════════════════════════════════════════════════════════════════

EXCLUDED_KEYWORDS = [
    # Sports
    "fifa", "world cup", "super bowl", "nba", "nfl", "mlb", "nhl",
    "premier league", "champions league", "la liga", "serie a", "bundesliga",
    "euros ", "euro 2026", "euro 2028", "olympic", "olympics", "medal",
    "touchdown", "goalkeeper", "tennis", "wimbledon", "us open",
    "grand slam", "formula 1", "f1 ", "nascar", "ufc ", "mma ",
    "boxing", "cricket", "rugby", "baseball", "basketball", "football",
    "soccer", "golf", "pga", "masters tournament", "stanley cup",
    "playoffs", "championship", "semifinal", "quarterfinal", "final match",
    "ballon d'or", "mvp", "scoring leader", "win the 2026", "win the 2027",
    "win the 2028", "super league", "copa america", "concacaf",
    "afc", "uefa", "conmebol", "game ", "match ", "season",
    # Entertainment / Pop culture
    "oscar", "grammy", "emmy", "tony award", "golden globe",
    "box office", "movie", "film ", "album", "spotify",
    "youtube", "tiktok", "instagram", "subscriber", "follower",
    "reality tv", "bachelor", "celebrity", "kardashian",
    # Crypto / Markets (not geopolitical)
    "bitcoin", "ethereum", "solana", "dogecoin", "memecoin",
    "crypto price", "btc ", "eth ", "sol ", "market cap",
    "all-time high", "ath ", "token", "defi", "nft ",
    # Science / Tech / Weather
    "hurricane", "earthquake", "temperature", "rainfall",
    "spacex", "mars ", "moon landing", "asteroid",
    "iphone", "apple ", "google ", "microsoft", "nvidia",
    "ai model", "chatgpt", "gpt-5",
    # Other noise
    "will it snow", "will it rain", "weather",
    "superbowl", "halftime", "concert", "tour ",
    "dating", "marriage", "divorce", "baby",
    "dog ", "cat ", "animal", "food ", "restaurant"
]

# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _is_excluded(question: str) -> bool:
    """Hard exclusion — returns True if market is sports/entertainment/crypto noise."""
    q = question.lower()
    return any(ex in q for ex in EXCLUDED_KEYWORDS)


def _is_geopolitical(market: dict) -> bool:
    """Check if market matches war/politics/geopolitical focus AND is not excluded."""
    question = market.get("question", "")
    if _is_excluded(question):
        return False
    text = (question + " " + " ".join(market.get("tags", []))).lower()
    return any(kw in text for kw in ALL_GEO_KEYWORDS)


def _passes_entry_criteria(m: dict) -> bool:
    """Entry criteria for quick scan alerts."""
    if m.get("days_left") is None:
        return False
    return (
        m["volume"] >= MIN_VOLUME
        and MIN_DAYS_LEFT <= m["days_left"] <= MAX_DAYS_LEFT
        and NO_PRICE_MIN <= m["no_price"] <= NO_PRICE_MAX
    )


def _keyword_relevance(question: str) -> int:
    """
    Score how deeply relevant a market is to active conflict/geopolitics.
    Tier 1 (war direct) = 3 pts each, Tier 2 (actors) = 2 pts, Tier 3 (flashpoints) = 1 pt.
    """
    q = question.lower()
    score = 0
    for kw in WAR_DIRECT:
        if kw in q:
            score += 3
    for kw in CONFLICT_ACTORS:
        if kw in q:
            score += 2
    for kw in GEO_FLASHPOINTS:
        if kw in q:
            score += 1
    return score


# ═══════════════════════════════════════════════════════════════════════
# MARKET FETCHER — Cast wide net, then filter hard
# ═══════════════════════════════════════════════════════════════════════

def _fetch_all_geo_markets() -> list:
    """
    Fetch ALL geopolitical markets from Polymarket using multiple strategies.
    Minimum volume 50K for reliable entry/exit.
    """
    all_markets = []
    seen_ids = set()

    def _add(raw):
        m = api.parse_market(raw)
        if m and m["id"] not in seen_ids and m["volume"] >= 50000:
            # HARD FILTER: Skip sports, entertainment, crypto noise
            if _is_excluded(m["question"]):
                return False
            seen_ids.add(m["id"])
            all_markets.append(m)
            return True
        return False

    # Strategy 1: Tag-based search
    GEO_TAGS = ["Politics", "Geopolitics", "World", "Ukraine", "Middle East",
                "Conflicts", "China", "Russia", "Elections"]
    for tag in GEO_TAGS:
        try:
            r = requests.get(f"{api.GAMMA_BASE}/markets", params={
                "limit": 100, "active": "true", "closed": "false",
                "tag": tag, "order": "volume", "ascending": "false"
            }, headers=api.HEADERS, timeout=15)
            if r.ok:
                for raw in r.json():
                    _add(raw)
            print(f"[SCANNER] Tag '{tag}' -> {len(all_markets)} total")
        except Exception as e:
            print(f"[SCANNER] Tag '{tag}' error: {e}")
        time.sleep(0.15)

    # Strategy 2: Top volume markets, keyword filtered (4 pages for wider net)
    for page in range(4):
        try:
            raw_list = api.get_markets(limit=200, offset=page * 200)
            if not raw_list:
                break
            for raw in raw_list:
                m = api.parse_market(raw)
                if m and m["id"] not in seen_ids and _is_geopolitical(m) and m["volume"] >= 50000:
                    seen_ids.add(m["id"])
                    all_markets.append(m)
        except Exception as e:
            print(f"[SCANNER] Volume page {page}: {e}")
            break
        time.sleep(0.15)

    # Strategy 3: Geopolitical events (grouped markets — catches sub-markets)
    try:
        for tag in ["Politics", "Geopolitics", "World", "Conflicts"]:
            r = requests.get(f"{api.GAMMA_BASE}/events", params={
                "limit": 50, "active": "true", "closed": "false",
                "tag": tag, "order": "volume", "ascending": "false"
            }, headers=api.HEADERS, timeout=15)
            if r.ok:
                for event in r.json():
                    for raw in event.get("markets", []):
                        _add(raw)
            time.sleep(0.15)
    except Exception as e:
        print(f"[SCANNER] Events fetch: {e}")

    # Strategy 4: Direct keyword search for edge cases
    edge_keywords = ["ceasefire", "invasion", "nuclear", "coup", "assassination", "regime change"]
    for kw in edge_keywords:
        try:
            r = requests.get(f"{api.GAMMA_BASE}/markets", params={
                "limit": 30, "active": "true", "closed": "false",
                "order": "volume", "ascending": "false"
            }, headers=api.HEADERS, timeout=10)
            if r.ok:
                for raw in r.json():
                    if kw in (raw.get("question", "") or "").lower():
                        _add(raw)
        except:
            pass
        time.sleep(0.1)

    print(f"[SCANNER] Total geopolitical markets found: {len(all_markets)}")
    return all_markets


# ═══════════════════════════════════════════════════════════════════════
# SIGNAL SCORING — MAX ROI: High uncertainty + High volume + Intel edge
# ═══════════════════════════════════════════════════════════════════════

def _get_velocity_score(m: dict) -> dict:
    """
    MAX ROI market structure scoring.

    KEY INSIGHT: ROI is maximized in UNCERTAIN markets (25-75% range)
    with HIGH VOLUME where our intel signals reveal the direction.

    Buy YES at 30% → resolves YES = 3.3x ROI (230% profit)
    Buy YES at 85% → resolves YES = 1.18x ROI (18% profit)

    We want the 30% ones where GDELT/Kalshi tell us it's going to 100%.

    Score: 0-40
    """
    score = 0
    flags = []

    yes_price = m["yes_price"]
    no_price = m["no_price"]
    volume = m["volume"]
    days_left = m.get("days_left") or 999

    # ── UNCERTAINTY = ROI POTENTIAL (0-20) ──
    # The further from 0% or 100%, the more profit potential
    # Sweet spot: 15-45% YES or 55-85% NO — huge upside if we're right
    distance_from_edge = min(yes_price, no_price)  # How far from certainty

    if 0.25 <= distance_from_edge <= 0.50:
        # MAXIMUM UNCERTAINTY — 25-50% on either side = 2x-4x ROI potential
        score += 20
        roi_potential = round((1 / min(yes_price, no_price) - 1) * 100)
        flags.append(f"MAX UNCERTAINTY — up to {roi_potential}% ROI")
    elif 0.15 <= distance_from_edge < 0.25:
        # HIGH UNCERTAINTY — still 1.5x-4x potential
        score += 15
        roi_potential = round((1 / min(yes_price, no_price) - 1) * 100)
        flags.append(f"HIGH UNCERTAINTY — up to {roi_potential}% ROI")
    elif 0.08 <= distance_from_edge < 0.15:
        # MODERATE — leaning one way but catalyst could flip it
        score += 8
        flags.append(f"MODERATE UNCERTAINTY ({yes_price:.0%}/{no_price:.0%})")
    else:
        # LOW UNCERTAINTY — market already decided, low ROI
        score += 2
        flags.append(f"LOW UNCERTAINTY — market settled")

    # ── VOLUME: We need LOTS of it for entry/exit (0-15) ──
    if volume >= 2_000_000:
        score += 15
        flags.append(f"MEGA VOLUME (${volume/1_000_000:.1f}M)")
    elif volume >= 1_000_000:
        score += 13
        flags.append(f"HIGH VOLUME (${volume/1_000_000:.1f}M)")
    elif volume >= 500_000:
        score += 11
        flags.append(f"SOLID VOLUME (${volume/1_000:.0f}K)")
    elif volume >= 200_000:
        score += 8
        flags.append(f"DECENT VOLUME (${volume/1_000:.0f}K)")
    elif volume >= 100_000:
        score += 5
    else:
        score += 1
        flags.append("LOW VOLUME — slippage risk")

    # ── TIMING (0-5) — shorter = faster ROI, but not the main factor ──
    if 1 <= days_left <= 14:
        score += 5
        flags.append(f"RESOLVES IN {days_left}d")
    elif 14 < days_left <= 30:
        score += 4
    elif 30 < days_left <= 60:
        score += 3
    elif 60 < days_left <= 120:
        score += 2
    else:
        score += 1

    # Calculate potential ROI for display
    if yes_price > 0 and yes_price < 1:
        yes_roi = round((1 / yes_price - 1) * 100)
        no_roi = round((1 / no_price - 1) * 100)
    else:
        yes_roi = 0
        no_roi = 0

    edge_type = f"YES@{yes_price:.0%} ({yes_roi}% ROI) | NO@{no_price:.0%} ({no_roi}% ROI)"

    return {
        "score": score,
        "edge_type": edge_type,
        "flags": flags,
        "velocity_class": flags[0] if flags else "STANDARD",
        "yes_roi": yes_roi,
        "no_roi": no_roi
    }


def _get_gdelt_signal(question: str) -> dict:
    """
    GDELT deep signal — checks 24h AND 72h windows for momentum.
    Scores breaking news, narrative velocity, and headline sentiment.
    Returns {score: 0-30, headlines: [...], narrative: str, is_breaking: bool}
    """
    question_lower = question.lower()
    key_entities = []

    for keyword, entity in ENTITY_MAP.items():
        if keyword in question_lower:
            key_entities.append(entity)

    if not key_entities:
        return {"score": 0, "headlines": [], "narrative": "no entities matched", "is_breaking": False}

    query_terms = key_entities[:3]  # Use up to 3 entities for precision
    gdelt_query = " ".join(f'"{t}"' for t in query_terms[:2])

    results = {"24h": [], "72h": []}

    for timespan in ["24h", "72h"]:
        try:
            r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
                "query": gdelt_query,
                "mode": "artlist",
                "maxrecords": 15,
                "timespan": timespan,
                "format": "json",
                "sort": "datedesc"
            }, timeout=6)
            if r.ok:
                data = r.json()
                results[timespan] = data.get("articles", [])
        except Exception as e:
            print(f"[SCANNER/GDELT] {timespan}: {e}")

    articles_24h = results["24h"]
    articles_72h = results["72h"]
    all_articles = articles_24h or articles_72h

    if not all_articles:
        return {"score": 0, "headlines": [], "narrative": "QUIET — no coverage", "is_breaking": False}

    headline_texts = [a.get("title", "") for a in all_articles[:8]]

    # Count breaking signal words
    signal_hits = 0
    breaking_hits = 0
    for h in headline_texts:
        hl = h.lower()
        for w in BREAKING_SIGNALS:
            if w in hl:
                signal_hits += 1
                if w in ["breaking", "confirmed", "official", "emergency", "attack",
                         "invasion", "assassination", "breakthrough", "deal reached"]:
                    breaking_hits += 1

    is_breaking = breaking_hits >= 2

    # Velocity: compare 24h vs 72h coverage
    count_24h = len(articles_24h)
    count_72h = len(articles_72h)
    accelerating = count_24h >= count_72h * 0.6 if count_72h > 0 else count_24h > 3

    # Score: coverage (0-16) + signal intensity (0-16) + momentum bonus (0-10)
    # HEAVY WEIGHT — intel signals are what find the EDGE in uncertainty
    volume_score = min(16, count_24h * 3)
    signal_score = min(16, signal_hits * 3)
    momentum_bonus = 10 if (accelerating and is_breaking) else (8 if accelerating else (3 if count_24h > 2 else 0))
    total = volume_score + signal_score + momentum_bonus

    narrative_parts = [f"{count_24h} articles/24h"]
    if signal_hits > 0:
        narrative_parts.append(f"{signal_hits} high-signal")
    if is_breaking:
        narrative_parts.append("BREAKING")
    if accelerating:
        narrative_parts.append("ACCELERATING")

    return {
        "score": min(40, total),
        "headlines": headline_texts[:5],
        "narrative": " | ".join(narrative_parts),
        "is_breaking": is_breaking,
        "accelerating": accelerating,
        "count_24h": count_24h
    }


def _get_kalshi_signal_cached(question: str, poly_yes: float, kalshi_markets: list) -> dict:
    """Kalshi cross-market divergence — bigger gap = bigger opportunity."""
    if not kalshi_markets:
        return {"score": 0, "divergence": 0, "ticker": ""}

    try:
        import kalshi_api
        best_match = None
        best_score = 0.0

        for m in kalshi_markets:
            title = m.get("title", "") or m.get("subtitle", "")
            score = kalshi_api._keyword_overlap(question, title)
            if score > best_score and score >= 0.35:  # Lower threshold to catch more
                best_score = score
                yes_bid = float(m.get("yes_bid_dollars", 0) or 0)
                yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
                yes_mid = (yes_bid + yes_ask) / 2 if (yes_bid and yes_ask) else yes_bid or yes_ask
                last_price = float(m.get("last_price_dollars", 0) or 0)
                k_yes = yes_mid or last_price

                best_match = {
                    "ticker": m.get("ticker", ""),
                    "title": title,
                    "yes_price": k_yes,
                    "score": best_score
                }

        if not best_match or best_match["yes_price"] == 0:
            return {"score": 0, "divergence": 0, "ticker": ""}

        divergence = abs(poly_yes - best_match["yes_price"]) * 100

        # HEAVY scoring — divergence IS the edge. Bigger gap = more alpha
        if divergence >= 20:
            score = 30  # MASSIVE — two markets disagree by 20+ points
        elif divergence >= 15:
            score = 25
        elif divergence >= 10:
            score = 20
        elif divergence >= 7:
            score = 14
        elif divergence >= 5:
            score = 10
        elif divergence >= 3:
            score = 5
        else:
            score = 1

        return {
            "score": min(30, score),
            "divergence": round(divergence, 1),
            "ticker": best_match["ticker"],
            "kalshi_yes": best_match["yes_price"]
        }
    except Exception as e:
        print(f"[SCANNER/KALSHI] {e}")
        return {"score": 0, "divergence": 0, "ticker": ""}


def _get_rss_signal_cached(question: str, rss_items: list) -> dict:
    """RSS alt-media signal — checks if story is being covered by non-Western sources."""
    if not rss_items:
        return {"score": 0, "hits": 0, "sources": []}

    question_lower = question.lower()
    q_words = set(w for w in question_lower.split() if len(w) > 3)
    relevant = 0
    sources_hit = set()

    for item in rss_items:
        title = item.get("title", "").lower()
        t_words = set(w for w in title.split() if len(w) > 3)
        overlap = q_words & t_words
        if len(overlap) >= 2:
            relevant += 1
            source = item.get("source", "unknown")
            sources_hit.add(source)

    score = min(20, relevant * 5)
    # Bonus if multiple independent sources covering it = confirmed narrative
    if len(sources_hit) >= 3:
        score = min(20, score + 7)
    elif len(sources_hit) >= 2:
        score = min(20, score + 3)

    return {"score": score, "hits": relevant, "sources": list(sources_hit)}


# ═══════════════════════════════════════════════════════════════════════
# AI INTELLIGENCE ENGINE — Aggressive trading analysis
# ═══════════════════════════════════════════════════════════════════════

def _ai_master_summary(top_3: list, all_market_count: int, scored_count: int) -> str:
    """
    Claude-powered intelligence briefing optimized for HIGH-RISK / HIGH-VELOCITY trading.
    Asks for specific entry points, catalyst timing, and edge case analysis.
    """
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        return ""

    picks_context = ""
    for i, pick in enumerate(top_3):
        m = pick["market"]
        gs = pick["gdelt_signal"]
        ks = pick["kalshi_signal"]
        rs = pick["rss_signal"]
        vs = pick["velocity_signal"]

        headlines = "\n".join(f"    - {h[:120]}" for h in gs.get("headlines", [])[:5])
        kalshi_note = f"Kalshi divergence: {ks['divergence']}pt (Poly YES {m['yes_price']:.0%} vs Kalshi {ks.get('kalshi_yes', 0):.0%})" if ks.get("divergence", 0) >= 3 else "No significant Kalshi divergence"
        rss_note = f"{rs['hits']} hits across {', '.join(rs.get('sources', [])[:3]) or 'various'}" if rs.get("hits", 0) > 0 else "No alt-media coverage"

        # Calculate ROI potential for AI context
        yes_roi = vs.get("yes_roi", 0)
        no_roi = vs.get("no_roi", 0)

        picks_context += f"""
--- PICK #{i+1} ---
Market: {m['question']}
URL: {m['url']}
YES price: {m['yes_price']:.0%} (potential ROI if YES resolves: {yes_roi}%)
NO price: {m['no_price']:.0%} (potential ROI if NO resolves: {no_roi}%)
Volume: ${m['volume']:,.0f} | Days left: {m.get('days_left', '?')}
Signal flags: {', '.join(vs.get('flags', []))}
Composite score: {pick['total_score']}/130
GDELT NEWS INTEL ({gs['score']}/40): {gs['narrative']}
  Breaking news detected: {'YES' if gs.get('is_breaking') else 'NO'}
  News momentum accelerating: {'YES' if gs.get('accelerating') else 'NO'}
  Latest headlines:
{headlines or '    (no recent headlines)'}
KALSHI DIVERGENCE ({ks['score']}/30): {kalshi_note}
RSS MULTI-SOURCE ({rs['score']}/20): {rss_note}
Conflict relevance score: {pick.get('keyword_score', 0)}
"""

    prompt = f"""You are an elite geopolitical prediction market analyst. Your ONLY job is to find the HIGHEST ROI trades in uncertain markets where intelligence signals reveal the likely direction before the market prices it in.

SCAN RESULTS:
- Scanned {all_market_count} Polymarket geopolitical/war/conflict markets
- Deep-scored {scored_count} candidates with GDELT news, Kalshi cross-market data, and RSS alt-media intel
- Selected these 3 based on: HIGH UNCERTAINTY (biggest ROI potential) + STRONG INTEL SIGNALS (edge in direction)

THE 3 HIGHEST-OPPORTUNITY MARKETS:
{picks_context}

YOUR ANALYSIS MUST INCLUDE:

1. SITUATION SNAPSHOT (2-3 sentences — what geopolitical events are creating market uncertainty RIGHT NOW)

2. For EACH of the 3 picks:
   - DIRECTION: Based on the intel signals (GDELT headlines, Kalshi pricing, RSS coverage), which way is this market ACTUALLY heading? BUY YES or BUY NO?
   - ENTRY: Current price to enter (e.g. "BUY YES at 35 cents")
   - TARGET: What price you expect it to reach and when
   - ROI: Calculate the expected profit percentage
   - THE EDGE: What do the GDELT headlines / Kalshi divergence / RSS intel tell us that the market hasn't priced in yet? Be SPECIFIC — cite the actual data.
   - CATALYST: What upcoming event (summit, deadline, military op, vote) will move this?
   - RISK: One sentence — what kills this trade?

3. BEST TRADE — The single highest ROI play:
   - Exact entry and target
   - Expected ROI percentage
   - Timeframe
   - Why the intel signals make this a high-conviction trade

4. CONTRARIAN ANGLE — One thing the data suggests that goes against consensus (1-2 sentences)

RULES:
- We are looking for MAX ROI. We prefer uncertain markets (30-70% range) where we can buy cheap and the intel tells us direction.
- A 35-cent YES that goes to 100 = 186% ROI. A 90-cent YES that goes to 100 = 11% ROI. WE WANT THE FIRST KIND.
- ALWAYS cite specific headlines, divergence numbers, or signal data to justify each call.
- Be AGGRESSIVE. No hedging, no disclaimers. Raw alpha.
- Under 500 words total.
- IMPORTANT: Use ONLY plain text. No markdown, no **, no ##, no HTML tags. Use CAPS for emphasis."""

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
            timeout=45
        )
        if r.ok:
            data = r.json()
            raw_text = data.get("content", [{}])[0].get("text", "")
            # Convert any markdown to Telegram-safe text
            text = raw_text
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'^#{1,3}\s*', '', text, flags=re.MULTILINE)
            text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
            return text.strip()
        else:
            print(f"[SCANNER/AI] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[SCANNER/AI] {e}")
    return ""


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENGINE — Deep scan with velocity-first scoring
# ═══════════════════════════════════════════════════════════════════════

def run_deep_scan() -> str:
    """
    WAR ROOM SCAN:
    1. Fetch ALL geo/conflict markets from Polymarket
    2. Pre-filter by keyword relevance + velocity scoring
    3. Deep-score top candidates with GDELT + Kalshi + RSS
    4. AI generates aggressive trading recommendations
    """
    print(f"[SCANNER] ═══ WAR ROOM SCAN STARTED ═══")
    start_time = time.time()

    # Step 1: Fetch all geopolitical markets
    all_markets = _fetch_all_geo_markets()
    if not all_markets:
        return "❌ No geopolitical markets found on Polymarket."

    # Step 2a: FAST pre-filter — velocity score + keyword relevance (no API calls)
    pre_scored = []
    for m in all_markets:
        vel_signal = _get_velocity_score(m)
        kw_score = _keyword_relevance(m["question"])

        # Combined fast score — must have REAL geopolitical relevance
        # kw_score >= 3 means at least one Tier 1 war term OR one conflict actor + flashpoint
        fast_score = vel_signal["score"] + min(10, kw_score)
        if fast_score >= 10 and kw_score >= 3 and not _is_excluded(m["question"]):
            pre_scored.append((fast_score, vel_signal, kw_score, m))

    # Sort by fast score, keep top 35 for deep analysis
    pre_scored.sort(key=lambda x: x[0], reverse=True)
    candidates = pre_scored[:35]
    print(f"[SCANNER] Pre-filtered: {len(all_markets)} -> {len(candidates)} candidates for deep scoring")

    # Step 2b: Load external data caches ONCE
    kalshi_cache = []
    try:
        import kalshi_api
        kalshi_cache = kalshi_api._get_kalshi_markets(limit=200)
        print(f"[SCANNER] Kalshi cache: {len(kalshi_cache)} markets loaded")
    except Exception as e:
        print(f"[SCANNER] Kalshi cache failed: {e}")

    rss_cache = []
    try:
        import rss_intel
        for feed in rss_intel.RSS_FEEDS:
            items = rss_intel._parse_rss(feed["url"])
            for item in items[:10]:
                item["source"] = feed.get("name", feed.get("url", "unknown"))
            rss_cache.extend(items[:10])
        print(f"[SCANNER] RSS cache: {len(rss_cache)} headlines loaded")
    except Exception as e:
        print(f"[SCANNER] RSS cache failed: {e}")

    # Step 3: Deep score each candidate
    scored = []
    for i, (fast_score, vel_signal, kw_score, m) in enumerate(candidates):
        gdelt_signal = _get_gdelt_signal(m["question"])
        kalshi_signal = _get_kalshi_signal_cached(m["question"], m["yes_price"], kalshi_cache)
        rss_signal = _get_rss_signal_cached(m["question"], rss_cache)

        # Composite score (0-130):
        # Market structure (uncertainty + volume + timing): 0-40
        # GDELT news heat (THE edge finder):               0-40
        # Kalshi cross-market divergence:                   0-30
        # RSS alt-media confirmation:                       0-20
        total_score = (
            vel_signal["score"] +
            gdelt_signal["score"] +
            kalshi_signal["score"] +
            rss_signal["score"]
        )

        # ── COMBO BONUSES — where signals converge, alpha compounds ──

        # JACKPOT: High uncertainty + breaking news = market hasn't priced it in yet
        uncertainty = min(m["yes_price"], m["no_price"])
        if uncertainty >= 0.25 and gdelt_signal.get("is_breaking"):
            total_score += 15
            vel_signal["flags"].append("JACKPOT: UNCERTAIN + BREAKING NEWS")

        # CONFIRMED EDGE: Kalshi divergence + GDELT = two sources agree market is wrong
        if kalshi_signal.get("divergence", 0) >= 8 and gdelt_signal["score"] >= 15:
            total_score += 12
            vel_signal["flags"].append("CONFIRMED MISPRICING")

        # NEWS SURGE: Multiple sources covering it + accelerating = catalyst imminent
        if gdelt_signal.get("accelerating") and rss_signal.get("hits", 0) >= 3:
            total_score += 8
            vel_signal["flags"].append("NEWS SURGE — catalyst imminent")

        # VOLUME + UNCERTAINTY: Big liquid market that's undecided = opportunity
        if m["volume"] >= 500_000 and uncertainty >= 0.20:
            total_score += 5

        # High keyword relevance to active conflicts
        if kw_score >= 8:
            total_score += 5

        scored.append({
            "market": m,
            "total_score": total_score,
            "velocity_signal": vel_signal,
            "gdelt_signal": gdelt_signal,
            "kalshi_signal": kalshi_signal,
            "rss_signal": rss_signal,
            "keyword_score": kw_score
        })

        if (i + 1) % 10 == 0:
            print(f"[SCANNER] Deep-scored {i+1}/{len(candidates)} candidates...")

    if not scored:
        return "❌ No qualifying markets passed scoring threshold."

    # Step 4: Sort and get top 3
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    top_3 = scored[:3]

    # Step 5: AI intelligence briefing
    print("[SCANNER] Generating AI war room briefing...")
    ai_summary = _ai_master_summary(top_3, len(all_markets), len(scored))

    elapsed = round(time.time() - start_time, 1)

    # Step 6: Format output — CLEAN INTEL BRIEFING ONLY
    # No data cards, no noise. Just the AI analysis + clickable links.
    medals = ["🥇", "🥈", "🥉"]
    lines = []

    if ai_summary:
        lines.append(f"🧠 <b>INTEL ANALYSIS & MAX ROI TRADE CALLS</b>")
        lines.append("")
        lines.append(ai_summary)
        lines.append("")
        lines.append(f"{'━' * 30}")
        lines.append(f"🔗 <b>QUICK LINKS</b>")
        for rank, pick in enumerate(top_3):
            m = pick["market"]
            short_q = m["question"][:60] + ("..." if len(m["question"]) > 60 else "")
            lines.append(f"{medals[rank]} <a href=\"{m['url']}\">{short_q}</a>")
        lines.append("")
        lines.append(f"{'━' * 30}")
        lines.append(f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append(f"📊 Scanned {len(all_markets)} markets | Scored {len(scored)} | {elapsed}s")
        lines.append(f"💡 /research [url] for deep dive on any pick")
    else:
        # Fallback if AI fails — minimal data output
        lines.append(f"🎯 <b>TOP 3 MAX ROI PICKS</b>")
        lines.append(f"📊 {len(all_markets)} markets scanned | {elapsed}s")
        lines.append("")
        for rank, pick in enumerate(top_3):
            m = pick["market"]
            vs = pick["velocity_signal"]
            yes_roi = vs.get("yes_roi", 0)
            no_roi = vs.get("no_roi", 0)
            vol = m["volume"]
            vol_str = f"${vol/1_000_000:.1f}M" if vol >= 1_000_000 else f"${vol/1_000:.0f}K"
            lines.append(f"{medals[rank]} <b>{m['question']}</b>")
            lines.append(f"   YES {m['yes_price']:.0%} ({yes_roi}% ROI) | NO {m['no_price']:.0%} ({no_roi}% ROI)")
            lines.append(f"   Vol: {vol_str} | Score: {pick['total_score']}/130")
            lines.append(f"   <a href=\"{m['url']}\">Open on Polymarket</a>")
            lines.append("")
        lines.append(f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    print(f"[SCANNER] ═══ WAR ROOM SCAN COMPLETE — {elapsed}s ═══")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# QUICK SCAN — Original scheduler-based scan for new market alerts
# ═══════════════════════════════════════════════════════════════════════

def _format_alert(m: dict) -> str:
    yes_pct = round(m["yes_price"] * 100, 1)
    no_pct = round(m["no_price"] * 100, 1)
    vol_k = round(m["volume"] / 1000, 1)

    lines = [
        f"📌 <b>{m['question']}</b>",
        "",
        f"YES: <b>${m['yes_price']:.2f}</b> ({yes_pct}%)   NO: <b>${m['no_price']:.2f}</b> ({no_pct}%)",
        f"📊 Volume: ${vol_k}K",
        f"⏳ Deadline: {m.get('end_date','?')[:10]} ({m['days_left']} days left)",
        "",
        f"✅ Passes all entry criteria",
        f"🔗 <a href=\"{m['url']}\">Open on Polymarket</a>",
        "",
        f"💡 Run /research to get divergence analysis"
    ]
    return "\n".join(lines)


def run_scan() -> int:
    """
    Quick scan for NEW markets (used by scheduler every 15 min).
    Returns count of new alerts sent.
    """
    print(f"[SCANNER] Running quick scan at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    alerted = 0

    try:
        markets_raw = api.get_markets(limit=200)
    except Exception as e:
        print(f"[SCANNER] API error: {e}")
        return 0

    for raw in markets_raw:
        m = api.parse_market(raw)
        if not m:
            continue

        if store.is_seen(m["id"]):
            continue

        if not _is_geopolitical(m):
            continue

        if not _passes_entry_criteria(m):
            store.mark_seen(m["id"])
            continue

        store.mark_seen(m["id"])
        msg = f"🆕 <b>NEW MARKET DETECTED</b>\n\n{_format_alert(m)}"
        tg.send(msg)
        alerted += 1
        print(f"[SCANNER] Alerted: {m['question'][:60]}")

    print(f"[SCANNER] Done. {alerted} new alerts sent.")
    return alerted
