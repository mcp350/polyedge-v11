from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=True, index=True)
    telegram_username = db.Column(db.String(64), nullable=True)
    first_name = db.Column(db.String(64), nullable=True)
    last_name = db.Column(db.String(64), nullable=True)
    email = db.Column(db.String(128), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    subscription_tier = db.Column(db.String(32), default="pro", nullable=False)
    subscription_start = db.Column(db.DateTime, nullable=True)
    subscription_end = db.Column(db.DateTime, nullable=True)
    monthly_price = db.Column(db.Float, default=79.99, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    stripe_customer_id = db.Column(db.String(64), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(64), nullable=True)
    stripe_subscription_status = db.Column(db.String(32), nullable=True)  # active, canceled, past_due, etc.
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, nullable=True)

    queries = db.relationship("ResearchQuery", backref="user", lazy=True, cascade="all, delete-orphan")
    predictions = db.relationship("Prediction", backref="user", lazy=True, cascade="all, delete-orphan")

    @property
    def display_name(self):
        parts = [p for p in [self.first_name, self.last_name] if p]
        if parts:
            return " ".join(parts)
        return self.telegram_username or f"User #{self.id}"

    @property
    def subscription_status(self):
        if not self.is_active:
            return "disabled"
        now = datetime.utcnow()
        if self.subscription_end and self.subscription_end < now:
            return "expired"
        if self.subscription_end:
            return "active"
        return "active"

    @property
    def mrr_contribution(self):
        if self.subscription_status == "active":
            return self.monthly_price
        return 0.0


class ResearchQuery(db.Model):
    __tablename__ = "research_queries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    market_url = db.Column(db.String(512), nullable=True)
    market_name = db.Column(db.String(256), nullable=True)
    markets_analyzed = db.Column(db.Integer, default=1)
    recommendation = db.Column(db.String(16), nullable=True)  # YES, NO, HOLD
    edge_pct = db.Column(db.Float, nullable=True)
    category = db.Column(db.String(64), nullable=True)
    queried_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Prediction(db.Model):
    __tablename__ = "predictions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    market_id = db.Column(db.String(128), nullable=True)
    market_name = db.Column(db.String(256), nullable=True)
    direction = db.Column(db.String(8), nullable=True)  # YES, NO
    confidence = db.Column(db.Float, nullable=True)
    entry_price = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(16), default="PENDING", nullable=False)  # PENDING, WON, LOST
    resolved_at = db.Column(db.DateTime, nullable=True)
    pnl_pct = db.Column(db.Float, nullable=True)
    category = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
