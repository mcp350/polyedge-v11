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

        # Resolve event URL and slug for buttons
        event_url = signal.get("event_url", "")
        event_slug = signal.get("event_slug", "") or signal.get("slug", "")
        slug = signal.get("slug", "")
        sig_type = signal.get("type", "")
        outcome = signal.get("outcome", "Yes")

        # Build action buttons
        buttons = []

        # Row 1: Polymarket event link (clickable URL)
        if event_url:
            buttons.append([{"text": "🔗 Open on Polymarket", "url": event_url}])

        if sig_type in ("NEW_POSITION", "INCREASED"):
            # Row 2: Research button — triggers full AI research on this event
            if event_url or event_slug:
                research_url = event_url or f"https://polymarket.com/event/{event_slug}"
                # Use whale_research_ callback to trigger research flow in main.py
                # Truncate URL to fit in callback_data (max 64 bytes)
                research_slug = event_slug or slug
                if research_slug:
                    buttons.append([
                        {"text": "🔬 Research Event", "callback_data": f"whale_research_{research_slug[:50]}"},
                    ])

            # Row 3: Buy buttons — use actual outcome names (handles multi-outcome markets)
            if slug:
                # Get all available outcomes from the market
                all_outcomes = signal.get("all_outcomes", [])
                if all_outcomes and len(all_outcomes) >= 2:
                    # Multi-outcome or binary — show actual names
                    o1, o2 = all_outcomes[0], all_outcomes[1]
                    buttons.append([
                        {"text": f"🟩 Buy {o1[:12]}", "callback_data": f"whale_buy_{slug[:42]}_{o1}"},
                        {"text": f"🟥 Buy {o2[:12]}", "callback_data": f"whale_buy_{slug[:42]}_{o2}"},
                    ])
                else:
                    # Fallback — show the whale's outcome + opposite
                    opp = "No" if outcome in ("Yes", "yes") else "Yes"
                    buttons.append([
                        {"text": f"🟩 Buy {outcome[:12]}", "callback_data": f"whale_buy_{slug[:42]}_{outcome}"},
                        {"text": f"🟥 Buy {opp[:12]}", "callback_data": f"whale_buy_{slug[:42]}_{opp}"},
                    ])

        # Row 4: View Trader
        buttons.append([
            {"text": f"📊 View Trader", "callback_data": f"ct_detail_{wallet_addr[:20]}"},
        ])
        buttons.append([{"text": "📋 My Copy Portfolio", "callback_data": "ct_following"}])

        # Send to each follower (free for all users)
        for chat_id in followers:

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
