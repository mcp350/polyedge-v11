"""
Polytragent — Wallet Tracker Module
Tracks user's Polymarket positions via public wallet address.
Read-only — no private keys, no signing, no custody.
"""
import os, json, time, requests, logging
from datetime import datetime, timezone

logger = logging.getLogger("wallet_tracker")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
WALLET_FILE = os.path.join(DATA_DIR, "wallets.json")

import config as _cfg
# Polymarket APIs
CLOB_API = _cfg.CLOB_BASE  # Routed through EU proxy when CLOB_PROXY_URL is set
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# Cache TTL
CACHE_TTL = 120  # 2 minutes

_cache = {}  # {chat_id: {"positions": [...], "fetched_at": timestamp}}


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load():
    _ensure_dir()
    if os.path.exists(WALLET_FILE):
        try:
            with open(WALLET_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"wallets": {}}


def _save(data):
    _ensure_dir()
    with open(WALLET_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ═══════════════════════════════════════════════
# WALLET MANAGEMENT
# ═══════════════════════════════════════════════

def connect_wallet(chat_id: str, address: str) -> dict:
    """Store user's public wallet address (read-only tracking)"""
    address = address.strip().lower()
    if not address.startswith("0x") or len(address) != 42:
        return {"success": False, "error": "Invalid Ethereum address. Must be 0x... (42 chars)"}

    data = _load()
    data["wallets"][str(chat_id)] = {
        "address": address,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "label": "",
        "last_synced": "",
        "total_syncs": 0,
    }
    _save(data)

    # Clear cache
    _cache.pop(str(chat_id), None)

    return {"success": True, "address": address}


def disconnect_wallet(chat_id: str) -> bool:
    """Remove wallet connection"""
    data = _load()
    cid = str(chat_id)
    if cid in data["wallets"]:
        del data["wallets"][cid]
        _save(data)
        _cache.pop(cid, None)
        return True
    return False


def get_wallet(chat_id: str) -> dict:
    """Get connected wallet info"""
    data = _load()
    return data["wallets"].get(str(chat_id))


def set_wallet_label(chat_id: str, label: str):
    """Set a friendly label for the wallet"""
    data = _load()
    cid = str(chat_id)
    if cid in data["wallets"]:
        data["wallets"][cid]["label"] = label
        _save(data)


# ═══════════════════════════════════════════════
# POLYMARKET POSITION FETCHING
# ═══════════════════════════════════════════════

def _fetch_clob_positions(address: str) -> list:
    """Fetch open positions from Polymarket CLOB API"""
    try:
        # Try the profile endpoint
        url = f"{DATA_API}/profile/{address}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            profile = resp.json()
            positions = profile.get("positions", [])
            if positions:
                return positions
    except Exception as e:
        logger.debug(f"Profile endpoint failed: {e}")

    try:
        # Try the positions endpoint
        url = f"{DATA_API}/positions"
        params = {"user": address, "sizeThreshold": "0.01"}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json() if isinstance(resp.json(), list) else []
    except Exception as e:
        logger.debug(f"Positions endpoint failed: {e}")

    try:
        # CLOB orders endpoint as fallback
        url = f"{CLOB_API}/data/orders"
        params = {"maker": address, "state": "open"}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json() if isinstance(resp.json(), list) else []
    except Exception as e:
        logger.debug(f"CLOB orders endpoint failed: {e}")

    return []


def _fetch_gamma_event(condition_id: str) -> dict:
    """Fetch event details from Gamma API"""
    try:
        url = f"{GAMMA_API}/markets"
        params = {"clob_token_ids": condition_id, "limit": 1}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            markets = resp.json()
            if markets:
                return markets[0]
    except:
        pass
    return {}


def _fetch_activity(address: str, limit: int = 20) -> list:
    """Fetch recent trading activity for a wallet"""
    try:
        url = f"{DATA_API}/activity"
        params = {"user": address, "limit": limit}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("history", data.get("activities", []))
    except Exception as e:
        logger.debug(f"Activity fetch failed: {e}")

    try:
        # Fallback: try trades endpoint
        url = f"{DATA_API}/trades"
        params = {"maker": address, "limit": limit}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug(f"Trades fetch failed: {e}")

    return []


def _fetch_pnl(address: str) -> dict:
    """Fetch PnL data for a wallet"""
    try:
        url = f"{DATA_API}/profit-loss"
        params = {"user": address}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass

    try:
        url = f"{DATA_API}/profile/{address}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            profile = resp.json()
            return {
                "total_pnl": profile.get("pnl", 0),
                "volume": profile.get("volume", 0),
                "markets_traded": profile.get("marketsTraded", 0),
                "positions_won": profile.get("positionsWon", 0),
                "positions_lost": profile.get("positionsLost", 0),
            }
    except:
        pass

    return {}


# ═══════════════════════════════════════════════
# HIGH-LEVEL FUNCTIONS (called by bot)
# ═══════════════════════════════════════════════

def get_portfolio_data(chat_id: str) -> dict:
    """
    Fetch full portfolio data for a user's connected wallet.
    Returns cached data if fresh enough.
    """
    cid = str(chat_id)
    wallet_info = get_wallet(cid)
    if not wallet_info:
        return {"connected": False}

    address = wallet_info["address"]

    # Check cache
    now = time.time()
    cached = _cache.get(cid)
    if cached and (now - cached.get("fetched_at", 0)) < CACHE_TTL:
        return cached["data"]

    # Fetch fresh data
    positions = _fetch_clob_positions(address)
    pnl_data = _fetch_pnl(address)

    # Process positions
    processed = []
    total_value = 0.0
    total_unrealized_pnl = 0.0

    for pos in positions:
        try:
            # Handle different response formats
            size = float(pos.get("size", pos.get("amount", 0)) or 0)
            if size <= 0:
                continue

            avg_price = float(pos.get("avgPrice", pos.get("avg_price", pos.get("price", 0))) or 0)
            cur_price = float(pos.get("curPrice", pos.get("current_price", avg_price)) or avg_price)
            outcome = pos.get("outcome", pos.get("side", ""))
            market_slug = pos.get("slug", pos.get("market_slug", ""))
            title = pos.get("title", pos.get("question", pos.get("market", market_slug)))
            condition_id = pos.get("conditionId", pos.get("condition_id", pos.get("asset", "")))
            token_id = pos.get("tokenId", pos.get("token_id", ""))

            # If title missing, try to enrich from Gamma
            if not title and condition_id:
                event = _fetch_gamma_event(condition_id)
                title = event.get("question", condition_id[:20] + "...")

            value = size * cur_price
            cost = size * avg_price
            unrealized = value - cost if avg_price > 0 else 0
            pnl_pct = ((cur_price - avg_price) / avg_price * 100) if avg_price > 0 else 0

            total_value += value
            total_unrealized_pnl += unrealized

            processed.append({
                "title": (title or "Unknown Market")[:60],
                "outcome": outcome,
                "size": size,
                "avg_price": avg_price,
                "cur_price": cur_price,
                "value": value,
                "unrealized_pnl": unrealized,
                "pnl_pct": pnl_pct,
                "condition_id": condition_id,
                "slug": market_slug,
            })
        except Exception as e:
            logger.debug(f"Position parse error: {e}")
            continue

    # Sort by value descending
    processed.sort(key=lambda x: x.get("value", 0), reverse=True)

    result = {
        "connected": True,
        "address": address,
        "label": wallet_info.get("label", ""),
        "positions": processed,
        "total_value": total_value,
        "total_positions": len(processed),
        "unrealized_pnl": total_unrealized_pnl,
        "realized_pnl": float(pnl_data.get("total_pnl", pnl_data.get("pnl", 0)) or 0),
        "total_volume": float(pnl_data.get("volume", 0) or 0),
        "markets_traded": int(pnl_data.get("markets_traded", pnl_data.get("marketsTraded", 0)) or 0),
        "positions_won": int(pnl_data.get("positions_won", pnl_data.get("positionsWon", 0)) or 0),
        "positions_lost": int(pnl_data.get("positions_lost", pnl_data.get("positionsLost", 0)) or 0),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    # Update cache
    _cache[cid] = {"data": result, "fetched_at": now}

    # Update last_synced
    data = _load()
    if cid in data["wallets"]:
        data["wallets"][cid]["last_synced"] = result["fetched_at"]
        data["wallets"][cid]["total_syncs"] = data["wallets"][cid].get("total_syncs", 0) + 1
        _save(data)

    return result


def get_recent_activity(chat_id: str, limit: int = 10) -> list:
    """Get recent trades for the connected wallet"""
    wallet_info = get_wallet(str(chat_id))
    if not wallet_info:
        return []

    raw = _fetch_activity(wallet_info["address"], limit)
    trades = []

    for t in raw[:limit]:
        try:
            trades.append({
                "type": t.get("type", t.get("side", "trade")),
                "title": (t.get("title", t.get("question", t.get("market", ""))))[:50],
                "outcome": t.get("outcome", t.get("side", "")),
                "size": float(t.get("size", t.get("amount", 0)) or 0),
                "price": float(t.get("price", 0) or 0),
                "timestamp": t.get("timestamp", t.get("createdAt", t.get("created_at", ""))),
            })
        except:
            continue

    return trades


# ═══════════════════════════════════════════════
# FORMATTING (for Telegram messages)
# ═══════════════════════════════════════════════

def _fmt_usd(val):
    if val >= 1_000_000:
        return f"${val/1_000_000:,.1f}M"
    elif val >= 1_000:
        return f"${val/1_000:,.1f}K"
    return f"${val:,.2f}"


def _pnl_emoji(val):
    if val > 0:
        return "🟢"
    elif val < 0:
        return "🔴"
    return "⚪"


def format_portfolio_summary(chat_id: str) -> str:
    """Format a portfolio summary message for Telegram"""
    data = get_portfolio_data(str(chat_id))

    if not data.get("connected"):
        return (
            "📊 <b>Portfolio Dashboard</b>\n\n"
            "No wallet connected.\n\n"
            "Connect your Polymarket wallet in Settings to see:\n"
            "• Live positions & P/L\n"
            "• Portfolio value tracking\n"
            "• Trade history\n"
            "• Win rate analytics"
        )

    addr = data["address"]
    label = data.get("label", "")
    display = f"{label} " if label else ""
    display += f"({addr[:6]}...{addr[-4:]})"

    total_pnl = data["unrealized_pnl"] + data["realized_pnl"]
    win_total = data["positions_won"] + data["positions_lost"]
    win_rate = (data["positions_won"] / win_total * 100) if win_total > 0 else 0

    msg = (
        f"📊 <b>Portfolio Dashboard</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👛 {display}\n\n"
        f"💼 <b>Portfolio Value:</b> {_fmt_usd(data['total_value'])}\n"
        f"📂 Open Positions: {data['total_positions']}\n\n"
        f"<b>📈 Performance</b>\n"
        f"{_pnl_emoji(data['unrealized_pnl'])} Unrealized P/L: {_fmt_usd(data['unrealized_pnl'])}\n"
        f"{_pnl_emoji(data['realized_pnl'])} Realized P/L: {_fmt_usd(data['realized_pnl'])}\n"
        f"{_pnl_emoji(total_pnl)} Total P/L: {_fmt_usd(total_pnl)}\n\n"
        f"<b>📊 Stats</b>\n"
        f"Markets Traded: {data['markets_traded']}\n"
        f"Total Volume: {_fmt_usd(data['total_volume'])}\n"
        f"Win Rate: {win_rate:.0f}% ({data['positions_won']}W / {data['positions_lost']}L)\n"
    )

    return msg


def format_positions_detail(chat_id: str) -> str:
    """Format detailed position view"""
    data = get_portfolio_data(str(chat_id))

    if not data.get("connected"):
        return "📂 <b>No wallet connected.</b>\nGo to Settings > Connect Wallet."

    positions = data.get("positions", [])
    if not positions:
        return (
            "📂 <b>Open Positions</b>\n\n"
            "No open positions found.\n\n"
            f"Wallet: {data['address'][:6]}...{data['address'][-4:]}"
        )

    msg = f"📂 <b>Open Positions ({len(positions)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for i, p in enumerate(positions[:15], 1):
        pnl_e = _pnl_emoji(p["unrealized_pnl"])
        side = f" ({p['outcome']})" if p.get("outcome") else ""
        pnl_str = f"+{p['pnl_pct']:.1f}%" if p["pnl_pct"] >= 0 else f"{p['pnl_pct']:.1f}%"

        msg += (
            f"<b>{i}. {p['title']}</b>{side}\n"
            f"   Size: {p['size']:.1f} @ ${p['avg_price']:.3f} → ${p['cur_price']:.3f}\n"
            f"   Value: {_fmt_usd(p['value'])} {pnl_e} {pnl_str}\n\n"
        )

    if len(positions) > 15:
        msg += f"<i>...and {len(positions) - 15} more positions</i>\n\n"

    msg += f"💼 Total: {_fmt_usd(data['total_value'])}"

    return msg


def format_activity(chat_id: str) -> str:
    """Format recent activity"""
    trades = get_recent_activity(str(chat_id))

    if not trades:
        wallet = get_wallet(str(chat_id))
        if not wallet:
            return "📜 <b>No wallet connected.</b>\nGo to Settings > Connect Wallet."
        return "📜 <b>Recent Activity</b>\n\nNo recent trades found."

    msg = "📜 <b>Recent Trades</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for t in trades:
        side = t.get("type", "trade").upper()
        emoji = "🟢" if side == "BUY" else "🔴" if side == "SELL" else "🔵"
        title = t.get("title", "Unknown")
        size = t.get("size", 0)
        price = t.get("price", 0)
        ts = t.get("timestamp", "")[:16].replace("T", " ")

        msg += f"{emoji} <b>{side}</b> — {title}\n"
        if size > 0:
            msg += f"   {size:.1f} shares @ ${price:.3f}"
        if ts:
            msg += f"   ({ts})"
        msg += "\n\n"

    return msg
