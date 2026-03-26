"""
ONBOARDING v5 — PolyEdge Telegram bot onboarding
Phase 2 Business Model: FREE access for everyone + Degen Mode ($79/mo) optional upsell
/start → auto wallet creation → welcome screen → quick setup (risk + categories) → main menu
No paywall, access codes for Degen Mode gifting only
Brand: PolyEdge
"""

import requests
from config import TELEGRAM_TOKEN
import user_store
import stripe_handler

BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Track users waiting to enter access code
_waiting_code = set()

# ═══════════════════════════════════════════════
# INLINE KEYBOARD HELPERS
# ═══════════════════════════════════════════════

def send_inline(chat_id, text, buttons, parse_mode="HTML"):
    try:
        r = requests.post(f"{BASE}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": buttons},
        }, timeout=15)
        if not r.ok:
            import re
            plain = re.sub(r"<[^>]+>", "", text)
            requests.post(f"{BASE}/sendMessage", json={
                "chat_id": chat_id, "text": plain,
                "reply_markup": {"inline_keyboard": buttons},
            }, timeout=15)
    except Exception as e:
        print(f"[ONBOARD] send_inline error: {e}")

def answer_callback(callback_query_id, text=""):
    try:
        requests.post(f"{BASE}/answerCallbackQuery", json={
            "callback_query_id": callback_query_id,
            "text": text,
        }, timeout=5)
    except:
        pass

