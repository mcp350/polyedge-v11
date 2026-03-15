"""
BTC ORDER BOOK — Support & Resistance from Binance
Fetches real-time order book depth to find key price levels.
Used for BTC price prediction markets on Polymarket.
"""

import requests
import re
from datetime import datetime, timezone
from config import ANTHROPIC_API_KEY

HEADERS = {"User-Agent": "PolymarketBot/2.0"}


def fetch_btc_orderbook(depth=500):
    """Fetch BTC/USDT order book from Binance."""
    try:
        r = requests.get("https://api.binance.com/api/v3/depth", params={
            "symbol": "BTCUSDT",
            "limit": depth
        }, headers=HEADERS, timeout=10)
        if r.ok:
            data = r.json()
            return {
                "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
                "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
            }
    except Exception as e:
        print(f"[BTC] Binance orderbook error: {e}")
    return None


def fetch_btc_price():
    """Get current BTC price from Binance."""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", params={
            "symbol": "BTCUSDT"
        }, headers=HEADERS, timeout=10)
        if r.ok:
            data = r.json()
            return {
                "price": float(data.get("lastPrice", 0)),
                "high_24h": float(data.get("highPrice", 0)),
                "low_24h": float(data.get("lowPrice", 0)),
                "change_24h": float(data.get("priceChangePercent", 0)),
                "volume_24h": float(data.get("quoteVolume", 0)),
            }
    except Exception as e:
        print(f"[BTC] Price error: {e}")
    return None


def find_support_resistance(orderbook, price_info, levels=5):
    """
    Analyze order book to find support and resistance levels.
    Clusters large orders at price levels.
    """
    if not orderbook or not price_info:
        return None

    current_price = price_info["price"]
    bids = orderbook["bids"]  # Buy orders (support)
    asks = orderbook["asks"]  # Sell orders (resistance)

    # Find clusters of large orders
    # Group bids into $500 buckets
    bid_clusters = {}
    for price, qty in bids:
        bucket = round(price / 500) * 500
        bid_clusters[bucket] = bid_clusters.get(bucket, 0) + (price * qty)

    ask_clusters = {}
    for price, qty in asks:
        bucket = round(price / 500) * 500
        ask_clusters[bucket] = ask_clusters.get(bucket, 0) + (price * qty)

    # Sort by total USD value (biggest walls)
    support_levels = sorted(bid_clusters.items(), key=lambda x: x[1], reverse=True)[:levels]
    resistance_levels = sorted(ask_clusters.items(), key=lambda x: x[1], reverse=True)[:levels]

    # Sort by price (closest to current)
    support_levels.sort(key=lambda x: x[0], reverse=True)
    resistance_levels.sort(key=lambda x: x[0])

    # Calculate bid/ask ratio (buying vs selling pressure)
    total_bids = sum(p * q for p, q in bids)
    total_asks = sum(p * q for p, q in asks)
    bid_ask_ratio = total_bids / total_asks if total_asks > 0 else 1.0

    # Identify the biggest wall
    all_walls = (
        [(p, v, "SUPPORT") for p, v in bid_clusters.items()] +
        [(p, v, "RESISTANCE") for p, v in ask_clusters.items()]
    )
    all_walls.sort(key=lambda x: x[1], reverse=True)
    biggest_wall = all_walls[0] if all_walls else None

    return {
        "current_price": current_price,
        "support": support_levels,
        "resistance": resistance_levels,
        "bid_ask_ratio": round(bid_ask_ratio, 3),
        "total_bid_usd": total_bids,
        "total_ask_usd": total_asks,
        "biggest_wall": biggest_wall,
        "pressure": "BULLISH" if bid_ask_ratio > 1.1 else "BEARISH" if bid_ask_ratio < 0.9 else "NEUTRAL"
    }


def _find_btc_polymarket_markets():
    """Find BTC-related prediction markets on Polymarket."""
    import polymarket_api as api
    markets = []

    # Search for BTC markets
    for query_tag in ["Bitcoin", "BTC", "Crypto"]:
        try:
            r = requests.get(f"{api.GAMMA_BASE}/markets", params={
                "limit": 50, "active": "true", "closed": "false",
                "tag": query_tag, "order": "volume", "ascending": "false"
            }, headers=HEADERS, timeout=15)
            if r.ok:
                for raw in r.json():
                    m = api.parse_market(raw)
                    if m and m["volume"] >= 50000:
                        q = m["question"].lower()
                        if any(w in q for w in ["bitcoin", "btc", "crypto"]):
                            markets.append(m)
        except:
            pass

    # Deduplicate
    seen = set()
    unique = []
    for m in markets:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)

    unique.sort(key=lambda x: x["volume"], reverse=True)
    return unique[:10]


