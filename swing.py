"""
MODULE 6 — NO Price Swing Detector + AI Summary
Fires when NO drops >=10% within 30 min on a tracked position.
"""

from datetime import datetime, timezone, timedelta
import re
import requests
import polymarket_api as api
import portfolio_store as store
import telegram_client as tg
from config import (
    SWING_DROP_THRESHOLD, SWING_WINDOW_MINUTES,
    SWING_COOLDOWN_HOURS, ANTHROPIC_API_KEY
)

def _news_snippet(question: str) -> str:
    stop = {"will","the","a","an","by","in","of","to","be","on","at","for","or","and","is"}
    words = re.sub(r"[^a-zA-Z0-9 ]", "", question).split()
    kw    = " ".join([w for w in words if w.lower() not in stop and len(w) > 3][:3])
    if not kw:
        return ""
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": kw, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": "PolymarketBot/1.0"}, timeout=8
        )
        if r.ok:
            m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text)
            if not m:
                m = re.search(r"<item>.*?<title>(.*?)</title>", r.text, re.DOTALL)
            if m:
                t = m.group(1).strip()
                if "Google" not in t:
                    return t[:150]
    except Exception:
        pass
    return ""

def _claude_summary(question: str, no_before: float, no_after: float, news: str) -> str:
    if ANTHROPIC_API_KEY == "YOUR_ANTHROPIC_API_KEY":
        return "Set ANTHROPIC_API_KEY in config.py to enable AI summaries."

    drop = round((no_before - no_after) / no_before * 100, 1)
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 180,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Polymarket NO price dropped -{drop}% in 30 min.\n"
                        f"Market: \"{question}\"\n"
                        f"NO: ${no_before:.2f} → ${no_after:.2f}\n"
                        f"Recent news: {news or 'none found'}\n\n"
                        f"In 2 sentences: what likely caused this? "
                        f"Real catalyst, whale, or noise? Should trader hold or exit?"
                    )
                }]
            },
            timeout=20
        )
        if r.ok:
            return r.json()["content"][0]["text"].strip()
        return f"API error {r.status_code}"
    except Exception as e:
        return f"AI unavailable: {e}"

def _verdict(has_news: bool, drop_pct: float) -> str:
    if has_news and drop_pct >= 15:
        return "⚡ REAL NEWS CATALYST — reassess position"
    if not has_news and drop_pct >= 15:
        return "🐋 WHALE MOVE — large trade, no news found"
    if drop_pct < 12:
        return "📉 LIQUIDITY THIN — likely noise"
    if has_news:
        return "⚠️ NEWS DETECTED — verify before deciding"
    return "❓ UNKNOWN — no clear explanation"

def _cooldown_ok(market_id: str) -> bool:
    last_str = store.get_last_swing_alert(market_id)
    if not last_str:
        return True
    try:
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last > timedelta(hours=SWING_COOLDOWN_HOURS)
    except Exception:
        return True

def run_swing_check():
    positions = store.get_positions()
    if not positions:
        return

    now = datetime.now(timezone.utc)
    print(f"[SWING] Checking {len(positions)} positions at {now.strftime('%H:%M UTC')}")

    for mid, pos in positions.items():
        q      = pos.get("question", "Unknown")
        entry  = pos.get("entry_price", 0)
        size   = pos.get("size_usd", 0)
        url    = pos.get("url", "")

        # Fetch current NO price
        raw = api.get_market_by_id(mid)
        if not raw:
            continue
        parsed = api.parse_market(raw)
        if not parsed:
            continue

        current_no = parsed["no_price"]
        store.save_price_snapshot(mid, current_no)

        # Find highest NO in window
        snapshots   = store.get_price_snapshots(mid)
        cutoff      = now - timedelta(minutes=SWING_WINDOW_MINUTES)
        window      = []
        for snap in snapshots[:-1]:  # exclude current
            try:
                ts = datetime.fromisoformat(snap["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    window.append(snap["price"])
            except Exception:
                continue

        if not window:
            continue

        highest_no   = max(window)
        drop_decimal = (highest_no - current_no) / highest_no if highest_no > 0 else 0

        if drop_decimal < SWING_DROP_THRESHOLD:
            continue
        if not _cooldown_ok(mid):
            continue

        # ── SWING DETECTED ──
        store.set_swing_alert_time(mid)
        drop_pct = round(drop_decimal * 100, 1)
        pnl_pct  = round((current_no - entry) / entry * 100, 1) if entry > 0 else 0
        pnl_usd  = round((current_no - entry) * size, 2)
        sign     = "+" if pnl_pct >= 0 else ""

        print(f"[SWING] -{drop_pct}% detected on: {q[:50]}")

        news    = _news_snippet(q)
        summary = _claude_summary(q, highest_no, current_no, news)
        verdict = _verdict(bool(news and len(news) > 20), drop_pct)

        msg = (
            f"⚠️ <b>NO SWING DETECTED</b>\n\n"
            f"📌 {q[:65]}\n\n"
            f"NO moved: <b>${highest_no:.2f} → ${current_no:.2f}</b> "
            f"(<b>-{drop_pct}%</b>) in {SWING_WINDOW_MINUTES} min\n"
        )
        if news:
            msg += f"\n📰 {news[:100]}\n"

        msg += (
            f"\n🔍 <b>AI Summary:</b>\n{summary}\n\n"
            f"<b>{verdict}</b>\n\n"
            f"Entry: ${entry:.2f}  |  P&L: {sign}{pnl_pct}% (${sign}{pnl_usd})\n"
            f"🔗 <a href=\"{url}\">Open Market</a>"
        )
        tg.send(msg)
