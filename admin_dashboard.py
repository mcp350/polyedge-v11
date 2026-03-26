"""
POLYTRAGENT — Admin Dashboard
Web-based dashboard for admin to monitor:
- Total users, active traders
- Trading volume (daily/weekly/monthly)
- Revenue from 1% fees
- Per-user activity
- Degen mode subscribers + MRR
- Whale wallet performance
- Real-time stats

Runs alongside the main bot on a separate port.
"""

import os, json, logging, time
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template_string, request
from functools import wraps

log = logging.getLogger("polytragent.admin")

ADMIN_PORT = int(os.environ.get("ADMIN_DASHBOARD_PORT", "8081"))
ADMIN_PASSWORD = os.environ.get("ADMIN_DASHBOARD_PASSWORD", "polytragent_admin")

app = Flask(__name__)

# ═══════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        token = request.args.get("token", "")
        if token == ADMIN_PASSWORD:
            return f(*args, **kwargs)
        if auth and auth.password == ADMIN_PASSWORD:
            return f(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return decorated


# ═══════════════════════════════════════════════
# DATA FETCHERS
# ═══════════════════════════════════════════════

def _load_users() -> dict:
    fp = os.path.join(os.path.dirname(__file__), "users.json")
    if not os.path.exists(fp):
        return {"users": {}, "stats": {}}
    try:
        with open(fp) as f:
            return json.load(f)
    except:
        return {"users": {}, "stats": {}}


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
# API ENDPOINTS
# ═══════════════════════════════════════════════

@app.route("/api/stats")
@require_auth
def api_stats():
    """Platform-wide statistics."""
    users_data = _load_users()
    fees_data = _load_fees()
    users = users_data.get("users", {})

    total_users = len(users)

    # Count degen subscribers
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

    # Fee breakdown by time period
    fees_list = fees_data.get("fees", [])
    daily_fees = sum(f.get("amount", 0) for f in fees_list if f.get("timestamp", "") >= day_ago)
    weekly_fees = sum(f.get("amount", 0) for f in fees_list if f.get("timestamp", "") >= week_ago)
    monthly_fees = sum(f.get("amount", 0) for f in fees_list if f.get("timestamp", "") >= month_ago)

    # Daily volume
    daily_volume = sum(
        f.get("trade_amount", 0) for f in fees_list if f.get("timestamp", "") >= day_ago
    )

    return jsonify({
        "total_users": total_users,
        "active_traders": active_traders,
        "degen_subscribers": degen_count,
        "mrr": degen_count * 79,
        "total_volume": round(total_volume, 2),
        "total_fees_collected": round(total_fees, 2),
        "daily_fees": round(daily_fees, 2),
        "weekly_fees": round(weekly_fees, 2),
        "monthly_fees": round(monthly_fees, 2),
        "daily_volume": round(daily_volume, 2),
        "timestamp": now.isoformat(),
    })


@app.route("/api/users")
@require_auth
def api_users():
    """List all users with stats."""
    users_data = _load_users()
    users = users_data.get("users", {})

    user_list = []
    for uid, u in users.items():
        stats = u.get("trading_stats", {})
        sub = u.get("subscription", {})
        user_list.append({
            "chat_id": uid,
            "username": u.get("username", ""),
            "first_name": u.get("first_name", ""),
            "created_at": u.get("created_at", ""),
            "last_active": u.get("last_active", ""),
            "plan": sub.get("plan", "free"),
            "wallet": u.get("wallet_address", ""),
            "total_buys": stats.get("total_buys", 0),
            "total_sells": stats.get("total_sells", 0),
            "total_volume": stats.get("total_volume", 0),
            "total_fees_paid": stats.get("total_fees_paid", 0),
        })

    # Sort by volume descending
    user_list.sort(key=lambda x: x["total_volume"], reverse=True)

    return jsonify({"users": user_list, "total": len(user_list)})


@app.route("/api/users/<chat_id>")
@require_auth
def api_user_detail(chat_id):
    """Detailed info for a specific user."""
    users_data = _load_users()
    user = users_data.get("users", {}).get(str(chat_id))
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify(user)


@app.route("/api/fees")
@require_auth
def api_fees():
    """Fee collection history."""
    fees_data = _load_fees()
    limit = int(request.args.get("limit", 100))
    fees = fees_data.get("fees", [])[-limit:]
    return jsonify({
        "total_collected": fees_data.get("total_collected", 0),
        "recent_fees": fees,
        "count": len(fees),
    })


@app.route("/api/trades")
@require_auth
def api_trades():
    """Recent auto-copy trades."""
    ce_data = _load_copy_executor()
    limit = int(request.args.get("limit", 100))
    trades = ce_data.get("executed_trades", [])[-limit:]
    return jsonify({"trades": trades, "count": len(trades)})


# ═══════════════════════════════════════════════
# WEB DASHBOARD (HTML)
# ═══════════════════════════════════════════════

DASHBOARD_HTML = """
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
</style>
</head>
<body>
<div class="header">
  <h1>🤖 Polytragent Admin Dashboard</h1>
  <p>Real-time platform metrics — <span class="refresh" onclick="loadData()">Refresh</span></p>
</div>
<div class="grid" id="stats-grid"></div>
<div class="section">
  <h2>👥 Users</h2>
  <table id="users-table">
    <thead><tr><th>#</th><th>User</th><th>Plan</th><th>Wallet</th><th>Volume</th><th>Fees Paid</th><th>Trades</th><th>Last Active</th></tr></thead>
    <tbody id="users-body"></tbody>
  </table>
</div>
<script>
const TOKEN = new URLSearchParams(window.location.search).get('token') || '';
async function api(path) {
  const r = await fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + TOKEN);
  return r.json();
}
function fmt(n) { return n >= 1000 ? '$' + (n/1000).toFixed(1) + 'k' : '$' + n.toFixed(2); }
async function loadData() {
  const stats = await api('/api/stats');
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
  const users = await api('/api/users');
  const tbody = document.getElementById('users-body');
  tbody.innerHTML = users.users.slice(0, 50).map((u, i) => `
    <tr>
      <td>${i+1}</td>
      <td><b>${u.first_name || u.username || u.chat_id}</b><br><span style="color:#666;font-size:11px">@${u.username}</span></td>
      <td><span class="badge ${u.plan === 'degen' ? 'degen' : 'free'}">${u.plan === 'degen' ? '🔥 DEGEN' : 'FREE'}</span></td>
      <td style="font-family:monospace;font-size:11px">${u.wallet ? u.wallet.slice(0,8) + '...' : '-'}</td>
      <td>${fmt(u.total_volume)}</td>
      <td>${fmt(u.total_fees_paid)}</td>
      <td>${u.total_buys + u.total_sells}</td>
      <td style="font-size:11px">${u.last_active ? u.last_active.slice(0,16) : '-'}</td>
    </tr>
  `).join('');
}
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""

@app.route("/")
@require_auth
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "polytragent-admin"})


# ═══════════════════════════════════════════════
# SERVER START
# ═══════════════════════════════════════════════

def start_admin_dashboard(port: int = None):
    """Start the admin dashboard server in a background thread."""
    import threading
    port = port or ADMIN_PORT

    def _run():
        try:
            app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
        except Exception as e:
            log.error(f"Admin dashboard error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"[ADMIN] Dashboard running on port {port}")
    return t
