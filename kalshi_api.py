"""
MODULE 8 — Kalshi Cross-Market Comparison
Compares Polymarket prices against Kalshi for the same events.
Free public API, no authentication needed for reading.
"""

import requests
import re
import telegram_client as tg

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "PolymarketBot/1.0", "Accept": "application/json"}


def _get_kalshi_markets(limit: int = 200, status: str = "open") -> list:
    """Fetch open markets from Kalshi."""
    try:
        r = requests.get(f"{KALSHI_BASE}/markets", params={
            "limit": limit,
            "status": status
        }, headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data.get("markets", [])
    except Exception as e:
        print(f"[KALSHI] API error: {e}")
    return []


def _get_kalshi_market(ticker: str) -> dict:
    """Fetch a single Kalshi market by ticker."""
    try:
        r = requests.get(f"{KALSHI_BASE}/markets/{ticker}",
                         headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data.get("market", {})
    except Exception as e:
        print(f"[KALSHI] Market fetch error: {e}")
    return {}


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    # Remove common filler words
    for w in ["will", "the", "a", "an", "by", "in", "of", "to", "be", "before", "after"]:
        text = text.replace(f" {w} ", " ")
    return " ".join(text.split())


def _keyword_overlap(text1: str, text2: str) -> float:
    """Calculate keyword overlap between two texts."""
    words1 = set(_normalize(text1).split())
    words2 = set(_normalize(text2).split())
    if not words1 or not words2:
        return 0.0
    overlap = words1 & words2
    return len(overlap) / min(len(words1), len(words2))


def find_kalshi_match(polymarket_question: str) -> dict:
    """
    Find a matching Kalshi market for a Polymarket question.
    Returns dict with kalshi_title, yes_price, no_price, ticker, volume.
    """
    markets = _get_kalshi_markets(limit=200)
    if not markets:
        return {}

    best_match = None
    best_score = 0.0

    for m in markets:
        title = m.get("title", "") or m.get("subtitle", "")
        score = _keyword_overlap(polymarket_question, title)
        if score > best_score and score >= 0.4:
            best_score = score
            yes_bid = float(m.get("yes_bid_dollars", 0) or 0)
            yes_ask = float(m.get("yes_ask_dollars", 0) or 0)
            yes_mid = (yes_bid + yes_ask) / 2 if (yes_bid and yes_ask) else yes_bid or yes_ask
            no_bid = float(m.get("no_bid_dollars", 0) or 0)
            no_ask = float(m.get("no_ask_dollars", 0) or 0)
            no_mid = (no_bid + no_ask) / 2 if (no_bid and no_ask) else no_bid or no_ask
            last_price = float(m.get("last_price_dollars", 0) or 0)
            volume = int(m.get("volume_fp", 0) or m.get("volume", 0) or 0)

            best_match = {
                "ticker": m.get("ticker", ""),
                "title": title,
                "yes_price": yes_mid or last_price,
                "no_price": no_mid or (1 - last_price) if last_price else 0,
                "volume": volume,
                "score": best_score
            }

    return best_match or {}


def compare_markets(poly_question: str, poly_yes: float, poly_no: float) -> str:
    """
    Compare a Polymarket position against Kalshi.
    Returns formatted string for Telegram.
    """
    match = find_kalshi_match(poly_question)
    if not match:
        return "  📊 Kalshi: no matching market found"

    k_yes = match["yes_price"]
    k_no = match["no_price"]
    ticker = match["ticker"]

    if k_yes == 0 and k_no == 0:
        return f"  📊 Kalshi: matched <b>{ticker}</b> but no active prices"

    # Calculate divergence
    poly_yes_pct = poly_yes * 100
    kalshi_yes_pct = k_yes * 100
    div = poly_yes_pct - kalshi_yes_pct

    lines = [
        f"  📊 <b>Kalshi Match:</b> {match['title'][:60]}",
        f"     Kalshi YES: <b>{k_yes:.0%}</b> | Polymarket YES: <b>{poly_yes:.0%}</b>",
        f"     Gap: <b>{'+' if div > 0 else ''}{div:.0f}pt</b>"
    ]

    if abs(div) >= 10:
        lines.append(f"     ⚡ <b>SIGNIFICANT CROSS-MARKET DIVERGENCE</b>")
    elif abs(div) >= 5:
        lines.append(f"     ✅ Moderate divergence")
    else:
        lines.append(f"     ⚪ Markets aligned")

    return "\n".join(lines)


def run_kalshi_scan() -> str:
    """Scan Kalshi for geopolitical markets and format as a briefing."""
    lines = ["📊 <b>KALSHI MARKET SCAN</b>\n"]

    markets = _get_kalshi_markets(limit=100)
    geo_keywords = [
        "war", "military", "russia", "ukraine", "china", "taiwan", "iran",
        "israel", "nato", "nuclear", "sanctions", "ceasefire", "tariff",
        "trump", "election", "fed", "rate", "bitcoin", "crypto"
    ]

    found = 0
    for m in markets:
        title = (m.get("title", "") or "").lower()
        if not any(kw in title for kw in geo_keywords):
            continue

        yes_bid = float(m.get("yes_bid_dollars", 0) or 0)
        last = float(m.get("last_price_dollars", 0) or 0)
        price = yes_bid or last
        vol = int(float(m.get("volume_fp", 0) or m.get("volume", 0) or 0))
        ticker = m.get("ticker", "")

        if price > 0:
            lines.append(
                f"  • <b>{m.get('title', '')[:65]}</b>\n"
                f"    YES: {price:.0%} | Vol: {vol:,} | {ticker}"
            )
            found += 1
            if found >= 10:
                break

    if found == 0:
        lines.append("  No geopolitical markets found on Kalshi right now.")

    return "\n".join(lines)
