"""
PREDICTION TRACKER — Learn from past predictions
Logs every AI forecast, tracks resolutions, calculates performance.
Storage: predictions.json alongside portfolio.json
"""

import json
import os
import re
import requests
from datetime import datetime, timezone
from collections import defaultdict

FILE = os.path.join(os.path.dirname(__file__), "predictions.json")

HEADERS = {"User-Agent": "PolymarketBot/2.0"}
GAMMA_BASE = "https://gamma-api.polymarket.com"


# ═══════════════════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════════════════

def _load():
    if not os.path.exists(FILE):
        return {
            "predictions": [],       # All logged predictions
            "resolutions": {},       # market_id -> {resolved, outcome, resolved_at}
            "stats_cache": {},       # Cached performance stats
            "last_resolution_check": "",  # ISO timestamp
        }
    try:
        with open(FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return {"predictions": [], "resolutions": {}, "stats_cache": {}, "last_resolution_check": ""}


def _save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
# LOGGING PREDICTIONS
# ═══════════════════════════════════════════════════════════════════════

def log_prediction(
    market_id: str,
    question: str,
    source: str,            # "top10", "researcher", "digest"
    poly_yes: float,        # Polymarket YES price at time of prediction
    poly_no: float,         # Polymarket NO price
    ai_forecast_yes: float, # AI's predicted YES probability (0-1)
    ai_recommendation: str, # "BUY_NO", "BUY_YES", "HOLD"
    manifold_yes: float = None,  # Manifold YES probability if available
    event_title: str = "",
    end_date: str = "",
    volume: float = 0,
    days_left: int = None,
    confidence: str = "",    # "high", "medium", "low"
    reasoning: str = "",     # Brief reason
):
    """Log a single AI prediction for future accuracy tracking."""
    d = _load()

    prediction = {
        "id": f"{market_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "market_id": market_id,
        "question": question[:200],
        "event_title": event_title[:150],
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),

        # Prices at prediction time
        "poly_yes": round(poly_yes, 4),
        "poly_no": round(poly_no, 4),
        "manifold_yes": round(manifold_yes, 4) if manifold_yes is not None else None,

        # AI forecast
        "ai_forecast_yes": round(ai_forecast_yes, 4),
        "ai_recommendation": ai_recommendation,
        "confidence": confidence,
        "reasoning": reasoning[:300],

        # Market context
        "end_date": end_date,
        "volume": volume,
        "days_left": days_left,

        # Resolution (filled later)
        "resolved": False,
        "outcome": None,        # "YES" or "NO"
        "final_yes_price": None,
        "pnl_if_followed": None,  # Hypothetical P&L
    }

    d["predictions"].append(prediction)

    # Keep last 500 predictions
    if len(d["predictions"]) > 500:
        d["predictions"] = d["predictions"][-500:]

    _save(d)
    print(f"[PREDICT] Logged: {question[:50]} | AI: {ai_forecast_yes:.0%} YES | Rec: {ai_recommendation}")
    return prediction["id"]


def log_batch_predictions(predictions_list: list):
    """Log multiple predictions at once (from top10)."""
    d = _load()
    for p in predictions_list:
        d["predictions"].append(p)
    if len(d["predictions"]) > 500:
        d["predictions"] = d["predictions"][-500:]
    _save(d)
    print(f"[PREDICT] Batch logged {len(predictions_list)} predictions")


# ═══════════════════════════════════════════════════════════════════════
# RESOLUTION TRACKING
# ═══════════════════════════════════════════════════════════════════════

