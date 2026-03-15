"""
WEB SERVER v8 — Polytragent Dashboard + Stripe webhooks + API + Copy Trading + Access Codes
Runs alongside the Telegram bot on a separate port.
Requires: pip install flask
"""

import os, json, threading
from flask import Flask, request, jsonify, send_from_directory, redirect
import user_store
import stripe_handler
import prediction_store as pstore
import onboarding
import telegram_client as tg
import copy_trading as ct

app = Flask(__name__, static_folder="dashboard")

# ═══════════════════════════════════════════════
# STRIPE WEBHOOK
# ═══════════════════════════════════════════════

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    result = stripe_handler.handle_webhook(payload, sig)

    if result.get("error"):
        return jsonify({"error": result["error"]}), 400

    action = result.get("action")
    chat_id = result.get("chat_id")
    if chat_id:
        if action == "activated":
            onboarding.notify_activated(chat_id)
        elif action == "renewed":
            tg.send("✅ <b>Subscription renewed!</b> Full Polytragent access continues.", chat_id)
        elif action == "payment_failed":
            onboarding.notify_payment_failed(chat_id)
        elif action == "cancelled":
            onboarding.notify_cancelled(chat_id)
        elif action == "cancel_scheduled":
            tg.send("ℹ️ Your Polytragent subscription will cancel at the end of the billing period. "
                     "You'll keep access until then.", chat_id)

    return jsonify({"status": "ok"}), 200

# ═══════════════════════════════════════════════
# DASHBOARD PAGE
# ═══════════════════════════════════════════════

@app.route("/dashboard")
def dashboard():
    return send_from_directory("dashboard", "index.html")

@app.route("/dashboard/admin")
def dashboard_admin():
    return send_from_directory("dashboard", "admin.html")

@app.route("/dashboard/<path:filename>")
def dashboard_static(filename):
    return send_from_directory("dashboard", filename)

# ═══════════════════════════════════════════════
# ADMIN API ENDPOINTS
# ═══════════════════════════════════════════════

def _require_admin(request):
    token = request.args.get("token", "")
    if not token:
        body = request.get_json(silent=True) or {}
        token = body.get("token", "")
    user = user_store.get_user_by_token(token) if token else None
    if not user or not user_store.is_admin(user.get("chat_id", "")):
        return None
    return user

@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    users = user_store.get_all_users()
    return jsonify(users)

