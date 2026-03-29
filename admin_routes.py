import os
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func

from models import Prediction, ResearchQuery, User, db

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "changeme")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin.dashboard"))
    error = None
    if request.method == "POST":
        if request.form.get("username") == ADMIN_USER and request.form.get("password") == ADMIN_PASS:
            session["admin_logged_in"] = True
            session.permanent = True
            return redirect(url_for("admin.dashboard"))
        error = "Invalid credentials"
    return render_template("admin/login.html", error=error)


@admin_bp.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin.login"))


@admin_bp.route("/")
@admin_bp.route("/dashboard")
@login_required
def dashboard():
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    mrr = db.session.query(func.sum(User.monthly_price)).filter(User.is_active == True).scalar() or 0.0
    total_queries = ResearchQuery.query.count()
    total_predictions = Prediction.query.count()

    won = Prediction.query.filter_by(status="WON").count()
    win_rate = round((won / total_predictions * 100), 1) if total_predictions > 0 else 0.0

    recent_users = (
        User.query.order_by(User.created_at.desc()).limit(8).all()
    )
    recent_queries = (
        db.session.query(ResearchQuery, User)
        .join(User, ResearchQuery.user_id == User.id)
        .order_by(ResearchQuery.queried_at.desc())
        .limit(8)
        .all()
    )

    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        active_users=active_users,
        mrr=mrr,
        total_queries=total_queries,
        total_predictions=total_predictions,
        win_rate=win_rate,
        recent_users=recent_users,
        recent_queries=recent_queries,
        active_page="dashboard",
    )


@admin_bp.route("/users")
@login_required
def users():
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")
    query = User.query

    if search:
        like = f"%{search}%"
        query = query.filter(
            User.telegram_username.ilike(like)
            | User.first_name.ilike(like)
            | User.last_name.ilike(like)
            | User.email.ilike(like)
        )
    if status_filter == "active":
        query = query.filter(User.is_active == True)
    elif status_filter == "inactive":
        query = query.filter(User.is_active == False)

    users_list = query.order_by(User.created_at.desc()).all()

    return render_template(
        "admin/users.html",
        users=users_list,
        search=search,
        status_filter=status_filter,
        active_page="users",
    )


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
def user_new():
    if request.method == "POST":
        u = User(
            telegram_id=_int_or_none(request.form.get("telegram_id")),
            telegram_username=_str_or_none(request.form.get("telegram_username")),
            first_name=_str_or_none(request.form.get("first_name")),
            last_name=_str_or_none(request.form.get("last_name")),
            email=_str_or_none(request.form.get("email")),
            is_active=request.form.get("is_active") == "1",
            subscription_tier=request.form.get("subscription_tier", "pro"),
            subscription_start=_date_or_none(request.form.get("subscription_start")),
            subscription_end=_date_or_none(request.form.get("subscription_end")),
            monthly_price=float(request.form.get("monthly_price") or 79.99),
            notes=_str_or_none(request.form.get("notes")),
        )
        db.session.add(u)
        db.session.commit()
        flash("User created successfully.", "success")
        return redirect(url_for("admin.user_detail", user_id=u.id))

    return render_template("admin/user_form.html", user=None, action="new", active_page="users")