def edit_message(chat_id, message_id, text, buttons=None, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        requests.post(f"{BASE}/editMessageText", json=payload, timeout=15)
    except Exception as e:
        print(f"[ONBOARD] edit error: {e}")

# ═══════════════════════════════════════════════
# ACCESS CODE HANDLING (Degen Mode only)
# ═══════════════════════════════════════════════

def is_waiting_for_code(chat_id: str) -> bool:
    return str(chat_id) in _waiting_code

def set_waiting_for_code(chat_id: str, waiting: bool):
    cid = str(chat_id)
    if waiting:
        _waiting_code.add(cid)
    else:
        _waiting_code.discard(cid)

def handle_access_code_input(chat_id: str, text: str):
    """Handle text input when user is entering an access code for Degen Mode."""
    set_waiting_for_code(chat_id, False)
    code = text.strip().upper()

    result = user_store.redeem_degen_access_code(chat_id, code)

    if result["status"] == "ok":
        notify_activated(chat_id)
        send_inline(chat_id,
            "🎉 <b>Degen Mode Activated!</b>\n\n"
            "Your access code has been redeemed.\n"
            "You now have Degen Mode benefits unlocked.",
            [[{"text": "🚀 Main Menu", "callback_data": "go_main_menu"}]])
    else:
        send_inline(chat_id,
            f"❌ <b>Invalid Code</b>\n\n{result['message']}\n\n"
            "You still have free access. Upgrade to Degen Mode to unlock advanced features.",
            [[{"text": "🔄 Try Another Code", "callback_data": "enter_code"}],
             [{"text": "💎 Subscribe to Degen ($79/mo)", "callback_data": "subscribe_degen"}],
             [{"text": "← Back", "callback_data": "go_main_menu"}]])

# ═══════════════════════════════════════════════
# ONBOARDING WIZARD — Risk Profile + Categories
# ═══════════════════════════════════════════════

def show_risk_profile_selection(chat_id, message_id=None):
    """Step 1: Risk profile selection"""
    text = (
        "📊 <b>Step 1: Risk Profile</b>\n\n"
        "Choose your risk tolerance. This controls position sizing, "
        "max exposure, and which signals you receive.\n\n"
        "🟢 <b>Conservative</b>\n"
        "• Max 2% per position\n"
        "• Focus on high-confidence signals only\n"
        "• Lower volatility markets preferred\n\n"
        "🟡 <b>Moderate</b> (Recommended)\n"
        "• Max 5% per position\n"
        "• Balanced signal mix\n"
        "• Standard market selection\n\n"
        "🔴 <b>Aggressive</b>\n"
        "• Max 10% per position\n"
        "• All signals including speculative\n"
        "• Higher volatility opportunities\n\n"
        "⚡ <b>Degen</b>\n"
        "• Unlimited position sizing\n"
        "• All signals, full risk\n"
        "• Maximum upside (maximum risk)"
    )
    buttons = [
        [{"text": "🟢 Conservative", "callback_data": "ob_risk_conservative"}],
        [{"text": "🟡 Moderate (Recommended)", "callback_data": "ob_risk_moderate"}],
        [{"text": "🔴 Aggressive", "callback_data": "ob_risk_aggressive"}],
        [{"text": "⚡ Degen", "callback_data": "ob_risk_degen"}],
        [{"text": "⏩ Skip", "callback_data": "onboard_categories"}],
    ]
    if message_id:
        edit_message(chat_id, message_id, text, buttons)
    else:
        send_inline(chat_id, text, buttons)

def show_category_selection(chat_id, message_id=None):
    """Step 2: Category selection"""
    current = user_store.get_categories(chat_id) or []
    def check(cat):
        return "✅" if cat in current else "⬜"

    text = (
        "📂 <b>Step 2: Market Categories</b>\n\n"
        "Select which market categories interest you. "
        "This personalizes your feed and signals.\n\n"
        "Tap to toggle, then press Done."
    )
    buttons = [
        [{"text": f"{check('defi')} DeFi", "callback_data": "ob_cat_defi"},
         {"text": f"{check('nfts')} NFTs", "callback_data": "ob_cat_nfts"}],
        [{"text": f"{check('tokens')} Tokens", "callback_data": "ob_cat_tokens"},
         {"text": f"{check('dao')} DAO", "callback_data": "ob_cat_dao"}],
        [{"text": f"{check('l2')} Layer 2s", "callback_data": "ob_cat_l2"},
         {"text": f"{check('bridges')} Bridges", "callback_data": "ob_cat_bridges"}],
        [{"text": "✅ Select All", "callback_data": "ob_cat_all"}],
        [{"text": "🚀 Done — Open Menu", "callback_data": "go_main_menu"}],
    ]
    if message_id:
        edit_message(chat_id, message_id, text, buttons)
    else:
        send_inline(chat_id, text, buttons)

def handle_onboarding_risk(chat_id, data, message_id=None):
    """Handle risk profile selection during onboarding."""
    profile = data.replace("ob_risk_", "")  # conservative, moderate, aggressive, degen
    user_store.update_user(chat_id, {"risk_profile": profile})
    # Show confirmation + move to categories
    emoji = {
        "conservative": "🟢",
        "moderate": "🟡",
        "aggressive": "🔴",
        "degen": "⚡"
    }.get(profile, "🟡")
    if message_id:
        edit_message(chat_id, message_id,
            f"{emoji} Risk profile set to <b>{profile.title()}</b>!\n\n"
            "Now let's choose your market categories.",
            [[{"text": "📂 Choose Categories", "callback_data": "onboard_categories"}],
             [{"text": "⏩ Skip to Menu", "callback_data": "go_main_menu"}]])
    else:
        send_inline(chat_id,
            f"{emoji} Risk profile set to <b>{profile.title()}</b>!\n\n"
            "Now let's choose your market categories.",
            [[{"text": "📂 Choose Categories", "callback_data": "onboard_categories"}],
             [{"text": "⏩ Skip to Menu", "callback_data": "go_main_menu"}]])

def handle_onboarding_category(chat_id, data, message_id=None):
    """Handle category toggle during onboarding."""
    cat = data.replace("ob_cat_", "")
    current = user_store.get_categories(chat_id) or []
    if cat == "all":
        current = ["defi", "nfts", "tokens", "dao", "l2", "bridges"]
    elif cat in current:
        current.remove(cat)
    else:
        current.append(cat)
    user_store.set_categories(chat_id, current)
    # Re-render category selection with updated checkmarks
    show_category_selection(chat_id, message_id)

# ═══════════════════════════════════════════════
# ONBOARDING — FREE ACCESS FOR ALL
# ═══════════════════════════════════════════════

def handle_start(chat_id, username="", first_name=""):
    """
    Phase 2 /start flow:
    1. Create user if not exists
    2. Auto-create wallet if user has no wallet
    3. Show welcome screen with wallet address
    4. Quick setup: risk profile → categories → main menu
    No paywall — everyone gets free access
    """
    user = user_store.get_user(chat_id)
    if not user:
        user = user_store.create_user(chat_id, username, first_name)

    # Auto-create wallet if user doesn't have one
    wallet_address = user.get("wallet_address")
    if not wallet_address:
        try:
            # Import wallet_manager from main or config
            from wallet_manager import create_wallet
            wallet_address = create_wallet()
            user_store.update_user(chat_id, {"wallet_address": wallet_address})
        except Exception as e:
            print(f"[ONBOARD] Wallet creation error: {e}")
            wallet_address = "Error creating wallet"

    name = first_name or username or "trader"

    # Welcome screen with wallet info
    send_inline(chat_id,
        f"👋 <b>Hey {name}!</b>\n\n"
        "Welcome to <b>PolyEdge</b> — your AI-powered prediction market trading agent.\n\n"
        "🧠 <b>What I do:</b>\n"
        "I use AI to research every market, score them, and deliver actionable trading signals.\n\n"
        "💰 <b>Your Wallet</b>\n"
        f"<code>{wallet_address}</code>\n\n"
        "<b>Free Access Enabled</b> ✅\n"
        "Everyone gets free access to core features. Upgrade to Degen Mode ($79/mo) for advanced tools.\n\n"
        "Let's personalize your experience:",
        [[{"text": "🚀 Quick Setup", "callback_data": "onboard_risk"}],
         [{"text": "⏩ Skip to Menu", "callback_data": "go_main_menu"}]])


def handle_callback(callback_query):
    data = callback_query.get("data", "")
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
    message_id = callback_query.get("message", {}).get("message_id")
    cb_id = callback_query.get("id")
    user_info = callback_query.get("from", {})
    username = user_info.get("username", "")
    first_name = user_info.get("first_name", "")

    if not user_store.get_user(chat_id):
        user_store.create_user(chat_id, username, first_name)

    answer_callback(cb_id)

    # Degen Mode subscription
    if data == "subscribe_degen":
        _start_subscribe_degen(chat_id, username, message_id)

    # Access code input (Degen Mode only)
    elif data == "enter_code":
        set_waiting_for_code(chat_id, True)
        send_inline(chat_id,
            "🔑 <b>Redeem Degen Mode Access Code</b>\n\n"
            "Send your access code now to activate Degen Mode.\n\n"
            "Don't have a code? Subscribe to Degen Mode ($79/mo) instead.",
            [[{"text": "💎 Subscribe to Degen", "callback_data": "subscribe_degen"}],
             [{"text": "← Back", "callback_data": "go_main_menu"}]])

    # Manage subscription
    elif data == "manage_sub":
        _manage_degen_subscription(chat_id)

    # Cancel Degen subscription
    elif data == "cancel_degen":
        user_store.cancel_degen_subscription(chat_id)
        notify_cancelled(chat_id)
        send_inline(chat_id,
            "😔 <b>Degen Mode Cancelled</b>\n\n"
            "Your Degen Mode subscription has been cancelled. "
            "You still have free access to all core features. "
            "Resubscribe anytime.",
            [[{"text": "💎 Resubscribe to Degen", "callback_data": "subscribe_degen"}],
             [{"text": "🚀 Main Menu", "callback_data": "go_main_menu"}]])

    # Go to main menu
    elif data == "go_main_menu":
        set_waiting_for_code(chat_id, False)
        # Send to main menu (handled by main.py)
        from main import send_main_menu
        send_main_menu(chat_id)

    # Onboarding wizard callbacks
    elif data == "onboard_risk":
        show_risk_profile_selection(chat_id, message_id)

    elif data.startswith("ob_risk_"):
        handle_onboarding_risk(chat_id, data, message_id)

    elif data == "onboard_categories":
        show_category_selection(chat_id, message_id)

    elif data.startswith("ob_cat_"):
        handle_onboarding_category(chat_id, data, message_id)


def _start_subscribe_degen(chat_id, username, message_id=None):
    """Initiate Degen Mode subscription ($79/mo)."""
    url = stripe_handler.create_checkout_session(chat_id, username, price="degen_monthly")
    if not url:
        send_inline(chat_id,
            "❌ Payment system not configured.\n\n"
            "You have free access. Contact support for Degen Mode setup.",
            [[{"text": "← Back", "callback_data": "go_main_menu"}]])
        return

    text = (
        "💎 <b>Upgrade to Degen Mode — $79/month</b>\n\n"
        "<b>Advanced Features:</b>\n"
        "• 📊 Advanced portfolio analytics\n"
        "• 🏆 Premium AI signals\n"
        "• 📈 Whale tracking & alerts\n"
        "• 🔄 Advanced copy trading\n"
        "• 📉 Backtesting engine\n"
        "• ⚙️ Unlimited custom settings\n"
        "• 🎯 Priority support\n\n"
        "✅ Cancel anytime. No lock-in.\n"
        "🔒 Secure payment via Stripe.\n\n"
        "You'll keep your free access even if you cancel."
    )
    buttons = [
        [{"text": "💳 Pay $79/mo — Open Checkout", "url": url}],
        [{"text": "🔑 I Have an Access Code", "callback_data": "enter_code"}],
        [{"text": "← Back", "callback_data": "go_main_menu"}],
    ]
    if message_id:
        edit_message(chat_id, message_id, text, buttons)
    else:
        send_inline(chat_id, text, buttons)


def _manage_degen_subscription(chat_id):
    """Show Degen Mode subscription management UI."""
    user = user_store.get_user(chat_id)
    has_degen = user.get("degen_subscription", False) if user else False

    if has_degen:
        # User has active Degen subscription
        url = stripe_handler.create_portal_session(chat_id)
        if url:
            send_inline(chat_id,
                "⚙️ <b>Manage Degen Mode</b>\n\n"
                "You have an active Degen Mode subscription.",
                [[{"text": "⚙️ Billing Portal", "url": url}],
                 [{"text": "❌ Cancel Degen", "callback_data": "cancel_degen"}],
                 [{"text": "← Back", "callback_data": "go_main_menu"}]])
        else:
            send_inline(chat_id,
                "⚙️ <b>Degen Mode Active</b>\n\n"
                "You have an active Degen Mode subscription.",
                [[{"text": "❌ Cancel Degen", "callback_data": "cancel_degen"}],
                 [{"text": "← Back", "callback_data": "go_main_menu"}]])
    else:
        # User doesn't have Degen subscription
        send_inline(chat_id,
            "⚙️ <b>Degen Mode Subscription</b>\n\n"
            "You have free access. Upgrade to Degen Mode for advanced features.",
            [[{"text": "💎 Subscribe ($79/mo)", "callback_data": "subscribe_degen"}],
             [{"text": "🔑 Enter Access Code", "callback_data": "enter_code"}],
             [{"text": "← Back", "callback_data": "go_main_menu"}]])


# ═══════════════════════════════════════════════
# SUBSCRIPTION EVENT NOTIFICATIONS
# ═══════════════════════════════════════════════

def notify_activated(chat_id):
    """Called when Degen Mode payment succeeds or access code redeemed."""
    send_inline(chat_id,
        "🎉 <b>Degen Mode Activated!</b>\n\n"
        "You now have access to all advanced features. Enjoy the edge.",
        [[{"text": "🚀 Main Menu", "callback_data": "go_main_menu"}]])

def notify_payment_failed(chat_id):
    """Called when Degen Mode payment fails."""
    send_inline(chat_id,
        "⚠️ <b>Payment Failed</b>\n\n"
        "Your Degen Mode payment didn't go through. "
        "Please update your payment method to keep your subscription.",
        [[{"text": "💳 Update Payment", "callback_data": "manage_sub"}],
         [{"text": "← Back", "callback_data": "go_main_menu"}]])

def notify_cancelled(chat_id):
    """Called when Degen Mode subscription is cancelled."""
    send_inline(chat_id,
        "😔 <b>Degen Mode Cancelled</b>\n\n"
        "Your Degen Mode subscription has been cancelled. "
        "You still have free access to all core features.",
        [[{"text": "💎 Resubscribe", "callback_data": "subscribe_degen"}],
         [{"text": "🚀 Main Menu", "callback_data": "go_main_menu"}]])
