"""
POLYTRAGENT — Copy Trading Executor
Automatically mirrors trades from followed whale wallets.
Uses polymarket_trading.py for execution and wallet_manager.py for user wallets.

Two modes:
1. SIGNAL mode (existing) — just sends Telegram alerts
2. AUTO mode (new) — actually executes trades automatically

Storage: data/copy_executor.json
"""

import os, json, logging, time, threading
from typing import Optional
from datetime import datetime, timezone, timedelta
import user_store

log = logging.getLogger("polytragent.copy_exec")

FILE = os.path.join(os.path.dirname(__file__), "data", "copy_executor.json")


# ═══════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════

def _load() -> dict:
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    if not os.path.exists(FILE):
        return {
            "auto_traders": {},     # chat_id -> {enabled, max_per_trade, daily_limit, daily_spent, mode, followed_wallets}
            "executed_trades": [],  # [{chat_id, signal_id, market, outcome, amount, order_id, timestamp}]
            "trade_log": [],        # Last 100 trade attempts
        }
    try:
        with open(FILE) as f:
            return json.load(f)
    except:
        return {"auto_traders": {}, "executed_trades": [], "trade_log": []}


def _save(data: dict):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════════
# AUTO COPY SETTINGS
# ═══════════════════════════════════════════════

def enable_auto_copy(chat_id: str, max_per_trade: float = 25.0,
                     daily_limit: float = 200.0) -> dict:
    """
    Enable auto copy trading for a user.
    Requires Degen Mode subscription ($79/month).

    Args:
        chat_id: User's Telegram chat ID
        max_per_trade: Maximum USDC per auto trade
        daily_limit: Maximum total USDC per day for auto trades
    """
    # Auto-copy requires Pro ($79/mo)
    if not user_store.is_degen(str(chat_id)):
        return {"success": False, "error": "Degen Mode subscription required for auto-trade. Upgrade for $79.99/month to unlock auto-copy trading."}

    data = _load()
    chat_str = str(chat_id)

    data["auto_traders"][chat_str] = {
        "enabled": True,
        "max_per_trade": max_per_trade,
        "daily_limit": daily_limit,
        "daily_spent": 0.0,
        "daily_reset": datetime.now(timezone.utc).date().isoformat(),
        "mode": "proportional",  # proportional | fixed | percentage
        "fixed_amount": max_per_trade,
        "percentage": 5.0,  # % of whale's trade size
        "min_trade_usd": 10.0,
        "max_slippage": 0.05,  # 5% max slippage
        "followed_wallets": [],  # specific wallets to auto-copy (empty = all followed)
        "enabled_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": 0,
        "total_volume": 0.0,
    }

    _save(data)
    return {"success": True, "settings": data["auto_traders"][chat_str]}


def disable_auto_copy(chat_id: str) -> dict:
    """Disable auto copy trading."""
    data = _load()
    chat_str = str(chat_id)

    if chat_str in data["auto_traders"]:
        data["auto_traders"][chat_str]["enabled"] = False
        _save(data)

    return {"success": True}


def get_auto_copy_settings(chat_id: str) -> Optional[dict]:
    """Get current auto-copy settings for a user."""
    data = _load()
    return data["auto_traders"].get(str(chat_id))


def update_auto_copy_settings(chat_id: str, **kwargs) -> dict:
    """Update specific auto-copy settings."""
    data = _load()
    chat_str = str(chat_id)

    if chat_str not in data["auto_traders"]:
        return {"success": False, "error": "Auto-copy not enabled. Use /auto_copy_on first."}

    settings = data["auto_traders"][chat_str]

    valid_keys = {"max_per_trade", "daily_limit", "mode", "fixed_amount",
                  "percentage", "min_trade_usd", "max_slippage"}

    for k, v in kwargs.items():
        if k in valid_keys:
            settings[k] = v

    _save(data)
    return {"success": True, "settings": settings}


def is_auto_copy_enabled(chat_id: str) -> bool:
    """Check if auto-copy is active for a user. Requires Degen Mode subscription."""
    if not user_store.is_degen(str(chat_id)):
        return False
    settings = get_auto_copy_settings(chat_id)
    return settings is not None and settings.get("enabled", False)