def check_resolutions():
    """
    Check Polymarket for resolved markets and update predictions.
    Returns count of newly resolved predictions.
    """
    d = _load()
    newly_resolved = 0

    # Get unique market IDs that need resolution
    unresolved = [p for p in d["predictions"] if not p.get("resolved")]
    if not unresolved:
        return 0

    # Group by market_id to avoid duplicate API calls
    market_ids = list(set(p["market_id"] for p in unresolved))
    print(f"[PREDICT] Checking {len(market_ids)} unresolved markets...")

    for mid in market_ids:
        if mid in d["resolutions"]:
            # Already resolved, just update predictions
            res = d["resolutions"][mid]
            for p in unresolved:
                if p["market_id"] == mid:
                    _apply_resolution(p, res)
                    newly_resolved += 1
            continue

        # Check Polymarket API
        try:
            r = requests.get(f"{GAMMA_BASE}/markets/{mid}",
                             headers=HEADERS, timeout=10)
            if not r.ok:
                # Try by conditionId
                r = requests.get(f"{GAMMA_BASE}/markets",
                                 params={"id": mid}, headers=HEADERS, timeout=10)
                if r.ok:
                    markets = r.json()
                    if isinstance(markets, list) and markets:
                        market = markets[0]
                    else:
                        continue
                else:
                    continue
            else:
                market = r.json()

            # Check if resolved
            closed = market.get("closed") in [True, "true"]
            resolved = market.get("resolved") in [True, "true"]

            if closed or resolved:
                # Determine outcome
                outcome_prices = market.get("outcomePrices", "[]")
                if isinstance(outcome_prices, str):
                    try:
                        prices = json.loads(outcome_prices)
                    except:
                        prices = []
                else:
                    prices = outcome_prices or []

                final_yes = float(prices[0]) if len(prices) > 0 else None

                if final_yes is not None:
                    if final_yes >= 0.95:
                        outcome = "YES"
                    elif final_yes <= 0.05:
                        outcome = "NO"
                    else:
                        outcome = "AMBIGUOUS"
                else:
                    outcome = "UNKNOWN"

                res = {
                    "outcome": outcome,
                    "final_yes_price": final_yes,
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                }
                d["resolutions"][mid] = res

                # Update all predictions for this market
                for p in d["predictions"]:
                    if p["market_id"] == mid and not p.get("resolved"):
                        _apply_resolution(p, res)
                        newly_resolved += 1

        except Exception as e:
            print(f"[PREDICT] Resolution check error {mid[:20]}: {e}")
            continue

    d["last_resolution_check"] = datetime.now(timezone.utc).isoformat()
    _save(d)
    print(f"[PREDICT] Resolved {newly_resolved} predictions")
    return newly_resolved


def _apply_resolution(prediction, resolution):
    """Apply resolution data to a prediction and calculate P&L."""
    prediction["resolved"] = True
    prediction["outcome"] = resolution["outcome"]
    prediction["final_yes_price"] = resolution.get("final_yes_price")

    outcome = resolution["outcome"]
    rec = prediction.get("ai_recommendation", "")

    # Calculate hypothetical P&L (assuming $100 bet)
    if outcome in ("YES", "NO"):
        if rec == "BUY_NO":
            entry_no = prediction["poly_no"]
            if outcome == "NO":
                # Won: paid entry_no, received $1
                prediction["pnl_if_followed"] = round((1.0 / entry_no - 1) * 100, 1)  # % ROI
                prediction["outcome_correct"] = True
            else:
                # Lost: paid entry_no, received $0
                prediction["pnl_if_followed"] = -100.0
                prediction["outcome_correct"] = False
        elif rec == "BUY_YES":
            entry_yes = prediction["poly_yes"]
            if outcome == "YES":
                prediction["pnl_if_followed"] = round((1.0 / entry_yes - 1) * 100, 1)
                prediction["outcome_correct"] = True
            else:
                prediction["pnl_if_followed"] = -100.0
                prediction["outcome_correct"] = False
        elif rec == "HOLD":
            prediction["pnl_if_followed"] = 0
            prediction["outcome_correct"] = None  # N/A for HOLD


# ═══════════════════════════════════════════════════════════════════════
# PERFORMANCE METRICS
# ═══════════════════════════════════════════════════════════════════════