@admin_bp.route("/users/<int:user_id>")
@login_required
def user_detail(user_id):
    u = User.query.get_or_404(user_id)
    queries = ResearchQuery.query.filter_by(user_id=user_id).order_by(ResearchQuery.queried_at.desc()).limit(50).all()
    predictions = Prediction.query.filter_by(user_id=user_id).order_by(Prediction.created_at.desc()).limit(50).all()

    won = sum(1 for p in u.predictions if p.status == "WON")
    total_pred = len(u.predictions)
    win_rate = round((won / total_pred * 100), 1) if total_pred > 0 else 0.0

    categories = {}
    for q in u.queries:
        cat = q.category or "Other"
        categories[cat] = categories.get(cat, 0) + 1

    return render_template(
        "admin/user_detail.html",
        user=u,
        queries=queries,
        predictions=predictions,
        win_rate=win_rate,
        categories=categories,
        active_page="users",
    )


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def user_edit(user_id):
    u = User.query.get_or_404(user_id)
    if request.method == "POST":
        u.telegram_id = _int_or_none(request.form.get("telegram_id"))
        u.telegram_username = _str_or_none(request.form.get("telegram_username"))
        u.first_name = _str_or_none(request.form.get("first_name"))
        u.last_name = _str_or_none(request.form.get("last_name"))
        u.email = _str_or_none(request.form.get("email"))
        u.is_active = request.form.get("is_active") == "1"
        u.subscription_tier = request.form.get("subscription_tier", "pro")
        u.subscription_start = _date_or_none(request.form.get("subscription_start"))
        u.subscription_end = _date_or_none(request.form.get("subscription_end"))
        u.monthly_price = float(request.form.get("monthly_price") or 79.99)
        u.notes = _str_or_none(request.form.get("notes"))
        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("admin.user_detail", user_id=u.id))

    return render_template("admin/user_form.html", user=u, action="edit", active_page="users")


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
def user_toggle(user_id):
    u = User.query.get_or_404(user_id)
    u.is_active = not u.is_active
    db.session.commit()
    status = "enabled" if u.is_active else "disabled"
    flash(f"User {u.display_name} {status}.", "success")
    return redirect(request.referrer or url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
def user_delete(user_id):
    u = User.query.get_or_404(user_id)
    name = u.display_name
    db.session.delete(u)
    db.session.commit()
    flash(f"User {name} deleted.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/add-query", methods=["POST"])
@login_required
def add_query(user_id):
    User.query.get_or_404(user_id)
    q = ResearchQuery(
        user_id=user_id,
        market_url=_str_or_none(request.form.get("market_url")),
        market_name=_str_or_none(request.form.get("market_name")),
        markets_analyzed=int(request.form.get("markets_analyzed") or 1),
        recommendation=_str_or_none(request.form.get("recommendation")),
        edge_pct=_float_or_none(request.form.get("edge_pct")),
        category=_str_or_none(request.form.get("category")),
        queried_at=_date_or_none(request.form.get("queried_at")) or datetime.utcnow(),
    )
    db.session.add(q)
    db.session.commit()
    flash("Query logged.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/add-prediction", methods=["POST"])
@login_required
def add_prediction(user_id):
    User.query.get_or_404(user_id)
    p = Prediction(
        user_id=user_id,
        market_name=_str_or_none(request.form.get("market_name")),
        direction=_str_or_none(request.form.get("direction")),
        confidence=_float_or_none(request.form.get("confidence")),
        entry_price=_float_or_none(request.form.get("entry_price")),
        status=request.form.get("status", "PENDING"),
        category=_str_or_none(request.form.get("category")),
        pnl_pct=_float_or_none(request.form.get("pnl_pct")),
        resolved_at=_date_or_none(request.form.get("resolved_at")),
    )
    db.session.add(p)
    db.session.commit()
    flash("Prediction logged.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/analytics")
@login_required
def analytics():
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    mrr = db.session.query(func.sum(User.monthly_price)).filter(User.is_active == True).scalar() or 0.0
    arr = mrr * 12

    total_queries = ResearchQuery.query.count()
    total_predictions = Prediction.query.count()
    won = Prediction.query.filter_by(status="WON").count()
    lost = Prediction.query.filter_by(status="LOST").count()
    pending = Prediction.query.filter_by(status="PENDING").count()
    win_rate = round((won / (won + lost) * 100), 1) if (won + lost) > 0 else 0.0

    avg_queries = round(total_queries / total_users, 1) if total_users > 0 else 0.0

    cat_rows = (
        db.session.query(ResearchQuery.category, func.count(ResearchQuery.id))
        .group_by(ResearchQuery.category)
        .order_by(func.count(ResearchQuery.id).desc())
        .all()
    )
    categories = [(cat or "Other", cnt) for cat, cnt in cat_rows]
    max_cat = categories[0][1] if categories else 1

    rec_rows = (
        db.session.query(ResearchQuery.recommendation, func.count(ResearchQuery.id))
        .group_by(ResearchQuery.recommendation)
        .order_by(func.count(ResearchQuery.id).desc())
        .all()
    )
    recommendations = [(rec or "N/A", cnt) for rec, cnt in rec_rows]

    tier_rows = (
        db.session.query(User.subscription_tier, func.count(User.id))
        .group_by(User.subscription_tier)
        .all()
    )
    tiers = dict(tier_rows)

    return render_template(
        "admin/analytics.html",
        total_users=total_users,
        active_users=active_users,
        mrr=mrr,
        arr=arr,
        total_queries=total_queries,
        total_predictions=total_predictions,
        won=won,
        lost=lost,
        pending=pending,
        win_rate=win_rate,
        avg_queries=avg_queries,
        categories=categories,
        max_cat=max_cat,
        recommendations=recommendations,
        tiers=tiers,
        active_page="analytics",
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _str_or_none(v):
    v = (v or "").strip()
    return v if v else None


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_or_none(v):
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None
