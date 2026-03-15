"""
STRIPE HANDLER — Checkout sessions + webhook processing
Requires: pip install stripe
Config: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID in config.py
"""

import stripe
import os
import config
import user_store

# Safely read Stripe config — won't crash if keys missing from config.py
STRIPE_SECRET_KEY    = getattr(config, "STRIPE_SECRET_KEY", "") or os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = getattr(config, "STRIPE_WEBHOOK_SECRET", "") or os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID      = getattr(config, "STRIPE_PRICE_ID", "") or os.environ.get("STRIPE_PRICE_ID", "")
BOT_DOMAIN           = getattr(config, "BOT_DOMAIN", "https://polytragent.com") or os.environ.get("BOT_DOMAIN", "https://polytragent.com")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
    print(f"[STRIPE] Configured with key ending ...{STRIPE_SECRET_KEY[-4:]}")
else:
    print("[STRIPE] WARNING: No STRIPE_SECRET_KEY found in config.py — payments disabled")

# ═══════════════════════════════════════════════
# CHECKOUT
# ═══════════════════════════════════════════════

def create_checkout_session(chat_id: str, username: str = "") -> str:
    """Create a Stripe Checkout session. Returns the checkout URL."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        print(f"[STRIPE] Cannot create checkout: SECRET_KEY={'SET' if STRIPE_SECRET_KEY else 'MISSING'}, PRICE_ID={'SET' if STRIPE_PRICE_ID else 'MISSING'}")
        return ""
    try:
        # Check if user already has a Stripe customer
        user = user_store.get_user(chat_id)
        customer_id = None
        if user:
            customer_id = user.get("subscription", {}).get("stripe_customer_id") or None

        params = {
            "mode": "subscription",
            "payment_method_types": ["card"],
            "line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}],
            "success_url": f"{BOT_DOMAIN}/dashboard?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            "cancel_url": f"{BOT_DOMAIN}/dashboard?status=cancelled",
            "metadata": {"telegram_chat_id": str(chat_id), "telegram_username": username},
            "subscription_data": {
                "metadata": {"telegram_chat_id": str(chat_id)},
            },
            "allow_promotion_codes": True,
        }
        if customer_id:
            params["customer"] = customer_id
        # Note: don't set customer_creation for subscription mode
        # Stripe auto-creates customers for subscriptions

        session = stripe.checkout.Session.create(**params)
        return session.url
    except Exception as e:
        print(f"[STRIPE] Checkout error: {e}")
        return ""

# ═══════════════════════════════════════════════
# CUSTOMER PORTAL (manage subscription)
# ═══════════════════════════════════════════════

def create_portal_session(chat_id: str) -> str:
    """Create a Stripe Customer Portal session for managing subscription."""
    try:
        user = user_store.get_user(chat_id)
        if not user:
            return ""
        customer_id = user.get("subscription", {}).get("stripe_customer_id")
        if not customer_id:
            return ""
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{BOT_DOMAIN}/dashboard",
        )
        return session.url
    except Exception as e:
        print(f"[STRIPE] Portal error: {e}")
        return ""

# ═══════════════════════════════════════════════
# WEBHOOK PROCESSING
# ═══════════════════════════════════════════════

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process Stripe webhook event. Returns action dict."""
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        return {"error": "Invalid payload"}
    except stripe.error.SignatureVerificationError:
        return {"error": "Invalid signature"}

    event_type = event["type"]
    obj = event["data"]["object"]

    print(f"[STRIPE] Webhook: {event_type}")

    # ── Checkout completed — activate subscription ──
    if event_type == "checkout.session.completed":
        chat_id = obj.get("metadata", {}).get("telegram_chat_id")
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        if chat_id:
            user_store.activate_subscription(
                chat_id=chat_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
            )
            return {"action": "activated", "chat_id": chat_id}

    # ── Subscription renewed / invoice paid ──
    elif event_type == "invoice.paid":
        sub_id = obj.get("subscription")
        customer_id = obj.get("customer")
        chat_id = _find_chat_id_by_customer(customer_id)
        if chat_id:
            user_store.activate_subscription(
                chat_id=chat_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=sub_id,
            )
            return {"action": "renewed", "chat_id": chat_id}

    # ── Payment failed ──
    elif event_type == "invoice.payment_failed":
        customer_id = obj.get("customer")
        chat_id = _find_chat_id_by_customer(customer_id)
        if chat_id:
            user_store.update_user(chat_id, {"subscription": {"status": "past_due"}})
            return {"action": "payment_failed", "chat_id": chat_id}

    # ── Subscription cancelled ──
    elif event_type == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        chat_id = _find_chat_id_by_customer(customer_id)
        if chat_id:
            user_store.deactivate_subscription(chat_id)
            return {"action": "cancelled", "chat_id": chat_id}

    # ── Subscription updated (cancel at period end, etc.) ──
    elif event_type == "customer.subscription.updated":
        customer_id = obj.get("customer")
        chat_id = _find_chat_id_by_customer(customer_id)
        cancel_at = obj.get("cancel_at_period_end", False)
        if chat_id and cancel_at:
            user_store.update_user(chat_id, {"subscription": {"cancel_at_period_end": True}})
            return {"action": "cancel_scheduled", "chat_id": chat_id}

    return {"action": "ignored", "type": event_type}


def _find_chat_id_by_customer(customer_id: str) -> str:
    """Find Telegram chat_id by Stripe customer ID."""
    if not customer_id:
        return ""
    for user in user_store.get_all_users():
        if user.get("subscription", {}).get("stripe_customer_id") == customer_id:
            return user.get("chat_id", "")
    return ""
