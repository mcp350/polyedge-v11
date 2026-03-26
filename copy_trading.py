"""
COPY TRADING ENGINE — Track whale wallets on Polymarket and mirror their trades.
Non-custodial: we send SIGNALS only (buy/sell alerts) — users execute themselves.

Features:
- Track top Polymarket wallets (by PnL / win rate)
- Detect new positions + changes in real-time
- Leaderboard of tracked traders
- Per-user follow system (follow specific wallets)
- Signal generation when followed wallets trade

Storage: copy_trading.json alongside main.py
Polymarket API: https://gamma-api.polymarket.com + CLOB API
"""

import json, os, time, requests, hashlib
from datetime import datetime, timezone, timedelta
import user_store

FILE = os.path.join(os.path.dirname(__file__), "copy_trading.json")

# ═══════════════════════════════════════════════
# POLYMARKET PROFILE / WALLET APIs
# ═══════════════════════════════════════════════

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Known top wallets to seed the leaderboard (public profiles)
DEFAULT_WALLETS = [
    # These will be discovered dynamically, but we seed a few known whales
]

HEADERS = {
    "User-Agent": "Polytragent/1.0",
    "Accept": "application/json",
}

# ═══════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════

def _load():
    if not os.path.exists(FILE):
        return {
            "wallets": {},        # wallet_id -> {alias, address, last_positions, stats, added_at}
            "followers": {},      # chat_id -> [wallet_ids they follow]
            "signals": [],        # recent signals [{wallet, market, action, amount, timestamp}]
            "leaderboard": [],    # cached leaderboard
            "settings": {
                "scan_interval": 300,   # 5 min
                "min_trade_usd": 500,   # minimum trade to signal
                "max_signals_per_hour": 20,
            },
            "last_scan": "",
        }
    try:
        with open(FILE) as f:
            return json.load(f)
    except:
        return _load.__wrapped__() if hasattr(_load, '__wrapped__') else {
            "wallets": {}, "followers": {}, "signals": [], "leaderboard": [],
            "settings": {"scan_interval": 300, "min_trade_usd": 500, "max_signals_per_hour": 20},
            "last_scan": "",
        }

def _save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ═══════════════════════════════════════════════
# POLYMARKET WALLET DATA FETCHING
# ═══════════════════════════════════════════════

def fetch_wallet_profile(address: str) -> dict:
    """Fetch a wallet's Polymarket profile (PnL, volume, positions)."""
    try:
        # Try gamma API profile endpoint
        r = requests.get(f"{GAMMA_BASE}/profiles/{address}", headers=HEADERS, timeout=15)
        if r.ok:
            return r.json()
    except Exception as e:
        print(f"[COPY] Profile fetch error for {address[:10]}: {e}")
    return {}

