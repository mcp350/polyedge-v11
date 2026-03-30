"""
WEB SERVER v12 — Polytragent Admin Console + Stripe Webhooks + API
Runs alongside the Telegram bot on port 8080.
Admin console at /admin with password protection.
"""

import os, json, threading, secrets, hashlib, pathlib
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, Response, redirect, session, make_response
import user_store
import stripe_handler
import prediction_store as pstore
import onboarding
import telegram_client as tg
import copy_trading as ct

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# ═══════════════════════════════════════════════
# ADMIN AUTH — form-based + token + basic auth
# ═══════════════════════════════════════════════

ADMIN_PASSWORD = os.environ.get("ADMIN_DASHBOARD_PASSWORD", "jofc~kqsgz-yL8tq?C#*")

def _check_admin_auth():
    # 1. Session cookie (form login)
    if session.get("admin_authenticated"):
        return True
    # 2. Query param token
    token = request.args.get("token", "")
    if token == ADMIN_PASSWORD:
        session["admin_authenticated"] = True
        return True
    # 3. HTTP Basic Auth
    auth = request.authorization
    if auth and auth.password == ADMIN_PASSWORD:
        return True
    return False

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Polytragent Admin — Login</title><style>
:root{--bg:#0d1117;--bg-card:#161b22;--border:#30363d;--text:#e6edf3;--text-muted:#8b949e;--green:#6ee7b7;--green-dim:#3fb950;--red:#f85149}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono','SF Mono','Fira Code',Consolas,monospace;font-size:13px;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}
.login-wrap{width:100%%;max-width:360px}.login-header{text-align:center;margin-bottom:28px}
.login-logo{font-size:22px;font-weight:700;color:var(--green);margin-bottom:6px}
.login-logo span{color:var(--text-muted);font-weight:400}
.login-sub{color:var(--text-muted);font-size:12px}
.card{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:28px}
.form-group{margin-bottom:16px}.form-label{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-muted);margin-bottom:6px}
input{width:100%%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:9px 12px;color:var(--text);font-family:inherit;font-size:13px;transition:border-color .15s;-webkit-appearance:none}
input:focus{outline:none;border-color:var(--green);box-shadow:0 0 0 3px rgba(110,231,183,.08)}
.btn-submit{width:100%%;background:var(--green);color:#0d1117;border:none;border-radius:6px;padding:10px;font-family:inherit;font-size:13px;font-weight:700;cursor:pointer;transition:background .15s;margin-top:8px}
.btn-submit:hover{background:var(--green-dim)}
.error{background:rgba(248,81,73,.1);border:1px solid rgba(248,81,73,.2);color:var(--red);padding:9px 12px;border-radius:6px;font-size:12px;margin-bottom:16px}
.footer{text-align:center;margin-top:20px;color:var(--text-muted);font-size:11px}
.cursor{display:inline-block;width:8px;height:13px;background:var(--green);animation:blink 1s step-end infinite;vertical-align:text-bottom;margin-left:1px}
@keyframes blink{0%%,100%%{opacity:1}50%%{opacity:0}}
</style></head><body>
<div class="login-wrap"><div class="login-header">
<div class="login-logo"><span>$ </span>polytragent<span>/admin</span></div>
<div class="login-sub">Sign in to your dashboard<span class="cursor"></span></div></div>
<div class="card">%s
<form method="POST" action="/admin/login">
<div class="form-group"><label class="form-label">Username</label>
<input type="text" name="username" autofocus autocomplete="username" placeholder="admin" required></div>
<div class="form-group"><label class="form-label">Password</label>
<input type="password" name="password" autocomplete="current-password" placeholder="••••••••" required></div>
<button type="submit" class="btn-submit">→ Sign In</button></form></div>
<div class="footer">polytragent.com · <a href="/" style="color:var(--text-muted)">back to site</a></div></div>
</body></html>"""

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return Response(_LOGIN_HTML % "", content_type="text/html")
    # POST — check credentials
    password = request.form.get("password", "")
    if password == ADMIN_PASSWORD:
        session["admin_authenticated"] = True
        return redirect("/admin")
    error_msg = '<div class="error">✗ Invalid credentials</div>'
    return Response(_LOGIN_HTML % error_msg, content_type="text/html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authenticated", None)
    return redirect("/admin/login")

def require_admin_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_admin_auth():
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return decorated

def _load_fees() -> dict:
    fp = os.path.join(os.path.dirname(__file__), "data", "fees.json")
    if not os.path.exists(fp):
        return {"total_collected": 0, "fees": []}
    try:
        with open(fp) as f:
            return json.load(f)
    except:
        return {"total_collected": 0, "fees": []}

def _load_copy_executor() -> dict:
    fp = os.path.join(os.path.dirname(__file__), "data", "copy_executor.json")
    if not os.path.exists(fp):
        return {"auto_traders": {}, "executed_trades": []}
    try:
        with open(fp) as f:
            return json.load(f)
    except:
        return {"auto_traders": {}, "executed_trades": []}

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
# OLD ADMIN API (token-based, for legacy compat)
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
# PUBLIC API ENDPOINTS
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
# ADMIN CONSOLE — /admin (password protected)
# ═══════════════════════════════════════════════

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<title>Polytragent Admin</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0a0a0f; color: #e0e0e0; min-height: 100vh; }
.header { background: linear-gradient(135deg, #0f1128, #1a1a3e); padding: 20px 28px; border-bottom: 1px solid #2a2a4a; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 22px; color: #00d4aa; letter-spacing: -0.5px; }
.header .meta { color: #666; font-size: 13px; }
.header .meta span { color: #00d4aa; cursor: pointer; }
.tabs { display: flex; gap: 0; background: #0f0f1a; border-bottom: 1px solid #1a1a2e; padding: 0 28px; }
.tab { padding: 12px 20px; cursor: pointer; color: #888; font-size: 13px; font-weight: 500; border-bottom: 2px solid transparent; transition: all 0.2s; }
.tab:hover { color: #ccc; }
.tab.active { color: #00d4aa; border-bottom-color: #00d4aa; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; padding: 20px 28px; }
.card { background: #12122a; border: 1px solid #1e1e3a; border-radius: 10px; padding: 16px; }
.card .label { color: #666; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
.card .value { font-size: 26px; font-weight: 700; color: #00d4aa; margin-top: 6px; }
.card .value.warn { color: #f59e0b; }
.card .sub { color: #555; font-size: 11px; margin-top: 4px; }
.section { padding: 14px 28px; }
.section h2 { font-size: 16px; color: #fff; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
table { width: 100%; border-collapse: collapse; background: #12122a; border-radius: 8px; overflow: hidden; }
th { background: #0f1128; color: #00d4aa; text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
td { padding: 9px 12px; border-top: 1px solid #1a1a2e; font-size: 13px; }
tr:hover { background: #1a1a3a; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; }
.badge.degen { background: #ff475720; color: #ff4757; }
.badge.free { background: #00d4aa20; color: #00d4aa; }
.badge.active { background: #3b82f620; color: #3b82f6; }
.mono { font-family: 'SF Mono', Monaco, monospace; font-size: 11px; color: #888; }
.green { color: #00d4aa; }
.red { color: #ff4757; }
.panel { display: none; }
.panel.active { display: block; }
.action-btn { background: #00d4aa20; color: #00d4aa; border: 1px solid #00d4aa40; padding: 4px 10px; border-radius: 6px; font-size: 11px; cursor: pointer; }
.action-btn:hover { background: #00d4aa30; }
.action-btn.danger { background: #ff475720; color: #ff4757; border-color: #ff475740; }
.search { background: #0f0f1a; border: 1px solid #1e1e3a; color: #e0e0e0; padding: 8px 14px; border-radius: 6px; font-size: 13px; width: 280px; }
.search::placeholder { color: #444; }
.empty { text-align: center; padding: 40px; color: #444; font-size: 14px; }
.controls { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="header">
  <h1>Polytragent Admin Console</h1>
  <div class="meta">v12.0 Free Trading Terminal | <span onclick="loadAll()">Refresh</span> | Auto-refresh 30s</div>
</div>
<div class="tabs">
  <div class="tab active" onclick="showTab('overview')">Overview</div>
  <div class="tab" onclick="showTab('users')">Users</div>
  <div class="tab" onclick="showTab('fees')">Fees & Revenue</div>
  <div class="tab" onclick="showTab('whales')">Whale Tracking</div>
  <div class="tab" onclick="showTab('actions')">Actions</div>
</div>

<!-- OVERVIEW TAB -->
<div id="tab-overview" class="panel active">
  <div class="grid" id="stats-grid"></div>
  <div class="section">
    <h2>Recent Activity</h2>
    <table id="activity-table">
      <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Details</th></tr></thead>
      <tbody id="activity-body"><tr><td colspan="4" class="empty">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<!-- USERS TAB -->
<div id="tab-users" class="panel">
  <div class="section">
    <div class="controls">
      <input class="search" id="user-search" placeholder="Search by username or chat ID..." oninput="filterUsers()">
    </div>
    <table id="users-table">
      <thead><tr><th>#</th><th>User</th><th>Chat ID</th><th>Plan</th><th>Wallet</th><th>Volume</th><th>Fees Paid</th><th>Trades</th><th>Joined</th><th>Last Active</th><th>Actions</th></tr></thead>
      <tbody id="users-body"><tr><td colspan="11" class="empty">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<!-- FEES TAB -->
<div id="tab-fees" class="panel">
  <div class="grid" id="fees-grid"></div>
  <div class="section">
    <h2>Fee Transactions</h2>
    <table id="fees-table">
      <thead><tr><th>Time</th><th>User</th><th>Side</th><th>Trade Amount</th><th>Fee Collected</th></tr></thead>
      <tbody id="fees-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<!-- WHALES TAB -->
<div id="tab-whales" class="panel">
  <div class="section">
    <h2>Whale Tracking System</h2>
    <div id="whale-stats"></div>
    <table id="whales-table">
      <thead><tr><th>#</th><th>Whale</th><th>Address</th><th>PnL</th><th>Volume</th><th>Followers</th><th>Signals</th><th>Last Checked</th></tr></thead>
      <tbody id="whales-body"><tr><td colspan="8" class="empty">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<!-- ACTIONS TAB -->
<div id="tab-actions" class="panel">
  <div class="section">
    <h2>Admin Actions</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:12px">
      <div class="card">
        <div class="label">Grant Degen Mode</div>
        <div style="margin-top:10px">
          <input class="search" id="grant-chat-id" placeholder="Enter Chat ID" style="width:100%;margin-bottom:8px">
          <button class="action-btn" onclick="grantDegen()">Grant Degen Mode</button>
        </div>
        <div id="grant-result" style="margin-top:8px;font-size:12px;color:#00d4aa"></div>
      </div>
      <div class="card">
        <div class="label">Revoke Degen Mode</div>
        <div style="margin-top:10px">
          <input class="search" id="revoke-chat-id" placeholder="Enter Chat ID" style="width:100%;margin-bottom:8px">
          <button class="action-btn danger" onclick="revokeDegen()">Revoke Access</button>
        </div>
        <div id="revoke-result" style="margin-top:8px;font-size:12px;color:#ff4757"></div>
      </div>
      <div class="card">
        <div class="label">Generate Degen Access Code</div>
        <div style="margin-top:10px">
          <input class="search" id="code-uses" placeholder="Max uses (default: 1)" style="width:100%;margin-bottom:8px">
          <input class="search" id="code-days" placeholder="Duration days (default: 30)" style="width:100%;margin-bottom:8px">
          <button class="action-btn" onclick="genCode()">Generate Code</button>
        </div>
        <div id="code-result" style="margin-top:8px;font-size:12px;color:#00d4aa;font-family:monospace"></div>
      </div>
      <div class="card">
        <div class="label">Send Broadcast Message</div>
        <div style="margin-top:10px">
          <input class="search" id="broadcast-msg" placeholder="Message (HTML supported)" style="width:100%;margin-bottom:8px">
          <button class="action-btn" onclick="sendBroadcast()">Send to All Users</button>
        </div>
        <div id="broadcast-result" style="margin-top:8px;font-size:12px;color:#00d4aa"></div>
      </div>
    </div>
  </div>
</div>

<script>
let allUsers = [];
function fmt(n) { n=parseFloat(n)||0; return n>=1e6?'$'+(n/1e6).toFixed(1)+'M':n>=1000?'$'+(n/1000).toFixed(1)+'k':'$'+n.toFixed(2); }
function ts(s) { return s ? s.slice(0,16).replace('T',' ') : '-'; }

function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  event.target.classList.add('active');
}

async function api(path, opts) {
  try {
    const r = await fetch(path, opts);
    return await r.json();
  } catch(e) { console.error('API error:', e); return {}; }
}

async function loadAll() {
  // Stats
  const stats = await api('/admin/api/stats');
  if (stats.total_users !== undefined) {
    document.getElementById('stats-grid').innerHTML = `
      <div class="card"><div class="label">Total Users</div><div class="value">${stats.total_users}</div><div class="sub">All registered</div></div>
      <div class="card"><div class="label">Active Traders</div><div class="value">${stats.active_traders}</div><div class="sub">Made at least 1 trade</div></div>
      <div class="card"><div class="label">Degen Subs</div><div class="value warn">${stats.degen_subscribers}</div><div class="sub">MRR: $${stats.mrr}</div></div>
      <div class="card"><div class="label">Total Volume</div><div class="value">${fmt(stats.total_volume)}</div></div>
      <div class="card"><div class="label">Total Fees</div><div class="value">${fmt(stats.total_fees_collected)}</div><div class="sub">1% per trade</div></div>
      <div class="card"><div class="label">24h Fees</div><div class="value green">${fmt(stats.daily_fees)}</div></div>
      <div class="card"><div class="label">7d Fees</div><div class="value green">${fmt(stats.weekly_fees)}</div></div>
      <div class="card"><div class="label">30d Fees</div><div class="value green">${fmt(stats.monthly_fees)}</div></div>
    `;
    document.getElementById('fees-grid').innerHTML = `
      <div class="card"><div class="label">Total Collected</div><div class="value">${fmt(stats.total_fees_collected)}</div></div>
      <div class="card"><div class="label">24h</div><div class="value green">${fmt(stats.daily_fees)}</div></div>
      <div class="card"><div class="label">7d</div><div class="value green">${fmt(stats.weekly_fees)}</div></div>
      <div class="card"><div class="label">30d</div><div class="value green">${fmt(stats.monthly_fees)}</div></div>
      <div class="card"><div class="label">24h Volume</div><div class="value">${fmt(stats.daily_volume)}</div></div>
    `;
  }

  // Users
  const ud = await api('/admin/api/users');
  allUsers = (ud.users || []);
  renderUsers(allUsers);

  // Fees
  const fd = await api('/admin/api/fees');
  const fees = (fd.recent_fees || []).slice(-30).reverse();
  document.getElementById('fees-body').innerHTML = fees.length ? fees.map(f => `
    <tr>
      <td class="mono">${ts(f.timestamp)}</td>
      <td>${f.chat_id || '-'}</td>
      <td><span class="badge ${f.side==='buy'?'active':'degen'}">${(f.side||'').toUpperCase()}</span></td>
      <td>${fmt(f.trade_amount||0)}</td>
      <td class="green">${fmt(f.amount||0)}</td>
    </tr>
  `).join('') : '<tr><td colspan="5" class="empty">No fee transactions yet</td></tr>';

  // Activity (from fees as proxy)
  document.getElementById('activity-body').innerHTML = fees.length ? fees.slice(0,15).map(f => `
    <tr>
      <td class="mono">${ts(f.timestamp)}</td>
      <td>${f.chat_id || '-'}</td>
      <td><span class="badge ${f.side==='buy'?'active':'degen'}">${(f.side||'BUY').toUpperCase()}</span></td>
      <td>Trade ${fmt(f.trade_amount||0)} → Fee ${fmt(f.amount||0)}</td>
    </tr>
  `).join('') : '<tr><td colspan="4" class="empty">No activity yet. Trades will appear here.</td></tr>';

  // Whales
  const wd = await api('/admin/api/whales');
  const whales = wd.wallets || [];
  document.getElementById('whale-stats').innerHTML = `
    <div class="grid" style="margin-bottom:12px">
      <div class="card"><div class="label">Tracked Wallets</div><div class="value">${wd.total||0}</div></div>
      <div class="card"><div class="label">Total Followers</div><div class="value">${wd.total_followers||0}</div></div>
      <div class="card"><div class="label">Signals Sent</div><div class="value">${wd.total_signals||0}</div></div>
      <div class="card"><div class="label">Last Scan</div><div class="value" style="font-size:14px">${ts(wd.last_scan)||'never'}</div></div>
    </div>
  `;
  document.getElementById('whales-body').innerHTML = whales.length ? whales.map((w,i) => `
    <tr>
      <td>${i+1}</td>
      <td><b>${w.alias||'Unknown'}</b><br><span style="color:#666;font-size:10px">${w.category||''}</span></td>
      <td class="mono">${(w.address||'').slice(0,10)}...</td>
      <td class="${w.pnl>=0?'green':'red'}">${w.pnl>=0?'+':''}${fmt(w.pnl||0)}</td>
      <td>${fmt(w.volume||0)}</td>
      <td>${w.followers_count||0}</td>
      <td>${w.total_signals||0}</td>
      <td class="mono">${ts(w.last_checked)}</td>
    </tr>
  `).join('') : '<tr><td colspan="8" class="empty">No whales tracked yet</td></tr>';
}

function renderUsers(users) {
  document.getElementById('users-body').innerHTML = users.length ? users.slice(0,100).map((u,i) => `
    <tr>
      <td>${i+1}</td>
      <td><b>${u.first_name||u.username||'-'}</b><br><span style="color:#666;font-size:10px">@${u.username||'-'}</span></td>
      <td class="mono">${u.chat_id}</td>
      <td><span class="badge ${u.plan==='degen'?'degen':'free'}">${u.plan==='degen'?'DEGEN':'FREE'}</span></td>
      <td class="mono">${u.wallet?(u.wallet.slice(0,8)+'...'):'-'}</td>
      <td>${fmt(u.total_volume)}</td>
      <td class="green">${fmt(u.total_fees_paid)}</td>
      <td>${(u.total_buys||0)+(u.total_sells||0)}</td>
      <td class="mono">${ts(u.created_at)}</td>
      <td class="mono">${ts(u.last_active)}</td>
      <td>
        ${u.plan!=='degen'?'<button class="action-btn" onclick="grantUser(\\''+u.chat_id+'\\')">Grant</button>':'<button class="action-btn danger" onclick="revokeUser(\\''+u.chat_id+'\\')">Revoke</button>'}
      </td>
    </tr>
  `).join('') : '<tr><td colspan="11" class="empty">No users yet</td></tr>';
}

function filterUsers() {
  const q = document.getElementById('user-search').value.toLowerCase();
  const filtered = allUsers.filter(u =>
    (u.username||'').toLowerCase().includes(q) ||
    (u.first_name||'').toLowerCase().includes(q) ||
    (u.chat_id||'').includes(q)
  );
  renderUsers(filtered);
}

async function grantDegen() {
  const cid = document.getElementById('grant-chat-id').value.trim();
  if (!cid) return;
  const r = await api('/admin/api/grant_degen', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({chat_id: cid})});
  document.getElementById('grant-result').textContent = r.status === 'ok' ? 'Degen Mode granted to ' + cid : (r.error || 'Error');
  loadAll();
}
function grantUser(cid) { document.getElementById('grant-chat-id').value=cid; grantDegen(); }

async function revokeDegen() {
  const cid = document.getElementById('revoke-chat-id').value.trim();
  if (!cid) return;
  const r = await api('/admin/api/revoke_degen', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({chat_id: cid})});
  document.getElementById('revoke-result').textContent = r.status === 'ok' ? 'Access revoked for ' + cid : (r.error || 'Error');
  loadAll();
}
function revokeUser(cid) { document.getElementById('revoke-chat-id').value=cid; revokeDegen(); }

async function genCode() {
  const uses = parseInt(document.getElementById('code-uses').value) || 1;
  const days = parseInt(document.getElementById('code-days').value) || 30;
  const r = await api('/admin/api/gen_code', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({max_uses: uses, duration_days: days})});
  document.getElementById('code-result').textContent = r.code ? 'Code: ' + r.code : (r.error || 'Error');
}

async function sendBroadcast() {
  const msg = document.getElementById('broadcast-msg').value.trim();
  if (!msg) return;
  const r = await api('/admin/api/broadcast', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})});
  document.getElementById('broadcast-result').textContent = r.status === 'ok' ? 'Sent to ' + r.sent + ' users' : (r.error || 'Error');
}

loadAll();
setInterval(loadAll, 30000);
</script>
</body>
</html>"""

@app.route("/admin")
@require_admin_auth
def admin_console():
    # Serve the full admin dashboard from templates/
    import pathlib
    tpl = pathlib.Path(__file__).parent / "templates" / "admin_dashboard.html"
    if tpl.exists():
        return Response(tpl.read_text(), content_type="text/html")
    # Fallback to embedded HTML if template missing
    return Response(ADMIN_HTML, content_type="text/html")

@app.route("/admin/api/stats")
@require_admin_auth
def admin_api_stats():
    users_data = user_store._load()
    fees_data = _load_fees()
    users = users_data.get("users", {})

    total_users = len(users)
    degen_count = 0
    active_traders = 0
    total_volume = 0
    total_fees = fees_data.get("total_collected", 0)

    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(days=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()

    for uid, u in users.items():
        sub = u.get("subscription", {})
        if sub.get("plan") == "degen" and sub.get("status") == "active":
            degen_count += 1
        stats = u.get("trading_stats", {})
        vol = stats.get("total_volume", 0)
        if vol > 0:
            active_traders += 1
            total_volume += vol

    fees_list = fees_data.get("fees", [])
    daily_fees = sum(f.get("amount", 0) for f in fees_list if f.get("timestamp", "") >= day_ago)
    weekly_fees = sum(f.get("amount", 0) for f in fees_list if f.get("timestamp", "") >= week_ago)
    monthly_fees = sum(f.get("amount", 0) for f in fees_list if f.get("timestamp", "") >= month_ago)
    daily_volume = sum(f.get("trade_amount", 0) for f in fees_list if f.get("timestamp", "") >= day_ago)

    return jsonify({
        "total_users": total_users, "active_traders": active_traders,
        "degen_subscribers": degen_count, "mrr": degen_count * 79,
        "total_volume": round(total_volume, 2), "total_fees_collected": round(total_fees, 2),
        "daily_fees": round(daily_fees, 2), "weekly_fees": round(weekly_fees, 2),
        "monthly_fees": round(monthly_fees, 2), "daily_volume": round(daily_volume, 2),
        "timestamp": now.isoformat(),
    })

@app.route("/admin/api/users")
@require_admin_auth
def admin_api_users():
    users_data = user_store._load()
    users = users_data.get("users", {})
    user_list = []
    for uid, u in users.items():
        stats = u.get("trading_stats", {})
        sub = u.get("subscription", {})
        user_list.append({
            "chat_id": uid, "username": u.get("username", ""),
            "first_name": u.get("first_name", ""), "created_at": u.get("created_at", ""),
            "last_active": u.get("last_active", ""), "plan": sub.get("plan", "free"),
            "wallet": u.get("wallet_address", ""),
            "total_buys": stats.get("total_buys", 0), "total_sells": stats.get("total_sells", 0),
            "total_volume": stats.get("total_volume", 0), "total_fees_paid": stats.get("total_fees_paid", 0),
        })
    user_list.sort(key=lambda x: x["total_volume"], reverse=True)
    return jsonify({"users": user_list, "total": len(user_list)})

@app.route("/admin/api/fees")
@require_admin_auth
def admin_api_fees():
    fees_data = _load_fees()
    limit = int(request.args.get("limit", 100))
    fees = fees_data.get("fees", [])[-limit:]
    return jsonify({"total_collected": fees_data.get("total_collected", 0), "recent_fees": fees, "count": len(fees)})

@app.route("/admin/api/trades")
@require_admin_auth
def admin_api_trades():
    ce_data = _load_copy_executor()
    limit = int(request.args.get("limit", 100))
    trades = ce_data.get("executed_trades", [])[-limit:]
    return jsonify({"trades": trades, "count": len(trades)})

@app.route("/admin/api/whales")
@require_admin_auth
def admin_api_whales():
    data = ct._load()
    wallets = list(data.get("wallets", {}).values())
    wallets.sort(key=lambda w: w.get("pnl", 0), reverse=True)
    total_followers = sum(len(f) for f in data.get("followers", {}).values())
    total_signals = len(data.get("signals", []))
    # Include real-time listener stats
    rt_stats = {"listener_status": "not_installed"}
    try:
        import whale_realtime as wrt
        rt_stats = wrt.get_realtime_stats()
    except ImportError:
        pass
    return jsonify({
        "wallets": wallets[:50],
        "total": len(wallets),
        "total_followers": total_followers,
        "total_signals": total_signals,
        "last_scan": data.get("last_scan", ""),
        "realtime": rt_stats,
    })

@app.route("/admin/api/whale-realtime")
@require_admin_auth
def admin_api_whale_realtime():
    """Real-time whale transaction log and stats."""
    try:
        import whale_realtime as wrt
        limit = int(request.args.get("limit", 50))
        stats = wrt.get_realtime_stats()
        txs = wrt.get_recent_whale_txs(limit)
        return jsonify({"stats": stats, "transactions": txs})
    except ImportError:
        return jsonify({"stats": {"listener_status": "not_installed"}, "transactions": []})

@app.route("/admin/api/user/<chat_id>")
@require_admin_auth
def admin_api_user_detail(chat_id):
    """Full detail view for a single user."""
    user = user_store.get_user(str(chat_id))
    if not user:
        return jsonify({"error": "User not found"}), 404
    # Get wallet balance
    balance = {"usdc": 0, "matic": 0}
    try:
        import wallet_manager
        addr = user.get("wallet_address", "")
        if addr:
            balance = wallet_manager.get_full_balance(addr)
    except: pass
    # Get copy trading info
    copy_info = {"auto_copy_enabled": False, "followed_wallets": 0}
    try:
        import copy_executor
        ce_settings = copy_executor.get_auto_copy_settings(str(chat_id))
        if ce_settings:
            copy_info["auto_copy_enabled"] = ce_settings.get("enabled", False)
            copy_info["total_copy_trades"] = ce_settings.get("total_trades", 0)
            copy_info["total_copy_volume"] = ce_settings.get("total_volume", 0)
        following = ct.get_following(str(chat_id))
        copy_info["followed_wallets"] = len(following)
    except: pass
    # Get recent fee transactions for this user
    fees_data = _load_fees()
    user_fees = [f for f in fees_data.get("fees", []) if str(f.get("chat_id", "")) == str(chat_id)][-20:]
    # Get copy trade executions for this user
    ce_data = _load_copy_executor()
    user_copy_trades = [t for t in ce_data.get("executed_trades", []) if str(t.get("chat_id", "")) == str(chat_id)][-20:]
    return jsonify({
        "user": {
            "chat_id": str(chat_id),
            "username": user.get("username", ""),
            "first_name": user.get("first_name", ""),
            "created_at": user.get("created_at", ""),
            "last_active": user.get("last_active", ""),
            "wallet_address": user.get("wallet_address", ""),
            "subscription": user.get("subscription", {}),
            "onboarding": user.get("onboarding", {}),
            "trading_stats": user.get("trading_stats", {}),
            "trade_settings": user.get("trade_settings", {}),
            "whale_tracking": user.get("whale_tracking", {}),
            "total_signals_received": user.get("total_signals_received", 0),
        },
        "balance": balance,
        "copy_trading": copy_info,
        "recent_fees": user_fees,
        "recent_copy_trades": user_copy_trades,
    })

@app.route("/admin/api/activity")
@require_admin_auth
def admin_api_activity():
    """Recent platform activity feed — combines fees, trades, and new users."""
    limit = int(request.args.get("limit", 50))
    activity = []
    # Recent fees
    fees_data = _load_fees()
    for f in fees_data.get("fees", [])[-limit:]:
        activity.append({
            "type": "trade", "timestamp": f.get("timestamp", ""),
            "chat_id": f.get("chat_id", ""), "side": f.get("side", ""),
            "amount": f.get("trade_amount", f.get("amount", 0)),
            "fee": f.get("fee", f.get("amount", 0)),
            "market": f.get("market", ""),
        })
    # Recent copy trade executions
    ce_data = _load_copy_executor()
    for t in ce_data.get("executed_trades", [])[-limit:]:
        activity.append({
            "type": "copy_trade", "timestamp": t.get("timestamp", ""),
            "chat_id": t.get("chat_id", ""), "action": t.get("action", ""),
            "amount": t.get("amount", 0), "market": t.get("market", ""),
            "signal_wallet": t.get("signal_wallet", "")[:10],
        })
    # New user signups (last 50)
    users_data = user_store._load()
    for uid, u in users_data.get("users", {}).items():
        activity.append({
            "type": "signup", "timestamp": u.get("created_at", ""),
            "chat_id": uid, "username": u.get("username", ""),
        })
    # Sort by timestamp descending
    activity.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return jsonify({"activity": activity[:limit]})

@app.route("/admin/api/grant_degen", methods=["POST"])
@require_admin_auth
def admin_grant_degen():
    body = request.get_json(silent=True) or {}
    chat_id = str(body.get("chat_id", "")).strip()
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400
    user = user_store.get_user(chat_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    user_store.activate_subscription(chat_id, plan="degen",
        stripe_customer_id="admin_grant", stripe_subscription_id="admin_grant")
    try:
        tg.send("🚀 <b>Degen Mode activated!</b>\nYou now have unlimited whale tracking and premium features.", chat_id)
    except: pass
    return jsonify({"status": "ok", "chat_id": chat_id})

@app.route("/admin/api/revoke_degen", methods=["POST"])
@require_admin_auth
def admin_revoke_degen():
    body = request.get_json(silent=True) or {}
    chat_id = str(body.get("chat_id", "")).strip()
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400
    user = user_store.get_user(chat_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    user_store.deactivate_subscription(chat_id)
    try:
        tg.send("ℹ️ Your Degen Mode has been deactivated. You still have free access.", chat_id)
    except: pass
    return jsonify({"status": "ok", "chat_id": chat_id})

@app.route("/admin/api/gen_code", methods=["POST"])
@require_admin_auth
def admin_gen_code():
    body = request.get_json(silent=True) or {}
    max_uses = body.get("max_uses", 1)
    duration = body.get("duration_days", 30)
    code = user_store.generate_access_code(created_by="admin_console", max_uses=max_uses, duration_days=duration)
    return jsonify({"status": "ok", "code": code})

@app.route("/admin/api/broadcast", methods=["POST"])
@require_admin_auth
def admin_broadcast():
    body = request.get_json(silent=True) or {}
    msg = body.get("message", "").strip()
    if not msg:
        return jsonify({"error": "message required"}), 400
    users = user_store.get_all_users()
    sent = 0
    for u in users:
        try:
            tg.send(msg, str(u.get("chat_id", "")))
            sent += 1
        except: pass
    return jsonify({"status": "ok", "sent": sent, "total": len(users)})

# ═══════════════════════════════════════════════
# HOME & HEALTH
# ═══════════════════════════════════════════════

@app.route("/")
def home():
    return jsonify({
        "service": "Polytragent",
        "version": "v12.0",
        "status": "running",
        "model": "FREE Trading Terminal + Degen Mode",
        "admin": "/admin",
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "polytragent", "version": "v12.2", "build": "2026-03-30-fix4"})

@app.route("/diag")
def diag():
    """Diagnostic endpoint: test if Gamma API and CLOB API are reachable"""
    import requests as _req
    import os
    results = {}
    results["env_proxy"] = {
        "HTTP_PROXY": os.environ.get("HTTP_PROXY", ""),
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", ""),
        "http_proxy": os.environ.get("http_proxy", ""),
        "https_proxy": os.environ.get("https_proxy", ""),
        "NO_PROXY": os.environ.get("NO_PROXY", ""),
    }
    # Test Gamma API (default - uses env proxy)
    try:
        r = _req.get("https://gamma-api.polymarket.com/events",
            params={"slug": "us-forces-enter-iran-by", "limit": 1}, timeout=10)
        results["gamma_default"] = {"status": r.status_code, "ok": r.ok, "len": len(r.text)}
    except Exception as e:
        results["gamma_default"] = {"error": str(e)[:200]}
    # Test Gamma API with trust_env=False (bypasses Railway proxy)
    try:
        s = _req.Session()
        s.trust_env = False
        r = s.get("https://gamma-api.polymarket.com/events",
            params={"slug": "us-forces-enter-iran-by", "limit": 1}, timeout=10)
        results["gamma_no_proxy"] = {"status": r.status_code, "ok": r.ok, "len": len(r.text)}
    except Exception as e:
        results["gamma_no_proxy"] = {"error": str(e)[:200]}
    # Test CLOB API through EU proxy
    try:
        import config
        r = _req.get(f"{config.CLOB_BASE}/time", timeout=10)
        results["clob_api"] = {"status": r.status_code, "base": config.CLOB_BASE}
    except Exception as e:
        results["clob_api"] = {"error": str(e)[:200]}
    # Test via system curl (bypasses Python proxy settings)
    try:
        import subprocess
        proc = subprocess.run(
            ['curl', '-s', '--max-time', '8', 'https://gamma-api.polymarket.com/events?slug=us-forces-enter-iran-by&limit=1'],
            capture_output=True, text=True, timeout=10
        )
        results["gamma_curl"] = {"returncode": proc.returncode, "len": len(proc.stdout), "stderr": proc.stderr[:200], "body_start": proc.stdout[:100]}
    except Exception as e:
        results["gamma_curl"] = {"error": str(e)[:200]}
    # Test httpbin to confirm general HTTPS works
    try:
        r = _req.get("https://httpbin.org/ip", timeout=8)
        results["httpbin"] = {"status": r.status_code, "body": r.text[:100]}
    except Exception as e:
        results["httpbin"] = {"error": str(e)[:200]}
    # Test via EU proxy passing gamma request as path
    try:
        r = _req.get("http://13.49.25.66/events?slug=us-forces-enter-iran-by&limit=1", timeout=8)
        results["gamma_via_clob_proxy"] = {"status": r.status_code, "body_start": r.text[:100]}
    except Exception as e:
        results["gamma_via_clob_proxy"] = {"error": str(e)[:200]}
    return jsonify(results)

# ═══════════════════════════════════════════════
# RUN SERVER
# ═══════════════════════════════════════════════

def start_server(port=8080):
    def run():
        app.run(host="0.0.0.0", port=port, debug=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print(f"[WEB] Polytragent Admin + API server started on port {port}")
    return t