# ═══════════════════════════════════════════════
# TRADE EXECUTION
# ═══════════════════════════════════════════════

def _reset_daily_if_needed(settings: dict) -> dict:
    """Reset daily spending if it's a new day."""
    today = datetime.now(timezone.utc).date().isoformat()
    if settings.get("daily_reset") != today:
        settings["daily_spent"] = 0.0
        settings["daily_reset"] = today
    return settings


def calculate_trade_amount(settings: dict, whale_trade_usd: float) -> float:
    """Calculate how much to trade based on settings and whale's trade size."""
    mode = settings.get("mode", "fixed")

    if mode == "fixed":
        amount = settings.get("fixed_amount", 25.0)
    elif mode == "percentage":
        pct = settings.get("percentage", 5.0) / 100
        amount = whale_trade_usd * pct
    elif mode == "proportional":
        # Scale based on max_per_trade relative to whale's position
        amount = min(settings.get("max_per_trade", 25.0), whale_trade_usd * 0.1)
    else:
        amount = settings.get("fixed_amount", 25.0)

    # Apply limits
    amount = min(amount, settings.get("max_per_trade", 25.0))
    amount = max(amount, settings.get("min_trade_usd", 10.0))

    # Check daily limit
    settings = _reset_daily_if_needed(settings)
    remaining = settings.get("daily_limit", 200.0) - settings.get("daily_spent", 0.0)

    if remaining <= 0:
        return 0.0

    amount = min(amount, remaining)
    return round(amount, 2)


