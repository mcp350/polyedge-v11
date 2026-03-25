"""
ONBOARDING v4 — Polytragent Telegram bot onboarding
/start → paywall with social proof → subscribe or access code
After activation → risk profile → category selection → main menu
Brand: Polytragent
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
# ACCESS CODE HANDLING
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
    """Handle text input when user is entering an access code."""
    set_waiting_for_code(chat_id, False)
    code = text.strip().upper()

    result = user_store.redeem_access_code(chat_id, code)

    if result["status"] == "ok":
        days = result.get("duration_days", 30)
        send_inline(chat_id,
            f"🎉 <b>Welcome to Polytragent!</b>\n\n"
            f"Your access code has been activated.\n"
            f"📅 Access granted for <b>{days} days</b>\n\n"
            "Let's set up your profile to personalize your experience.",
            [[{"text": "🚀 Set Up Profile", "callback_data": "onboard_risk"}],
             [{"text": "⏩ Skip to Menu", "callback_data": "go_main_menu"}]])
    else:
        send_inline(chat_id,
            f"❌ <b>Invalid Code</b>\n\n{result['message']}",
            [[{"text": "🔄 Try Another Code", "callback_data": "enter_code"}],
             [{"text": "⚡ Subscribe — $99/mo", "callback_data": "subscribe"}],
             [{"text": "← Back", "callback_data": "main_menu"}]])

# ═══════════════════════════════════════════════
# ONBOARDING WIZARD — Risk Profile + Categories
# ═══════════════════════════════════════════════

def show_risk_profile_selection(chat_id, message_id=None):
    """Step 1: Risk profile selection (Spec Section 3.1 Step 2)"""
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
        "• Higher volatility opportunities"
    )
    buttons = [
        [{"text": "🟢 Conservative", "callback_data": "ob_risk_conservative"}],
        [{"text": "🟡 Moderate (Recommended)", "callback_data": "ob_risk_moderate"}],
        [{"text": "🔴 Aggressive", "callback_data": "ob_risk_aggressive"}],
        [{"text": "⏩ Skip", "callback_data": "onboard_categories"}],
    ]
    if message_id:
        edit_message(chat_id, message_id, text, buttons)
    else:
        send_inline(chat_id, text, buttons)

def show_category_selection(chat_id, message_id=None):
    """Step 2: Category selection (Spec Section 3.1 Step 3)"""
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
        [{"text": f"{check('crypto')} Crypto", "callback_data": "ob_cat_crypto"},
         {"text": f"{check('politics')} Politics", "callback_data": "ob_cat_politics"}],
        [{"text": f"{check('sports')} Sports", "callback_data": "ob_cat_sports"},
         {"text": f"{check('world')} World Events", "callback_data": "ob_cat_world"}],
        [{"text": f"{check('entertainment')} Entertainment", "callback_data": "ob_cat_entertainment"},
         {"text": f"{check('finance')} Finance", "callback_data": "ob_cat_finance"}],
        [{"text": "✅ Select All", "callback_data": "ob_cat_all"}],
        [{"text": "🚀 Done — Open Menu", "callback_data": "go_main_menu"}],
    ]
    if message_id:
        edit_message(chat_id, message_id, text, buttons)
    else:
        send_inline(chat_id, text, buttons)

def handle_onboarding_risk(chat_id, data, message_id=None):
    """Handle risk profile selection during onboarding."""
    profile = data.replace("ob_risk_", "")  # conservative, moderate, aggressive
    user_store.update_user(chat_id, {"risk_profile": profile})
    # Show confirmation + move to categories
    emoji = {"conservative": "🟢", "moderate": "🟡", "aggressive": "🔴"}.get(profile, "🟡")
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
        current = ["crypto", "politics", "sports", "world", "entertainment", "finance"]
    elif cat in current:
        current.remove(cat)
    else:
        current.append(cat)
    user_store.set_categories(chat_id, current)
    # Re-render category selection with updated checkmarks
    show_category_selection(chat_id, message_id)

# ═══════════════════════════════════════════════
# ONBOARDING — PAID ONLY
# ═══════════════════════════════════════════════

def handle_start(chat_id, username="", first_name=""):
    user = user_store.get_user(chat_id)
    if not user:
        user = user_store.create_user(chat_id, username, first_name)

    if user_store.is_subscribed(chat_id):
        # Subscribed user — send straight to main menu
        from main import send_main_menu
        send_main_menu(chat_id)
        return

    name = first_name or username or "trader"

    try:
        import prediction_store as pstore
        perf = pstore.get_performance()
        total = perf.get("total", 0)
        win_rate = perf.get("win_rate") or 0
        stats_line = f"📊 <b>{total} predictions tracked, {win_rate:.0f}% win rate</b>\n\n" if total > 0 else ""
    except:
        stats_line = ""

    subscriber_count = len(user_store.get_all_subscribers())
    social_proof = f"👥 <b>{subscriber_count} active members</b>\n" if subscriber_count > 0 else ""

    send_inline(chat_id,
        f"👋 <b>Hey {name}!</b>\n\n"
        "Welcome to <b>Polytragent</b> — your AI-powered Polymarket trading agent.\n\n"
        "🧠 <b>What I do:</b>\n"
        "I use AI to research every market on Polymarket, score them, "
        "and deliver actionable trading signals — so you don't have to.\n\n"
        "🔥 <b>What you get ($99/mo):</b>\n"
        "• 📊 Portfolio dashboard & risk management\n"
        "• 🏆 AI strategy signals & direct betting\n"
        "• 📈 Market analysis, whale alerts & news intel\n"
        "• 🔄 Copy trade top wallets — real-time\n"
        "• 📉 Strategy backtesting engine\n"
        "• ⚙️ Custom risk profiles & settings\n\n"
        f"{stats_line}"
        f"{social_proof}"
        "✅ Cancel anytime. No lock-in.\n"
        "🔒 Secure payment via Stripe.\n\n"
        "👇 Choose how to get started:",
        [[{"text": "⚡ Subscribe — $99/mo", "callback_data": "subscribe"}],
         [{"text": "🔑 I Have an Access Code", "callback_data": "enter_code"}]])


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

    if data == "subscribe":
        _start_subscribe(chat_id, username, message_id)

    elif data == "enter_code":
        set_waiting_for_code(chat_id, True)
        send_inline(chat_id,
            "🔑 <b>Enter Access Code</b>\n\n"
            "Send your access code now (e.g., PTA-XXXXXXXX).\n\n"
            "The code will be validated and your access will be activated immediately.",
            [[{"text": "← Cancel", "callback_data": "main_menu"}]])

    elif data == "dashboard":
        if not user_store.is_subscribed(chat_id) and not user_store.is_admin(chat_id):
            handle_start(chat_id, username, first_name)
            return
        _send_dashboard_link(chat_id)

    elif data == "manage_sub":
        if not user_store.is_subscribed(chat_id) and not user_store.is_admin(chat_id):
            handle_start(chat_id, username, first_name)
            return
        _manage_subscription(chat_id)

    elif data == "run_top10":
        if not user_store.is_subscribed(chat_id) and not user_store.is_admin(chat_id):
            handle_start(chat_id, username, first_name)
            return
        from main import _handle
        _handle("/top10", chat_id)

    elif data == "main_menu":
        set_waiting_for_code(chat_id, False)
        handle_start(chat_id, username, first_name)

    elif data == "go_main_menu":
        # Send to v10 main menu (5-section grid)
        set_waiting_for_code(chat_id, False)
        if user_store.is_subscribed(chat_id) or user_store.is_admin(chat_id):
            from main import send_main_menu
            send_main_menu(chat_id)
        else:
            handle_start(chat_id, username, first_name)

    # ── Onboarding wizard callbacks ──
    elif data == "onboard_risk":
        show_risk_profile_selection(chat_id, message_id)

    elif data.startswith("ob_risk_"):
        handle_onboarding_risk(chat_id, data, message_id)

    elif data == "onboard_categories":
        show_category_selection(chat_id, message_id)

    elif data.startswith("ob_cat_"):
        handle_onboarding_category(chat_id, data, message_id)

    elif data.startswith("cat_"):
        _handle_category(chat_id, data, message_id)


def _start_subscribe(chat_id, username, message_id=None):
    url = stripe_handler.create_checkout_session(chat_id, username)
    if not url:
        send_inline(chat_id,
            "❌ Payment system not configured yet.\n\n"
            "Contact admin for an access code, or try again later.",
            [[{"text": "🔑 Enter Access Code", "callback_data": "enter_code"}],
             [{"text": "🔄 Try again", "callback_data": "subscribe"}]])
        return

    text = (
        "⚡ <b>Subscribe to Polytragent — $99/month</b>\n\n"
        "<b>Unlock everything:</b>\n"
        "• 📊 Portfolio dashboard & risk management\n"
        "• 🏆 AI strategy signals & direct betting\n"
        "• 📈 Market analysis, whale & price alerts\n"
        "• 🔄 Copy trade top wallets in real-time\n"
        "• 📉 Strategy backtesting engine\n"
        "• ⚙️ Custom risk profiles & position sizing\n\n"
        "✅ Cancel anytime. No lock-in.\n"
        "🔒 Secure payment via Stripe.\n\n"
        "👇 Click below to complete payment:"
    )
    buttons = [
        [{"text": "💳 Pay $99/mo — Open Checkout", "url": url}],
        [{"text": "🔑 I Have an Access Code", "callback_data": "enter_code"}],
        [{"text": "← Back", "callback_data": "main_menu"}],
    ]
    if message_id:
        edit_message(chat_id, message_id, text, buttons)
    else:
        send_inline(chat_id, text, buttons)


def _handle_category(chat_id, data, message_id):
    cat = data.replace("cat_", "")
    current = user_store.get_categories(chat_id) or []
    if cat == "all":
        current = ["crypto", "politics", "sports", "world", "entertainment", "finance"]
    elif cat in current:
        current.remove(cat)
    else:
        current.append(cat)
    user_store.set_categories(chat_id, current)


def _send_dashboard_link(chat_id):
    from config import BOT_DOMAIN
    token = user_store.generate_dashboard_token(chat_id)
    if token:
        send_inline(chat_id,
            f"📊 <b>Polytragent Dashboard</b>\n\n"
            f"Click below to open your personal dashboard.\n"
            f"This link is unique to you — don't share it.",
            [[{"text": "🔗 Open Dashboard", "url": f"{BOT_DOMAIN}/dashboard?token={token}"}]])
    else:
        send_inline(chat_id, "❌ Error generating dashboard link.", [])


def _manage_subscription(chat_id):
    user = user_store.get_user(chat_id)
    sub = user.get("subscription", {}) if user else {}

    # If access-code subscriber, show different UI
    if sub.get("access_code"):
        expires = sub.get("expires_at", "")[:10] if sub.get("expires_at") else "N/A"
        send_inline(chat_id,
            f"⚙️ <b>Your Polytragent Subscription</b>\n\n"
            f"📋 Plan: <b>Pro (Access Code)</b>\n"
            f"🔑 Code: <b>{sub.get('access_code', 'N/A')}</b>\n"
            f"📅 Expires: <b>{expires}</b>\n\n"
            "To extend, enter a new access code or subscribe via Stripe.",
            [[{"text": "🔑 Enter New Code", "callback_data": "enter_code"}],
             [{"text": "⚡ Subscribe via Stripe", "callback_data": "subscribe"}],
             [{"text": "← Back", "callback_data": "go_main_menu"}]])
        return

    url = stripe_handler.create_portal_session(chat_id)
    if url:
        send_inline(chat_id,
            "⚙️ <b>Manage Polytragent Subscription</b>\n\n"
            "Click below to manage your billing, update payment method, or cancel.",
            [[{"text": "⚙️ Open Billing Portal", "url": url}],
             [{"text": "← Back", "callback_data": "go_main_menu"}]])
    else:
        send_inline(chat_id,
            "❌ No active Stripe subscription found.\n\n"
            "You can subscribe or use an access code.",
            [[{"text": "⚡ Subscribe — $99/mo", "callback_data": "subscribe"}],
             [{"text": "🔑 Enter Access Code", "callback_data": "enter_code"}]])


# ═══════════════════════════════════════════════
# SUBSCRIPTION EVENT NOTIFICATIONS
# ═══════════════════════════════════════════════

def notify_activated(chat_id):
    """Called when Stripe payment succeeds or subscription activates."""
    send_inline(chat_id,
        "🎉 <b>Welcome to Polytragent!</b>\n\n"
        "Your subscription is <b>active</b>. You now have full access.\n\n"
        "Let's personalize your experience with a quick setup:",
        [[{"text": "🚀 Set Up Profile", "callback_data": "onboard_risk"}],
         [{"text": "⏩ Skip to Menu", "callback_data": "go_main_menu"}]])

def notify_payment_failed(chat_id):
    send_inline(chat_id,
        "⚠️ <b>Payment Failed</b>\n\n"
        "Your last payment didn't go through. "
        "Please update your payment method to keep your Polytragent access.",
        [[{"text": "💳 Update Payment", "callback_data": "manage_sub"}]])

def notify_cancelled(chat_id):
    send_inline(chat_id,
        "😔 <b>Subscription Cancelled</b>\n\n"
        "Your Polytragent access has been deactivated. "
        "You can resubscribe anytime to get back in.",
        [[{"text": "⚡ Resubscribe — $99/mo", "callback_data": "subscribe"}],
         [{"text": "🔑 Enter Access Code", "callback_data": "enter_code"}]])
