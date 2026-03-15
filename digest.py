"""
INTEL DIGEST — Consolidated Intelligence Briefing
Replaces individual RSS/GDELT/news spam with ONE clean briefing.

Collects all intel sources → matches to Polymarket → AI creates 3 best predictions
with urgency scoring for time-sensitive events.
"""

import time
import re
import requests
import hashlib
from datetime import datetime, timezone
import telegram_client as tg
import polymarket_api as api
from config import ANTHROPIC_API_KEY, TELEGRAM_CHAT_ID

# ═══════════════════════════════════════════════════════════════════════
# INTEL COLLECTION — Gather ALL sources silently (no Telegram spam)
# ═══════════════════════════════════════════════════════════════════════

HEADERS = {"User-Agent": "PolymarketBot/2.0"}

# RSS feeds
RSS_FEEDS = [
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "region": "Middle East"},
    {"name": "RFE/RL", "url": "https://www.rferl.org/api/z-pqpiev-qpp", "region": "Russia/EastEU"},
    {"name": "TASS", "url": "https://tass.com/rss/v2.xml", "region": "Russia"},
    {"name": "Xinhua", "url": "http://www.xinhuanet.com/english/rss/worldrss.xml", "region": "China"},
]

# GDELT narrative categories
GDELT_QUERIES = [
    {"label": "Military Conflict", "query": '("military strike" OR "military operation" OR "armed conflict" OR invasion OR "troops deployed")'},
    {"label": "Nuclear Threat", "query": '("nuclear weapon" OR "nuclear test" OR "nuclear threat" OR "nuclear escalation")'},
    {"label": "Sanctions & Trade", "query": '("new sanctions" OR "trade war" OR "economic sanctions" OR embargo OR tariff)'},
    {"label": "Regime Change", "query": '(coup OR "regime change" OR "government overthrown" OR "martial law")'},
    {"label": "Ceasefire & Peace", "query": '(ceasefire OR "peace deal" OR "peace agreement" OR truce)'},
    {"label": "Election Crisis", "query": '("election fraud" OR "contested election" OR "election crisis" OR "election result")'},
]

# High-urgency signal words — if headline contains these, flag as URGENT
URGENCY_WORDS = [
    "breaking", "just in", "confirmed", "emergency", "attack now",
    "launched", "struck", "killed", "collapsed", "declared war",
    "invasion begun", "ceasefire broken", "deal reached", "signed",
    "deployed", "intercepted", "shot down", "assassination",
    "nuclear", "mobilization", "martial law", "coup"
]

# Geopolitical filter — only keep relevant headlines
GEO_KEYWORDS = [
    "war", "ceasefire", "invasion", "military", "troops", "missile",
    "strike", "bomb", "drone", "airstrike", "nuclear", "sanctions",
    "tariff", "embargo", "coup", "regime", "election", "peace",
    "conflict", "attack", "killed", "escalation", "retaliation",
    "iran", "israel", "russia", "ukraine", "china", "taiwan",
    "gaza", "hamas", "hezbollah", "houthi", "nato", "north korea",
    "syria", "yemen", "kremlin", "pentagon", "summit", "treaty",
    "ceasefire", "withdrawal", "offensive", "casualties"
]