def get_performance() -> dict:
    """Calculate comprehensive performance metrics."""
    d = _load()
    preds = d["predictions"]

    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") in ("YES", "NO")]
    unresolved = [p for p in preds if not p.get("resolved")]
    all_recs = [p for p in resolved if p.get("ai_recommendation") in ("BUY_NO", "BUY_YES")]

    stats = {
        "total_predictions": len(preds),
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "total_trades": len(all_recs),
    }

    if not all_recs:
        stats["win_rate"] = None
        stats["avg_roi"] = None
        stats["total_roi"] = None
        stats["best_trade"] = None
        stats["worst_trade"] = None
        stats["calibration"] = {}
        stats["by_source"] = {}
        stats["by_recommendation"] = {}
        return stats

    # Win rate
    wins = [p for p in all_recs if p.get("outcome_correct") is True]
    losses = [p for p in all_recs if p.get("outcome_correct") is False]
    stats["win_rate"] = round(len(wins) / len(all_recs) * 100, 1) if all_recs else 0
    stats["wins"] = len(wins)
    stats["losses"] = len(losses)

    # ROI
    rois = [p["pnl_if_followed"] for p in all_recs if p.get("pnl_if_followed") is not None]
    if rois:
        stats["avg_roi"] = round(sum(rois) / len(rois), 1)
        stats["total_roi"] = round(sum(rois), 1)
        stats["best_trade"] = max(rois)
        stats["worst_trade"] = min(rois)

    # Win rate by recommendation type
    stats["by_recommendation"] = {}
    for rec_type in ("BUY_NO", "BUY_YES"):
        rec_preds = [p for p in all_recs if p["ai_recommendation"] == rec_type]
        if rec_preds:
            rec_wins = sum(1 for p in rec_preds if p.get("outcome_correct") is True)
            rec_rois = [p["pnl_if_followed"] for p in rec_preds if p.get("pnl_if_followed") is not None]
            stats["by_recommendation"][rec_type] = {
                "count": len(rec_preds),
                "win_rate": round(rec_wins / len(rec_preds) * 100, 1),
                "avg_roi": round(sum(rec_rois) / len(rec_rois), 1) if rec_rois else 0,
            }

    # Win rate by source
    stats["by_source"] = {}
    for source in set(p.get("source", "unknown") for p in all_recs):
        src_preds = [p for p in all_recs if p.get("source") == source]
        if src_preds:
            src_wins = sum(1 for p in src_preds if p.get("outcome_correct") is True)
            src_rois = [p["pnl_if_followed"] for p in src_preds if p.get("pnl_if_followed") is not None]
            stats["by_source"][source] = {
                "count": len(src_preds),
                "win_rate": round(src_wins / len(src_preds) * 100, 1),
                "avg_roi": round(sum(src_rois) / len(src_rois), 1) if src_rois else 0,
            }

    # Calibration: group predictions by AI forecast bucket and check actual outcomes
    stats["calibration"] = _calculate_calibration(resolved)

    # Recent streak
    recent = sorted(all_recs, key=lambda p: p.get("timestamp", ""), reverse=True)[:10]
    streak = 0
    for p in recent:
        if p.get("outcome_correct") is True:
            streak += 1
        else:
            break
    stats["recent_streak"] = streak

    return stats


def _calculate_calibration(resolved_preds) -> dict:
    """
    Calibration: When AI says 70% YES, does YES happen ~70% of the time?
    Groups into buckets: 0-20%, 20-40%, 40-60%, 60-80%, 80-100%
    """
    buckets = {
        "0-20%": {"predicted": [], "actual_yes": 0, "total": 0},
        "20-40%": {"predicted": [], "actual_yes": 0, "total": 0},
        "40-60%": {"predicted": [], "actual_yes": 0, "total": 0},
        "60-80%": {"predicted": [], "actual_yes": 0, "total": 0},
        "80-100%": {"predicted": [], "actual_yes": 0, "total": 0},
    }

    for p in resolved_preds:
        ai_yes = p.get("ai_forecast_yes", 0.5)
        pct = ai_yes * 100

        if pct < 20:
            bucket = "0-20%"
        elif pct < 40:
            bucket = "20-40%"
        elif pct < 60:
            bucket = "40-60%"
        elif pct < 80:
            bucket = "60-80%"
        else:
            bucket = "80-100%"

        buckets[bucket]["predicted"].append(ai_yes)
        buckets[bucket]["total"] += 1
        if p.get("outcome") == "YES":
            buckets[bucket]["actual_yes"] += 1

    result = {}
    for name, data in buckets.items():
        if data["total"] > 0:
            avg_predicted = sum(data["predicted"]) / len(data["predicted"]) * 100
            actual_rate = data["actual_yes"] / data["total"] * 100
            result[name] = {
                "count": data["total"],
                "avg_predicted": round(avg_predicted, 1),
                "actual_yes_rate": round(actual_rate, 1),
                "calibration_error": round(abs(avg_predicted - actual_rate), 1),
            }
    return result


# ═══════════════════════════════════════════════════════════════════════
# FORMATTED OUTPUT
# ═══════════════════════════════════════════════════════════════════════

