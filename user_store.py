"""
USER STORE v4 — New Polytragent Business Model
Storage: users.json alongside main.py
Brand: Polytragent

Business Model:
- FREE: Full access to basic features + 20 whale wallet tracking
- DEGEN MODE ($79/mo): Unlimited whale tracking, auto-copy execution, AI trade finder
"""

import json, os, time, secrets, hashlib, string, random
from datetime import datetime, timezone, timedelta

FILE = os.path.join(os.path.dirname(__file__), "users.json")
DEGEN_PRICE = 79  # $79/mo for Degen Mode
FREE_WHALE_LIMIT = 20
DEGEN_WHALE_LIMIT = 9999

# ═══════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════

def _load():
    if not os.path.exists(FILE):
        return {
            "users": {},
            "tokens": {},
            "access_codes": {},
            "stats": {
                "total_signups": 0,
                "total_degen_subscribers": 0,
                "total_volume": 0,
                "total_fees_collected": 0,
            }
        }
    try:
        with open(FILE) as f:
            data = json.load(f)
        # Ensure required keys exist (migration)
        if "access_codes" not in data:
            data["access_codes"] = {}
        if "stats" not in data:
            data["stats"] = {
                "total_signups": 0,
                "total_degen_subscribers": 0,
                "total_volume": 0,
                "total_fees_collected": 0,
            }
        return data
    except:
        return {
            "users": {},
            "tokens": {},
            "access_codes": {},
            "stats": {
                "total_signups": 0,
                "total_degen_subscribers": 0,
                "total_volume": 0,
                "total_fees_collected": 0,
            }
        }

def _save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ═══════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════

