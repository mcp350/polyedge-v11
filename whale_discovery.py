"""
POLYTRAGENT — Whale Directory + Real-Time Trade Monitor
Curated list of top Polymarket whale wallets.
Users browse the directory and follow whales to get real-time trade notifications.

Sources: Polymarket leaderboard (verified March 2026), on-chain data.
"""

import os, json, time, logging, requests
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("polytragent.whale_discovery")

import config as _cfg
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = _cfg.CLOB_BASE  # Routed through EU proxy when CLOB_PROXY_URL is set
HEADERS = {"User-Agent": "Polytragent/2.0", "Accept": "application/json"}

CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "whale_cache.json")

# ═══════════════════════════════════════════════
# CURATED WHALE DIRECTORY — Top Polymarket Traders
# Verified from polymarket.com/leaderboard (March 2026)
# ═══════════════════════════════════════════════

WHALE_DIRECTORY = [
    # ── MEGA WHALES (PnL > $2M) ──
    {
        "address": "0x02227b8f5a9636e895607edd3185ed6ee5598ff7",
        "name": "HorizonSplendidView",
        "category": "🐋 Mega Whale",
        "pnl": 4016108, "volume": 12394130, "win_rate": 68,
        "bio": "Top monthly PnL. Massive conviction bets on macro events.",
    },
    {
        "address": "0xc2e7800b5af46e6093872b177b7a5e7f0563be51",
        "name": "beachboy4",
        "category": "🐋 Mega Whale",
        "pnl": 3762305, "volume": 14130713, "win_rate": 65,
        "bio": "Consistent top-3 performer. Diversified across political & crypto markets.",
    },
    {
        "address": "0xefbc5fec8d7b0acdc8911bdd9a98d6964308f9a2",
        "name": "reachingthesky",
        "category": "🐋 Mega Whale",
        "pnl": 3742635, "volume": 13750267, "win_rate": 63,
        "bio": "High-volume whale with strong PnL. Multi-market exposure.",
    },
    {
        "address": "0x492442eab586f242b53bda933fd5de859c8a3782",
        "name": "Multicolored-Self",
        "category": "🐋 Mega Whale",
        "pnl": 3043726, "volume": 99506296, "win_rate": 49,
        "bio": "Massive volume trader. $99M+ total volume. High-frequency style.",
    },
    {
        "address": "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",
        "name": "HighVolume Alpha",
        "category": "🐋 Mega Whale",
        "pnl": 2475414, "volume": 220030741, "win_rate": 52,
        "bio": "Highest volume on Polymarket. $220M+ traded. Market maker style.",
    },
    {
        "address": "0x019782cab5d844f02bafb71f512758be78579f3c",
        "name": "majorexploiter",
        "category": "🐋 Mega Whale",
        "pnl": 2416975, "volume": 6949025, "win_rate": 72,
        "bio": "Highest win rate among whales. Sharp information edge.",
    },
    # ── WHALES (PnL $1M-$2M) ──
    {
        "address": "0xbddf61af533ff524d27154e589d2d7a81510c684",
        "name": "Countryside",
        "category": "🦈 Whale",
        "pnl": 1732605, "volume": 35612171, "win_rate": 71,
        "bio": "High win rate + strong PnL. Consistent across market types.",
    },
    {
        "address": "0x37c1874a60d348903594a96703e0507c518fc53a",
        "name": "CemeterySun",
        "category": "🦈 Whale",
        "pnl": 1616707, "volume": 72240349, "win_rate": 58,
        "bio": "Heavy volume trader with $72M+ volume. Market-making + directional.",
    },
    {
        "address": "0xdc876e6873772d38716fda7f2452a78d426d7ab6",
        "name": "432614799197",
        "category": "🦈 Whale",
        "pnl": 1522283, "volume": 33468641, "win_rate": 60,
        "bio": "Anonymous whale. Strong PnL to volume ratio.",
    },
    {
        "address": "0x93abbc022ce98d6f45d4444b594791cc4b7a9723",
        "name": "gatorr",
        "category": "🦈 Whale",
        "pnl": 1505104, "volume": 19122361, "win_rate": 64,
        "bio": "Well-known Polymarket trader. Solid fundamentals-based approach.",
    },
    {
        "address": "0xf195721ad850377c96cd634457c70cd9e8308057",
        "name": "CERTuo",
        "category": "🦈 Whale",
        "pnl": 1459921, "volume": 7419260, "win_rate": 67,
        "bio": "High conviction, lower volume. Excellent PnL efficiency.",
    },
    {
        "address": "0xee613b3fc183ee44f9da9c05f53e2da107e3debf",
        "name": "sovereign2013",
        "category": "🦈 Whale",
        "pnl": 1257843, "volume": 75999705, "win_rate": 52,
        "bio": "$76M volume. One of the most active traders on Polymarket.",
    },
    {
        "address": "0x2005d16a84ceefa912d4e380cd32e7ff827875ea",
        "name": "RN1",
        "category": "🦈 Whale",
        "pnl": 1133350, "volume": 83647673, "win_rate": 55,
        "bio": "Top-10 by volume. $83M+ traded. Consistent performer.",
    },
    {
        "address": "0x59a0744db1f39ff3afccd175f80e6e8dfc239a09",
        "name": "Blessed-Sunshine",
        "category": "🦈 Whale",
        "pnl": 1078644, "volume": 5480943, "win_rate": 69,
        "bio": "Sharp trader. Great PnL-to-volume ratio. Information edge.",
    },
    {
        "address": "0x03e8a544e97eeff5753bc1e90d46e5ef22af1697",
        "name": "weflyhigh",
        "category": "🦈 Whale",
        "pnl": 1010520, "volume": 42441000, "win_rate": 59,
        "bio": "$42M volume. Strong directional bets across categories.",
    },
    # ── SHARP TRADERS (PnL $500K-$1M, high win rate) ──
    {
        "address": "0x204f72f35326db932158cba6adff0b9a1da95e14",
        "name": "swisstony",
        "category": "🎯 Sharp",
        "pnl": 951669, "volume": 146776289, "win_rate": 56,
        "bio": "#2 all-time by volume. $146M+ traded. Market maker.",
    },
    {
        "address": "0x8f037a2e4fd49d11267f4ab874ab7ba745ac64d6",
        "name": "Anointed-Connect",
        "category": "🎯 Sharp",
        "pnl": 939966, "volume": 20253250, "win_rate": 62,
        "bio": "Consistent mid-volume trader with solid win rate.",
    },
    {
        "address": "0x07921379f7b31ef93da634b688b2fe36897db778",
        "name": "ewelmealt",
        "category": "🎯 Sharp",
        "pnl": 927802, "volume": 4394653, "win_rate": 70,
        "bio": "Low volume, high precision. Top 5 PnL efficiency.",
    },
    {
        "address": "0x507e52ef684ca2dd91f90a9d26d149dd3288beae",
        "name": "GamblingIsAllYouNeed",
        "category": "🎯 Sharp",
        "pnl": 807647, "volume": 54870943, "win_rate": 57,
        "bio": "Massive volume degen. $54M traded with strong profit.",
    },
    {
        "address": "0xe90bec87d9ef430f27f9dcfe72c34b76967d5da2",
        "name": "gmanas",
        "category": "🎯 Sharp",
        "pnl": 679166, "volume": 12123037, "win_rate": 61,
        "bio": "Balanced approach. Strong across political and crypto markets.",
    },
    # ── SMART MONEY (High volume, profitable) ──
    {
        "address": "0xd84c2b6d65dc596f49c7b6aadd6d74ca91e407b9",
        "name": "BoneReader",
        "category": "🧠 Smart Money",
        "pnl": 549257, "volume": 93543763, "win_rate": 54,
        "bio": "$93M volume. Algorithmic-style trading patterns.",
    },
    {
        "address": "0x1f0ebc543b2d411f66947041625c0aa1ce61cf86",
        "name": "SilentRunner",
        "category": "🧠 Smart Money",
        "pnl": 386132, "volume": 80953772, "win_rate": 53,
        "bio": "$80M volume. Consistent profit from high-frequency approach.",
    },
    {
        "address": "0xd218e474776403a330142299f7796e8ba32eb5c9",
        "name": "PhemexWhale",
        "category": "🧠 Smart Money",
        "pnl": 900130, "volume": 951421, "win_rate": 65,
        "bio": "Featured in Phemex top-10. High win rate specialist.",
    },
    {
        "address": "0x9d84ce0306f8551e02efef1680475fc0f1dc1344",
        "name": "MegaProfitMaker",
        "category": "🧠 Smart Money",
        "pnl": 2618357, "volume": 967535, "win_rate": 63,
        "bio": "30-day PnL champion. Extreme profit-to-volume ratio.",
    },
    {
        "address": "0x6480542954b70a674a74bd1a6015dec362dc8dc5",
        "name": "tripping",
        "category": "⚡ Active Trader",
        "pnl": 9826, "volume": 126119517, "win_rate": 50,
        "bio": "#3 all-time by volume. $126M traded. Pure market maker.",
    },
]