def _parse_rss_silent(url):
    """Parse RSS feed — returns items, sends nothing to Telegram."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if not r.ok:
            return []
        items = []
        for m in re.finditer(r"<item>(.*?)</item>", r.text, re.DOTALL):
            xml = m.group(1)
            title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", xml)
            link_m = re.search(r"<link>(.*?)</link>", xml)
            title = (title_m.group(1) or title_m.group(2) or "").strip() if title_m else ""
            link = link_m.group(1).strip() if link_m else ""
            if title:
                items.append({"title": title, "link": link})
        return items[:15]
    except:
        return []


def _fetch_gdelt_silent(query, timespan="8h", max_records=10):
    """Fetch GDELT articles — returns data, sends nothing to Telegram."""
    try:
        r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": query, "mode": "artlist", "maxrecords": max_records,
            "timespan": timespan, "format": "json", "sort": "datedesc"
        }, headers=HEADERS, timeout=12)
        if r.ok:
            return r.json().get("articles", [])
    except:
        pass
    return []


def _is_geo_relevant(title):
    """Check if headline is geopolitically relevant."""
    t = title.lower()
    return any(kw in t for kw in GEO_KEYWORDS)


def _urgency_score(title):
    """Score urgency 0-10. Higher = more time-sensitive."""
    t = title.lower()
    score = 0
    for w in URGENCY_WORDS:
        if w in t:
            score += 2
    return min(10, score)


def collect_all_intel():
    """
    Silently collect ALL intel from all sources.
    Returns structured dict with categorized headlines.
    """
    print("[DIGEST] Collecting intel from all sources...")
    intel = {
        "rss": [],
        "gdelt": [],
        "themes": {},  # grouped by theme
        "urgent": [],  # time-sensitive items
    }

    # 1. RSS feeds
    for feed in RSS_FEEDS:
        items = _parse_rss_silent(feed["url"])
        for item in items:
            if _is_geo_relevant(item["title"]):
                urgency = _urgency_score(item["title"])
                entry = {
                    "title": item["title"],
                    "link": item.get("link", ""),
                    "source": feed["name"],
                    "region": feed["region"],
                    "urgency": urgency
                }
                intel["rss"].append(entry)
                if urgency >= 4:
                    intel["urgent"].append(entry)
    print(f"[DIGEST] RSS: {len(intel['rss'])} relevant headlines")

    # 2. GDELT narratives (8h lookback)
    for nq in GDELT_QUERIES:
        articles = _fetch_gdelt_silent(nq["query"], timespan="8h", max_records=8)
        for art in articles:
            title = art.get("title", "").strip()
            if not title or not _is_geo_relevant(title):
                continue
            urgency = _urgency_score(title)
            entry = {
                "title": title,
                "link": art.get("url", ""),
                "source": f"GDELT/{art.get('domain', '')}",
                "region": nq["label"],
                "urgency": urgency
            }
            intel["gdelt"].append(entry)
            if urgency >= 4:
                intel["urgent"].append(entry)

            # Group by theme
            theme = nq["label"]
            if theme not in intel["themes"]:
                intel["themes"][theme] = []
            intel["themes"][theme].append(entry)
        time.sleep(0.3)

    print(f"[DIGEST] GDELT: {len(intel['gdelt'])} headlines across {len(intel['themes'])} themes")
    print(f"[DIGEST] Urgent items: {len(intel['urgent'])}")

    # Deduplicate by title similarity
    seen = set()
    for key in ["rss", "gdelt", "urgent"]:
        deduped = []
        for item in intel[key]:
            h = hashlib.md5(item["title"][:60].lower().encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                deduped.append(item)
        intel[key] = deduped

    return intel


# ═══════════════════════════════════════════════════════════════════════
# POLYMARKET MATCHING — Find markets that match the intel
# ═══════════════════════════════════════════════════════════════════════

EXCLUDED_KEYWORDS = [
    "fifa", "world cup", "super bowl", "nba", "nfl", "mlb", "nhl",
    "premier league", "champions league", "olympic", "olympics",
    "boxing", "cricket", "rugby", "baseball", "basketball", "soccer",
    "oscar", "grammy", "emmy", "golden globe", "movie", "album",
    "bitcoin", "ethereum", "solana", "dogecoin", "memecoin", "nft",
    "hurricane", "earthquake", "spacex", "iphone", "chatgpt",
    "weather", "concert", "dating", "restaurant"
]


def _is_excluded(q):
    ql = q.lower()
    return any(ex in ql for ex in EXCLUDED_KEYWORDS)


def fetch_geo_markets():
    """Fetch geopolitical Polymarket markets for matching."""
    markets = []
    seen = set()

    def _add(raw):
        m = api.parse_market(raw)
        if m and m["id"] not in seen and m["volume"] >= 50000 and not _is_excluded(m["question"]):
            seen.add(m["id"])
            markets.append(m)

    # Tag search
    for tag in ["Politics", "Geopolitics", "World", "Ukraine", "Middle East", "Conflicts", "China", "Russia", "Elections"]:
        try:
            r = requests.get(f"{api.GAMMA_BASE}/markets", params={
                "limit": 100, "active": "true", "closed": "false",
                "tag": tag, "order": "volume", "ascending": "false"
            }, headers=api.HEADERS, timeout=15)
            if r.ok:
                for raw in r.json():
                    _add(raw)
        except:
            pass
        time.sleep(0.15)

    # Volume pages
    for page in range(3):
        try:
            raw_list = api.get_markets(limit=200, offset=page * 200)
            if not raw_list:
                break
            for raw in raw_list:
                m = api.parse_market(raw)
                if m and m["id"] not in seen and not _is_excluded(m["question"]):
                    q = (m["question"] + " " + " ".join(m.get("tags", []))).lower()
                    if any(kw in q for kw in GEO_KEYWORDS):
                        seen.add(m["id"])
                        markets.append(m)
        except:
            break
        time.sleep(0.15)

    print(f"[DIGEST] Found {len(markets)} Polymarket geo markets")
    return markets


def match_intel_to_markets(intel, markets):
    """
    Match intel headlines to Polymarket markets.
    Returns list of (market, matching_headlines, match_score, urgency_score).
    """
    matched = []

    for m in markets:
        q_words = set(w.lower() for w in m["question"].split() if len(w) > 3)
        matching_headlines = []
        total_urgency = 0

        all_intel = intel["rss"] + intel["gdelt"]
        for item in all_intel:
            t_words = set(w.lower() for w in item["title"].split() if len(w) > 3)
            overlap = q_words & t_words
            if len(overlap) >= 2:
                matching_headlines.append(item)
                total_urgency = max(total_urgency, item["urgency"])

        if matching_headlines:
            # Score: more matching headlines + higher urgency + market uncertainty = better
            uncertainty = min(m["yes_price"], m["no_price"])
            roi_potential = round((1 / max(uncertainty, 0.01) - 1) * 100) if uncertainty < 0.95 else 5

            match_score = (
                len(matching_headlines) * 10 +      # intel coverage
                total_urgency * 5 +                   # time-sensitivity
                (20 if 0.25 <= uncertainty <= 0.50 else 10 if 0.15 <= uncertainty < 0.25 else 5) +  # uncertainty bonus
                (15 if m["volume"] >= 1_000_000 else 10 if m["volume"] >= 500_000 else 5)  # volume
            )

            # RECENCY BONUS: if urgent intel matches an uncertain market = GOLD
            if total_urgency >= 4 and uncertainty >= 0.20:
                match_score += 25  # Major urgency bonus

            matched.append({
                "market": m,
                "headlines": matching_headlines[:5],
                "match_score": match_score,
                "urgency": total_urgency,
                "roi_potential": roi_potential,
                "num_sources": len(set(h["source"] for h in matching_headlines))
            })

    matched.sort(key=lambda x: x["match_score"], reverse=True)
    return matched


# ═══════════════════════════════════════════════════════════════════════
# AI DIGEST — Claude creates the consolidated briefing
# ═══════════════════════════════════════════════════════════════════════

def _ai_digest(intel, top_matches):
    """Claude AI creates the final consolidated digest."""
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        return ""

    # Build intel summary for AI
    urgent_section = ""
    if intel["urgent"]:
        urgent_lines = []
        for item in intel["urgent"][:8]:
            urgent_lines.append(f"  [{item['source']}] {item['title'][:100]} (urgency: {item['urgency']}/10)")
        urgent_section = "URGENT/BREAKING ITEMS:\n" + "\n".join(urgent_lines)

    theme_section = ""
    for theme, items in intel["themes"].items():
        theme_section += f"\n{theme} ({len(items)} headlines):\n"
        for item in items[:3]:
            theme_section += f"  - [{item['source']}] {item['title'][:100]}\n"

    rss_section = "RSS HEADLINES (non-Western media):\n"
    for item in intel["rss"][:10]:
        rss_section += f"  [{item['source']}/{item['region']}] {item['title'][:100]}\n"

    # Build market matches for AI
    market_section = ""
    for i, match in enumerate(top_matches[:5]):
        m = match["market"]
        yes_price = m["yes_price"]
        no_price = m["no_price"]
        vol = m["volume"]
        vol_str = f"${vol/1_000_000:.1f}M" if vol >= 1_000_000 else f"${vol/1_000:.0f}K"

        headline_list = "\n".join(f"    - [{h['source']}] {h['title'][:80]}" for h in match["headlines"][:4])

        market_section += f"""
