"""
USER STORE v3 — Multi-user management with Stripe + Access Codes
Storage: users.json alongside main.py
Brand: Polytragent
"""

import json, os, time, secrets, hashlib, string, random
from datetime import datetime, timezone, timedelta

FILE = os.path.join(os.path.dirname(__file__), "users.json")
PLAN_PRICE = 99  # $99/mo single tier

# ═══════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════

def _load():
    if not os.path.exists(FILE):
        return {"users": {}, "tokens": {}, "access_codes": {},
                "stats": {"total_signups": 0, "total_paid": 0}}
    try:
        with open(FILE) as f:
            data = json.load(f)
        # Ensure access_codes key exists (migration)
        if "access_codes" not in data:
            data["access_codes"] = {}
        return data
    except:
        return {"users": {}, "tokens": {}, "access_codes": {},
                "stats": {"total_signups": 0, "total_paid": 0}}

def _save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ═══════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════

def get_user(chat_id: str) -> dict:
    data = _load()
    return data["users"].get(str(chat_id))

def create_user(chat_id: str, username: str = "", first_name: str = "") -> dict:
    data = _load()
    cid = str(chat_id)
    if cid in data["users"]:
        return data["users"][cid]
    user = {
        "chat_id": cid,
        "username": username,
        "first_name": first_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "subscription": {
            "status": "inactive",       # inactive | active | cancelled | past_due
            "stripe_customer_id": "",
            "stripe_subscription_id": "",
            "plan": "",                 # "" | "pro"
            "started_at": "",
            "expires_at": "",
            "cancel_at_period_end": False,
            "access_code": "",          # which code was used
        },
        "onboarding": {
            "step": "welcome",           # welcome | categories | complete
            "categories": [],            # selected categories
            "completed_at": "",
        },
        "dashboard_token": "",           # for web dashboard login
        "total_signals_received": 0,
        "last_active": datetime.now(timezone.utc).isoformat(),
    }
    data["users"][cid] = user
    data["stats"]["total_signups"] = data["stats"].get("total_signups", 0) + 1
    _save(data)
    return user

def update_user(chat_id: str, updates: dict):
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return
    for key, val in updates.items():
        if isinstance(val, dict) and isinstance(data["users"][cid].get(key), dict):
            data["users"][cid][key].update(val)
        else:
            data["users"][cid][key] = val
    data["users"][cid]["last_active"] = datetime.now(timezone.utc).isoformat()
    _save(data)

def is_subscribed(chat_id: str) -> bool:
    user = get_user(str(chat_id))
    if not user:
        return False
    sub = user.get("subscription", {})
    status = sub.get("status", "")
    if status not in ("active", "past_due"):
        return False
    # Check expiry for code-based subs
    expires = sub.get("expires_at", "")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                deactivate_subscription(str(chat_id))
                return False
        except:
            pass
    return True

def activate_subscription(chat_id: str, stripe_customer_id: str = "",
                          stripe_subscription_id: str = "", expires_at: str = "",
                          access_code: str = ""):
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return
    data["users"][cid]["subscription"] = {
        "status": "active",
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "plan": "pro",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "cancel_at_period_end": False,
        "access_code": access_code,
    }
    data["stats"]["total_paid"] = data["stats"].get("total_paid", 0) + 1
    _save(data)

def deactivate_subscription(chat_id: str):
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return
    data["users"][cid]["subscription"]["status"] = "cancelled"
    _save(data)

# ═══════════════════════════════════════════════
# ACCESS CODE SYSTEM
# ═══════════════════════════════════════════════

def generate_access_code(created_by: str = "admin", max_uses: int = 1,
                         duration_days: int = 30, note: str = "") -> str:
    """Generate a new access code. Returns the code string."""
    code = "PTA-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    data = _load()
    data["access_codes"][code] = {
        "code": code,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "max_uses": max_uses,
        "uses": 0,
        "used_by": [],
        "duration_days": duration_days,  # how many days of access per redemption
        "note": note,
        "active": True,
    }
    _save(data)
    return code