def execute_copy_trade(chat_id: str, signal: dict) -> dict:
    """
    Execute a copy trade based on a whale signal.

    Args:
        chat_id: User's chat ID
        signal: Signal dict from copy_trading.py with keys:
            - wallet: whale wallet address
            - market: market slug/id
            - market_title: human readable title
            - action: "NEW_POSITION" | "INCREASED" | "CLOSED"
            - outcome: "Yes" | "No"
            - amount_usd: whale's trade size
            - token_id: the specific token (if available)

    Returns:
        dict with execution result
    """
    import polymarket_trading as trading

    data = _load()
    chat_str = str(chat_id)
    settings = data["auto_traders"].get(chat_str)

    if not settings or not settings.get("enabled"):
        return {"success": False, "error": "Auto-copy not enabled", "skipped": True}

    # Auto-trade requires Degen Mode subscription
    if not user_store.is_degen(chat_str):
        return {"success": False, "error": "Degen Mode required for auto-trade", "skipped": True}

    # Reset daily counter if needed
    settings = _reset_daily_if_needed(settings)

    # Check if we should copy this specific wallet
    followed = settings.get("followed_wallets", [])
    if followed and signal.get("wallet", "").lower() not in [w.lower() for w in followed]:
        return {"success": False, "error": "Wallet not in auto-copy list", "skipped": True}

    action = signal.get("action", "").upper()

    # Determine trade direction
    if action in ("NEW_POSITION", "INCREASED"):
        # Buy the same outcome
        whale_amount = float(signal.get("amount_usd", 0))
        trade_amount = calculate_trade_amount(settings, whale_amount)

        if trade_amount <= 0:
            return {"success": False, "error": "Daily limit reached", "skipped": True}

        # Resolve market and execute
        market_ref = signal.get("market", "")
        outcome = signal.get("outcome", "Yes")
        token_id = signal.get("token_id", "")

        if token_id:
            # Direct token trade from user's own wallet
            result = trading.market_buy(
                token_id=token_id,
                amount=trade_amount,
                neg_risk=signal.get("neg_risk", False),
                chat_id=chat_str,
            )
        else:
            # Resolve via slug/URL — trade from user's wallet
            result = trading.quick_buy(market_ref, outcome, trade_amount, chat_id=chat_str)

        if result.get("success"):
            # Update spending
            settings["daily_spent"] = settings.get("daily_spent", 0) + trade_amount
            settings["total_trades"] = settings.get("total_trades", 0) + 1
            settings["total_volume"] = settings.get("total_volume", 0) + trade_amount
            data["auto_traders"][chat_str] = settings

            # Log trade
            trade_record = {
                "chat_id": chat_str,
                "signal_wallet": signal.get("wallet", ""),
                "market": signal.get("market_title", market_ref),
                "outcome": outcome,
                "amount": trade_amount,
                "action": "BUY",
                "order_id": result.get("order_id", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            data["executed_trades"].append(trade_record)
            data["executed_trades"] = data["executed_trades"][-500:]  # Keep last 500
            _save(data)

        result["trade_amount"] = trade_amount
        result["copy_of"] = signal.get("wallet", "")[:10]
        return result

    elif action == "CLOSED":
        # The whale closed a position — we should sell too
        token_id = signal.get("token_id", "")
        market_ref = signal.get("market", "")
        outcome = signal.get("outcome", "Yes")

        # We need to know how many shares we have to sell
        # For now, try to sell all shares we hold in this token
        if token_id:
            # Get user's positions to find share count
            positions = trading.get_positions(chat_id=chat_str)
            our_shares = 0
            for pos in positions:
                if pos.get("asset", pos.get("token_id", "")) == token_id:
                    our_shares = float(pos.get("size", 0))
                    break

            if our_shares > 0:
                result = trading.market_sell(
                    token_id=token_id,
                    amount=our_shares,
                    neg_risk=signal.get("neg_risk", False),
                    chat_id=chat_str,
                )

                if result.get("success"):
                    settings["total_trades"] = settings.get("total_trades", 0) + 1
                    data["auto_traders"][chat_str] = settings

                    trade_record = {
                        "chat_id": chat_str,
                        "signal_wallet": signal.get("wallet", ""),
                        "market": signal.get("market_title", market_ref),
                        "outcome": outcome,
                        "shares": our_shares,
                        "action": "SELL",
                        "order_id": result.get("order_id", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    data["executed_trades"].append(trade_record)
                    data["executed_trades"] = data["executed_trades"][-500:]
                    _save(data)

                return result
            else:
                return {"success": False, "error": "No position to close", "skipped": True}
        else:
            result = trading.quick_sell(market_ref, outcome, 0, chat_id=chat_str)  # Will need share count
            return result

    return {"success": False, "error": f"Unknown action: {action}", "skipped": True}


# ═══════════════════════════════════════════════
# PROCESS SIGNALS BATCH
# ═══════════════════════════════════════════════

def process_signals_for_user(chat_id: str, signals: list, send_fn=None) -> list:
    """
    Process a batch of signals for auto-copy execution.

    Args:
        chat_id: User's chat ID
        signals: List of signal dicts from copy_trading scan
        send_fn: Optional callback to send Telegram messages (chat_id, text)

    Returns:
        List of execution results
    """
    if not is_auto_copy_enabled(chat_id):
        return []

    results = []
    for signal in signals:
        try:
            result = execute_copy_trade(chat_id, signal)

            if result.get("skipped"):
                continue

            results.append(result)

            # Notify user
            if send_fn:
                if result.get("success"):
                    msg = (
                        f"🤖 <b>Auto-Copy Trade Executed</b>\n\n"
                        f"📊 {signal.get('market_title', 'Unknown')}\n"
                        f"🎯 {signal.get('outcome', '?')} | ${result.get('trade_amount', 0):.2f}\n"
                        f"👤 Copying: {signal.get('wallet', '')[:10]}...\n"
                        f"✅ Order: <code>{result.get('order_id', '')[:16]}</code>"
                    )
                else:
                    msg = (
                        f"🤖 <b>Auto-Copy Failed</b>\n\n"
                        f"📊 {signal.get('market_title', 'Unknown')}\n"
                        f"❌ {result.get('error', 'Unknown error')}"
                    )

                try:
                    send_fn(str(chat_id), msg)
                except:
                    pass

            # Small delay between trades
            time.sleep(1)

        except Exception as e:
            log.error(f"Auto-copy error for {chat_id}: {e}")
            results.append({"success": False, "error": str(e)})

    return results


def get_auto_copy_stats(chat_id: str) -> dict:
    """Get auto-copy trading statistics for a user."""
    data = _load()
    chat_str = str(chat_id)
    settings = data["auto_traders"].get(chat_str)

    if not settings:
        return {"enabled": False}

    settings = _reset_daily_if_needed(settings)

    # Count recent trades
    recent_trades = [
        t for t in data["executed_trades"]
        if t.get("chat_id") == chat_str
    ]

    today_trades = [
        t for t in recent_trades
        if t.get("timestamp", "").startswith(datetime.now(timezone.utc).date().isoformat())
    ]

    return {
        "enabled": settings.get("enabled", False),
        "mode": settings.get("mode", "fixed"),
        "max_per_trade": settings.get("max_per_trade", 25.0),
        "daily_limit": settings.get("daily_limit", 200.0),
        "daily_spent": settings.get("daily_spent", 0.0),
        "daily_remaining": settings.get("daily_limit", 200.0) - settings.get("daily_spent", 0.0),
        "total_trades": settings.get("total_trades", 0),
        "total_volume": settings.get("total_volume", 0.0),
        "today_trades": len(today_trades),
        "recent_trades": recent_trades[-10:],
    }


# ═══════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════

def format_auto_copy_settings(settings: dict) -> str:
    """Format auto-copy settings for Telegram display."""
    if not settings or not settings.get("enabled"):
        return (
            "🤖 <b>Auto-Copy Trading: OFF</b>\n\n"
            "Use /auto_copy_on to enable automatic trade execution "
            "when your followed whales trade."
        )

    mode_labels = {
        "fixed": "Fixed Amount",
        "percentage": "% of Whale Trade",
        "proportional": "Proportional",
    }

    return (
        f"🤖 <b>Auto-Copy Trading: ON</b>\n\n"
        f"📊 Mode: <b>{mode_labels.get(settings.get('mode', 'fixed'), 'Fixed')}</b>\n"
        f"💰 Max per trade: <b>${settings.get('max_per_trade', 25):.2f}</b>\n"
        f"📅 Daily limit: <b>${settings.get('daily_limit', 200):.2f}</b>\n"
        f"💸 Spent today: <b>${settings.get('daily_spent', 0):.2f}</b>\n"
        f"🔄 Total trades: <b>{settings.get('total_trades', 0)}</b>\n"
        f"📈 Total volume: <b>${settings.get('total_volume', 0):.2f}</b>\n"
        f"\n⚙️ /auto_copy_settings to adjust"
    )


def format_auto_copy_stats(stats: dict) -> str:
    """Format auto-copy stats for Telegram display."""
    if not stats.get("enabled"):
        return "🤖 Auto-copy is <b>disabled</b>. Use /auto_copy_on to enable."

    lines = [
        "🤖 <b>Auto-Copy Stats</b>",
        "",
        f"📊 Mode: <b>{stats.get('mode', 'fixed')}</b>",
        f"💰 Max/trade: <b>${stats.get('max_per_trade', 0):.2f}</b>",
        f"📅 Daily: <b>${stats.get('daily_spent', 0):.2f}</b> / <b>${stats.get('daily_limit', 0):.2f}</b>",
        f"🔄 Today: <b>{stats.get('today_trades', 0)} trades</b>",
        f"📈 All-time: <b>{stats.get('total_trades', 0)} trades</b> | <b>${stats.get('total_volume', 0):.2f}</b>",
    ]

    recent = stats.get("recent_trades", [])
    if recent:
        lines.append("\n📋 <b>Recent Trades:</b>")
        for t in recent[-5:]:
            action = t.get("action", "?")
            market = (t.get("market", "Unknown"))[:30]
            amount = t.get("amount", t.get("shares", 0))
            emoji = "🟩" if action == "BUY" else "🟥"
            lines.append(f"  {emoji} {action} {market} — ${amount:.2f}")

    return "\n".join(lines)
