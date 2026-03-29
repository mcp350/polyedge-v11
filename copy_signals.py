"""
COPY SIGNALS — Real-time notification dispatcher for copy trading.
Sends Telegram alerts to followers when tracked wallets trade.
Integrates with the scheduler in main.py.
"""

import time, threading
from datetime import datetime, timezone
import copy_trading as ct
import telegram_client as tg
import onboarding
import user_store
import copy_executor as ce

# ═══════════════════════════════════════════════
# SIGNAL DISPATCHER
# ═══════════════════════════════════════════════

def dispatch_signals(signals: list):
    """Send copy trading signals to all relevant followers."""
    if not signals:
        return 0

    sent_count = 0
    for signal in signals:
        wallet_addr = signal.get("wallet", "")
        if not wallet_addr:
            continue

        # Get all followers of this wallet
        followers = ct.get_followers_of(wallet_addr)
        if not followers:
            continue

        # Format the signal message
        msg = ct.format_signal(signal)
        market_id = signal.get("market_id", "")

        # Build action buttons
        buttons = []
        if market_id:
            polymarket_url = f"https://polymarket.com/event/{market_id}"
            buttons.append([{"text": "🔗 Open on Polymarket", "url": polymarket_url}])

        side = signal.get("side", "yes").upper()
        sig_type = signal.get("type", "")

        if sig_type in ("NEW_POSITION", "INCREASED"):
            # Add one-tap copy trade button with market slug
            slug = signal.get("market", signal.get("slug", ""))
            outcome = signal.get("outcome", signal.get("side", "yes")).capitalize()
            if slug:
                buttons.append([
                    {"text": f"🟩 Copy Buy {outcome}", "callback_data": f"copy_buy_{wallet_addr[:10]}_{slug[:30]}_{outcome}"},
                ])
            buttons.append([
                {"text": f"📊 View Trader", "callback_data": f"ct_detail_{wallet_addr[:20]}"},
            ])
        elif sig_type == "CLOSED":
            buttons.append([
                {"text": "📊 View Trader", "callback_data": f"ct_detail_{wallet_addr[:20]}"},
            ])

        buttons.append([{"text": "📋 My Copy Portfolio", "callback_data": "ct_following"}])

        # Send to each follower
        for chat_id in followers:
            # Check if subscriber (copy trading is paid)
            if not user_store.is_subscribed(chat_id) and not user_store.is_admin(chat_id):
                continue

            try:
                onboarding.send_inline(chat_id, msg, buttons)
                sent_count += 1
                time.sleep(0.05)  # Rate limiting
            except Exception as e:
                print(f"[COPY-SIG] Send error to {chat_id}: {e}")

            # ── AUTO COPY-TRADE EXECUTION ──
            # If user has auto-copy enabled, execute the trade automatically
            try:
                if ce.is_auto_copy_enabled(chat_id):
                    result = ce.execute_copy_trade(chat_id, signal)
                    if result.get("success"):
                        amt = result.get("trade_amount", 0)
                        onboarding.send_inline(chat_id,
                            f"🤖 <b>Auto-Copy Executed!</b>\n\n"
                            f"💰 ${amt:.2f} → {signal.get('outcome', '?')} "
                            f"on {signal.get('market_title', 'Unknown')[:50]}\n"
                            f"📋 Copying: {signal.get('wallet', '')[:10]}...",
                            [[{"text": "📊 My Positions", "callback_data": "trading_positions"},
                              {"text": "⚙️ Auto-Copy", "callback_data": "menu_auto_copy"}]])
                        print(f"[COPY-SIG] Auto-copy executed for {chat_id}: ${amt:.2f}")
                    elif not result.get("skipped"):
                        onboarding.send_inline(chat_id,
                            f"⚠️ <b>Auto-Copy Failed</b>\n\n"
                            f"Error: {result.get('error', 'Unknown')}\n"
                            f"Market: {signal.get('market_title', 'Unknown')[:50]}",
                            [[{"text": "⚙️ Auto-Copy Settings", "callback_data": "auto_copy_settings_menu"}]])
            except Exception as e:
                print(f"[COPY-SIG] Auto-copy error for {chat_id}: {e}")

    print(f"[COPY-SIG] Dispatched {sent_count} signal notifications")
    return sent_count

# ═══════════════════════════════════════════════
# SCHEDULED SCANNER
# ═══════════════════════════════════════════════

def run_copy_scan():
    """Run a full copy trading scan — called by scheduler."""
    print(f"[COPY-SIG] Starting scan at {datetime.now(timezone.utc).isoformat()}")

    try:
        signals = ct.scan_all_wallets()
        if signals:
            sent = dispatch_signals(signals)
            print(f"[COPY-SIG] Scan complete: {len(signals)} signals, {sent} notifications sent")
        else:
            print("[COPY-SIG] Scan complete: no new signals")
        return signals
    except Exception as e:
        print(f"[COPY-SIG] Scan error: {e}")
        return []

def run_leaderboard_refresh():
    """Refresh the leaderboard — called periodically."""
    try:
        leaders = ct.refresh_leaderboard()
        print(f"[COPY-SIG] Leaderboard refreshed: {len(leaders)} traders")
        return leaders
    except Exception as e:
        print(f"[COPY-SIG] Leaderboard error: {e}")
        return []

# ═══════════════════════════════════════════════
# ADMIN NOTIFICATIONS
# ═══════════════════════════════════════════════

def send_admin_copy_stats():
    """Send copy trading stats to admin."""
    stats = ct.get_copy_stats()
    from config import TELEGRAM_CHAT_ID
    tg.send(
        f"📋 <b>Copy Trading Stats</b>\n\n"
        f"👛 Tracked wallets: {stats['total_wallets']} ({stats['active_wallets']} active)\n"
        f"👥 Followers: {stats['unique_followers']} users, {stats['total_follow_relations']} follows\n"
        f"🔔 Total signals: {stats['total_signals']}\n"
        f"🕐 Last scan: {stats['last_scan'][:19] if stats['last_scan'] != 'never' else 'never'}",
        str(TELEGRAM_CHAT_ID)
    )