def redeem_access_code(chat_id: str, code: str) -> dict:
    """Redeem an access code. Returns {"status": "ok"|"error", "message": "..."}"""
    data = _load()
    cid = str(chat_id)
    code = code.strip().upper()

    if code not in data.get("access_codes", {}):
        return {"status": "error", "message": "Invalid code. Check and try again."}

    ac = data["access_codes"][code]

    if not ac.get("active", True):
        return {"status": "error", "message": "This code has been deactivated."}

    if ac["uses"] >= ac["max_uses"]:
        return {"status": "error", "message": "This code has reached its maximum uses."}

    if cid in ac.get("used_by", []):
        return {"status": "error", "message": "You've already used this code."}

    # Check if user already has active subscription
    if is_subscribed(cid):
        return {"status": "error", "message": "You already have an active subscription!"}

    # Activate subscription
    duration = ac.get("duration_days", 30)
    expires = (datetime.now(timezone.utc) + timedelta(days=duration)).isoformat()

    # Update access code usage
    ac["uses"] += 1
    ac["used_by"].append(cid)
    data["access_codes"][code] = ac

    # Activate user subscription
    if cid not in data["users"]:
        return {"status": "error", "message": "User not found. Send /start first."}

    data["users"][cid]["subscription"] = {
        "status": "active",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "plan": "pro",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires,
        "cancel_at_period_end": False,
        "access_code": code,
    }
    data["stats"]["total_paid"] = data["stats"].get("total_paid", 0) + 1
    _save(data)

    return {
        "status": "ok",
        "message": f"Access granted for {duration} days!",
        "expires_at": expires,
        "duration_days": duration,
    }

def get_all_access_codes() -> list:
    data = _load()
    return list(data.get("access_codes", {}).values())

def deactivate_access_code(code: str) -> bool:
    data = _load()
    code = code.strip().upper()
    if code in data.get("access_codes", {}):
        data["access_codes"][code]["active"] = False
        _save(data)
        return True
    return False

def get_access_code(code: str) -> dict:
    data = _load()
    return data.get("access_codes", {}).get(code.strip().upper())

# ═══════════════════════════════════════════════
# ONBOARDING STATE
# ═══════════════════════════════════════════════

def set_onboarding_step(chat_id: str, step: str):
    update_user(str(chat_id), {"onboarding": {"step": step}})

def set_categories(chat_id: str, categories: list):
    update_user(str(chat_id), {"onboarding": {"categories": categories, "step": "complete",
                                                "completed_at": datetime.now(timezone.utc).isoformat()}})

def get_categories(chat_id: str) -> list:
    user = get_user(str(chat_id))
    if not user:
        return []
    return user.get("onboarding", {}).get("categories", [])

# ═══════════════════════════════════════════════
# DASHBOARD TOKEN
# ═══════════════════════════════════════════════

def generate_dashboard_token(chat_id: str) -> str:
    token = secrets.token_urlsafe(32)
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return ""
    data["users"][cid]["dashboard_token"] = token
    # Reverse lookup
    data["tokens"][token] = cid
    _save(data)
    return token

def get_user_by_token(token: str) -> dict:
    data = _load()
    cid = data.get("tokens", {}).get(token)
    if not cid:
        return None
    return data["users"].get(cid)

# ═══════════════════════════════════════════════
# ADMIN / STATS
# ═══════════════════════════════════════════════

def get_all_subscribers() -> list:
    data = _load()
    return [u for u in data["users"].values()
            if u.get("subscription", {}).get("status") in ("active", "past_due")]

def get_all_users() -> list:
    data = _load()
    return list(data["users"].values())

def get_stats() -> dict:
    data = _load()
    users = list(data["users"].values())
    active = [u for u in users if u.get("subscription", {}).get("status") == "active"]
    code_users = [u for u in active if u.get("subscription", {}).get("access_code")]
    stripe_users = [u for u in active if not u.get("subscription", {}).get("access_code")]
    codes = list(data.get("access_codes", {}).values())
    active_codes = [c for c in codes if c.get("active")]
    return {
        "total_users": len(users),
        "active_subscribers": len(active),
        "stripe_subscribers": len(stripe_users),
        "code_subscribers": len(code_users),
        "mrr": len(stripe_users) * PLAN_PRICE,
        "total_signups": data["stats"].get("total_signups", 0),
        "total_codes": len(codes),
        "active_codes": len(active_codes),
    }

def is_admin(chat_id: str) -> bool:
    """Check if user is admin (your chat ID from config)."""
    from config import TELEGRAM_CHAT_ID
    return str(chat_id) == str(TELEGRAM_CHAT_ID)
