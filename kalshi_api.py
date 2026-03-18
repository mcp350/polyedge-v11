"""
MODULE 8 — Kalshi API Integration (Full)
Cross-market comparison, event data, orderbook, trades.
API: https://api.elections.kalshi.com/trade-api/v2
Public endpoints: markets, events, trades (no auth for reading)
"""

import requests
import re
import time
import telegram_client as tg

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "Polytragent/1.0", "Accept": "application/json"}

# ═══════════════════════════════════════════════
# API ENDPOINTS — Markets
# ═══════════════════════════════════════════════

def _get_kalshi_markets(limit: int = 200, status: str = "open", cursor: str = None) -> list:
    """Fetch markets from Kalshi with pagination."""
    try:
        params = {"limit": min(limit, 200), "status": status}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{KALSHI_BASE}/markets", params=params,
                         headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data.get("markets", [])
    except Exception as e:
        print(f"[KALSHI] Markets API error: {e}")
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


def get_market_orderbook(ticker: str, depth: int = 10) -> dict:
    """Fetch order book for a market — shows bid/ask depth for YES and NO."""
    try:
        r = requests.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook",
                         params={"depth": depth},
                         headers=HEADERS, timeout=15)
        if r.ok:
            return r.json().get("orderbook", {})
    except Exception as e:
        print(f"[KALSHI] Orderbook error: {e}")
    return {}


def get_market_candlesticks(ticker: str, period: str = "1d", limit: int = 30) -> list:
    """Fetch OHLC candlestick data for a market."""
    try:
        r = requests.get(f"{KALSHI_BASE}/markets/{ticker}/candlesticks",
                         params={"period_interval": period, "limit": limit},
                         headers=HEADERS, timeout=15)
        if r.ok:
            return r.json().get("candlesticks", [])
    except Exception as e:
        print(f"[KALSHI] Candlesticks error: {e}")
    return []


# ═══════════════════════════════════════════════
# API ENDPOINTS — Events
# ═══════════════════════════════════════════════

