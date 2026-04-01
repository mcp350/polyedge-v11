"""
POLYTRAGENT — Top Picks Engine
Auto-curates 20 best whale wallets every 24 hours based on Polymarket on-chain data.

Ranking categories:
  1. Most Trades     — highest trade count in recent activity
  2. Biggest P&L     — highest cumulative profit/loss
  3. Biggest Wins    — largest single winning positions
  4. Most Efficient  — best PnL-to-volume ratio (ROI)

Data sources:
  - data-api.polymarket.com/trades  (recent trades, paginated)
  - data-api.polymarket.com/positions (per-wallet P&L)

Cache: data/top_picks_cache.json — refreshed every 24 hours.
"""

import os, json, time, logging, requests, threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

log = logging.getLogger("polytragent.top_picks")

CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "top_picks_cache.json")
TRADES_URL = "https://data-api.polymarket.com/trades"
POSITIONS_URL = "https://data-api.polymarket.com/positions"
HEADERS = {"User-Agent": "Polytragent/2.0", "Accept": "application/json"}
CACHE_TTL_HOURS = 24
NUM_PICKS = 20
TRADE_PAGES = 20       # Fetch 20 pages × 100 trades = 2000 recent trades
TRADES_PER_PAGE = 100


# ═══════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════

def _load_cache() -> Optional[dict]:
    """Load cached top picks. Returns None if stale or missing."""
    try:
        if not os.path.exists(CACHE_FILE):
            return None
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        updated = cache.get("updated_at", "")
        if not updated:
            return None
        updated_dt = datetime.fromisoformat(updated)
        if datetime.now(timezone.utc) - updated_dt > timedelta(hours=CACHE_TTL_HOURS):
            return None  # Stale
        return cache
    except Exception as e:
        log.warning(f"Cache load error: {e}")
        return None