--- MARKET #{i+1} (Match Score: {match['match_score']}, Urgency: {match['urgency']}/10) ---
Question: {m['question']}
URL: {m['url']}
YES: {yes_price:.0%} | NO: {no_price:.0%} | Volume: {vol_str}
Days left: {m.get('days_left', '?')}
Max ROI potential: {match['roi_potential']}%
Sources covering this: {match['num_sources']}
Matching headlines:
{headline_list}
"""

    prompt = f"""You are an elite geopolitical intelligence analyst creating a DIGEST BRIEFING for a prediction market trader.

PAST 8 HOURS INTEL COLLECTED:
{urgent_section}

{theme_section}

{rss_section}

POLYMARKET MATCHES (markets that match the intel above):
{market_section}

CREATE A DIGEST WITH EXACTLY THIS STRUCTURE:

1. SITUATION REPORT (3-4 sentences max)
   - What happened in the past 8 hours across geopolitics
   - Written so anyone can understand — like explaining to a smart friend
   - Mention specific events, countries, actors

2. THREE BEST PREDICTIONS (for each):
   - MARKET: [the question]
   - DIRECTION: BUY YES or BUY NO (be decisive)
   - ENTRY: Current price
   - TARGET: Expected resolution price
   - EXPECTED ROI: Calculate it
   - WHY: 2-3 sentences explaining the edge — cite specific headlines from the intel
   - URGENCY: Rate LOW / MEDIUM / HIGH / CRITICAL
     If the intel is BREAKING or from the last few hours AND the market hasn't moved yet = CRITICAL
     If the market resolves soon AND there's fresh intel = HIGH

