"""
WEB SERVER v9 — Polytragent Dashboard + Admin Console + Stripe webhooks + API
Runs alongside the Telegram bot on port 8080.
Admin console at /admin with password protection.
Requires: pip install flask
"""

import os, json, threading
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, redirect, render_template_string, Response
import user_store
import stripe_handler
import prediction_store as pstore
import onboarding
import telegram_client as tg
import copy_trading as ct

app = Flask(__name__, static_folder="dashboard")

# ═══════════════════════════════════════════════
# ADMIN CONSOLE — Password Protected
# ═══════════════════════════════════════════════

ADMIN_PASSWORD = os.environ.get("ADMIN_DASHBOARD_PASSWORD", "jofc~kqsgz-yL8tq?C#*")

def _check_admin_auth():
    """Check admin auth via Basic Auth or ?token= param."""
    token = request.args.get("token", "")
    if token == ADMIN_PASSWORD:
        return True
    auth = request.authorization
    if auth and auth.password == ADMIN_PASSWORD:
        return True
    return False

def require_admin_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_admin_auth():
            return Response(
                "Admin login required", 401,
                {"WWW-Authenticate": 'Basic realm="Polytragent Admin"'})
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
# ADMIN CONSOLE — /admin (password protected)
# ═══════════════════════════════════════════════

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Polytragent Admin</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0a; color: #e0e0e0; }
.header { background: linear-gradient(135deg, #1a1a2e, #16213e); padding: 24px 32px; border-bottom: 1px solid #333; }
.header h1 { font-size: 24px; color: #00d4aa; }
.header p { color: #888; font-size: 14px; margin-top: 4px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; padding: 24px 32px; }
.card { background: #1a1a2e; border: 1px solid #333; border-radius: 12px; padding: 20px; }
.card .label { color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
.card .value { font-size: 28px; font-weight: 700; color: #00d4aa; margin-top: 8px; }
.card .value.red { color: #ff4757; }
.card .sub { color: #666; font-size: 12px; margin-top: 4px; }
.section { padding: 16px 32px; }
.section h2 { font-size: 18px; color: #fff; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; background: #1a1a2e; border-radius: 8px; overflow: hidden; }
th { background: #16213e; color: #00d4aa; text-align: left; padding: 12px; font-size: 12px; text-transform: uppercase; }
td { padding: 10px 12px; border-top: 1px solid #222; font-size: 13px; }
tr:hover { background: #1e1e3a; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.badge.degen { background: #ff47571a; color: #ff4757; }
.badge.free { background: #00d4aa1a; color: #00d4aa; }
.refresh { color: #00d4aa; cursor: pointer; font-size: 13px; }
.fee-section { padding: 16px 32px; }
</style>
</head>
<body>
<div class="header">
  <h1>Polytragent Admin Console</h1>
  <p>v12.0 — Free Trading Terminal | <span class="refresh" onclick="loadData()">Refresh</span> | Auto-refresh: 30s</p>
</div>
<div class="grid" id="stats-grid"></div>
<div class="section">
  <h2>Users</h2>
  <table id="users-table">
    <thead><tr><th>#</th><th>User</th><th>Plan</th><th>Wallet</th><th>Volume</th><th>Fees Paid</th><th>Trades</th><th>Last Active</th></tr></thead>
    <tbody id="users-body"></tbody>
  </table>
</div>
<div class="fee-section">
  <h2>Recent Fees</h2>
  <table id="fees-table">
    <thead><tr><th>Time</th><th>User</th><th>Side</th><th>Trade Amount</th><th>Fee</th></tr></thead>
    <tbody id="fees-body"></tbody>
  </table>
</div>
<script>
const AUTH = btoa(':' + (new URLSearchParams(window.location.search).get('token') || ''));
async function api(path) {
  const headers = {};
  const token = new URLSearchParams(window.location.search).get('token');
  if (token) path += (path.includes('?') ? '&' : '?') + 'token=' + token;
  const r = await fetch(path);
  return r.json();
}
function fmt(n) { return n >= 1e6 ? '$'+(n/1e6).toFixed(1)+'M' : n >= 1000 ? '$'+(n/1000).toFixed(1)+'k' : '$'+n.toFixed(2); }
async function loadData() {
  try {
    const stats = await api('/admin/api/stats');
    document.getElementById('stats-grid').innerHTML = `
      <div class="card"><div class="label">Total Users</div><div class="value">${stats.total_users}</div></div>
      <div class="card"><div class="label">Active Traders</div><div class="value">${stats.active_traders}</div></div>
      <div class="card"><div class="label">Degen Subscribers</div><div class="value">${stats.degen_subscribers}</div><div class="sub">MRR: $${stats.mrr}</div></div>
      <div class="card"><div class="label">Total Volume</div><div class="value">${fmt(stats.total_volume)}</div></div>
      <div class="card"><div class="label">Total Fees</div><div class="value">${fmt(stats.total_fees_collected)}</div></div>
      <div class="card"><div class="label">24h Fees</div><div class="value">${fmt(stats.daily_fees)}</div></div>
      <div class="card"><div class="label">7d Fees</div><div class="value">${fmt(stats.weekly_fees)}</div></div>
      <div class="card"><div class="label">30d Fees</div><div class="value">${fmt(stats.monthly_fees)}</div></div>
    `;
    const users = await api('/admin/api/users');
    document.getElementById('users-body').innerHTML = users.users.slice(0, 50).map((u, i) => `
      <tr>
        <td>${i+1}</td>
        <td><b>${u.first_name || u.username || u.chat_id}</b><br><span style="color:#666;font-size:11px">@${u.username}</span></td>
        <td><span class="badge ${u.plan === 'degen' ? 'degen' : 'free'}">${u.plan === 'degen' ? 'DEGEN' : 'FREE'}</span></td>
        <td style="font-family:monospace;font-size:11px">${u.wallet ? u.wallet.slice(0,8) + '...' : '-'}</td>
        <td>${fmt(u.total_volume)}</td>
        <td>${fmt(u.total_fees_paid)}</td>
        <td>${u.total_buys + u.total_sells}</td>
        <td style="font-size:11px">${u.last_active ? u.last_active.slice(0,16).replace('T',' ') : '-'}</td>
      </tr>
    `).join('');
    const fees = await api('/admin/api/fees');
    document.getElementById('fees-body').innerHTML = (fees.recent_fees || []).slice(-20).reverse().map(f => `
      <tr>
        <td style="font-size:11px">${(f.timestamp || '').slice(0,19).replace('T',' ')}</td>
        <td>${f.chat_id || '-'}</td>
        <td>${(f.side || '').toUpperCase()}</td>
        <td>${fmt(f.trade_amount || 0)}</td>
        <td style="color:#00d4aa">${fmt(f.amount || 0)}</td>
      </tr>
    `).join('');
  } catch(e) { console.error('Load error:', e); }
}
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""

@app.route("/admin")
@require_admin_auth
def admin_console():
    return render_template_string(ADMIN_HTML)

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

# ═══════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════

@app.route("/")
def home():
    return jsonify({
        "service": "Polytragent",
        "version": "v12.0",
        "status": "running",
        "model": "FREE Trading Terminal + Degen Mode ($79/mo)",
        "admin": "/admin",
        "health": "/health",
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "polytragent", "version": "v12"})

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