def _save_cache(picks: list, stats: dict):
    """Save top picks to cache file."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    cache = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "picks": picks,
        "stats": stats,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, default=str)
    log.info(f"[TOP_PICKS] Cached {len(picks)} picks")


# ═══════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════

def _fetch_recent_trades(pages: int = TRADE_PAGES) -> list:
    """Fetch recent trades across multiple pages."""
    all_trades = []
    for page in range(pages):
        try:
            offset = page * TRADES_PER_PAGE
            r = requests.get(
                TRADES_URL,
                params={"limit": TRADES_PER_PAGE, "offset": offset},
                headers=HEADERS, timeout=15,
            )
            if r.status_code != 200:
                log.warning(f"[TOP_PICKS] Trades page {page} returned {r.status_code}")
                break
            data = r.json()
            if not data:
                break
            all_trades.extend(data)
            time.sleep(0.3)  # Rate limit
        except Exception as e:
            log.warning(f"[TOP_PICKS] Trades page {page} error: {e}")
            break
    log.info(f"[TOP_PICKS] Fetched {len(all_trades)} trades from {pages} pages")
    return all_trades


def _aggregate_traders(trades: list) -> dict:
    """Aggregate trades by wallet address."""
    wallets = {}
    for t in trades:
        addr = t.get("proxyWallet", "").lower()
        if not addr:
            continue
        if addr not in wallets:
            wallets[addr] = {
                "address": addr,
                "name": t.get("name", ""),
                "pseudonym": t.get("pseudonym", ""),
                "trade_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "total_volume_usd": 0.0,
                "largest_trade_usd": 0.0,
            }
        w = wallets[addr]
        w["trade_count"] += 1
        trade_val = float(t.get("size", 0)) * float(t.get("price", 1))
        w["total_volume_usd"] += trade_val
        w["largest_trade_usd"] = max(w["largest_trade_usd"], trade_val)
        side = t.get("side", "").upper()
        if side == "BUY":
            w["buy_count"] += 1
        else:
            w["sell_count"] += 1
        # Keep latest name
        name = t.get("name", "")
        if name and name != addr:
            w["name"] = name

    return wallets


def _fetch_wallet_positions(address: str, limit: int = 200) -> dict:
    """Fetch position data for a wallet. Returns aggregated PnL stats."""
    try:
        r = requests.get(
            POSITIONS_URL,
            params={"user": address, "limit": limit, "sizeThreshold": 0},
            headers=HEADERS, timeout=15,
        )
        if r.status_code != 200:
            return {"pnl": 0, "wins": 0, "losses": 0, "biggest_win": 0, "total_invested": 0}
        positions = r.json()
        if not isinstance(positions, list):
            return {"pnl": 0, "wins": 0, "losses": 0, "biggest_win": 0, "total_invested": 0}

        total_pnl = 0.0
        wins = 0
        losses = 0
        biggest_win = 0.0
        total_invested = 0.0

        for p in positions:
            pnl = float(p.get("cashPnl", 0))
            invested = float(p.get("initialValue", 0))
            total_pnl += pnl
            total_invested += invested
            if pnl > 0:
                wins += 1
                biggest_win = max(biggest_win, pnl)
            elif pnl < 0:
                losses += 1

        return {
            "pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": losses,
            "biggest_win": round(biggest_win, 2),
            "total_invested": round(total_invested, 2),
            "position_count": len(positions),
        }
    except Exception as e:
        log.warning(f"[TOP_PICKS] Position fetch error for {address[:10]}: {e}")
        return {"pnl": 0, "wins": 0, "losses": 0, "biggest_win": 0, "total_invested": 0}


# ═══════════════════════════════════════════════
# SCORING & RANKING
# ═══════════════════════════════════════════════

def _score_and_rank(wallets: dict) -> list:
    """
    Score wallets across 4 categories and pick top 20.
    Each wallet gets a composite score from:
      - trade_count_score (most trades)
      - pnl_score (biggest P&L)
      - biggest_win_score (biggest single win)
      - efficiency_score (PnL / volume ratio)
    """
    # Filter out wallets with very few trades (noise)
    candidates = [w for w in wallets.values() if w["trade_count"] >= 3]
    if not candidates:
        candidates = list(wallets.values())

    # Sort by volume to get top 80 candidates for position lookup
    candidates.sort(key=lambda x: x["total_volume_usd"], reverse=True)
    candidates = candidates[:80]

    log.info(f"[TOP_PICKS] Enriching {len(candidates)} candidates with position data...")

    # Enrich with position data (slow — rate limited)
    for i, w in enumerate(candidates):
        pos_data = _fetch_wallet_positions(w["address"])
        w.update(pos_data)
        if (i + 1) % 10 == 0:
            log.info(f"[TOP_PICKS] Enriched {i+1}/{len(candidates)} wallets")
        time.sleep(0.4)  # Rate limit

    # Filter to wallets with actual positions
    enriched = [w for w in candidates if w.get("position_count", 0) > 0]
    if len(enriched) < 20:
        enriched = candidates  # Fall back

    # ── Normalize scores (0-100) ──
    def _normalize(vals):
        if not vals:
            return []
        mn, mx = min(vals), max(vals)
        rng = mx - mn if mx != mn else 1
        return [(v - mn) / rng * 100 for v in vals]

    # Score: Most Trades
    trade_counts = [w["trade_count"] for w in enriched]
    trade_scores = _normalize(trade_counts)

    # Score: Biggest PnL
    pnls = [w.get("pnl", 0) for w in enriched]
    pnl_scores = _normalize(pnls)

    # Score: Biggest Wins
    big_wins = [w.get("biggest_win", 0) for w in enriched]
    win_scores = _normalize(big_wins)

    # Score: Efficiency (PnL / volume, avoid div by 0)
    efficiencies = []
    for w in enriched:
        vol = w.get("total_volume_usd", 1)
        pnl = w.get("pnl", 0)
        efficiencies.append(pnl / max(vol, 1))
    eff_scores = _normalize(efficiencies)

    # Composite score (weighted)
    for i, w in enumerate(enriched):
        w["score_trades"] = round(trade_scores[i], 1)
        w["score_pnl"] = round(pnl_scores[i], 1)
        w["score_wins"] = round(win_scores[i], 1)
        w["score_efficiency"] = round(eff_scores[i], 1)

        # Composite: weighted average
        w["composite_score"] = round(
            trade_scores[i] * 0.20 +       # 20% trade activity
            pnl_scores[i] * 0.35 +          # 35% total PnL
            win_scores[i] * 0.20 +           # 20% biggest wins
            eff_scores[i] * 0.25,            # 25% efficiency
            1
        )

        # Assign primary category badge
        scores = {
            "🔥 Most Trades": trade_scores[i],
            "💰 Biggest P&L": pnl_scores[i],
            "🏆 Biggest Wins": win_scores[i],
            "🎯 Most Efficient": eff_scores[i],
        }
        w["primary_badge"] = max(scores, key=scores.get)

        # Win rate
        wins = w.get("wins", 0)
        losses = w.get("losses", 0)
        w["win_rate"] = round(wins / max(wins + losses, 1) * 100, 1)

    # Sort by composite score, take top 20
    enriched.sort(key=lambda x: x["composite_score"], reverse=True)
    top_picks = enriched[:NUM_PICKS]

    # Assign ranks
    for i, w in enumerate(top_picks):
        w["rank"] = i + 1

    return top_picks


# ═══════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════

def get_top_picks(force_refresh: bool = False) -> list:
    """
    Get the current top 20 picks. Uses cache if fresh (< 24h).
    If stale, triggers background refresh and returns old data or curated fallback.
    """
    if not force_refresh:
        cache = _load_cache()
        if cache and cache.get("picks"):
            return cache["picks"]

    # Try to refresh
    try:
        picks = _refresh_picks()
        return picks
    except Exception as e:
        log.error(f"[TOP_PICKS] Refresh failed: {e}")
        # Return stale cache if available
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE) as f:
                    return json.load(f).get("picks", [])
        except:
            pass
        return []


def _refresh_picks() -> list:
    """Full refresh: fetch trades, aggregate, score, cache."""
    log.info("[TOP_PICKS] Starting full refresh...")
    start = time.time()

    # 1. Fetch recent trades
    trades = _fetch_recent_trades()
    if not trades:
        log.warning("[TOP_PICKS] No trades fetched")
        return []

    # 2. Aggregate by wallet
    wallets = _aggregate_traders(trades)
    log.info(f"[TOP_PICKS] Found {len(wallets)} unique wallets from {len(trades)} trades")

    # 3. Score and rank
    top_picks = _score_and_rank(wallets)

    # 4. Format for storage
    picks = []
    for w in top_picks:
        picks.append({
            "rank": w["rank"],
            "address": w["address"],
            "name": w.get("name") or w.get("pseudonym") or f"Wallet_{w['address'][:8]}",
            "badge": w["primary_badge"],
            "composite_score": w["composite_score"],
            "trade_count": w["trade_count"],
            "volume_usd": round(w["total_volume_usd"], 2),
            "pnl": w.get("pnl", 0),
            "biggest_win": w.get("biggest_win", 0),
            "win_rate": w.get("win_rate", 0),
            "efficiency": round(w.get("pnl", 0) / max(w.get("total_volume_usd", 1), 1) * 100, 2),
            "wins": w.get("wins", 0),
            "losses": w.get("losses", 0),
            "position_count": w.get("position_count", 0),
        })

    elapsed = time.time() - start
    stats = {
        "total_trades_scanned": len(trades),
        "unique_wallets": len(wallets),
        "candidates_enriched": min(len(wallets), 80),
        "refresh_time_sec": round(elapsed, 1),
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }

    _save_cache(picks, stats)
    log.info(f"[TOP_PICKS] Refresh complete in {elapsed:.1f}s — {len(picks)} picks cached")
    return picks


def refresh_in_background():
    """Trigger a background refresh (non-blocking)."""
    t = threading.Thread(target=_refresh_picks, daemon=True)
    t.start()
    log.info("[TOP_PICKS] Background refresh started")


# ═══════════════════════════════════════════════
# FORMATTING (for Telegram)
# ═══════════════════════════════════════════════

def format_top_picks_page(page: int = 0, per_page: int = 5) -> tuple:
    """Format a page of top picks for Telegram. Returns (text, buttons)."""
    picks = get_top_picks()
    if not picks:
        return ("🏆 <b>Top Picks</b>\n\n⏳ Loading... Picks are being refreshed.\nCheck back in a few minutes.", [])

    total = len(picks)
    start = page * per_page
    end = min(start + per_page, total)
    page_picks = picks[start:end]

    # Cache freshness
    cache = _load_cache()
    updated = ""
    if cache:
        try:
            dt = datetime.fromisoformat(cache["updated_at"])
            hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if hours_ago < 1:
                updated = f"Updated {int(hours_ago * 60)}m ago"
            else:
                updated = f"Updated {int(hours_ago)}h ago"
        except:
            updated = ""

    lines = []
    for p in page_picks:
        pnl_str = f"+${p['pnl']:,.0f}" if p['pnl'] > 0 else f"-${abs(p['pnl']):,.0f}" if p['pnl'] < 0 else "$0"
        lines.append(
            f"<b>#{p['rank']}</b> {p['badge']} <b>{p['name']}</b>\n"
            f"   💰 P&L: {pnl_str}  •  🏆 {p['win_rate']}% WR\n"
            f"   📈 {p['trade_count']} trades  •  ${p['volume_usd']:,.0f} vol\n"
            f"   🎯 Efficiency: {p['efficiency']:+.1f}%"
        )

    text = (
        f"🏆 <b>Top Picks — Best Traders</b>\n"
        f"<i>{updated}  •  Refreshes every 24h</i>\n\n"
        + "\n\n".join(lines)
        + f"\n\n📊 Showing {start+1}-{end} of {total}"
    )

    # Buttons: follow each pick + pagination
    buttons = []
    for p in page_picks:
        short_addr = f"{p['address'][:6]}...{p['address'][-4:]}"
        buttons.append([
            {"text": f"👀 #{p['rank']} {p['name']}", "callback_data": f"tp_detail_{p['rank']}"},
            {"text": f"➕ Follow", "callback_data": f"tp_follow_{p['rank']}"},
        ])

    # Pagination
    nav = []
    if page > 0:
        nav.append({"text": "← Prev", "callback_data": f"tp_page_{page-1}"})
    if end < total:
        nav.append({"text": "Next →", "callback_data": f"tp_page_{page+1}"})
    if nav:
        buttons.append(nav)
    buttons.append([{"text": "🔄 Refresh", "callback_data": "tp_refresh"},
                     {"text": "← Back", "callback_data": "menu_whales"}])

    return text, buttons


def format_pick_detail(rank: int) -> tuple:
    """Format detailed view of a single pick. Returns (text, buttons)."""
    picks = get_top_picks()
    pick = None
    for p in picks:
        if p["rank"] == rank:
            pick = p
            break
    if not pick:
        return ("❌ Pick not found.", [[{"text": "← Back", "callback_data": "tp_page_0"}]])

    pnl_str = f"+${pick['pnl']:,.0f}" if pick['pnl'] > 0 else f"-${abs(pick['pnl']):,.0f}"
    bw_str = f"+${pick['biggest_win']:,.0f}" if pick['biggest_win'] > 0 else "$0"

    text = (
        f"🏆 <b>#{pick['rank']} — {pick['name']}</b>\n"
        f"{pick['badge']}\n\n"
        f"📍 <code>{pick['address']}</code>\n\n"
        f"💰 <b>P&L:</b> {pnl_str}\n"
        f"🏆 <b>Win Rate:</b> {pick['win_rate']}% ({pick['wins']}W / {pick['losses']}L)\n"
        f"📈 <b>Trades:</b> {pick['trade_count']} recent\n"
        f"💵 <b>Volume:</b> ${pick['volume_usd']:,.0f}\n"
        f"🎯 <b>Efficiency:</b> {pick['efficiency']:+.1f}%\n"
        f"🏅 <b>Biggest Win:</b> {bw_str}\n"
        f"📊 <b>Positions:</b> {pick['position_count']}\n\n"
        f"<b>Composite Score:</b> {pick['composite_score']}/100"
    )

    buttons = [
        [{"text": f"➕ Follow {pick['name']}", "callback_data": f"tp_follow_{pick['rank']}"}],
        [{"text": "← Back to Top Picks", "callback_data": "tp_page_0"}],
    ]
    return text, buttons


def get_pick_by_rank(rank: int) -> Optional[dict]:
    """Get a pick by its rank number."""
    picks = get_top_picks()
    for p in picks:
        if p["rank"] == rank:
            return p
    return None


# ═══════════════════════════════════════════════
# AUTO-REFRESH SCHEDULER (24h loop)
# ═══════════════════════════════════════════════

_scheduler_running = False

def start_auto_refresh():
    """Start a background thread that refreshes top picks every 24 hours."""
    global _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True

    def _loop():
        while True:
            try:
                cache = _load_cache()
                if cache is None:
                    # Stale or missing — refresh now
                    log.info("[TOP_PICKS] Auto-refresh triggered (cache stale/missing)")
                    _refresh_picks()
                else:
                    log.info("[TOP_PICKS] Cache still fresh, skipping refresh")
            except Exception as e:
                log.error(f"[TOP_PICKS] Auto-refresh error: {e}")
            # Sleep 1 hour, check again
            time.sleep(3600)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    log.info("[TOP_PICKS] Auto-refresh scheduler started (checks every 1h)")
