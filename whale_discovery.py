"""
POLYTRAGENT — Whale Discovery Engine
Discovers, ranks, and tracks the most profitable wallets on Polymarket.
Sources: Polymarket leaderboard, CLOB API, on-chain data.

Features:
- Fetch top wallets by PnL, volume, win rate
- Rank and filter wallets
- Cache leaderboard data
- Provide wallet stats for user selection
"""

import os, json, time, logging, requests
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("polytragent.whale_discovery")

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"
HEADERS = {"User-Agent": "Polytragent/2.0", "Accept": "application/json"}

CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "whale_cache.json")
CACHE_TTL = 3600  # 1 hour cache


# ═══════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════

def _load_cache() -> dict:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    if not os.path.exists(CACHE_FILE):
        return {"wallets": [], "updated_at": "", "categories": {}}
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return {"wallets": [], "updated_at": "", "categories": {}}


def _save_cache(data: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _is_cache_fresh() -> bool:
    cache = _load_cache()
    updated = cache.get("updated_at", "")
    if not updated:
        return False
    try:
        dt = datetime.fromisoformat(updated)
        return (datetime.now(timezone.utc) - dt).total_seconds() < CACHE_TTL
    except:
        return False


# ═══════════════════════════════════════════════
# FETCH TOP WALLETS FROM POLYMARKET
# ═══════════════════════════════════════════════

def fetch_leaderboard(limit: int = 100) -> list:
    """Fetch top traders from Polymarket leaderboard API."""
    wallets = []
    try:
        # Try the Polymarket profiles/leaderboard endpoint
        for offset in range(0, limit, 50):
            r = requests.get(f"{GAMMA_BASE}/leaderboard",
                params={"limit": min(50, limit - offset), "offset": offset,
                        "window": "all"},
                headers=HEADERS, timeout=15)
            if r.ok:
                data = r.json()
                entries = data if isinstance(data, list) else data.get("results", data.get("leaderboard", []))
                wallets.extend(entries)
            else:
                break
            time.sleep(0.5)
    except Exception as e:
        log.error(f"Leaderboard fetch error: {e}")

    # Fallback: try profiles endpoint
    if not wallets:
        try:
            r = requests.get(f"{DATA_BASE}/profiles/leaderboard",
                params={"limit": limit, "window": "all", "sort": "pnl"},
                headers=HEADERS, timeout=15)
            if r.ok:
                data = r.json()
                wallets = data if isinstance(data, list) else data.get("results", [])
        except Exception as e:
            log.error(f"Profiles leaderboard error: {e}")

    return wallets


def fetch_wallet_stats(address: str) -> Optional[dict]:
    """Fetch detailed stats for a specific wallet."""
    try:
        r = requests.get(f"{DATA_BASE}/profiles/{address.lower()}",
            headers=HEADERS, timeout=15)
        if r.ok:
            return r.json()
    except Exception as e:
        log.error(f"Wallet stats error for {address[:10]}: {e}")
    return None


def fetch_wallet_positions(address: str) -> list:
    """Fetch current open positions for a wallet."""
    try:
        r = requests.get(f"https://clob.polymarket.com/positions",
            params={"user": address.lower()},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else data.get("positions", [])
    except Exception as e:
        log.error(f"Positions error: {e}")
    return []


def fetch_wallet_trades(address: str, limit: int = 50) -> list:
    """Fetch recent trades for a wallet."""
    try:
        r = requests.get(f"{DATA_BASE}/trades",
            params={"maker": address.lower(), "limit": limit},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else data.get("data", data.get("results", []))
    except Exception as e:
        log.error(f"Trades error: {e}")
    return []


# ═══════════════════════════════════════════════
# RANKING & CATEGORIZATION
# ═══════════════════════════════════════════════

def rank_wallets(wallets: list) -> list:
    """
    Score and rank wallets by profitability.
    Score = weighted combination of PnL, win rate, volume, and consistency.
    """
    scored = []
    for w in wallets:
        try:
            pnl = float(w.get("pnl", w.get("profit", w.get("totalPnl", 0))) or 0)
            volume = float(w.get("volume", w.get("totalVolume", 0)) or 0)
            win_rate = float(w.get("winRate", w.get("win_rate", 0)) or 0)
            num_trades = int(w.get("numTrades", w.get("trades_count", w.get("positions", 0))) or 0)

            # Score formula: PnL weight 40%, win rate 30%, volume 20%, consistency 10%
            pnl_score = min(pnl / 10000, 10) if pnl > 0 else max(pnl / 10000, -5)
            wr_score = (win_rate / 100) * 10 if win_rate > 0 else 0
            vol_score = min(volume / 100000, 10) if volume > 0 else 0
            consistency = min(num_trades / 50, 10) if num_trades > 0 else 0

            total_score = (pnl_score * 0.4) + (wr_score * 0.3) + (vol_score * 0.2) + (consistency * 0.1)

            address = w.get("address", w.get("proxyWallet", w.get("id", "")))
            name = w.get("name", w.get("username", w.get("displayName", "")))

            scored.append({
                "address": address,
                "name": name or f"{address[:6]}...{address[-4:]}" if address else "Unknown",
                "pnl": pnl,
                "volume": volume,
                "win_rate": win_rate,
                "num_trades": num_trades,
                "score": round(total_score, 2),
                "category": _categorize_wallet(pnl, volume, win_rate, num_trades),
                "raw": w,
            })
        except Exception as e:
            log.error(f"Score error: {e}")
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def _categorize_wallet(pnl, volume, win_rate, num_trades) -> str:
    """Categorize a wallet by trading style."""
    if pnl > 100000:
        return "🐋 Mega Whale"
    elif pnl > 50000:
        return "🦈 Whale"
    elif pnl > 10000 and win_rate > 65:
        return "🎯 Sharp"
    elif volume > 500000 and num_trades > 100:
        return "⚡ Active Trader"
    elif win_rate > 70:
        return "🧠 Smart Money"
    elif pnl > 5000:
        return "💰 Profitable"
    else:
        return "📊 Trader"


# ═══════════════════════════════════════════════
# MAIN DISCOVERY FUNCTIONS
# ═══════════════════════════════════════════════

def discover_top_wallets(force_refresh: bool = False) -> list:
    """
    Get ranked list of top Polymarket wallets.
    Uses cache if fresh, otherwise fetches from API.
    """
    if not force_refresh and _is_cache_fresh():
        cache = _load_cache()
        return cache.get("wallets", [])

    log.info("Discovering top wallets...")
    raw_wallets = fetch_leaderboard(100)

    if not raw_wallets:
        # Return cached even if stale
        cache = _load_cache()
        return cache.get("wallets", [])

    ranked = rank_wallets(raw_wallets)

    # Save to cache
    cache = {
        "wallets": ranked[:100],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "categories": {
            "mega_whales": [w for w in ranked if "Mega" in w.get("category", "")][:10],
            "whales": [w for w in ranked if "Whale" in w.get("category", "") and "Mega" not in w.get("category", "")][:10],
            "sharp": [w for w in ranked if "Sharp" in w.get("category", "")][:10],
            "smart_money": [w for w in ranked if "Smart" in w.get("category", "")][:10],
            "active": [w for w in ranked if "Active" in w.get("category", "")][:10],
        },
        "total_fetched": len(raw_wallets),
    }
    _save_cache(cache)

    return ranked[:100]


def get_wallet_detail(address: str) -> Optional[dict]:
    """Get detailed info for a specific whale wallet."""
    stats = fetch_wallet_stats(address)
    if not stats:
        return None

    positions = fetch_wallet_positions(address)
    recent_trades = fetch_wallet_trades(address, 20)

    pnl = float(stats.get("pnl", stats.get("profit", stats.get("totalPnl", 0))) or 0)
    volume = float(stats.get("volume", stats.get("totalVolume", 0)) or 0)
    win_rate = float(stats.get("winRate", stats.get("win_rate", 0)) or 0)

    return {
        "address": address,
        "name": stats.get("name", stats.get("username", f"{address[:6]}...{address[-4:]}")),
        "pnl": pnl,
        "volume": volume,
        "win_rate": win_rate,
        "num_positions": len(positions),
        "positions": positions[:10],
        "recent_trades": recent_trades[:10],
        "category": _categorize_wallet(pnl, volume, win_rate, len(recent_trades)),
        "profile_url": f"https://polymarket.com/profile/{address}",
    }


def search_wallets(query: str) -> list:
    """Search wallets by address prefix or name."""
    wallets = discover_top_wallets()
    query_lower = query.lower()

    results = []
    for w in wallets:
        if query_lower in w.get("address", "").lower() or \
           query_lower in w.get("name", "").lower():
            results.append(w)

    return results[:20]


def get_categories() -> dict:
    """Get wallets organized by category."""
    cache = _load_cache()
    return cache.get("categories", {})


# ═══════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════

def format_leaderboard(wallets: list, page: int = 0, per_page: int = 10) -> str:
    """Format wallet leaderboard for Telegram."""
    if not wallets:
        return "📭 No wallets discovered yet. Try /refresh_whales"

    start = page * per_page
    end = start + per_page
    page_wallets = wallets[start:end]

    lines = [
        f"🐋 <b>Top Polymarket Wallets</b>",
        f"<i>Page {page + 1}/{max(1, (len(wallets) + per_page - 1) // per_page)}</i>",
        "",
    ]

    for i, w in enumerate(page_wallets, start + 1):
        name = w.get("name", "Unknown")[:20]
        pnl = w.get("pnl", 0)
        wr = w.get("win_rate", 0)
        cat = w.get("category", "")
        addr = w.get("address", "")[:10]

        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"{i}. {cat} <b>{name}</b>\n"
            f"   {pnl_emoji} PnL: ${pnl:,.0f} | WR: {wr:.0f}% | <code>{addr}...</code>"
        )

    lines.append(f"\n💡 /track <number> to follow a wallet")
    return "\n".join(lines)


def format_wallet_detail(detail: dict) -> str:
    """Format detailed wallet info for Telegram."""
    if not detail:
        return "❌ Wallet not found"

    pnl = detail.get("pnl", 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"

    lines = [
        f"{detail.get('category', '')} <b>{detail.get('name', 'Unknown')}</b>",
        "",
        f"📊 <b>Stats:</b>",
        f"   {pnl_emoji} PnL: <b>${pnl:,.2f}</b>",
        f"   📈 Volume: <b>${detail.get('volume', 0):,.0f}</b>",
        f"   🎯 Win Rate: <b>{detail.get('win_rate', 0):.1f}%</b>",
        f"   📂 Open Positions: <b>{detail.get('num_positions', 0)}</b>",
        "",
        f"🔗 <a href=\"{detail.get('profile_url', '#')}\">View on Polymarket</a>",
        f"📋 Address: <code>{detail.get('address', '')}</code>",
    ]

    positions = detail.get("positions", [])
    if positions:
        lines.append("\n📂 <b>Open Positions:</b>")
        for p in positions[:5]:
            title = p.get("title", p.get("question", ""))[:35]
            outcome = p.get("outcome", "")
            size = float(p.get("size", 0))
            lines.append(f"   • {title} — {outcome} ({size:.0f} shares)")

    trades = detail.get("recent_trades", [])
    if trades:
        lines.append("\n📜 <b>Recent Trades:</b>")
        for t in trades[:5]:
            side = t.get("side", "?").upper()
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
            emoji = "🟩" if side == "BUY" else "🟥"
            lines.append(f"   {emoji} {side} ${price:.2f} × {size:.0f}")

    return "\n".join(lines)