# ═══════════════════════════════════════════════
# DIRECTORY ACCESS
# ═══════════════════════════════════════════════

def get_directory() -> list:
    """Get the full curated whale directory."""
    return WHALE_DIRECTORY


def get_whale_by_index(index: int) -> dict:
    """Get a whale from directory by 1-based index."""
    if 1 <= index <= len(WHALE_DIRECTORY):
        return WHALE_DIRECTORY[index - 1]
    return {}


def get_whale_by_address(address: str) -> dict:
    """Find a whale by wallet address."""
    addr_lower = address.lower()
    for w in WHALE_DIRECTORY:
        if w["address"].lower() == addr_lower:
            return w
    return {}


def get_whales_by_category(category_key: str) -> list:
    """Get whales filtered by category keyword."""
    return [w for w in WHALE_DIRECTORY if category_key.lower() in w.get("category", "").lower()]


def search_whales(query: str) -> list:
    """Search whales by name or address."""
    q = query.lower()
    return [w for w in WHALE_DIRECTORY if q in w.get("name", "").lower() or q in w.get("address", "").lower()]


# ═══════════════════════════════════════════════
# LIVE WALLET DATA (optional enrichment)
# ═══════════════════════════════════════════════

def fetch_wallet_positions(address: str) -> list:
    """Fetch current open positions for a wallet from CLOB API."""
    try:
        r = requests.get(f"{CLOB_BASE}/positions",
            params={"user": address.lower()},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else data.get("positions", [])
    except Exception as e:
        log.warning(f"Positions fetch error for {address[:10]}: {e}")
    return []


def fetch_wallet_trades(address: str, limit: int = 20) -> list:
    """Fetch recent trades for a wallet."""
    trades = []
    try:
        r = requests.get(f"{CLOB_BASE}/trades",
            params={"maker": address.lower(), "limit": str(limit)},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            trades = data if isinstance(data, list) else data.get("trades", data.get("results", []))
    except Exception as e:
        log.warning(f"Trades fetch error: {e}")

    # Also check as taker
    try:
        r = requests.get(f"{CLOB_BASE}/trades",
            params={"taker": address.lower(), "limit": str(limit)},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            taker_trades = data if isinstance(data, list) else data.get("trades", data.get("results", []))
            trades.extend(taker_trades)
    except:
        pass

    trades.sort(key=lambda t: t.get("timestamp", t.get("created_at", "")), reverse=True)
    return trades[:limit]


# ═══════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════

def format_directory_page(page: int = 0, per_page: int = 5) -> tuple:
    """
    Format a page of the whale directory for Telegram.
    Returns (message_text, inline_buttons).
    """
    whales = WHALE_DIRECTORY
    total_pages = max(1, (len(whales) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_whales = whales[start:end]

    lines = [
        f"🐋 <b>Top Polymarket Whales</b>",
        f"<i>Page {page + 1}/{total_pages} — {len(whales)} verified traders</i>\n",
    ]

    for i, w in enumerate(page_whales, start + 1):
        pnl = w["pnl"]
        pnl_str = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
        wr = w.get("win_rate", 0)
        lines.append(
            f"<b>{i}. {w['category']} {w['name']}</b>\n"
            f"   💰 PnL: {pnl_str} | WR: {wr}%\n"
            f"   📊 Vol: ${w['volume']:,.0f}\n"
            f"   💬 {w.get('bio', '')}"
        )
        lines.append("")

    # Build follow buttons (one per whale on this page)
    buttons = []
    for i, w in enumerate(page_whales, start + 1):
        short_name = w["name"][:16]
        buttons.append([{"text": f"➕ Follow #{i} {short_name}", "callback_data": f"whale_follow_{i}"}])

    # Navigation row
    nav = []
    if page > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"whale_page_{page - 1}"})
    if page < total_pages - 1:
        nav.append({"text": "Next ➡️", "callback_data": f"whale_page_{page + 1}"})
    if nav:
        buttons.append(nav)

    buttons.append([{"text": "📋 My Follows", "callback_data": "ct_following"},
                    {"text": "← Menu", "callback_data": "main_menu"}])

    return "\n".join(lines), buttons


def format_whale_detail(whale: dict) -> tuple:
    """Format a single whale's detail view. Returns (text, buttons)."""
    if not whale:
        return "❌ Whale not found.", [[{"text": "← Back", "callback_data": "menu_whales"}]]

    pnl = whale["pnl"]
    pnl_str = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
    addr = whale["address"]

    text = (
        f"{whale['category']} <b>{whale['name']}</b>\n\n"
        f"💰 PnL: <b>{pnl_str}</b>\n"
        f"📊 Volume: ${whale['volume']:,.0f}\n"
        f"🎯 Win Rate: {whale.get('win_rate', 0)}%\n"
        f"💬 {whale.get('bio', '')}\n\n"
        f"📋 Address: <code>{addr}</code>\n"
        f"🔗 <a href=\"https://polymarket.com/profile/{addr}\">View on Polymarket</a>"
    )

    idx = next((i for i, w in enumerate(WHALE_DIRECTORY, 1) if w["address"] == addr), 0)
    buttons = []
    if idx:
        buttons.append([{"text": f"➕ Follow This Whale", "callback_data": f"whale_follow_{idx}"}])
    buttons.append([{"text": "🐋 Back to Directory", "callback_data": "menu_whales"},
                    {"text": "← Menu", "callback_data": "main_menu"}])

    return text, buttons