@app.route("/api/admin/grant", methods=["POST"])
def api_admin_grant():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    body = request.get_json(silent=True) or {}
    chat_id = body.get("chat_id", "")
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400
    user = user_store.get_user(chat_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    user_store.activate_subscription(chat_id, stripe_customer_id="admin_grant",
                                      stripe_subscription_id="admin_grant")
    try:
        tg.send("🎉 <b>Your Polytragent subscription has been activated!</b>\n\n"
                "You now have full access to all features.\n"
                "Use /help to see everything available.", chat_id)
    except:
        pass
    return jsonify({"status": "ok", "chat_id": chat_id, "action": "granted"})

@app.route("/api/admin/revoke", methods=["POST"])
def api_admin_revoke():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    body = request.get_json(silent=True) or {}
    chat_id = body.get("chat_id", "")
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400
    user = user_store.get_user(chat_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    user_store.deactivate_subscription(chat_id)
    try:
        tg.send("ℹ️ Your Polytragent subscription has been deactivated.\n"
                "Use /subscribe to re-activate.", chat_id)
    except:
        pass
    return jsonify({"status": "ok", "chat_id": chat_id, "action": "revoked"})

@app.route("/api/admin/logs", methods=["GET"])
def api_admin_logs():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    log_path = os.path.join(os.path.dirname(__file__), "bot.log")
    lines = []
    try:
        with open(log_path) as f:
            all_lines = f.readlines()
            lines = [l.rstrip() for l in all_lines[-200:]]
    except:
        lines = ["Log file not found"]
    return jsonify({"logs": lines})

# ── ACCESS CODE API ──

@app.route("/api/admin/codes", methods=["GET"])
def api_admin_codes():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    codes = user_store.get_all_access_codes()
    return jsonify(codes)

@app.route("/api/admin/generate_code", methods=["POST"])
def api_admin_generate_code():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    body = request.get_json(silent=True) or {}
    max_uses = body.get("max_uses", 1)
    duration = body.get("duration_days", 30)
    note = body.get("note", "")
    code = user_store.generate_access_code(
        created_by="admin_dashboard", max_uses=max_uses,
        duration_days=duration, note=note)
    return jsonify({"status": "ok", "code": code, "max_uses": max_uses, "duration_days": duration})

@app.route("/api/admin/deactivate_code", methods=["POST"])
def api_admin_deactivate_code():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 403
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    if not code:
        return jsonify({"error": "code required"}), 400
    if user_store.deactivate_access_code(code):
        return jsonify({"status": "ok", "code": code})
    return jsonify({"error": "Code not found"}), 404

# ═══════════════════════════════════════════════
# API ENDPOINTS (for dashboard)
# ═══════════════════════════════════════════════

@app.route("/api/user", methods=["GET"])
def api_user():
    token = request.args.get("token", "")
    if not token:
        return jsonify({"error": "No token"}), 401
    user = user_store.get_user_by_token(token)
    if not user:
        return jsonify({"error": "Invalid token"}), 401

    chat_id = user.get("chat_id", "")
    following = ct.get_following(chat_id)

    return jsonify({
        "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "subscription": {
            "status": user.get("subscription", {}).get("status", "inactive"),
            "plan": user.get("subscription", {}).get("plan", ""),
            "started_at": user.get("subscription", {}).get("started_at", ""),
            "cancel_at_period_end": user.get("subscription", {}).get("cancel_at_period_end", False),
            "access_code": user.get("subscription", {}).get("access_code", ""),
            "expires_at": user.get("subscription", {}).get("expires_at", ""),
        },
        "categories": user.get("onboarding", {}).get("categories", []),
        "created_at": user.get("created_at", ""),
        "total_signals": user.get("total_signals_received", 0),
        "copy_trading": {
            "following_count": len(following),
            "following": [{"alias": w.get("alias", ""), "address": w["address"][:10] + "...",
                          "pnl": w.get("pnl", 0)} for w in following[:10]],
        },
    })

@app.route("/api/performance", methods=["GET"])
def api_performance():
    perf = pstore.get_performance()
    return jsonify(perf)

@app.route("/api/predictions", methods=["GET"])
def api_predictions():
    token = request.args.get("token", "")
    page = int(request.args.get("page", "0"))
    limit = min(int(request.args.get("limit", "20")), 50)

    data = pstore._load()
    predictions = data.get("predictions", [])
    resolutions = data.get("resolutions", {})

    predictions = sorted(predictions, key=lambda p: p.get("timestamp", ""), reverse=True)

    start = page * limit
    page_preds = predictions[start:start + limit]

    results = []
    for pred in page_preds:
        mid = pred.get("market_id", "")
        res = resolutions.get(mid, {})
        results.append({
            "market_id": mid,
            "question": pred.get("question", "")[:100],
            "recommendation": pred.get("recommendation", ""),
            "confidence": pred.get("confidence", 0),
            "ai_probability": pred.get("ai_probability"),
            "market_price": pred.get("market_price"),
            "manifold_price": pred.get("manifold_yes"),
            "timestamp": pred.get("timestamp", ""),
            "source": pred.get("source", ""),
            "resolved": res.get("resolved", False),
            "outcome": res.get("outcome", ""),
            "correct": res.get("correct"),
        })

    return jsonify({
        "predictions": results,
        "total": len(predictions),
        "page": page,
        "pages": (len(predictions) + limit - 1) // limit,
    })

# ═══════════════════════════════════════════════
# COPY TRADING API
# ═══════════════════════════════════════════════

@app.route("/api/copy/leaderboard", methods=["GET"])
def api_copy_leaderboard():
    leaders = ct.get_leaderboard()
    return jsonify({
        "leaderboard": leaders[:20],
        "total": len(leaders),
    })

@app.route("/api/copy/wallet/<address>", methods=["GET"])
def api_copy_wallet(address):
    wallet = ct.get_wallet(address)
    if not wallet:
        return jsonify({"error": "Wallet not found"}), 404
    token = request.args.get("token", "")
    user = user_store.get_user_by_token(token) if token else None
    is_sub = user and (user_store.is_subscribed(user["chat_id"]) or user_store.is_admin(user["chat_id"]))

    result = {
        "address": wallet["address"],
        "alias": wallet.get("alias", ""),
        "pnl": wallet.get("pnl", 0),
        "volume": wallet.get("volume", 0),
        "win_rate": wallet.get("win_rate", 0),
        "markets_traded": wallet.get("markets_traded", 0),
        "followers_count": wallet.get("followers_count", 0),
        "total_signals": wallet.get("total_signals", 0),
        "last_checked": wallet.get("last_checked", ""),
    }

    if is_sub:
        result["positions"] = wallet.get("last_positions", {})
        result["positions_count"] = len(wallet.get("last_positions", {}))
    else:
        result["positions_count"] = len(wallet.get("last_positions", {}))
        result["positions_locked"] = True

    return jsonify(result)

@app.route("/api/copy/signals", methods=["GET"])
def api_copy_signals():
    token = request.args.get("token", "")
    user = user_store.get_user_by_token(token) if token else None
    if not user or not (user_store.is_subscribed(user["chat_id"]) or user_store.is_admin(user["chat_id"])):
        return jsonify({"error": "Subscription required", "locked": True}), 403

    limit = min(int(request.args.get("limit", "20")), 50)
    data = ct._load()
    signals = data.get("signals", [])[:limit]

    return jsonify({
        "signals": signals,
        "total": len(data.get("signals", [])),
    })

@app.route("/api/copy/following", methods=["GET"])
def api_copy_following():
    token = request.args.get("token", "")
    user = user_store.get_user_by_token(token) if token else None
    if not user:
        return jsonify({"error": "Auth required"}), 401

    following = ct.get_following(user["chat_id"])
    return jsonify({
        "following": [{
            "address": w["address"],
            "alias": w.get("alias", ""),
            "pnl": w.get("pnl", 0),
            "volume": w.get("volume", 0),
            "positions_count": len(w.get("last_positions", {})),
            "total_signals": w.get("total_signals", 0),
        } for w in following],
        "count": len(following),
    })

@app.route("/api/copy/stats", methods=["GET"])
def api_copy_stats():
    stats = ct.get_copy_stats()
    return jsonify(stats)

@app.route("/api/stats", methods=["GET"])
def api_stats():
    token = request.args.get("token", "")
    user = user_store.get_user_by_token(token) if token else None
    if not user or not user_store.is_admin(user.get("chat_id", "")):
        return jsonify({"error": "Unauthorized"}), 403
    stats = user_store.get_stats()
    stats["copy_trading"] = ct.get_copy_stats()
    return jsonify(stats)

# ═══════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════

@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "polytragent", "version": "v9"})

# ═══════════════════════════════════════════════
# RUN SERVER
# ═══════════════════════════════════════════════

def start_server(port=8080):
    def run():
        app.run(host="0.0.0.0", port=port, debug=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print(f"[WEB] Polytragent Dashboard + API server started on port {port}")
    return t