def _ai_btc_analysis(price_info, sr_data, btc_markets):
    """Claude AI analyzes BTC order book and makes predictions."""
    if not ANTHROPIC_API_KEY or "YOUR" in ANTHROPIC_API_KEY:
        return ""

    # Format support/resistance
    support_text = ""
    for price, value in sr_data["support"]:
        support_text += f"  ${price:,.0f} — ${value/1_000_000:.2f}M buy wall\n"

    resistance_text = ""
    for price, value in sr_data["resistance"]:
        resistance_text += f"  ${price:,.0f} — ${value/1_000_000:.2f}M sell wall\n"

    # Format BTC Polymarket markets
    market_text = ""
    for m in btc_markets[:5]:
        market_text += f"  {m['question'][:70]} | YES {m['yes_price']:.0%} | Vol ${m['volume']/1_000:.0f}K\n"

    prompt = f"""You are a crypto market analyst with order book expertise. Analyze BTC's current position.

CURRENT BTC DATA:
Price: ${price_info['price']:,.2f}
24H High: ${price_info['high_24h']:,.2f}
24H Low: ${price_info['low_24h']:,.2f}
24H Change: {price_info['change_24h']:.2f}%
24H Volume: ${price_info['volume_24h']/1_000_000_000:.2f}B

ORDER BOOK ANALYSIS:
Bid/Ask Ratio: {sr_data['bid_ask_ratio']} ({sr_data['pressure']})
Total Buy Orders: ${sr_data['total_bid_usd']/1_000_000:.1f}M
Total Sell Orders: ${sr_data['total_ask_usd']/1_000_000:.1f}M

KEY SUPPORT LEVELS (buy walls):
{support_text}
KEY RESISTANCE LEVELS (sell walls):
{resistance_text}
Biggest Wall: ${sr_data['biggest_wall'][0]:,.0f} ({sr_data['biggest_wall'][2]}) — ${sr_data['biggest_wall'][1]/1_000_000:.2f}M

BTC POLYMARKET PREDICTIONS:
{market_text}

Provide:

1. BIAS: BULLISH / BEARISH / NEUTRAL — based on order book pressure
2. KEY LEVELS:
   - Immediate support: $X (if this breaks, next stop $Y)
   - Immediate resistance: $X (if this breaks, target $Y)
3. ORDER BOOK SIGNAL: What the bid/ask ratio and wall positions tell us
4. PREDICTION: Where BTC goes in the next 24H and 7D with % confidence
5. POLYMARKET TRADES: For each BTC market listed, should you BUY YES or NO? At what entry?
6. RISK: What event could invalidate this analysis

Max 400 words. Plain text only. Be decisive with price targets."""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 700,
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
        print(f"[BTC/AI] {e}")
    return ""


def run_btc_orderbook() -> str:
    """
    Full BTC analysis:
    1. Fetch Binance order book
    2. Find support/resistance levels
    3. Find BTC Polymarket markets
    4. AI analysis + trade calls
    """
    print("[BTC] ═══ BTC ORDER BOOK ANALYSIS ═══")

    # Fetch data
    price_info = fetch_btc_price()
    if not price_info:
        return "Could not fetch BTC price data."

    orderbook = fetch_btc_orderbook(depth=500)
    if not orderbook:
        return "Could not fetch BTC order book."

    sr_data = find_support_resistance(orderbook, price_info)
    if not sr_data:
        return "Could not analyze order book."

    btc_markets = _find_btc_polymarket_markets()

    # AI analysis
    print("[BTC] Running AI analysis...")
    ai_text = _ai_btc_analysis(price_info, sr_data, btc_markets)

    now = datetime.now(timezone.utc)

    # Build output
    lines = []
    lines.append(f"₿ <b>BTC ORDER BOOK</b> — {now.strftime('%H:%M UTC')}")
    lines.append(f"{'━' * 30}")
    lines.append(f"💰 <b>${price_info['price']:,.0f}</b> ({'+' if price_info['change_24h'] > 0 else ''}{price_info['change_24h']:.1f}% 24H)")
    lines.append(f"📊 H: ${price_info['high_24h']:,.0f} | L: ${price_info['low_24h']:,.0f}")
    lines.append(f"📈 Vol: ${price_info['volume_24h']/1_000_000_000:.1f}B")
    lines.append("")

    # Pressure indicator
    ratio = sr_data["bid_ask_ratio"]
    pressure_emoji = "🟢" if sr_data["pressure"] == "BULLISH" else "🔴" if sr_data["pressure"] == "BEARISH" else "⚪"
    lines.append(f"{pressure_emoji} <b>Pressure: {sr_data['pressure']}</b> (B/A ratio: {ratio})")
    lines.append(f"   Bids: ${sr_data['total_bid_usd']/1_000_000:.1f}M | Asks: ${sr_data['total_ask_usd']/1_000_000:.1f}M")
    lines.append("")

    # Support levels
    lines.append(f"🟢 <b>SUPPORT</b>")
    for price, value in sr_data["support"][:4]:
        bar_len = min(10, int(value / 500_000))
        bar = "█" * bar_len
        lines.append(f"  ${price:,.0f} — ${value/1_000_000:.1f}M {bar}")

    lines.append(f"\n🔴 <b>RESISTANCE</b>")
    for price, value in sr_data["resistance"][:4]:
        bar_len = min(10, int(value / 500_000))
        bar = "█" * bar_len
        lines.append(f"  ${price:,.0f} — ${value/1_000_000:.1f}M {bar}")

    # AI analysis
    if ai_text:
        lines.append(f"\n{'━' * 30}")
        lines.append(f"🧠 <b>AI ANALYSIS</b>")
        lines.append("")
        lines.append(ai_text)

    # BTC Polymarket links
    if btc_markets:
        lines.append(f"\n{'━' * 30}")
        lines.append(f"🔗 <b>BTC POLYMARKET BETS</b>")
        for m in btc_markets[:5]:
            q = m["question"][:50] + ("..." if len(m["question"]) > 50 else "")
            lines.append(f'  <a href="{m["url"]}">{q}</a> (YES {m["yes_price"]:.0%})')

    lines.append(f"\n💡 /swings for all market movers | /top10 for best picks")

    return "\n".join(lines)
