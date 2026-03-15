"""
PRICE SWINGS — Top 10 biggest movers (1D, 12H, 1H)
Tracks YES and NO price movements to identify catalysts and momentum.
Uses Polymarket CLOB price history API.
"""

import time
import re
import requests
from datetime import datetime, timezone
import polymarket_api as api
from config import ANTHROPIC_API_KEY

HEADERS = {"User-Agent": "PolymarketBot/2.0"}


def _get_price_history(token_id, interval="1d"):
    """
    Fetch price history from Polymarket CLOB API.
    interval: 1h, 6h, 1d, 1w, 1m, max
    """
    try:
        r = requests.get(f"{api.CLOB_BASE}/prices-history", params={
            "market": token_id,
            "interval": interval,
            "fidelity": 5
        }, headers=HEADERS, timeout=12)
        if r.ok:
            data = r.json()
            return data.get("history", [])
    except Exception as e:
        print(f"[SWINGS] Price history error: {e}")
    return []


def _calc_swing(history):
    """
    Calculate price swing from history data.
    Returns (current_price, start_price, change_pct, direction).
    """
    if not history or len(history) < 2:
        return None

    start_price = history[0].get("p", 0)
    end_price = history[-1].get("p", 0)

    if start_price == 0:
        return None

    change_pct = ((end_price - start_price) / start_price) * 100
    direction = "UP" if change_pct > 0 else "DOWN"

    return {
        "current": end_price,
        "start": start_price,
        "change_pct": round(change_pct, 2),
        "direction": direction,
        "abs_change": abs(change_pct)
    }


def _get_condition_token(market_raw):
    """Extract the YES token ID for CLOB price history."""
    # Try different fields where token IDs might be stored
    tokens = market_raw.get("clobTokenIds")
    if tokens:
        if isinstance(tokens, str):
            try:
                import json
                tokens = json.loads(tokens)
            except:
                tokens = []
        if isinstance(tokens, list) and len(tokens) > 0:
            return tokens[0]  # First token is YES

    # Try condition_id
    cid = market_raw.get("conditionId") or market_raw.get("condition_id")
    if cid:
        return cid

    return None


def scan_price_swings(timeframe="1d", top_n=10):
    """
    Scan all active markets for biggest price swings.
    timeframe: "1h", "6h", "1d"
    Returns sorted list of biggest movers.
    """
    print(f"[SWINGS] Scanning price swings ({timeframe})...")

    # Fetch active markets
    all_markets = []
    for page in range(3):
        try:
            raw_list = api.get_markets(limit=200, offset=page * 200)
            if not raw_list:
                break
            for raw in raw_list:
                m = api.parse_market(raw)
                if m and m["volume"] >= 100_000:
                    m["_raw"] = raw
                    all_markets.append(m)
        except:
            break
        time.sleep(0.15)

    print(f"[SWINGS] Checking {len(all_markets)} markets for {timeframe} swings...")

    swings = []
    checked = 0

    for m in all_markets[:80]:  # Check top 80 by volume
        token_id = _get_condition_token(m.get("_raw", m.get("raw", {})))
        if not token_id:
            continue

        history = _get_price_history(token_id, interval=timeframe)
        swing = _calc_swing(history)

        if swing and swing["abs_change"] >= 1.0:  # At least 1% move
            swings.append({
                "market": m,
                "swing": swing,
                "timeframe": timeframe
            })
            checked += 1

        time.sleep(0.15)  # Rate limit

    swings.sort(key=lambda x: x["swing"]["abs_change"], reverse=True)
    print(f"[SWINGS] Found {len(swings)} significant movers in {timeframe}")
    return swings[:top_n]