def _generate_wallet_address() -> str:
    """Generate a unique wallet address for the user."""
    return "0x" + secrets.token_hex(20)

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
        "wallet_address": _generate_wallet_address(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "subscription": {
            "plan": "",  # "" (free) or "degen"
            "status": "active",  # All users start with free access
            "stripe_customer_id": "",
            "stripe_subscription_id": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": "",
            "cancel_at_period_end": False,
            "access_code": "",  # For degen gifting
        },
        "onboarding": {
            "step": "welcome",
            "categories": [],
            "completed_at": "",
        },
        "dashboard_token": "",
        "whale_tracking": {
            "tracked_wallets": [],
            "limit": FREE_WHALE_LIMIT,
        },
        "trading_stats": {
            "total_buys": 0,
            "total_sells": 0,
            "total_volume": 0,
            "total_fees_paid": 0,
            "total_pnl": 0,
        },
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
    """
    ALL users now have subscriptions (free tier is default).
    Returns True for everyone.
    Use is_degen() to check for premium tier.
    """
    user = get_user(str(chat_id))
    return user is not None

def is_degen(chat_id: str) -> bool:
    """Check if user has active Degen Mode subscription."""
    user = get_user(str(chat_id))
    if not user:
        return False

    sub = user.get("subscription", {})
    plan = sub.get("plan", "")
    status = sub.get("status", "")

    if plan != "degen" or status not in ("active", "past_due"):
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

def get_wallet_tracking_limit(chat_id: str) -> int:
    """Get the whale wallet tracking limit for this user."""
    if is_degen(chat_id):
        return DEGEN_WHALE_LIMIT
    return FREE_WHALE_LIMIT

def activate_subscription(chat_id: str, plan: str = "degen", stripe_customer_id: str = "",
                          stripe_subscription_id: str = "", expires_at: str = "",
                          access_code: str = ""):
    """
    Activate a subscription. Plan can be "degen" or "" (free is default).
    For degen: can be paid (stripe) or gifted (access_code).
    """
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return

    data["users"][cid]["subscription"] = {
        "plan": plan,
        "status": "active",
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "cancel_at_period_end": False,
        "access_code": access_code,
    }

    # Update whale tracking limit
    if plan == "degen":
        data["users"][cid]["whale_tracking"]["limit"] = DEGEN_WHALE_LIMIT
        data["stats"]["total_degen_subscribers"] = data["stats"].get("total_degen_subscribers", 0) + 1
    else:
        data["users"][cid]["whale_tracking"]["limit"] = FREE_WHALE_LIMIT

    _save(data)

def deactivate_subscription(chat_id: str):
    """Deactivate Degen Mode subscription (user reverts to free tier)."""
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return

    was_degen = data["users"][cid]["subscription"].get("plan") == "degen"

    data["users"][cid]["subscription"] = {
        "plan": "",
        "status": "cancelled",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "started_at": "",
        "expires_at": "",
        "cancel_at_period_end": False,
        "access_code": "",
    }

    # Reset whale tracking limit to free tier
    data["users"][cid]["whale_tracking"]["limit"] = FREE_WHALE_LIMIT

    # Decrement degen counter if applicable
    if was_degen:
        current = data["stats"].get("total_degen_subscribers", 0)
        data["stats"]["total_degen_subscribers"] = max(0, current - 1)

    _save(data)

# ═══════════════════════════════════════════════
# TRADING STATS
# ═══════════════════════════════════════════════

def record_trade(chat_id: str, amount: float, fee: float, side: str):
    """
    Record a trade execution.
    side: "buy" or "sell"
    amount: trade amount in USD
    fee: fee paid in USD
    """
    data = _load()
    cid = str(chat_id)
    if cid not in data["users"]:
        return

    stats = data["users"][cid]["trading_stats"]

    if side == "buy":
        stats["total_buys"] += 1
    elif side == "sell":
        stats["total_sells"] += 1

    stats["total_volume"] += amount
    stats["total_fees_paid"] += fee

    # Update platform stats
    data["stats"]["total_volume"] = data["stats"].get("total_volume", 0) + amount
    data["stats"]["total_fees_collected"] = data["stats"].get("total_fees_collected", 0) + fee

    _save(data)

def get_trading_stats(chat_id: str) -> dict:
    """Get trading stats for a user."""
    user = get_user(str(chat_id))
    if not user:
        return {}
    return user.get("trading_stats", {})

# ═══════════════════════════════════════════════
# WHALE WALLET TRACKING
# ═══════════════════════════════════════════════

def add_tracked_wallet(chat_id: str, wallet_address: str) -> dict:
    """Add a whale wallet to track. Returns {"status": "ok"|"error", "message": "..."}"""
    user = get_user(str(chat_id))
    if not user:
        return {"status": "error", "message": "User not found."}

    tracked = user["whale_tracking"]["tracked_wallets"]
    limit = user["whale_tracking"]["limit"]

    if len(tracked) >= limit:
        return {"status": "error", "message": f"Reached wallet tracking limit ({limit}). Upgrade to Degen Mode for unlimited."}

    if wallet_address in tracked:
        return {"status": "error", "message": "Already tracking this wallet."}

    tracked.append(wallet_address)
    update_user(str(chat_id), {"whale_tracking": {"tracked_wallets": tracked}})
    return {"status": "ok", "message": f"Now tracking {wallet_address}"}

def remove_tracked_wallet(chat_id: str, wallet_address: str) -> dict:
    """Remove a whale wallet from tracking."""
    user = get_user(str(chat_id))
    if not user:
        return {"status": "error", "message": "User not found."}

    tracked = user["whale_tracking"]["tracked_wallets"]
    if wallet_address in tracked:
        tracked.remove(wallet_address)
        update_user(str(chat_id), {"whale_tracking": {"tracked_wallets": tracked}})
        return {"status": "ok", "message": f"Stopped tracking {wallet_address}"}

    return {"status": "error", "message": "Wallet not in tracking list."}

def get_tracked_wallets(chat_id: str) -> list:
    """Get list of tracked wallets for a user."""
    user = get_user(str(chat_id))
    if not user:
        return []
    return user.get("whale_tracking", {}).get("tracked_wallets", [])

# ═══════════════════════════════════════════════
# ACCESS CODE SYSTEM (for Degen gifting)
# ═══════════════════════════════════════════════

def generate_access_code(created_by: str = "admin", max_uses: int = 1,
                         duration_days: int = 30, note: str = "") -> str:
    """Generate a new access code for Degen Mode gifting. Returns the code string."""
    code = "PTA-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    data = _load()
    data["access_codes"][code] = {
        "code": code,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "max_uses": max_uses,
        "uses": 0,
        "used_by": [],
        "duration_days": duration_days,
        "note": note,
        "active": True,
    }
    _save(data)
    return code

def redeem_access_code(chat_id: str, code: str) -> dict:
    """Redeem a Degen Mode access code. Returns {"status": "ok"|"error", "message": "..."}"""
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

    if cid not in data["users"]:
        return {"status": "error", "message": "User not found. Send /start first."}

    # Activate Degen Mode subscription
    duration = ac.get("duration_days", 30)
    expires = (datetime.now(timezone.utc) + timedelta(days=duration)).isoformat()

    # Update access code usage
    ac["uses"] += 1
    ac["used_by"].append(cid)
    data["access_codes"][code] = ac

    # Activate Degen Mode for user
    data["users"][cid]["subscription"] = {
        "plan": "degen",
        "status": "active",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires,
        "cancel_at_period_end": False,
        "access_code": code,
    }
    data["users"][cid]["whale_tracking"]["limit"] = DEGEN_WHALE_LIMIT
    data["stats"]["total_degen_subscribers"] = data["stats"].get("total_degen_subscribers", 0) + 1
    _save(data)

    return {
        "status": "ok",
        "message": f"Degen Mode activated for {duration} days!",
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

def preload_access_codes():
    """Pre-load 100 Degen Mode access codes on boot if not already loaded."""
    CODES = [
        "PTA-FLP4DJAT","PTA-OPW4B8N6","PTA-UUSPYU89","PTA-MWIUAPI3","PTA-C5Q38DF4",
        "PTA-WWPOPNP4","PTA-Q7ZIK3OC","PTA-47QKKGH2","PTA-TK0PV9TN","PTA-J1NIOJCR",
        "PTA-SREOPR4X","PTA-H7I8ZZSG","PTA-3CA7FQBE","PTA-B0TL8MSN","PTA-F7K2PVAL",
        "PTA-AK82ZWHG","PTA-ZRLSRO8A","PTA-V45Q8UCJ","PTA-00LSKYYI","PTA-3VZU0JH4",
        "PTA-30QLKWQR","PTA-U2I4PTI0","PTA-CB8RFFBQ","PTA-SWTFS6DA","PTA-496U9YV1",
        "PTA-J0G23C4J","PTA-PIYSPOE3","PTA-AK99WZLC","PTA-M50QPVOC","PTA-8C1LX61S",
        "PTA-32H33RGC","PTA-XUAARLFV","PTA-PU38OJ0S","PTA-YDGYKB2Q","PTA-05EEQH5E",
        "PTA-CVSLR4DT","PTA-3NYZUPO7","PTA-8Q7YK35V","PTA-9HJNID94","PTA-CBQNJYOP",
        "PTA-6KURB2O6","PTA-O35W3I2N","PTA-0DI84M2P","PTA-A2XD932F","PTA-YJAEPYKH",
        "PTA-H5T21A5H","PTA-167477ND","PTA-BWEK875N","PTA-DPOEWFZZ","PTA-2FG7KMDP",
        "PTA-18WF2L22","PTA-6CKCNS9H","PTA-J679GSTE","PTA-AN8IJVHC","PTA-13FSLT9Z",
        "PTA-L94MZXQQ","PTA-BYJ3RHRV","PTA-NA6BFZ4Q","PTA-VRDPIXRG","PTA-IQ38KEA9",
        "PTA-L0CP46HE","PTA-BZMMU7L9","PTA-BRGAAY3D","PTA-FQLXMVNH","PTA-CZDM6HRP",
        "PTA-2CLHIL8V","PTA-JPFV3SGO","PTA-1C2YOMUG","PTA-ACS675V8","PTA-9MALW2FW",
        "PTA-Z7WOD739","PTA-JPBDEE5C","PTA-IKW4JRSD","PTA-MJ9DFZNV","PTA-VHMA7QF5",
        "PTA-OFVD89IE","PTA-155LHWLR","PTA-THFBX447","PTA-DHX32VS9","PTA-1DGQ2ORD",
        "PTA-N1Y31PVP","PTA-S51KCKJQ","PTA-Z4Z87OJ7","PTA-FTC567RI","PTA-JB7J5952",
        "PTA-NWBPZZ66","PTA-QT7A6OXJ","PTA-G7L6M9DM","PTA-HI6V8XXN","PTA-N5DK5HY6",
        "PTA-2P9GD9B6","PTA-YPEN0VYZ","PTA-YDSLF5GW","PTA-PKMGFNPV","PTA-1KULI59I",
        "PTA-2H746CO3","PTA-G2ZZI5MX","PTA-DILFJH9D","PTA-0UPVWCDJ","PTA-6Y8ZUQ0Q",
    ]
    data = _load()
    added = 0
    for code in CODES:
        if code not in data.get("access_codes", {}):
            data["access_codes"][code] = {
                "code": code,
                "created_by": "admin",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "max_uses": 1,
                "uses": 0,
                "used_by": [],
                "duration_days": 30,
                "note": "Degen Mode gift code v4.0",
                "active": True,
            }
            added += 1
    if added > 0:
        _save(data)
    return added

# ═══════════════════════════════════════════════
# ONBOARDING STATE
# ═══════════════════════════════════════════════

def set_onboarding_step(chat_id: str, step: str):
    update_user(str(chat_id), {"onboarding": {"step": step}})

def set_categories(chat_id: str, categories: list):
    update_user(str(chat_id), {
        "onboarding": {
            "categories": categories,
            "step": "complete",
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
    })

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

def get_all_users() -> list:
    """Get all users on the platform."""
    data = _load()
    return list(data["users"].values())

def get_platform_stats() -> dict:
    """Get comprehensive platform statistics."""
    data = _load()
    users = list(data["users"].values())
    degen_users = [u for u in users if u.get("subscription", {}).get("plan") == "degen"]
    free_users = [u for u in users if u.get("subscription", {}).get("plan") != "degen"]
    codes = list(data.get("access_codes", {}).values())
    active_codes = [c for c in codes if c.get("active")]
    used_codes = [c for c in codes if c.get("uses") > 0]

    # Calculate total trading volume and fees
    total_volume = sum(u.get("trading_stats", {}).get("total_volume", 0) for u in users)
    total_fees = sum(u.get("trading_stats", {}).get("total_fees_paid", 0) for u in users)
    total_pnl = sum(u.get("trading_stats", {}).get("total_pnl", 0) for u in users)

    # Calculate MRR (Monthly Recurring Revenue)
    mrr = len(degen_users) * DEGEN_PRICE

    return {
        "total_users": len(users),
        "free_users": len(free_users),
        "degen_subscribers": len(degen_users),
        "mrr": mrr,
        "total_volume": total_volume,
        "total_fees_collected": total_fees,
        "total_pnl": total_pnl,
        "total_signups": data["stats"].get("total_signups", 0),
        "access_codes": {
            "total": len(codes),
            "active": len(active_codes),
            "used": len(used_codes),
        },
    }

def get_stats() -> dict:
    """Get aggregated stats for dashboard display."""
    stats = get_platform_stats()
    return stats

def is_admin(chat_id: str) -> bool:
    """Check if user is admin (your chat ID from config)."""
    from config import TELEGRAM_CHAT_ID
    return str(chat_id) == str(TELEGRAM_CHAT_ID)


# ═══════════════════════════════════════════════
# TRADE SETTINGS — per-user buy/sell configuration
# ═══════════════════════════════════════════════

DEFAULT_TRADE_SETTINGS = {
    "buy": {
        "default_amount": 25,       # $ per trade
        "slippage": 2.0,            # % slippage tolerance
        "expiration": 60,           # seconds until order expires (0=GTC)
        "auto_confirm": False,      # skip confirmation step
    },
    "sell": {
        "take_profit": 0,           # % gain to auto-sell (0=off)
        "stop_loss": 0,             # % loss to auto-sell (0=off)
        "sell_amount": 100,         # % of position to sell
        "trailing_stop": 0,         # % trailing stop (0=off)
    },
    "safety": {
        "max_position": 500,        # max $ in single market
        "daily_limit": 2000,        # max $ per day
        "min_volume": 5000,         # min market volume to trade
        "min_liquidity": 1000,      # min market liquidity
    },
}

def get_trade_settings(chat_id: str) -> dict:
    """Get user's trade settings, creating defaults if needed."""
    user = get_user(str(chat_id))
    if not user:
        return dict(DEFAULT_TRADE_SETTINGS)
    settings = user.get("trade_settings")
    if not settings:
        settings = dict(DEFAULT_TRADE_SETTINGS)
        update_user(chat_id, {"trade_settings": settings})
    # Ensure all keys exist (in case new fields added)
    for section in DEFAULT_TRADE_SETTINGS:
        if section not in settings:
            settings[section] = dict(DEFAULT_TRADE_SETTINGS[section])
        else:
            for key, default_val in DEFAULT_TRADE_SETTINGS[section].items():
                if key not in settings[section]:
                    settings[section][key] = default_val
    return settings

def update_trade_setting(chat_id: str, section: str, key: str, value):
    """Update a single trade setting. section = buy|sell|safety"""
    settings = get_trade_settings(str(chat_id))
    if section not in settings:
        return False
    if key not in settings[section]:
        return False
    # Type-cast to match default type
    default_val = DEFAULT_TRADE_SETTINGS[section][key]
    if isinstance(default_val, float):
        value = float(value)
    elif isinstance(default_val, int):
        value = int(float(value))
    elif isinstance(default_val, bool):
        value = bool(value)
    settings[section][key] = value
    update_user(str(chat_id), {"trade_settings": settings})
    return True

def reset_trade_settings(chat_id: str):
    """Reset user's trade settings to defaults."""
    update_user(str(chat_id), {"trade_settings": dict(DEFAULT_TRADE_SETTINGS)})
    return DEFAULT_TRADE_SETTINGS
