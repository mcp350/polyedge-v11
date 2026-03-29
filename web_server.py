import os
import json
from datetime import datetime, timedelta
from pathlib import Path

import stripe
from flask import Flask, abort, jsonify, request, send_file

from models import db, User

BASE_DIR = Path(__file__).parent

app = Flask(__name__, static_folder=None, template_folder=str(BASE_DIR / "templates"))

# ── Database ──────────────────────────────────────────────────────────────────
database_url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR}/polytragent.db")
# Railway Postgres URLs start with postgres:// — SQLAlchemy needs postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "polytragent-admin-secret-change-me")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# ── Stripe ─────────────────────────────────────────────────────────────────────
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = "price_1TE8TFKw9xinQ3R3rhgqV1X3"

db.init_app(app)


@app.context_processor
def inject_now():
    from datetime import datetime
    return {"now": datetime.utcnow()}


# ── Admin blueprint ───────────────────────────────────────────────────────────
from admin_routes import admin_bp  # noqa: E402

app.register_blueprint(admin_bp)

# ── Create tables on startup ──────────────────────────────────────────────────
with app.app_context():
    db.create_all()


# ── Marketing site routes ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.route("/features")
@app.route("/features/")
def features_index():
    return send_file(BASE_DIR / "features" / "index.html")


@app.route("/features/<path:filename>")
def features_file(filename):
    filepath = BASE_DIR / "features" / filename
    if filepath.is_file():
        return send_file(filepath)
    html_path = BASE_DIR / "features" / (filename + ".html")
    if html_path.is_file():
        return send_file(html_path)
    abort(404)


@app.route("/blog")
@app.route("/blog/")
def blog_index():
    return send_file(BASE_DIR / "blog" / "index.html")


@app.route("/blog/<path:filename>")
def blog_file(filename):
    filepath = BASE_DIR / "blog" / filename
    if filepath.is_file():
        return send_file(filepath)
    html_path = BASE_DIR / "blog" / (filename + ".html")
    if html_path.is_file():
        return send_file(html_path)
    abort(404)


@app.route("/compare")
@app.route("/compare/")
def compare_index():
    return send_file(BASE_DIR / "compare" / "index.html")


@app.route("/compare/<path:filename>")
def compare_file(filename):
    filepath = BASE_DIR / "compare" / filename
    if filepath.is_file():
        return send_file(filepath)
    html_path = BASE_DIR / "compare" / (filename + ".html")
    if html_path.is_file():
        return send_file(html_path)
    abort(404)


@app.route("/resources")
@app.route("/resources/")
def resources_index():
    return send_file(BASE_DIR / "resources" / "index.html")


@app.route("/resources/<path:filename>")
def resources_file(filename):
    filepath = BASE_DIR / "resources" / filename
    if filepath.is_file():
        return send_file(filepath)
    html_path = BASE_DIR / "resources" / (filename + ".html")
    if html_path.is_file():
        return send_file(html_path)
    abort(404)


@app.route("/features.html")
def features_html():
    return send_file(BASE_DIR / "features.html")


@app.route("/robots.txt")
def robots():
    return send_file(BASE_DIR / "robots.txt")


@app.route("/sitemap.xml")
def sitemap():
    return send_file(BASE_DIR / "sitemap.xml")


@app.route("/<path:filename>")
def static_files(filename):
    filepath = BASE_DIR / filename
    if filepath.is_file():
        return send_file(filepath)
    html_path = BASE_DIR / (filename + ".html")
    if html_path.is_file():
        return send_file(html_path)
    abort(404)


# ── Stripe routes ─────────────────────────────────────────────────────────────

@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    if not stripe.api_key:
        return jsonify({"error": "Stripe not configured"}), 503
    base_url = request.host_url.rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{base_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/checkout/cancel",
            allow_promotion_codes=True,
        )
        return jsonify({"url": session.url})
    except stripe.StripeError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except (ValueError, stripe.SignatureVerificationError):
        return "", 400

    event_type = event["type"]

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        customer_email = session.get("customer_details", {}).get("email")
        _upsert_stripe_subscription(customer_id, subscription_id, "active", customer_email)

    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        sub = event["data"]["object"]
        _upsert_stripe_subscription(sub["customer"], sub["id"], sub["status"])

    elif event_type == "customer.subscription.deleted":
        sub = event["data"]["object"]
        _upsert_stripe_subscription(sub["customer"], sub["id"], "canceled")

    elif event_type == "invoice.payment_failed":
        sub_id = event["data"]["object"].get("subscription")
        customer_id = event["data"]["object"].get("customer")
        if sub_id:
            _upsert_stripe_subscription(customer_id, sub_id, "past_due")

    return "", 200


def _upsert_stripe_subscription(customer_id, subscription_id, status, email=None):
    """Create or update a User record based on Stripe subscription events."""
    if not customer_id:
        return
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user is None and email:
        user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            stripe_customer_id=customer_id,
            subscription_tier="degen" if status == "active" else "free",
        )
        db.session.add(user)
    else:
        user.stripe_customer_id = customer_id

    user.stripe_subscription_id = subscription_id
    user.stripe_subscription_status = status
    if status == "active":
        user.subscription_tier = "degen"
        user.is_active = True
        if not user.subscription_start:
            user.subscription_start = datetime.utcnow()
    elif status in ("canceled", "unpaid"):
        user.subscription_tier = "free"
        user.subscription_end = datetime.utcnow()
    db.session.commit()


@app.route("/checkout/success")
def checkout_success():
    return send_file(BASE_DIR / "checkout_success.html")


@app.route("/checkout/cancel")
def checkout_cancel():
    return send_file(BASE_DIR / "checkout_cancel.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def start_server(port=8080):
    """Start Flask web server in a background thread"""
    import threading
    def run():
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print(f"[WEB] Server started on port {port}")
