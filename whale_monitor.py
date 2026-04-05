"""
WHALE MONITOR — Real-time trade detection via data-api.polymarket.com
Runs every 45 seconds to catch whale trades as they happen.

Unlike copy_trading.py (position snapshot diffing), this module:
  - Polls the trades endpoint directly for actual trade records
  - Tracks last-seen trade ID per wallet to avoid duplicate alerts
  - Sends 🐋 Whale Alert! notifications in the canonical format
  - Only scans wallets that currently have at least one follower (efficient)

Storage: data/whale_monitor.json
  {"last_trade_ids": {"0xwallet": "trade_id"}, "last_scan": "ISO"}
"""

import json, os, time, subprocess
from datetime import datetime, timezone
from urllib.parse import urlencode

import copy_trading as ct
import onboarding
import copy_executor as ce

# ── Copy Trade Cache ──────────────────────────────────────────────────────────
# Maps short index → trade details so callback_data stays short
_copy_trade_cache: dict = {}  # idx -> {slug, outcome, question, price, whale_amount}
_copy_trade_counter = 0

def _store_copy_trade(slug: str, outcome: str, question: str, price: float, whale_amount: float,
                      token_id: str = "", neg_risk: bool = False, event_slug: str = "") -> int:
    """Store trade details in cache and return a short integer key."""
    global _copy_trade_counter
    _copy_trade_counter = (_copy_trade_counter + 1) % 10000
    _copy_trade_cache[_copy_trade_counter] = {
        "slug": slug,
        "outcome": outcome,
        "question": question,
        "price": price,
        "whale_amount": whale_amount,
        "token_id": token_id,
        "neg_risk": neg_risk,
        "event_slug": event_slug,
    }
    print(f"[WHALE_MON] Cached trade #{_copy_trade_counter}: token_id={token_id!r} neg_risk={neg_risk} event_slug={event_slug!r}")
    return _copy_trade_counter

# ── Constants ────────────────────────────────────────────────────────────────

DATA_API_BASE  = "https://data-api.polymarket.com"
SCAN_INTERVAL  = 45   # seconds between full sweeps
MIN_VALUE_USD  = 50   # ignore trades below $50
MONITOR_FILE   = os.path.join(os.path.dirname(__file__), "data", "whale_monitor.json")

# ── Storage ──────────────────────────────────────────────────────────────────

def _load() -> dict:
    os.makedirs(os.path.dirname(MONITOR_FILE), exist_ok=True)
    if not os.path.exists(MONITOR_FILE):
        return {"last_trade_ids": {}, "last_scan": ""}
    try:
        with open(MONITOR_FILE) as f:
            return json.load(f)
    except Exception:
        return {"last_trade_ids": {}, "last_scan": ""}


