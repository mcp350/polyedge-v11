# ─────────────────────────────────────────────
#  POLYTRAGENT — CONFIG (Railway / ENV-aware)
# ─────────────────────────────────────────────
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Polymarket Trading API (builders.polymarket.com) ──
POLY_API_KEY = os.environ.get("POLY_API_KEY", "")
POLY_API_SECRET = os.environ.get("POLY_API_SECRET", "")
POLY_API_PASSPHRASE = os.environ.get("POLY_API_PASSPHRASE", "")
POLY_PRIVATE_KEY = os.environ.get("POLY_PRIVATE_KEY", "")

# ── Polygon Network ──
POLYGON_RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon-rpc.com")

# ── Stripe (Degen Mode) ──
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_DEGEN_PRICE_ID = os.environ.get("STRIPE_DEGEN_PRICE_ID", "")
DEGEN_MODE_PRICE = 79  # $79/month for degen mode
BOT_DOMAIN = os.environ.get("BOT_DOMAIN", "https://polytragent.com")

# ── Trading Fees ──
TRADE_FEE_BUY = 0.01  # 1% buy fee
TRADE_FEE_SELL = 0.01  # 1% sell fee
FEE_WALLET_ADDRESS = os.environ.get("FEE_WALLET_ADDRESS", "")

# ── Whale Tracking Limits ──
FREE_WALLET_TRACKING_LIMIT = 20  # Free users can track up to 20 whale wallets
DEGEN_WALLET_TRACKING_LIMIT = 9999  # Unlimited for degen mode

# ── Module 1: Market Scanner ──
SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "15"))
MIN_VOLUME = int(os.environ.get("MIN_VOLUME", "50000"))
MIN_DAYS_LEFT = int(os.environ.get("MIN_DAYS_LEFT", "14"))
MAX_DAYS_LEFT = int(os.environ.get("MAX_DAYS_LEFT", "45"))
NO_PRICE_MIN = float(os.environ.get("NO_PRICE_MIN", "0.55"))
NO_PRICE_MAX = float(os.environ.get("NO_PRICE_MAX", "0.90"))
MAX_YES_PRICE = float(os.environ.get("MAX_YES_PRICE", "0.45"))
MIN_NO_PRICE = float(os.environ.get("MIN_NO_PRICE", "0.55"))
MAX_NO_PRICE = float(os.environ.get("MAX_NO_PRICE", "0.90"))

GEOPOLITICAL_KEYWORDS = [
    "strike", "invasion", "invade", "war", "military",
    "ceasefire", "sanctions", "regime", "troops", "missile",
    "nuclear", "attack", "bomb", "nato", "russia", "ukraine",
    "iran", "china", "taiwan", "israel", "conflict", "coup",
    "collapse", "election", "greenland", "panama", "tariff",
    "fed", "rate", "referendum", "annexe", "occupy"
]

# ── Module 2: Daily Report ──
DAILY_REPORT_HOUR = int(os.environ.get("DAILY_REPORT_HOUR", "8"))
DAILY_REPORT_MINUTE = int(os.environ.get("DAILY_REPORT_MINUTE", "0"))
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Vilnius")
EXIT_ZONE_NO_PRICE = float(os.environ.get("EXIT_ZONE_NO_PRICE", "0.93"))
EXIT_ZONE_DAYS_LEFT = int(os.environ.get("EXIT_ZONE_DAYS_LEFT", "7"))
DANGER_ZONE_NO = float(os.environ.get("DANGER_ZONE_NO", "0.65"))

# ── Module 4: Whale Alert ──
WHALE_TRADE_USD = int(os.environ.get("WHALE_TRADE_USD", "5000"))

# ── Module 6: Swing Detector ──
SWING_DROP_THRESHOLD = float(os.environ.get("SWING_DROP_THRESHOLD", "0.10"))
SWING_WINDOW_MINUTES = int(os.environ.get("SWING_WINDOW_MINUTES", "30"))
SWING_COOLDOWN_HOURS = int(os.environ.get("SWING_COOLDOWN_HOURS", "2"))

# ── Module 5: News ──
NEWS_POLL_MINUTES = int(os.environ.get("NEWS_POLL_MINUTES", "5"))

# ── Module 9: ACLED ──
ACLED_EMAIL = os.environ.get("ACLED_EMAIL", "")
ACLED_PASSWORD = os.environ.get("ACLED_PASSWORD", "")

# ── Research ──
MIN_DIVERGENCE_EDGE = int(os.environ.get("MIN_DIVERGENCE_EDGE", "8"))

# ── Feature Flags ──
ENABLE_SCANNER = os.environ.get("ENABLE_SCANNER", "true").lower() == "true"
ENABLE_MONITOR = os.environ.get("ENABLE_MONITOR", "true").lower() == "true"
ENABLE_WHALE = os.environ.get("ENABLE_WHALE", "true").lower() == "true"
ENABLE_SWING = os.environ.get("ENABLE_SWING", "true").lower() == "true"
ENABLE_NEWS = os.environ.get("ENABLE_NEWS", "true").lower() == "true"
ENABLE_REPORTER = os.environ.get("ENABLE_REPORTER", "true").lower() == "true"
ENABLE_DIGEST = os.environ.get("ENABLE_DIGEST", "true").lower() == "true"
ENABLE_LIVE_TRADING = os.environ.get("ENABLE_LIVE_TRADING", "true").lower() == "true"
ENABLE_AUTO_COPY = os.environ.get("ENABLE_AUTO_COPY", "true").lower() == "true"