3. BOTTOM LINE (1-2 sentences)
   - The single most important thing happening right now
   - One contrarian angle the data suggests

RULES:
- If there's BREAKING news that matches an uncertain market (25-75% range), mark it CRITICAL urgency
- Explain WHY each prediction is likely to be correct — reference specific intel
- Focus on ROI: uncertain markets (30-70%) with intel edge = best trades
- MAX 500 words. No fluff. No disclaimers.
- Use ONLY plain text. No markdown, no **, no ##. Use CAPS for emphasis.
- Be clear enough that someone with zero context can understand the situation and trades."""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=50
        )
        if r.ok:
            text = r.json().get("content", [{}])[0].get("text", "")
            # Convert any markdown to Telegram HTML
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'^#{1,3}\s*', '', text, flags=re.MULTILINE)
            return text.strip()
        else:
            print(f"[DIGEST/AI] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[DIGEST/AI] {e}")
    return ""


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENTRY — Called by /digest command or scheduler
# ═══════════════════════════════════════════════════════════════════════

def run_digest() -> str:
    """
    Full intel digest:
    1. Collect all sources silently
    2. Fetch Polymarket geo markets
    3. Match intel to markets
    4. AI creates consolidated briefing with 3 predictions
    """
    print("[DIGEST] ═══ INTEL DIGEST STARTED ═══")
    start = time.time()

    # Step 1: Collect intel
    intel = collect_all_intel()
    total_headlines = len(intel["rss"]) + len(intel["gdelt"])
    if total_headlines == 0:
        return "❌ No intel collected — all sources may be down. Try again in a few minutes."

    # Step 2: Fetch Polymarket markets
    markets = fetch_geo_markets()
    if not markets:
        return "❌ No Polymarket geo markets found."

    # Step 3: Match intel to markets
    matched = match_intel_to_markets(intel, markets)
    if not matched:
        return (
            f"📊 Collected {total_headlines} headlines but no strong matches to Polymarket markets.\n"
            f"Try /scan for the full market scanner instead."
        )

    # Step 4: AI digest
    print(f"[DIGEST] Generating AI briefing from {total_headlines} headlines + {len(matched)} market matches...")
    ai_text = _ai_digest(intel, matched[:5])

    elapsed = round(time.time() - start, 1)

    # Step 5: Format output
    lines = []

    # Header
    now = datetime.now(timezone.utc)
    lines.append(f"📋 <b>INTEL DIGEST</b> — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"{'━' * 30}")
    lines.append("")

    # Urgency banner if any
    if intel["urgent"]:
        lines.append(f"🚨 <b>{len(intel['urgent'])} URGENT ITEMS DETECTED</b>")
        lines.append("")

    # AI briefing
    if ai_text:
        lines.append(ai_text)
    else:
        # Fallback if AI fails
        lines.append("<b>TOP MATCHES (AI unavailable):</b>")
        for i, match in enumerate(matched[:3]):
            m = match["market"]
            lines.append(f"\n{i+1}. {m['question']}")
            lines.append(f"   YES {m['yes_price']:.0%} | NO {m['no_price']:.0%} | ROI up to {match['roi_potential']}%")
            lines.append(f"   Urgency: {'CRITICAL' if match['urgency'] >= 6 else 'HIGH' if match['urgency'] >= 4 else 'MEDIUM'}")
            for h in match["headlines"][:2]:
                lines.append(f"   - [{h['source']}] {h['title'][:70]}")

    # Quick links
    lines.append("")
    lines.append(f"{'━' * 30}")
    lines.append(f"🔗 <b>POLYMARKET LINKS</b>")
    medals = ["🥇", "🥈", "🥉"]
    for i, match in enumerate(matched[:3]):
        m = match["market"]
        q = m["question"][:55] + ("..." if len(m["question"]) > 55 else "")
        urgency_tag = " 🚨" if match["urgency"] >= 4 else ""
        lines.append(f"{medals[i]} <a href=\"{m['url']}\">{q}</a>{urgency_tag}")

    # Footer
    lines.append("")
    lines.append(f"{'━' * 30}")
    lines.append(f"📊 {total_headlines} headlines | {len(markets)} markets | {len(matched)} matches | {elapsed}s")
    lines.append(f"💡 /research [url] for deep dive | /scan for full market scan")

    result = "\n".join(lines)
    print(f"[DIGEST] ═══ DIGEST COMPLETE — {elapsed}s ═══")
    return result