def _ai_swing_analysis(swings_1d, swings_12h, swings_1h):
    """Claude AI analyzes the biggest movers and identifies catalysts."""
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        return ""

    swing_text = ""

    for label, swings in [("24 HOUR", swings_1d), ("12 HOUR", swings_12h), ("1 HOUR", swings_1h)]:
        swing_text += f"\n=== TOP MOVERS — {label} ===\n"
        for i, s in enumerate(swings[:5]):
            m = s["market"]
            sw = s["swing"]
            vol_str = f"${m['volume']/1_000_000:.1f}M" if m["volume"] >= 1_000_000 else f"${m['volume']/1_000:.0f}K"
            swing_text += (
                f"{i+1}. {m['question'][:70]}\n"
                f"   Price: {sw['start']:.0%} -> {sw['current']:.0%} ({sw['direction']} {sw['abs_change']:.1f}%)\n"
                f"   Volume: {vol_str} | Days left: {m.get('days_left', '?')}\n"
            )

    prompt = f"""You are a prediction market analyst tracking price movements. Analyze these price swings and identify likely catalysts.

{swing_text}

For each timeframe (24H, 12H, 1H), provide:

1. BIGGEST MOVER: What happened? What news/event caused this swing?
2. MOMENTUM PLAY: Which swing looks like it will continue? Why?
3. REVERSAL CANDIDATE: Which swing looks overextended and might reverse?

Then give:
URGENT TRADES: Any swings happening RIGHT NOW (1H) that you should act on immediately
TREND CONFIRMATION: Any swings consistent across all 3 timeframes (strong trend)
CATALYST WATCH: What upcoming events could cause the NEXT big swing

RULES:
- Be specific about likely catalysts (news events, deadlines, rulings)
- Flag if a swing looks like insider trading or manipulation
- For each trade suggestion, give entry price and direction
- Max 500 words. Plain text only. No markdown. No disclaimers."""

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
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'^#{1,3}\s*', '', text, flags=re.MULTILINE)
            return text.strip()
        else:
            print(f"[SWINGS/AI] HTTP {r.status_code}")
    except Exception as e:
        print(f"[SWINGS/AI] {e}")
    return ""


def run_swings() -> str:
    """
    Full swing scan:
    1. Scan 1D, 12H, 1H price swings
    2. AI analyzes catalysts and momentum
    """
    print("[SWINGS] ═══ SWING SCAN STARTED ═══")
    start = time.time()

    # Scan all 3 timeframes
    swings_1d = scan_price_swings("1d", top_n=10)
    swings_12h = scan_price_swings("6h", top_n=10)  # CLOB uses 6h not 12h
    swings_1h = scan_price_swings("1h", top_n=10)

    if not swings_1d and not swings_12h and not swings_1h:
        return "No significant price swings detected. Markets are quiet."

    # AI analysis
    print("[SWINGS] Running AI catalyst analysis...")
    ai_text = _ai_swing_analysis(swings_1d, swings_12h, swings_1h)

    elapsed = round(time.time() - start, 1)
    now = datetime.now(timezone.utc)

    # Build output
    lines = []
    lines.append(f"📈 <b>PRICE SWINGS</b> — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"{'━' * 30}")

    # Show top movers per timeframe
    for label, swings, emoji in [("24H", swings_1d, "📊"), ("12H", swings_12h, "⏰"), ("1H", swings_1h, "⚡")]:
        if swings:
            lines.append(f"\n{emoji} <b>TOP 5 — {label}</b>")
            for i, s in enumerate(swings[:5]):
                m = s["market"]
                sw = s["swing"]
                arrow = "🟢" if sw["direction"] == "UP" else "🔴"
                q = m["question"][:45] + ("..." if len(m["question"]) > 45 else "")
                lines.append(
                    f"  {arrow} {sw['direction']} {sw['abs_change']:.1f}% | "
                    f"{sw['start']:.0%}→{sw['current']:.0%} | {q}"
                )

    # AI analysis
    if ai_text:
        lines.append(f"\n{'━' * 30}")
        lines.append(f"🧠 <b>CATALYST ANALYSIS</b>")
        lines.append("")
        lines.append(ai_text)

    # Quick links to biggest movers
    lines.append(f"\n{'━' * 30}")
    lines.append(f"🔗 <b>BIGGEST MOVERS</b>")
    all_swings = sorted(
        swings_1d + swings_12h + swings_1h,
        key=lambda x: x["swing"]["abs_change"],
        reverse=True
    )
    seen_urls = set()
    link_count = 0
    for s in all_swings:
        m = s["market"]
        if m["url"] not in seen_urls and link_count < 5:
            seen_urls.add(m["url"])
            q = m["question"][:45] + ("..." if len(m["question"]) > 45 else "")
            sw = s["swing"]
            arrow = "🟢" if sw["direction"] == "UP" else "🔴"
            lines.append(f'{arrow} <a href="{m["url"]}">{q}</a> ({sw["direction"]} {sw["abs_change"]:.1f}%)')
            link_count += 1

    lines.append(f"\n📊 Scanned markets across 3 timeframes | {elapsed}s")
    lines.append(f"💡 /top10 for best picks | /research [url] for deep dive")

    return "\n".join(lines)