def fetch_wallet_positions(address: str) -> list:
    """Fetch current open positions for a wallet."""
    positions = []
    try:
        # Polymarket CLOB positions endpoint
        r = requests.get(f"{CLOB_BASE}/positions",
            params={"user": address}, headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                positions = data
            elif isinstance(data, dict):
                positions = data.get("positions", data.get("results", []))
    except Exception as e:
        print(f"[COPY] Positions fetch error: {e}")

    # Fallback: try gamma API
    if not positions:
        try:
            r = requests.get(f"{GAMMA_BASE}/positions",
                params={"user": address, "sizeThreshold": "0.1"},
                headers=HEADERS, timeout=15)
            if r.ok:
                data = r.json()
                positions = data if isinstance(data, list) else data.get("positions", [])
        except:
            pass

    return positions

def fetch_wallet_trades(address: str, limit: int = 50) -> list:
    """Fetch recent trades for a wallet address."""
    trades = []
    try:
        r = requests.get(f"{CLOB_BASE}/trades",
            params={"maker": address, "limit": str(limit)},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            trades = data if isinstance(data, list) else data.get("trades", data.get("results", []))
    except Exception as e:
        print(f"[COPY] Trades fetch error: {e}")

    # Also check as taker
    try:
        r = requests.get(f"{CLOB_BASE}/trades",
            params={"taker": address, "limit": str(limit)},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            taker_trades = data if isinstance(data, list) else data.get("trades", data.get("results", []))
            trades.extend(taker_trades)
    except:
        pass

    # Sort by timestamp descending
    trades.sort(key=lambda t: t.get("timestamp", t.get("created_at", "")), reverse=True)
    return trades[:limit]

def discover_top_wallets(limit: int = 20) -> list:
    """Discover top-performing wallets from Polymarket leaderboard."""
    wallets = []
    try:
        # Polymarket leaderboard API
        r = requests.get(f"{GAMMA_BASE}/leaderboard",
            params={"limit": str(limit), "window": "all"},
            headers=HEADERS, timeout=15)
        if r.ok:
            data = r.json()
            leaders = data if isinstance(data, list) else data.get("leaders", data.get("results", []))
            for entry in leaders:
                addr = entry.get("address", entry.get("proxyWallet", entry.get("user", "")))
                if addr:
                    wallets.append({
                        "address": addr,
                        "username": entry.get("username", entry.get("name", "")),
                        "pnl": float(entry.get("pnl", entry.get("profit", 0))),
                        "volume": float(entry.get("volume", entry.get("totalVolume", 0))),
                        "markets_traded": int(entry.get("marketsTraded", entry.get("numMarkets", 0))),
                        "rank": entry.get("rank", 0),
                    })
    except Exception as e:
        print(f"[COPY] Leaderboard fetch error: {e}")

    # Fallback: try profiles endpoint
    if not wallets:
        try:
            r = requests.get(f"{GAMMA_BASE}/profiles",
                params={"limit": str(limit), "sortBy": "pnl", "order": "desc"},
                headers=HEADERS, timeout=15)
            if r.ok:
                data = r.json()
                profiles = data if isinstance(data, list) else data.get("profiles", [])
                for p in profiles:
                    addr = p.get("address", p.get("proxyWallet", ""))
                    if addr:
                        wallets.append({
                            "address": addr,
                            "username": p.get("username", p.get("name", "")),
                            "pnl": float(p.get("pnl", p.get("profit", 0))),
                            "volume": float(p.get("volume", 0)),
                            "markets_traded": int(p.get("marketsTraded", 0)),
                            "rank": 0,
                        })
        except:
            pass

    return wallets

def fetch_market_info(condition_id: str) -> dict:
    """Fetch market info for a given condition/token."""
    try:
        r = requests.get(f"{GAMMA_BASE}/markets/{condition_id}", headers=HEADERS, timeout=10)
        if r.ok:
            return r.json()
    except:
        pass
    # Try as token
    try:
        r = requests.get(f"{GAMMA_BASE}/markets",
            params={"condition_id": condition_id}, headers=HEADERS, timeout=10)
        if r.ok:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except:
        pass
    return {}

# ═══════════════════════════════════════════════
# WALLET MANAGEMENT
# ═══════════════════════════════════════════════

def add_wallet(address: str, alias: str = "") -> dict:
    """Add a wallet to track."""
    data = _load()
    wid = address.lower()

    if wid in data["wallets"]:
        return {"status": "exists", "wallet": data["wallets"][wid]}

    # Fetch initial profile
    profile = fetch_wallet_profile(address)
    positions = fetch_wallet_positions(address)

    wallet = {
        "address": address,
        "alias": alias or profile.get("username", f"Whale-{wid[:8]}"),
        "username": profile.get("username", ""),
        "added_at": datetime.now(timezone.utc).isoformat(),
        "pnl": float(profile.get("pnl", profile.get("profit", 0))),
        "volume": float(profile.get("volume", 0)),
        "win_rate": float(profile.get("winRate", profile.get("win_rate", 0))),
        "markets_traded": int(profile.get("marketsTraded", 0)),
        "followers_count": 0,
        "last_positions": _snapshot_positions(positions),
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "total_signals": 0,
        "active": True,
    }

    data["wallets"][wid] = wallet
    _save(data)
    return {"status": "added", "wallet": wallet}

def remove_wallet(address: str) -> bool:
    """Remove a wallet from tracking."""
    data = _load()
    wid = address.lower()
    if wid in data["wallets"]:
        del data["wallets"][wid]
        # Remove from all followers
        for cid in data["followers"]:
            if wid in data["followers"][cid]:
                data["followers"][cid].remove(wid)
        _save(data)
        return True
    return False

def get_tracked_wallets() -> list:
    """Get all tracked wallets with stats."""
    data = _load()
    wallets = list(data["wallets"].values())
    # Sort by PnL descending
    wallets.sort(key=lambda w: w.get("pnl", 0), reverse=True)
    return wallets

def get_wallet(address: str) -> dict:
    """Get a specific tracked wallet."""
    data = _load()
    return data["wallets"].get(address.lower())

def _snapshot_positions(positions: list) -> dict:
    """Create a snapshot of positions for comparison."""
    snap = {}
    for pos in positions:
        market_id = pos.get("market", pos.get("conditionId", pos.get("condition_id", "")))
        if market_id:
            snap[market_id] = {
                "size": float(pos.get("size", pos.get("amount", 0))),
                "side": pos.get("side", pos.get("outcome", "")).lower(),
                "avg_price": float(pos.get("avgPrice", pos.get("average_price", 0))),
                "value_usd": float(pos.get("currentValue", pos.get("value", 0))),
                "title": pos.get("title", pos.get("question", ""))[:80],
            }
    return snap

# ═══════════════════════════════════════════════
# FOLLOW/UNFOLLOW SYSTEM
# ═══════════════════════════════════════════════

def follow_wallet(chat_id: str, address: str) -> dict:
    """User follows a wallet to receive copy trading signals."""
    data = _load()
    cid = str(chat_id)
    wid = address.lower()

    if wid not in data["wallets"]:
        return {"status": "error", "message": "Wallet not tracked. Add it first with /ct_add"}

    if cid not in data["followers"]:
        data["followers"][cid] = []

    if wid in data["followers"][cid]:
        return {"status": "exists", "message": "Already following this wallet"}

    # Check wallet tracking limit
    limit = user_store.get_wallet_tracking_limit(cid)
    current_count = get_following_count(cid)
    if current_count >= limit:
        return {"status": "error", "message": f"You've reached your limit of {limit} tracked wallets. Upgrade to Degen Mode for unlimited tracking."}

    data["followers"][cid].append(wid)
    data["wallets"][wid]["followers_count"] = data["wallets"][wid].get("followers_count", 0) + 1
    _save(data)

    alias = data["wallets"][wid].get("alias", wid[:10])
    return {"status": "followed", "message": f"Now following {alias}. You'll get signals when they trade."}

def unfollow_wallet(chat_id: str, address: str) -> dict:
    """User unfollows a wallet."""
    data = _load()
    cid = str(chat_id)
    wid = address.lower()

    if cid in data["followers"] and wid in data["followers"][cid]:
        data["followers"][cid].remove(wid)
        if wid in data["wallets"]:
            data["wallets"][wid]["followers_count"] = max(0,
                data["wallets"][wid].get("followers_count", 1) - 1)
        _save(data)
        return {"status": "unfollowed", "message": "Unfollowed successfully."}

    return {"status": "error", "message": "Not following this wallet."}

def get_following(chat_id: str) -> list:
    """Get wallets a user is following."""
    data = _load()
    cid = str(chat_id)
    following_ids = data.get("followers", {}).get(cid, [])
    wallets = []
    for wid in following_ids:
        w = data["wallets"].get(wid)
        if w:
            wallets.append(w)
    return wallets

def get_following_count(chat_id: str) -> int:
    """Get the number of wallets a user is following."""
    data = _load()
    cid = str(chat_id)
    return len(data.get("followers", {}).get(cid, []))

def get_followers_of(address: str) -> list:
    """Get chat_ids of users following a specific wallet."""
    data = _load()
    wid = address.lower()
    followers = []
    for cid, followed in data.get("followers", {}).items():
        if wid in followed:
            followers.append(cid)
    return followers

# ═══════════════════════════════════════════════
# SIGNAL DETECTION — Compare snapshots to find new trades
# ═══════════════════════════════════════════════

def scan_wallet_changes(address: str) -> list:
    """Compare current positions with last snapshot to detect changes."""
    data = _load()
    wid = address.lower()
    wallet = data["wallets"].get(wid)
    if not wallet:
        return []

    # Fetch current positions
    current_positions = fetch_wallet_positions(address)
    current_snap = _snapshot_positions(current_positions)
    old_snap = wallet.get("last_positions", {})

    signals = []
    min_usd = data.get("settings", {}).get("min_trade_usd", 500)

    # Detect NEW positions (not in old snapshot)
    for mid, pos in current_snap.items():
        if mid not in old_snap:
            if pos["value_usd"] >= min_usd or pos["size"] >= min_usd:
                signals.append({
                    "type": "NEW_POSITION",
                    "wallet": address,
                    "alias": wallet.get("alias", ""),
                    "market_id": mid,
                    "title": pos["title"],
                    "side": pos["side"],
                    "size": pos["size"],
                    "avg_price": pos["avg_price"],
                    "value_usd": pos["value_usd"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        else:
            # Detect INCREASED positions
            old_size = old_snap[mid]["size"]
            new_size = pos["size"]
            if new_size > old_size * 1.15:  # 15%+ increase
                increase_usd = (new_size - old_size) * pos["avg_price"]
                if increase_usd >= min_usd or (new_size - old_size) >= min_usd:
                    signals.append({
                        "type": "INCREASED",
                        "wallet": address,
                        "alias": wallet.get("alias", ""),
                        "market_id": mid,
                        "title": pos["title"],
                        "side": pos["side"],
                        "old_size": old_size,
                        "new_size": new_size,
                        "increase": new_size - old_size,
                        "avg_price": pos["avg_price"],
                        "value_usd": pos["value_usd"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

    # Detect CLOSED positions (were in old, not in current)
    for mid, pos in old_snap.items():
        if mid not in current_snap:
            signals.append({
                "type": "CLOSED",
                "wallet": address,
                "alias": wallet.get("alias", ""),
                "market_id": mid,
                "title": pos["title"],
                "side": pos["side"],
                "size": pos["size"],
                "avg_price": pos["avg_price"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    # Update snapshot
    data["wallets"][wid]["last_positions"] = current_snap
    data["wallets"][wid]["last_checked"] = datetime.now(timezone.utc).isoformat()
    if signals:
        data["wallets"][wid]["total_signals"] = wallet.get("total_signals", 0) + len(signals)
        data["signals"] = (signals + data.get("signals", []))[:500]  # Keep last 500
    _save(data)

    return signals

def scan_all_wallets() -> list:
    """Scan all tracked wallets for position changes."""
    data = _load()
    all_signals = []

    for wid, wallet in data["wallets"].items():
        if not wallet.get("active", True):
            continue
        try:
            signals = scan_wallet_changes(wallet["address"])
            all_signals.extend(signals)
            time.sleep(1)  # Rate limiting between wallets
        except Exception as e:
            print(f"[COPY] Scan error for {wallet.get('alias', wid[:10])}: {e}")

    data = _load()  # Reload after all scans
    data["last_scan"] = datetime.now(timezone.utc).isoformat()
    _save(data)

    print(f"[COPY] Scanned {len(data['wallets'])} wallets — {len(all_signals)} new signals")
    return all_signals

# ═══════════════════════════════════════════════
# LEADERBOARD
# ═══════════════════════════════════════════════

def refresh_leaderboard() -> list:
    """Refresh the trader leaderboard with fresh data."""
    data = _load()

    # First try to discover from Polymarket
    discovered = discover_top_wallets(20)

    # Merge with existing tracked wallets
    leaderboard = []
    seen = set()

    # Add discovered wallets
    for w in discovered:
        addr = w["address"].lower()
        if addr not in seen:
            seen.add(addr)
            leaderboard.append({
                "address": w["address"],
                "alias": w.get("username") or f"Trader-{addr[:6]}",
                "pnl": w["pnl"],
                "volume": w["volume"],
                "markets_traded": w["markets_traded"],
                "rank": w.get("rank", 0),
                "tracked": addr in data["wallets"],
                "followers": data["wallets"].get(addr, {}).get("followers_count", 0),
            })

    # Add tracked wallets not in leaderboard
    for wid, wallet in data["wallets"].items():
        if wid not in seen:
            leaderboard.append({
                "address": wallet["address"],
                "alias": wallet.get("alias", ""),
                "pnl": wallet.get("pnl", 0),
                "volume": wallet.get("volume", 0),
                "markets_traded": wallet.get("markets_traded", 0),
                "rank": 0,
                "tracked": True,
                "followers": wallet.get("followers_count", 0),
            })

    # Sort by PnL
    leaderboard.sort(key=lambda x: x["pnl"], reverse=True)

    data["leaderboard"] = leaderboard[:50]  # Keep top 50
    _save(data)

    return leaderboard[:50]

def get_leaderboard() -> list:
    """Get cached leaderboard."""
    data = _load()
    return data.get("leaderboard", [])

# ═══════════════════════════════════════════════
# SIGNAL FORMATTING (for Telegram)
# ═══════════════════════════════════════════════

def format_signal(signal: dict) -> str:
    """Format a copy trading signal for Telegram."""
    sig_type = signal.get("type", "")
    alias = signal.get("alias", "Unknown")
    title = signal.get("title", "Unknown market")[:60]
    side = signal.get("side", "").upper()

    if sig_type == "NEW_POSITION":
        size = signal.get("size", 0)
        price = signal.get("avg_price", 0)
        value = signal.get("value_usd", 0)
        return (
            f"🔔 <b>COPY SIGNAL — NEW POSITION</b>\n\n"
            f"👤 Trader: <b>{alias}</b>\n"
            f"📌 {title}\n"
            f"📊 Side: <b>{side or 'YES'}</b>\n"
            f"💰 Size: <b>{_fmt_usd(size)}</b> @ {price:.2f}\n"
            f"💵 Value: {_fmt_usd(value)}\n\n"
            f"🕐 {signal.get('timestamp', '')[:19].replace('T', ' ')} UTC"
        )

    elif sig_type == "INCREASED":
        old = signal.get("old_size", 0)
        new = signal.get("new_size", 0)
        increase = signal.get("increase", 0)
        return (
            f"📈 <b>COPY SIGNAL — POSITION INCREASED</b>\n\n"
            f"👤 Trader: <b>{alias}</b>\n"
            f"📌 {title}\n"
            f"📊 Side: <b>{side or 'YES'}</b>\n"
            f"💰 Added: <b>+{_fmt_usd(increase)}</b>\n"
            f"📊 {_fmt_usd(old)} → {_fmt_usd(new)}\n\n"
            f"🕐 {signal.get('timestamp', '')[:19].replace('T', ' ')} UTC"
        )

    elif sig_type == "CLOSED":
        size = signal.get("size", 0)
        return (
            f"🔻 <b>COPY SIGNAL — POSITION CLOSED</b>\n\n"
            f"👤 Trader: <b>{alias}</b>\n"
            f"📌 {title}\n"
            f"📊 Was: <b>{side or 'YES'}</b> ({_fmt_usd(size)})\n"
            f"⚡ Trader exited this position.\n\n"
            f"🕐 {signal.get('timestamp', '')[:19].replace('T', ' ')} UTC"
        )

    return f"🔔 Copy signal from {alias}: {title}"

def format_leaderboard(leaderboard: list = None) -> str:
    """Format the leaderboard for Telegram."""
    if leaderboard is None:
        leaderboard = get_leaderboard()

    if not leaderboard:
        return (
            "📋 <b>Copy Trading Leaderboard</b>\n\n"
            "No traders found yet. Refreshing...\n\n"
            "Use /ct_refresh to fetch top Polymarket traders."
        )

    lines = ["📋 <b>Copy Trading Leaderboard</b>\n"]

    for i, trader in enumerate(leaderboard[:15]):
        alias = trader.get("alias", "Unknown")[:15]
        pnl = trader.get("pnl", 0)
        vol = trader.get("volume", 0)
        markets = trader.get("markets_traded", 0)
        tracked = "✅" if trader.get("tracked") else ""
        followers = trader.get("followers", 0)

        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        pnl_str = f"+{_fmt_usd(pnl)}" if pnl > 0 else f"-{_fmt_usd(abs(pnl))}"

        rank = i + 1
        line = f"{rank}. <b>{alias}</b> {tracked}\n"
        line += f"   {pnl_emoji} PnL: {pnl_str} | Vol: {_fmt_usd(vol)}"
        if followers > 0:
            line += f" | 👥 {followers}"
        lines.append(line)

    lines.append(f"\n<i>Showing top {min(15, len(leaderboard))} traders</i>")
    lines.append("Use /ct_follow &lt;number&gt; to follow a trader")
    return "\n".join(lines)

def format_following(chat_id: str) -> str:
    """Format user's followed wallets."""
    wallets = get_following(chat_id)
    limit = user_store.get_wallet_tracking_limit(str(chat_id))
    current_count = len(wallets)

    if not wallets:
        return (
            "📋 <b>Your Copy Trading Portfolio</b>\n\n"
            "You're not following any traders yet.\n\n"
            "🔍 /ct_leaderboard — See top traders\n"
            "➕ /ct_follow &lt;address_or_number&gt; — Follow a trader"
        )

    lines = ["📋 <b>Your Copy Trading Portfolio</b>\n"]
    lines.append(f"Following <b>{current_count} / {limit}</b> trader{'s' if len(wallets) != 1 else ''}:\n")

    for i, w in enumerate(wallets):
        alias = w.get("alias", "Unknown")[:18]
        pnl = w.get("pnl", 0)
        signals = w.get("total_signals", 0)
        positions = len(w.get("last_positions", {}))

        pnl_str = f"+{_fmt_usd(pnl)}" if pnl >= 0 else f"-{_fmt_usd(abs(pnl))}"
        lines.append(
            f"{i+1}. <b>{alias}</b>\n"
            f"   💰 PnL: {pnl_str} | 📊 {positions} positions | 🔔 {signals} signals"
        )

    lines.append("\nUse /ct_unfollow &lt;number&gt; to stop following")
    if current_count >= limit:
        lines.append("⚠️ You've reached your wallet tracking limit. Upgrade to Degen Mode for unlimited tracking.")
    return "\n".join(lines)

def format_wallet_detail(address: str) -> str:
    """Format detailed view of a tracked wallet."""
    wallet = get_wallet(address)
    if not wallet:
        return "❌ Wallet not found. Use /ct_add to track it."

    alias = wallet.get("alias", "Unknown")
    pnl = wallet.get("pnl", 0)
    volume = wallet.get("volume", 0)
    win_rate = wallet.get("win_rate", 0)
    markets = wallet.get("markets_traded", 0)
    followers = wallet.get("followers_count", 0)
    signals = wallet.get("total_signals", 0)
    positions = wallet.get("last_positions", {})

    pnl_str = f"+{_fmt_usd(pnl)}" if pnl >= 0 else f"-{_fmt_usd(abs(pnl))}"

    lines = [
        f"👤 <b>{alias}</b>\n",
        f"📍 {address[:8]}...{address[-6:]}",
        f"",
        f"💰 PnL: <b>{pnl_str}</b>",
        f"📊 Volume: {_fmt_usd(volume)}",
        f"🎯 Win Rate: {win_rate:.1f}%" if win_rate else "",
        f"📈 Markets: {markets}",
        f"👥 Followers: {followers}",
        f"🔔 Signals: {signals}",
        f"",
        f"<b>Open Positions ({len(positions)}):</b>",
    ]

    for mid, pos in list(positions.items())[:10]:
        title = pos.get("title", mid)[:50]
        side = pos.get("side", "?").upper()
        size = pos.get("size", 0)
        lines.append(f"  • {title}\n    {side} | {_fmt_usd(size)}")

    if len(positions) > 10:
        lines.append(f"  ...and {len(positions) - 10} more")

    lines.append(f"\n🕐 Last checked: {wallet.get('last_checked', 'never')[:19].replace('T', ' ')}")

    return "\n".join([l for l in lines if l is not None])

def format_recent_signals(limit: int = 10) -> str:
    """Format recent copy trading signals."""
    data = _load()
    signals = data.get("signals", [])[:limit]

    if not signals:
        return (
            "🔔 <b>Recent Copy Signals</b>\n\n"
            "No signals yet. Signals appear when tracked wallets make trades.\n\n"
            "Add wallets with /ct_add or follow traders from /ct_leaderboard"
        )

    lines = [f"🔔 <b>Recent Copy Signals</b> (last {len(signals)})\n"]

    for sig in signals:
        sig_type = sig.get("type", "")
        alias = sig.get("alias", "?")[:12]
        title = sig.get("title", "?")[:40]
        ts = sig.get("timestamp", "")[:16].replace("T", " ")

        if sig_type == "NEW_POSITION":
            emoji = "🟢"
            action = "OPENED"
        elif sig_type == "INCREASED":
            emoji = "📈"
            action = "ADDED"
        elif sig_type == "CLOSED":
            emoji = "🔻"
            action = "CLOSED"
        else:
            emoji = "🔔"
            action = sig_type

        size_str = _fmt_usd(sig.get("size", sig.get("increase", 0)))
        lines.append(f"{emoji} <b>{alias}</b> {action} | {title} | {size_str}\n   {ts}")

    return "\n".join(lines)

# ═══════════════════════════════════════════════
# COPY TRADING STATS
# ═══════════════════════════════════════════════

def get_copy_stats() -> dict:
    """Get copy trading system stats."""
    data = _load()
    total_wallets = len(data["wallets"])
    active_wallets = sum(1 for w in data["wallets"].values() if w.get("active", True))
    total_followers = sum(len(f) for f in data["followers"].values())
    unique_followers = len([f for f in data["followers"].values() if f])
    total_signals = len(data.get("signals", []))

    return {
        "total_wallets": total_wallets,
        "active_wallets": active_wallets,
        "total_follow_relations": total_followers,
        "unique_followers": unique_followers,
        "total_signals": total_signals,
        "last_scan": data.get("last_scan", "never"),
    }

# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

def _fmt_usd(amount) -> str:
    """Format USD amount."""
    try:
        amount = float(amount)
    except:
        return "$0"
    if abs(amount) >= 1_000_000:
        return f"${amount/1_000_000:.1f}M"
    elif abs(amount) >= 1_000:
        return f"${amount/1_000:.1f}K"
    else:
        return f"${amount:.0f}"