def format_performance() -> str:
    """Format performance metrics for Telegram display."""
    stats = get_performance()

    lines = []
    lines.append("📊 <b>PREDICTION PERFORMANCE</b>\n")

    # Overview
    lines.append(f"📈 Total predictions logged: <b>{stats['total_predictions']}</b>")
    lines.append(f"✅ Resolved: <b>{stats['resolved']}</b> | ⏳ Pending: <b>{stats['unresolved']}</b>")
    lines.append(f"🎯 Trade recommendations: <b>{stats['total_trades']}</b>")
    lines.append("")

    if stats["win_rate"] is None:
        lines.append("⚪ <b>No resolved trades yet.</b>")
        lines.append("Predictions are being tracked. As markets resolve,")
        lines.append("accuracy metrics will appear here.")
        lines.append("")
        lines.append(f"⏳ <b>{stats['unresolved']} predictions awaiting resolution</b>")

        # Show some pending predictions
        d = _load()
        pending = [p for p in d["predictions"] if not p.get("resolved")]
        if pending:
            recent = sorted(pending, key=lambda p: p.get("timestamp", ""), reverse=True)[:5]
            lines.append("")
            lines.append("━" * 30)
            lines.append("<b>Recent Predictions:</b>")
            for p in recent:
                rec = p.get("ai_recommendation", "?")
                emoji = "🔴" if rec == "BUY_NO" else "🟢" if rec == "BUY_YES" else "⚪"
                ts = p.get("timestamp", "")[:10]
                lines.append(f"  {emoji} {rec} | {p['question'][:45]}")
                lines.append(f"     AI: {p['ai_forecast_yes']:.0%} YES | Poly: {p['poly_yes']:.0%} | {ts}")

        return "\n".join(lines)

    # Performance summary
    lines.append("━" * 30)
    lines.append("<b>TRADE PERFORMANCE</b>\n")

    # Win rate with visual bar
    wr = stats["win_rate"]
    bar_len = 20
    filled = int(wr / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    lines.append(f"🎯 Win rate: <b>{wr}%</b> ({stats['wins']}W / {stats['losses']}L)")
    lines.append(f"  [{bar}]")
    lines.append("")

    # ROI
    if stats.get("avg_roi") is not None:
        roi_emoji = "💰" if stats["avg_roi"] > 0 else "📉"
        lines.append(f"{roi_emoji} Avg ROI per trade: <b>{stats['avg_roi']:+.1f}%</b>")
        lines.append(f"💵 Total ROI (all trades): <b>{stats['total_roi']:+.1f}%</b>")
        lines.append(f"🏆 Best trade: <b>{stats['best_trade']:+.1f}%</b>")
        lines.append(f"💀 Worst trade: <b>{stats['worst_trade']:+.1f}%</b>")
        lines.append("")

    # Streak
    if stats.get("recent_streak", 0) >= 2:
        lines.append(f"🔥 Current streak: <b>{stats['recent_streak']} wins</b>")
        lines.append("")

    # By recommendation type
    if stats.get("by_recommendation"):
        lines.append("━" * 30)
        lines.append("<b>BY STRATEGY</b>\n")
        for rec_type, data in stats["by_recommendation"].items():
            emoji = "🔴" if rec_type == "BUY_NO" else "🟢"
            lines.append(f"  {emoji} <b>{rec_type}</b>: {data['win_rate']}% win ({data['count']} trades, {data['avg_roi']:+.1f}% avg ROI)")

    # By source
    if stats.get("by_source"):
        lines.append("")
        lines.append("━" * 30)
        lines.append("<b>BY SOURCE</b>\n")
        for source, data in stats["by_source"].items():
            lines.append(f"  📊 <b>{source}</b>: {data['win_rate']}% win ({data['count']} trades, {data['avg_roi']:+.1f}% avg ROI)")

    # Calibration
    if stats.get("calibration"):
        lines.append("")
        lines.append("━" * 30)
        lines.append("<b>CALIBRATION</b> (AI accuracy by confidence)\n")
        for bucket, data in sorted(stats["calibration"].items()):
            if data["count"] >= 2:  # Only show buckets with enough data
                cal_emoji = "✅" if data["calibration_error"] < 15 else "⚠️" if data["calibration_error"] < 25 else "❌"
                lines.append(f"  {cal_emoji} AI said <b>{bucket}</b> → actually <b>{data['actual_yes_rate']:.0f}%</b> YES ({data['count']} samples)")

    # Last check
    d = _load()
    last_check = d.get("last_resolution_check", "Never")
    if last_check and last_check != "Never":
        last_check = last_check[:16].replace("T", " ") + " UTC"
    lines.append(f"\n🕐 Last resolution check: {last_check}")

    return "\n".join(lines)


def get_accuracy_context() -> str:
    """
    Get DETAILED accuracy context for AI prompts.
    Includes specific wins, losses, biases, and lessons learned.
    This is the core learning mechanism — the AI sees its past mistakes.
    """
    d = _load()
    preds = d["predictions"]
    stats = get_performance()

    if stats["win_rate"] is None and len(preds) == 0:
        return ""

    lines = []

    # ── Overall stats ──
    if stats["win_rate"] is not None:
        lines.append(f"YOUR TRACK RECORD: {stats['win_rate']}% win rate across {stats['total_trades']} resolved trades. Avg ROI: {stats.get('avg_roi', 0):+.1f}%.")

        # Strategy breakdown
        if stats.get("by_recommendation"):
            for rec, data in stats["by_recommendation"].items():
                lines.append(f"  {rec} strategy: {data['win_rate']}% win ({data['count']} trades, {data['avg_roi']:+.1f}% avg ROI).")

    # ── Calibration warnings ──
    if stats.get("calibration"):
        for bucket, data in stats["calibration"].items():
            if data["count"] >= 3 and data["calibration_error"] > 12:
                direction = "overconfident" if data["avg_predicted"] > data["actual_yes_rate"] else "underconfident"
                lines.append(f"  CALIBRATION BIAS: When you predict {bucket} YES, actual rate is {data['actual_yes_rate']:.0f}%. You are {direction} in this range — ADJUST.")

    # ── Specific recent wins (learn what worked) ──
    resolved = [p for p in preds if p.get("resolved") and p.get("outcome") in ("YES", "NO")
                and p.get("ai_recommendation") in ("BUY_NO", "BUY_YES")]
    wins = [p for p in resolved if p.get("outcome_correct") is True]
    losses = [p for p in resolved if p.get("outcome_correct") is False]

    if wins:
        recent_wins = sorted(wins, key=lambda p: p.get("timestamp", ""), reverse=True)[:3]
        lines.append("RECENT WINS (repeat these patterns):")
        for w in recent_wins:
            roi = w.get("pnl_if_followed", 0)
            lines.append(f"  WIN: {w['ai_recommendation']} on \"{w['question'][:60]}\" — Poly YES was {w['poly_yes']:.0%}, resolved {w['outcome']}. ROI: {roi:+.0f}%")

    if losses:
        recent_losses = sorted(losses, key=lambda p: p.get("timestamp", ""), reverse=True)[:3]
        lines.append("RECENT LOSSES (avoid these mistakes):")
        for l in recent_losses:
            lines.append(f"  LOSS: {l['ai_recommendation']} on \"{l['question'][:60]}\" — Poly YES was {l['poly_yes']:.0%}, resolved {l['outcome']}. WRONG.")

    # ── Pattern analysis: what event types do we get right/wrong ──
    if len(resolved) >= 5:
        # Check if certain keywords correlate with wins/losses
        win_words = defaultdict(int)
        loss_words = defaultdict(int)
        keyword_set = ["ceasefire", "capture", "war", "iran", "russia", "ukraine", "china",
                       "trump", "tariff", "sanctions", "israel", "nato", "nuclear", "strike"]
        for p in resolved:
            q = p.get("question", "").lower()
            is_win = p.get("outcome_correct", False)
            for kw in keyword_set:
                if kw in q:
                    if is_win:
                        win_words[kw] += 1
                    else:
                        loss_words[kw] += 1

        # Find strong patterns (3+ samples)
        strong_wins = [(kw, win_words[kw]) for kw in keyword_set
                       if win_words[kw] >= 2 and win_words[kw] > loss_words.get(kw, 0)]
        strong_losses = [(kw, loss_words[kw]) for kw in keyword_set
                         if loss_words[kw] >= 2 and loss_words[kw] > win_words.get(kw, 0)]

        if strong_wins:
            lines.append(f"STRONG TOPICS (you predict well): {', '.join(f'{kw} ({c}W)' for kw, c in strong_wins[:4])}")
        if strong_losses:
            lines.append(f"WEAK TOPICS (you predict poorly): {', '.join(f'{kw} ({c}L)' for kw, c in strong_losses[:4])}. BE EXTRA CAREFUL here.")

    # ── Divergence analysis: are we better when we disagree with market? ──
    if len(resolved) >= 5:
        high_div = [p for p in resolved if abs(p.get("ai_forecast_yes", 0.5) - p.get("poly_yes", 0.5)) >= 0.15]
        low_div = [p for p in resolved if abs(p.get("ai_forecast_yes", 0.5) - p.get("poly_yes", 0.5)) < 0.15]
        if high_div:
            hd_win = sum(1 for p in high_div if p.get("outcome_correct") is True)
            hd_rate = hd_win / len(high_div) * 100 if high_div else 0
            lines.append(f"HIGH DIVERGENCE BETS (you disagreed with market by 15%+): {hd_rate:.0f}% win rate ({len(high_div)} trades)")
        if low_div:
            ld_win = sum(1 for p in low_div if p.get("outcome_correct") is True)
            ld_rate = ld_win / len(low_div) * 100 if low_div else 0
            lines.append(f"LOW DIVERGENCE BETS (you mostly agreed with market): {ld_rate:.0f}% win rate ({len(low_div)} trades)")

    if not lines:
        return ""

    return "\n".join(lines)


def format_history(page: int = 0, per_page: int = 10) -> str:
    """Format prediction history for /history command."""
    d = _load()
    preds = d["predictions"]

    if not preds:
        return "📜 <b>No predictions logged yet.</b>\nRun /top10 or /research to start tracking."

    # Sort by timestamp descending
    sorted_preds = sorted(preds, key=lambda p: p.get("timestamp", ""), reverse=True)
    total = len(sorted_preds)
    start = page * per_page
    end = min(start + per_page, total)
    page_preds = sorted_preds[start:end]
    total_pages = (total + per_page - 1) // per_page

    lines = []
    lines.append(f"📜 <b>PREDICTION HISTORY</b> (page {page+1}/{total_pages})")
    lines.append(f"Total: {total} predictions\n")

    # Summary counts
    resolved = sum(1 for p in preds if p.get("resolved"))
    pending = total - resolved
    wins = sum(1 for p in preds if p.get("outcome_correct") is True)
    losses = sum(1 for p in preds if p.get("outcome_correct") is False)
    lines.append(f"✅ {wins}W | ❌ {losses}L | ⏳ {pending} pending")
    lines.append("━" * 30)

    for p in page_preds:
        rec = p.get("ai_recommendation", "?")
        resolved = p.get("resolved", False)
        outcome = p.get("outcome", "")
        correct = p.get("outcome_correct")
        ts = p.get("timestamp", "")[:10]
        roi = p.get("pnl_if_followed")

        # Status emoji
        if not resolved:
            status = "⏳"
        elif correct is True:
            status = "✅"
        elif correct is False:
            status = "❌"
        else:
            status = "⚪"

        # Recommendation emoji
        rec_emoji = "🔴" if rec == "BUY_NO" else "🟢" if rec == "BUY_YES" else "⚪"

        lines.append(f"\n{status} {rec_emoji} <b>{rec}</b> | {ts}")
        lines.append(f"  {p['question'][:55]}")
        lines.append(f"  AI: {p['ai_forecast_yes']:.0%} YES | Poly: {p['poly_yes']:.0%}")

        if resolved and outcome:
            roi_str = f" | ROI: {roi:+.0f}%" if roi is not None else ""
            lines.append(f"  Resolved: <b>{outcome}</b>{roi_str}")

        # Show manifold if available
        if p.get("manifold_yes") is not None:
            lines.append(f"  Manifold: {p['manifold_yes']:.0%} YES")

    if total_pages > 1:
        lines.append(f"\n━━━\n📄 /history {page+2} for next page" if page + 1 < total_pages else "")

    return "\n".join(lines)


def get_event_track_record(event_keywords: list) -> str:
    """
    Get past predictions related to specific event keywords.
    Used by researcher to show "we've seen this before" context.
    """
    d = _load()
    preds = d["predictions"]
    if not preds:
        return ""

    # Find related past predictions
    related = []
    for p in preds:
        q = (p.get("question", "") + " " + p.get("event_title", "")).lower()
        if any(kw.lower() in q for kw in event_keywords):
            related.append(p)

    if not related:
        return ""

    resolved = [p for p in related if p.get("resolved") and p.get("outcome") in ("YES", "NO")]
    pending = [p for p in related if not p.get("resolved")]

    lines = []
    if resolved:
        wins = sum(1 for p in resolved if p.get("outcome_correct") is True)
        total = len(resolved)
        lines.append(f"RELATED PAST PREDICTIONS: {wins}/{total} correct on similar events.")
        for p in sorted(resolved, key=lambda x: x.get("timestamp", ""), reverse=True)[:3]:
            correct_str = "CORRECT" if p.get("outcome_correct") else "WRONG"
            lines.append(f"  {p['ai_recommendation']} \"{p['question'][:50]}\" — {correct_str} (resolved {p['outcome']})")

    if pending:
        lines.append(f"PENDING: {len(pending)} related predictions awaiting resolution.")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# AI PREDICTION PARSER
# ═══════════════════════════════════════════════════════════════════════

def parse_ai_predictions(ai_text: str, markets_data: list, source: str = "top10") -> list:
    """
    Parse AI analysis text to extract predictions and log them.
    Looks for patterns like:
    - "AI FORECAST: 35%" or "My estimate: 25%"
    - "BUY NO" / "BUY YES" / "HOLD"
    - Percentage forecasts near market questions
    """
    if not ai_text or not markets_data:
        return []

    logged = []

    for m in markets_data:
        question = m.get("question", "")
        market_id = m.get("id", "")
        if not market_id:
            continue

        # Try to find AI's forecast for this market in the text
        # Look for keywords from the question near percentage values
        q_words = [w.lower() for w in re.sub(r'[^a-zA-Z0-9 ]', '', question).split()
                   if len(w) > 3][:4]

        ai_yes = None
        recommendation = "HOLD"
        reasoning = ""

        # Search for forecast percentages near question keywords
        for word in q_words:
            # Find text sections mentioning this word
            pattern = re.compile(rf'{re.escape(word)}.{{0,200}}?(\d{{1,2}})%', re.IGNORECASE)
            match = pattern.search(ai_text)
            if match:
                pct = int(match.group(1))
                if 1 <= pct <= 99:
                    ai_yes = pct / 100
                    # Get surrounding text as reasoning
                    start = max(0, match.start() - 50)
                    end = min(len(ai_text), match.end() + 100)
                    reasoning = ai_text[start:end].strip()
                    break

        # Determine recommendation from AI text
        q_lower = question.lower()
        text_lower = ai_text.lower()

        # Look for BUY NO / BUY YES near question keywords
        for word in q_words:
            idx = text_lower.find(word)
            if idx >= 0:
                nearby = text_lower[max(0, idx-100):idx+200]
                if "buy no" in nearby:
                    recommendation = "BUY_NO"
                elif "buy yes" in nearby:
                    recommendation = "BUY_YES"
                elif "hold" in nearby:
                    recommendation = "HOLD"
                break

        # Fallback: if no AI forecast found, use divergence logic
        if ai_yes is None:
            # Default: assume AI roughly agrees with Polymarket
            ai_yes = m.get("yes_price", 0.5)

        # Determine confidence
        yes_p = m.get("yes_price", 0.5)
        divergence = abs(ai_yes - yes_p)
        if divergence >= 0.20:
            confidence = "high"
        elif divergence >= 0.10:
            confidence = "medium"
        else:
            confidence = "low"

        pred_id = log_prediction(
            market_id=market_id,
            question=question,
            source=source,
            poly_yes=m.get("yes_price", 0),
            poly_no=m.get("no_price", 0),
            ai_forecast_yes=ai_yes,
            ai_recommendation=recommendation,
            manifold_yes=m.get("_manifold_yes"),
            event_title=m.get("_event_title", ""),
            end_date=m.get("end_date", ""),
            volume=m.get("volume", 0),
            days_left=m.get("days_left"),
            confidence=confidence,
            reasoning=reasoning[:200],
        )
        logged.append(pred_id)

    return logged