def get_events(limit: int = 200, status: str = "open", with_markets: bool = True) -> list:
    """Fetch events from Kalshi (non-multivariate)."""
    try:
        params = {
            "limit": min(limit, 200),
            "status": status,
            "with_nested_markets": with_markets,
        }
        r = requests.get(f"{KALSHI_BASE}/events", params=params,
                         headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data.get("events", [])
    except Exception as e:
        print(f"[KALSHI] Events API error: {e}")
    return []


def get_event(event_ticker: str) -> dict:
    """Fetch a single event by ticker."""
    try:
        r = requests.get(f"{KALSHI_BASE}/events/{event_ticker}",
                         params={"with_nested_markets": True},
                         headers=HEADERS, timeout=15)
        if r.ok:
            return r.json().get("event", {})
    except Exception as e:
        print(f"[KALSHI] Event fetch error: {e}")
    return {}


# ═══════════════════════════════════════════════
# API ENDPOINTS — Trades
# ═══════════════════════════════════════════════

def get_trades(ticker: str = None, limit: int = 100, min_ts: int = None) -> list:
    """Fetch recent trades, optionally filtered by market ticker."""
    try:
        params = {"limit": min(limit, 1000)}
        if ticker:
            params["ticker"] = ticker
        if min_ts:
            params["min_ts"] = min_ts
        r = requests.get(f"{KALSHI_BASE}/markets/trades", params=params,
                         headers=HEADERS, timeout=15)
        if r.ok:
            return r.json().get("trades", [])
    except Exception as e:
        print(f"[KALSHI] Trades API error: {e}")
    return []


# ═══════════════════════════════════════════════
# API ENDPOINTS — Series
# ═══════════════════════════════════════════════

def get_series(series_ticker: str) -> dict:
    """Fetch a series (template for recurring events)."""
    try:
        r = requests.get(f"{KALSHI_BASE}/series/{series_ticker}",
                         headers=HEADERS, timeout=15)
        if r.ok:
            return r.json().get("series", {})
    except Exception as e:
        print(f"[KALSHI] Series error: {e}")
    return {}


# ═══════════════════════════════════════════════
# MATCHING & COMPARISON
# ═══════════════════════════════════════════════

def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
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


def _parse_market_prices(m: dict) -> dict:
    """Extract standardized price data from a Kalshi market object."""
    yes_bid = float(m.get("yes_bid", 0) or m.get("yes_bid_dollars", 0) or 0)
    yes_ask = float(m.get("yes_ask", 0) or m.get("yes_ask_dollars", 0) or 0)
    no_bid = float(m.get("no_bid", 0) or m.get("no_bid_dollars", 0) or 0)
    no_ask = float(m.get("no_ask", 0) or m.get("no_ask_dollars", 0) or 0)
    last_price = float(m.get("last_price", 0) or m.get("last_price_dollars", 0) or 0)

    # Kalshi prices can be in cents (0-100) or dollars (0-1)
    # Normalize to 0-1 range
    if yes_bid > 1:
        yes_bid /= 100
    if yes_ask > 1:
        yes_ask /= 100
    if no_bid > 1:
        no_bid /= 100
    if no_ask > 1:
        no_ask /= 100
    if last_price > 1:
        last_price /= 100

    yes_mid = (yes_bid + yes_ask) / 2 if (yes_bid and yes_ask) else yes_bid or yes_ask or last_price
    no_mid = (no_bid + no_ask) / 2 if (no_bid and no_ask) else no_bid or no_ask or (1 - last_price if last_price else 0)

    volume = int(float(m.get("volume", 0) or m.get("volume_fp", 0) or 0))
    open_interest = int(float(m.get("open_interest", 0) or 0))

    return {
        "yes_price": yes_mid,
        "no_price": no_mid,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "last_price": last_price,
        "volume": volume,
        "open_interest": open_interest,
        "spread": round(abs(yes_ask - yes_bid), 4) if yes_ask and yes_bid else 0,
    }


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
        if score > best_score and score >= 0.35:
            best_score = score
            prices = _parse_market_prices(m)
            best_match = {
                "ticker": m.get("ticker", ""),
                "event_ticker": m.get("event_ticker", ""),
                "title": title,
                "yes_price": prices["yes_price"],
                "no_price": prices["no_price"],
                "volume": prices["volume"],
                "open_interest": prices["open_interest"],
                "spread": prices["spread"],
                "score": best_score,
                "raw": m,
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

    if match.get("volume"):
        lines.append(f"     Vol: {match['volume']:,} | OI: {match.get('open_interest', 0):,}")

    if abs(div) >= 10:
        lines.append(f"     ⚡ <b>SIGNIFICANT CROSS-MARKET DIVERGENCE</b>")
    elif abs(div) >= 5:
        lines.append(f"     ✅ Moderate divergence — potential edge")
    else:
        lines.append(f"     ⚪ Markets aligned")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
# FORMATTED OUTPUTS — For Telegram
# ═══════════════════════════════════════════════

def format_kalshi_event(event_ticker: str) -> str:
    """Full event breakdown for Telegram."""
    event = get_event(event_ticker)
    if not event:
        return f"❌ Event {event_ticker} not found on Kalshi."

    title = event.get("title", event_ticker)
    markets = event.get("markets", [])

    msg = f"📊 <b>Kalshi Event: {title}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if not markets:
        msg += "No active markets in this event.\n"
        return msg

    for m in markets[:15]:
        mtitle = m.get("title", m.get("ticker", "?"))[:55]
        prices = _parse_market_prices(m)
        status = m.get("status", "?")
        ticker = m.get("ticker", "")

        msg += f"<b>{mtitle}</b>\n"
        msg += f"  YES: ${prices['yes_price']:.2f} | NO: ${prices['no_price']:.2f}"
        if prices["spread"]:
            msg += f" | Spread: ${prices['spread']:.2f}"
        msg += "\n"
        if prices["volume"]:
            msg += f"  Vol: {prices['volume']:,} | OI: {prices['open_interest']:,}\n"
        msg += f"  Status: {status} | {ticker}\n\n"

    return msg


def format_orderbook(ticker: str) -> str:
    """Format orderbook for Telegram display."""
    ob = get_market_orderbook(ticker, depth=5)
    if not ob:
        return f"❌ No orderbook data for {ticker}"

    market = _get_kalshi_market(ticker)
    title = market.get("title", ticker)[:50] if market else ticker

    msg = f"📈 <b>Orderbook: {title}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    yes_bids = ob.get("yes", [])
    no_bids = ob.get("no", [])

    if yes_bids:
        msg += "<b>YES Side:</b>\n"
        for level in yes_bids[:5]:
            price = level.get("price", 0)
            qty = level.get("quantity", 0)
            if price > 1:
                price /= 100
            msg += f"  ${price:.2f} — {qty:,} contracts\n"
        msg += "\n"

    if no_bids:
        msg += "<b>NO Side:</b>\n"
        for level in no_bids[:5]:
            price = level.get("price", 0)
            qty = level.get("quantity", 0)
            if price > 1:
                price /= 100
            msg += f"  ${price:.2f} — {qty:,} contracts\n"

    return msg


def format_recent_trades(ticker: str, limit: int = 10) -> str:
    """Format recent trades for a market."""
    trades = get_trades(ticker=ticker, limit=limit)
    if not trades:
        return f"No recent trades for {ticker}"

    market = _get_kalshi_market(ticker)
    title = market.get("title", ticker)[:50] if market else ticker

    msg = f"💱 <b>Recent Trades: {title}</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for t in trades[:limit]:
        price = float(t.get("yes_price", 0) or t.get("price", 0) or 0)
        if price > 1:
            price /= 100
        qty = t.get("count", 0) or t.get("quantity", 0)
        side = t.get("taker_side", "?")
        ts = t.get("created_time", "")[:16].replace("T", " ")
        msg += f"  {ts} | {side.upper()} | ${price:.2f} | {qty} contracts\n"

    return msg


# ═══════════════════════════════════════════════
# SCAN — Geopolitical & Trending Markets
# ═══════════════════════════════════════════════

def run_kalshi_scan() -> str:
    """Scan Kalshi for trending and geopolitical markets."""
    lines = ["📊 <b>KALSHI MARKET SCAN</b>\n"]

    markets = _get_kalshi_markets(limit=200)
    if not markets:
        return "📊 <b>KALSHI</b>\n\nCould not fetch Kalshi markets."

    geo_keywords = [
        "war", "military", "russia", "ukraine", "china", "taiwan", "iran",
        "israel", "nato", "nuclear", "sanctions", "ceasefire", "tariff",
        "trump", "election", "fed", "rate", "bitcoin", "crypto", "recession",
        "congress", "supreme", "border", "immigration", "ai", "openai"
    ]

    # Sort by volume (highest first)
    def get_vol(m):
        return float(m.get("volume", 0) or m.get("volume_fp", 0) or 0)
    markets.sort(key=get_vol, reverse=True)

    found = 0
    for m in markets:
        title = (m.get("title", "") or "").lower()
        if not any(kw in title for kw in geo_keywords):
            continue

        prices = _parse_market_prices(m)
        ticker = m.get("ticker", "")

        if prices["yes_price"] > 0:
            lines.append(
                f"  • <b>{m.get('title', '')[:65]}</b>\n"
                f"    YES: {prices['yes_price']:.0%} | "
                f"Vol: {prices['volume']:,} | "
                f"OI: {prices['open_interest']:,} | {ticker}"
            )
            found += 1
            if found >= 15:
                break

    if found == 0:
        lines.append("  No trending geopolitical markets found.")
    else:
        lines.append(f"\n  📊 {found} markets found, sorted by volume")

    return "\n".join(lines)


def run_kalshi_top_volume() -> str:
    """Top 10 Kalshi markets by volume — for Global Stats."""
    markets = _get_kalshi_markets(limit=200)
    if not markets:
        return ""

    def get_vol(m):
        return float(m.get("volume", 0) or m.get("volume_fp", 0) or 0)
    markets.sort(key=get_vol, reverse=True)

    msg = "📊 <b>Kalshi — Top Markets by Volume</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for i, m in enumerate(markets[:10], 1):
        title = (m.get("title", "?"))[:55]
        prices = _parse_market_prices(m)
        msg += f"{i}. <b>{title}</b>\n"
        msg += f"   YES: ${prices['yes_price']:.2f} | Vol: {prices['volume']:,}\n\n"

    return msg