def _save(data: dict):
    os.makedirs(os.path.dirname(MONITOR_FILE), exist_ok=True)
    with open(MONITOR_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── HTTP via curl (bypasses Railway's polymarket.com block) ──────────────────

def _curl_get(url: str, timeout: int = 15):
    """Fetch a URL via curl subprocess. Returns parsed JSON or None."""
    try:
        proc = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout),
             "-H", "Accept: application/json",
             "-H", "User-Agent: PolymarketBot/1.0",
             url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        raw = proc.stdout.strip()
        # Skip any non-JSON prefix (HTTP headers leaking, etc.)
        start = next((i for i, c in enumerate(raw) if c in "[{"), -1)
        if start == -1:
            return None
        result, _ = json.JSONDecoder().raw_decode(raw[start:])
        return result
    except Exception as e:
        print(f"[WHALE_MON] curl error fetching {url}: {e}")
        return None


# ── Trade fetching ────────────────────────────────────────────────────────────

def fetch_recent_trades(address: str, limit: int = 10) -> list:
    """
    Fetch the most recent trades for *address* (as maker).
    Primary: data-api.polymarket.com  (curl)
    Fallback: CLOB proxy endpoint     (requests)
    """
    # Primary — data-api
    url = f"{DATA_API_BASE}/trades?maker={address}&limit={limit}"
    result = _curl_get(url)
    trades = _extract_list(result)
    if trades:
        return trades

    # Fallback — CLOB proxy (already configured to bypass Railway)
    try:
        import requests as _req
        import config as _cfg
        r = _req.get(
            f"{_cfg.CLOB_BASE}/trades",
            params={"maker": address, "limit": str(limit)},
            headers={"User-Agent": "PolymarketBot/1.0", "Accept": "application/json"},
            timeout=15,
        )
        if r.ok:
            data = r.json()
            trades = data if isinstance(data, list) else data.get("trades", data.get("results", []))
            if trades:
                return trades
    except Exception as e:
        print(f"[WHALE_MON] CLOB fallback error for {address[:10]}: {e}")

    return []


def _extract_list(result) -> list:
    """Normalise API response to a plain list of trade dicts."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("data", "trades", "results", "items"):
            if isinstance(result.get(key), list):
                return result[key]
    return []


# ── Trade parsing ─────────────────────────────────────────────────────────────

def _trade_id(trade: dict) -> str:
    """Return a stable unique identifier for a trade."""
    return (
        trade.get("id")
        or trade.get("taker_order_id")
        or trade.get("transaction_hash")
        or f"{trade.get('maker_address','')}-{trade.get('match_time','')}-{trade.get('price','')}"
    )


def _trade_timestamp(trade: dict) -> str:
    """Return ISO-8601 string for when the trade occurred."""
    raw = trade.get("timestamp") or trade.get("match_time") or trade.get("created_at") or ""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
        except Exception:
            return str(raw)
    return str(raw)


def _resolve_market_via_gamma(token_id: str) -> dict:
    """Resolve a CLOB token ID to market data via the Gamma API."""
    try:
        import polymarket_api as api
        r = api._get(f"{api.GAMMA_BASE}/markets", params={"clob_token_ids": str(token_id)})
        if r and isinstance(r, list) and len(r) > 0:
            m = r[0]
            import json as _json
            title = m.get("question", "")
            slug = m.get("slug", "") or m.get("market_slug", "")
            event_slug = m.get("event_slug", "") or m.get("eventSlug", "")
            all_outcomes = []
            outcome_for_token = ""
            raw_tokens = m.get("clobTokenIds", "") or m.get("clob_token_ids", "")
            raw_outcomes = m.get("outcomes", "")
            try:
                token_list = _json.loads(raw_tokens) if isinstance(raw_tokens, str) else (raw_tokens or [])
                all_outcomes = _json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else (raw_outcomes or [])
                for i, tid in enumerate(token_list):
                    if str(tid) == str(token_id) and i < len(all_outcomes):
                        outcome_for_token = all_outcomes[i]
                        break
            except Exception:
                pass
            return {
                "title": title,
                "slug": slug,
                "event_slug": event_slug,
                "all_outcomes": all_outcomes,
                "outcome_for_token": outcome_for_token,
            }
    except Exception as e:
        print(f"[WHALE_MON] Gamma resolve error for token {token_id}: {e}")
    return {}


def _parse_trade(trade: dict, wallet: dict) -> dict | None:
    """
    Convert a raw trade dict into our internal signal format.
    Returns None if the trade is too small to notify about.
    """
    # ── Outcome (YES / NO) ──
    outcome_raw = trade.get("outcome") or trade.get("side") or "YES"
    if isinstance(outcome_raw, str):
        ol = outcome_raw.strip().lower()
        if ol in ("yes", "1", "true", "buy"):
            outcome = "YES"
        elif ol in ("no", "0", "false", "sell"):
            outcome = "NO"
        else:
            outcome = outcome_raw.upper()[:3]
    else:
        outcome = "YES"

    # ── Side (BUY / SELL) ──
    side_raw = trade.get("side") or trade.get("trader_side") or "BUY"
    side = "BUY" if side_raw.upper() in ("BUY", "MAKER", "LONG") else "SELL"

    # ── Size & price ──
    try:
        size  = float(trade.get("size")  or trade.get("amount") or 0)
        price = float(trade.get("price") or trade.get("avg_price") or 0)
    except (TypeError, ValueError):
        size, price = 0.0, 0.0

    # size can be shares; value ≈ shares × price
    value_usd = size * price if 0 < price <= 1 else size

    if value_usd < MIN_VALUE_USD and size < MIN_VALUE_USD:
        return None  # skip micro trades

    # ── Market ID & title ──
    market_id = str(
        trade.get("conditionId") or trade.get("market") or trade.get("condition_id") or ""
    )
    # Try to pull title from the stored last_positions snapshot
    title = _lookup_title(market_id) or trade.get("title") or trade.get("question") or ""

    token_id    = str(trade.get("asset") or trade.get("asset_id") or "")
    event_slug  = str(trade.get("eventSlug") or "")
    market_slug = str(trade.get("slug") or "")
    all_outcomes = []
    neg_risk    = outcome not in ("YES", "NO")  # multi-outcome markets need neg_risk=True

    # ── Enrich via Gamma API when slugs are missing ──
    if token_id and (not event_slug or not market_slug or not title):
        gamma = _resolve_market_via_gamma(token_id)
        if gamma:
            if not title and gamma.get("title"):
                title = gamma["title"][:80]
            if not market_slug and gamma.get("slug"):
                market_slug = gamma["slug"]
            if not event_slug and gamma.get("event_slug"):
                event_slug = gamma["event_slug"]
            all_outcomes = gamma.get("all_outcomes", [])
            # Use the exact outcome name from Gamma if available
            if gamma.get("outcome_for_token"):
                outcome = gamma["outcome_for_token"]
                neg_risk = outcome not in ("Yes", "No", "YES", "NO")

    print(f"[WHALE_MON] Parsed trade: asset={trade.get('asset')!r} token_id={token_id!r} "
          f"outcome={outcome!r} neg_risk={neg_risk} event_slug={event_slug!r} market_slug={market_slug!r}")

    return {
        "type":       "TRADE",
        "wallet":     wallet["address"],
        "alias":      wallet.get("alias", ""),
        "market_id":  market_id,
        "title":      str(title)[:80],
        "side":       outcome.lower(),          # "yes" / "no"
        "action":     f"{side} {outcome}",      # e.g. "BUY YES"
        "size":       size,
        "avg_price":  price,
        "value_usd":  value_usd,
        "trade_id":   _trade_id(trade),
        "timestamp":  _trade_timestamp(trade),
        "token_id":   token_id,
        "neg_risk":   neg_risk,
        "event_slug": event_slug,
        "market_slug": market_slug,
        "all_outcomes": all_outcomes,
    }


def _lookup_title(market_id: str) -> str:
    """
    Try to find the market title in the copy_trading position snapshots
    so we don't need a separate API call per trade.
    """
    if not market_id:
        return ""
    try:
        ct_data = ct._load()
        for wallet in ct_data.get("wallets", {}).values():
            pos = wallet.get("last_positions", {}).get(market_id)
            if pos and pos.get("title"):
                return pos["title"]
    except Exception:
        pass
    return ""


# ── Per-wallet scan ───────────────────────────────────────────────────────────

def _check_wallet(address: str, wallet: dict, last_ids: dict) -> list:
    """
    Fetch recent trades for *address*, return signals for any trades
    that are newer than the last-seen trade ID.
    Updates *last_ids* in place.
    """
    wid    = address.lower()
    trades = fetch_recent_trades(address, limit=10)
    if not trades:
        return []

    last_id = last_ids.get(wid)
    signals = []

    for trade in trades:
        tid = _trade_id(trade)
        if not tid:
            continue
        # Stop when we reach a trade we've already notified about
        if last_id and tid == last_id:
            break
        sig = _parse_trade(trade, wallet)
        if sig:
            signals.append(sig)

    # Always advance the cursor to the newest trade
    newest_id = _trade_id(trades[0]) if trades else None
    if newest_id:
        last_ids[wid] = newest_id

    return signals


# ── Full sweep ────────────────────────────────────────────────────────────────

def scan_followed_wallets() -> list:
    """
    Scan every wallet that has at least one follower.
    Returns a list of new trade signals ready for dispatch.
    """
    monitor_data = _load()
    last_ids     = monitor_data.get("last_trade_ids", {})

    ct_data   = ct._load()
    followers = ct_data.get("followers", {})

    # Build the set of followed wallet IDs (lower-case)
    followed_ids = set()
    for user_follows in followers.values():
        followed_ids.update(user_follows)

    wallets_to_scan = [
        (wid, w)
        for wid, w in ct_data.get("wallets", {}).items()
        if wid in followed_ids and w.get("active", True)
    ]

    print(f"[WHALE_MON] Scanning {len(wallets_to_scan)} followed wallets …")

    all_signals = []
    for wid, wallet in wallets_to_scan:
        try:
            sigs = _check_wallet(wallet["address"], wallet, last_ids)
            all_signals.extend(sigs)
        except Exception as e:
            print(f"[WHALE_MON] Error scanning {wallet.get('alias', wid[:10])}: {e}")
        time.sleep(0.5)  # gentle rate-limiting between wallets

    monitor_data["last_trade_ids"] = last_ids
    monitor_data["last_scan"]      = datetime.now(timezone.utc).isoformat()
    _save(monitor_data)

    if all_signals:
        print(f"[WHALE_MON] {len(all_signals)} new trades detected")
    return all_signals


# ── Market slug lookup ────────────────────────────────────────────────────────

def _lookup_event_slug(condition_id: str) -> str:
    """Try to resolve a condition_id to a Polymarket event slug via Gamma API."""
    if not condition_id:
        return ""
    try:
        url = f"https://gamma-api.polymarket.com/markets?conditionId={condition_id}&limit=1"
        result = _curl_get(url)
        markets = result if isinstance(result, list) else []
        if markets:
            m = markets[0]
            return m.get("eventSlug") or m.get("slug") or ""
    except Exception:
        pass
    return ""


# ── Notification formatting ───────────────────────────────────────────────────

def _fmt_usd(amount: float) -> str:
    try:
        amount = float(amount)
    except Exception:
        return "$0"
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if abs(amount) >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:.0f}"


def _fmt_price(price: float) -> str:
    try:
        price = float(price)
    except Exception:
        return "N/A"
    if price <= 0:
        return "N/A"
    # Polymarket prices are 0-1; display as cents
    return f"{price * 100:.0f}¢"


def format_whale_alert(signal: dict) -> str:
    """Format a trade signal as the canonical 🐋 Whale Alert message."""
    alias   = signal.get("alias") or "Unknown Whale"
    address = signal.get("wallet", "")
    short   = f"{address[:6]}...{address[-4:]}" if len(address) > 12 else address

    action    = signal.get("action", "BUY YES")
    title     = signal.get("title") or "Unknown market"
    value_usd = signal.get("value_usd", 0)
    price     = signal.get("avg_price", 0)
    ts        = signal.get("timestamp", "")

    # Build event URL for inline link
    event_slug = signal.get("event_slug", "") or signal.get("market_slug", "")
    event_link = ""
    if event_slug:
        event_link = f'\n🔗 <a href="https://polymarket.com/event/{event_slug}">View on Polymarket</a>'

    msg = (
        f"🐋 <b>Whale Alert!</b>\n\n"
        f"<b>{alias}</b>\n"
        f"Wallet: <code>{short}</code>\n"
        f"Action: <b>{action}</b>\n"
        f"Market: <i>\"{title}\"</i>\n"
        f"Amount: <b>{_fmt_usd(value_usd)}</b>\n"
        f"Price: <b>{_fmt_price(price)}</b>"
        f"{event_link}"
    )
    if ts:
        msg += f"\n\n<i>🕐 {str(ts)[:19].replace('T', ' ')} UTC</i>"
    return msg


# ── Dispatch ──────────────────────────────────────────────────────────────────

def dispatch_whale_alerts(signals: list) -> int:
    """Send whale alert notifications to all followers of each traded wallet."""
    if not signals:
        return 0

    sent = 0
    for signal in signals:
        wallet_addr = signal.get("wallet", "")
        if not wallet_addr:
            continue

        followers = ct.get_followers_of(wallet_addr)
        if not followers:
            continue

        msg       = format_whale_alert(signal)
        market_id = signal.get("market_id", "")
        outcome   = signal.get("side", "yes").title()  # normalize to Title case (Yes/No)
        question  = signal.get("title", "Unknown market")
        price     = signal.get("avg_price", 0)
        value_usd = signal.get("value_usd", 0)

        # Pull token_id, neg_risk and slugs from trade signal
        token_id    = signal.get("token_id", "")
        neg_risk    = signal.get("neg_risk", False)
        event_slug  = signal.get("event_slug", "")
        market_slug = signal.get("market_slug", "")
        link_slug   = event_slug or market_slug

        print(f"[WHALE_MON] Dispatching signal: wallet={wallet_addr[:10]} token_id={token_id!r} "
              f"neg_risk={neg_risk} event_slug={event_slug!r} market_slug={market_slug!r}")

        # Cache trade details for the Copy Trade flow
        cache_idx = _store_copy_trade(
            slug=market_slug or event_slug or market_id,
            outcome=outcome,
            question=question,
            price=price,
            whale_amount=value_usd,
            token_id=token_id,
            neg_risk=neg_risk,
            event_slug=link_slug,
        )

        # ── Inline buttons ──────────────────────────────────────────────────
        buttons = []
        all_outcomes = signal.get("all_outcomes", [])

        # Row 1 — Clickable Polymarket event link (URL button)
        event_url = ""
        if link_slug:
            event_url = f"https://polymarket.com/event/{link_slug}"
            buttons.append([{"text": "🔗 Open on Polymarket", "url": event_url}])

        # Row 2 — Research Event (CALLBACK button → triggers AI research in bot)
        if link_slug:
            buttons.append([
                {"text": "🔬 Research Event", "callback_data": f"whale_research_{link_slug[:50]}"},
            ])

        # Row 3 — Buy buttons with actual outcome names (matches research buy flow)
        slug_for_buy = market_slug or event_slug or ""
        if slug_for_buy:
            if all_outcomes and len(all_outcomes) >= 2:
                # Multi-outcome — cache each outcome separately
                o1, o2 = all_outcomes[0], all_outcomes[1]
                idx1 = _store_copy_trade(slug=slug_for_buy, outcome=o1, question=question,
                    price=price, whale_amount=value_usd, token_id=token_id, neg_risk=neg_risk, event_slug=event_slug or market_slug)
                idx2 = _store_copy_trade(slug=slug_for_buy, outcome=o2, question=question,
                    price=price, whale_amount=value_usd, token_id="", neg_risk=neg_risk, event_slug=event_slug or market_slug)
                buttons.append([
                    {"text": f"🟩 Buy {o1[:15]}", "callback_data": f"copytrade_{idx1}"},
                    {"text": f"🟥 Buy {o2[:15]}", "callback_data": f"copytrade_{idx2}"},
                ])
            else:
                # Binary market — Yes/No
                opp = "No" if outcome in ("Yes", "YES") else "Yes"
                idx_opp = _store_copy_trade(slug=slug_for_buy, outcome=opp, question=question,
                    price=price, whale_amount=value_usd, token_id="", neg_risk=neg_risk, event_slug=event_slug or market_slug)
                buttons.append([
                    {"text": f"🟩 Buy {outcome[:15]}", "callback_data": f"copytrade_{cache_idx}"},
                    {"text": f"🟥 Buy {opp[:15]}", "callback_data": f"copytrade_{idx_opp}"},
                ])

        # Row 4 — View Trader + My Portfolio
        buttons.append([
            {"text": "👁 View Trader",   "callback_data": f"ct_detail_{wallet_addr[:20]}"},
            {"text": "📋 My Portfolio",  "callback_data": "ct_following"},
        ])

        for chat_id in followers:
            try:
                onboarding.send_inline(chat_id, msg, buttons)
                sent += 1
                time.sleep(0.05)  # Telegram rate-limit
            except Exception as e:
                print(f"[WHALE_MON] Send error → {chat_id}: {e}")

            # Auto-copy execution for Degen subscribers
            try:
                if ce.is_auto_copy_enabled(chat_id):
                    result = ce.execute_copy_trade(chat_id, signal)
                    if result.get("success"):
                        amt = result.get("trade_amount", 0)
                        onboarding.send_inline(
                            chat_id,
                            f"🤖 <b>Auto-Copy Executed!</b>\n\n"
                            f"💰 ${amt:.2f} → {outcome} on "
                            f"{signal.get('title', 'Unknown')[:50]}\n"
                            f"📋 Copying: {wallet_addr[:10]}...",
                            [[
                                {"text": "📊 My Positions", "callback_data": "trading_positions"},
                                {"text": "⚙️ Auto-Copy",    "callback_data": "menu_auto_copy"},
                            ]],
                        )
            except Exception as e:
                print(f"[WHALE_MON] Auto-copy error for {chat_id}: {e}")

    print(f"[WHALE_MON] Dispatched {sent} whale alert notifications")
    return sent


# ── Public scan entry-point ───────────────────────────────────────────────────

def run_monitor_scan() -> list:
    """
    One full sweep: detect new trades → dispatch alerts.
    Called by the background loop or manually for testing.
    """
    try:
        signals = scan_followed_wallets()
        if signals:
            dispatch_whale_alerts(signals)
        return signals
    except Exception as e:
        print(f"[WHALE_MON] Scan error: {e}")
        return []


# ── Background loop ───────────────────────────────────────────────────────────

def monitor_loop():
    """
    Infinite loop — polls followed wallets every SCAN_INTERVAL seconds.
    Start as a daemon thread from main.py:
        threading.Thread(target=whale_monitor.monitor_loop, daemon=True).start()
    """
    print(f"[WHALE_MON] Monitor loop started (interval={SCAN_INTERVAL}s)")
    time.sleep(20)   # stagger startup so other systems initialise first

    while True:
        try:
            run_monitor_scan()
        except Exception as e:
            print(f"[WHALE_MON] Loop error: {e}")
        time.sleep(SCAN_INTERVAL)
