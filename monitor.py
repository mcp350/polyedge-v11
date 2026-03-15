"""
MODULE 2 — Position Monitor & Daily Report
Fetches current prices for all tracked positions.
Sends daily 08:00 report with P&L, flags, and actions.
"""

from datetime import datetime, timezone
import polymarket_api as api
import portfolio_store as store
import telegram_client as tg
from config import EXIT_ZONE_NO_PRICE, EXIT_ZONE_DAYS_LEFT, DANGER_ZONE_NO

def _days_left(end_date_str: str) -> int:
    try:
        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now    = datetime.now(timezone.utc)
        return max(0, (end_dt - now).days)
    except Exception:
        return 999

def _get_current_no_price(market_id: str) -> float:
    """Fetch current NO price for a market."""
    m = api.get_market_by_id(market_id)
    if m:
        parsed = api.parse_market(m)
        if parsed:
            return parsed["no_price"]
    return 0.0

def _position_status(no_price: float, days_left: int) -> tuple:
    """Returns (emoji, label) for a position."""
    if no_price >= EXIT_ZONE_NO_PRICE:
        return "🟢", "EXIT ZONE — take profit"
    if days_left <= EXIT_ZONE_DAYS_LEFT:
        return "🟡", "EXIT ZONE — time exit"
    if no_price <= DANGER_ZONE_NO:
        return "🔴", "DANGER — adverse move"
    return "✅", "ON TRACK"

def build_report() -> str:
    """Build the daily portfolio report string."""
    positions = store.get_positions()

    if not positions:
        return (
            "📊 <b>DAILY PORTFOLIO REPORT</b>\n\n"
            "No active positions tracked.\n\n"
            "Use /add [market_url] [entry_price] [size_usd] to add one."
        )

    now_str  = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines    = [f"📊 <b>DAILY PORTFOLIO REPORT — {now_str}</b>\n"]
    total_pnl_pct  = 0.0
    total_pnl_usd  = 0.0
    actions_needed = []

    for mid, pos in positions.items():
        entry  = pos.get("entry_price", 0.85)
        size   = pos.get("size_usd", 20)
        q      = pos.get("question", "Unknown market")[:60]
        url    = pos.get("url", "")
        end    = pos.get("end_date", "")
        days   = _days_left(end)

        current = _get_current_no_price(mid)
        if current == 0.0:
            current = entry  # Fallback if API fails

        pnl_pct = ((current - entry) / entry) * 100
        pnl_usd = (current - entry) * size
        # Daily theta estimate (linear decay to $1.00 over remaining days)
        daily_theta = (1.00 - current) / max(days, 1) if days > 0 else 0

        emoji, status = _position_status(current, days)

        total_pnl_pct += pnl_pct
        total_pnl_usd += pnl_usd

        sign = "+" if pnl_pct >= 0 else ""
        lines.append(
            f"{emoji} <b>{q}</b>\n"
            f"   Entry ${entry:.2f} → Now ${current:.2f} | "
            f"{sign}{pnl_pct:.1f}% (${sign}{pnl_usd:.2f})\n"
            f"   Theta ≈ +${daily_theta:.3f}/day | {days}d left\n"
            f"   Status: {status}"
        )

        if "EXIT" in status or "DANGER" in status:
            actions_needed.append(f"• {status}: {q[:40]}...")

    lines.append(f"\n─────────────────────────")
    sign = "+" if total_pnl_usd >= 0 else ""
    lines.append(f"💰 Total P&L: {sign}${total_pnl_usd:.2f}")

    if actions_needed:
        lines.append(f"\n⚡ <b>ACTION NEEDED:</b>")
        lines.extend(actions_needed)

    lines.append(f"\n/portfolio — full list  |  /scan — check now")
    return "\n".join(lines)

def send_daily_report():
    """Called by scheduler at 08:00."""
    print(f"[MONITOR] Sending daily report")
    report = build_report()
    tg.send(report)

def check_positions_now() -> str:
    """Called by /portfolio command."""
    return build_report()
