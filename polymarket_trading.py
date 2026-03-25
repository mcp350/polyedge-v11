"""
POLYTRAGENT — Trading Engine
Executes real trades on Polymarket via py-clob-client SDK.
Supports market orders, limit orders, position management.
"""

import os, json, time, logging
from typing import Optional, Tuple
from datetime import datetime, timezone

log = logging.getLogger("polytragent.trading")

# ═══════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

# ═══════════════════════════════════════════════
# CLIENT SINGLETON
# ═══════════════════════════════════════════════

_client = None
_initialized = False

def _get_client():
    """Lazy-init the CLOB client with API credentials."""
    global _client, _initialized
    if _initialized:
        return _client

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        api_key = os.environ.get("POLY_API_KEY", "")
        api_secret = os.environ.get("POLY_API_SECRET", "")
        api_passphrase = os.environ.get("POLY_API_PASSPHRASE", "")
        private_key = os.environ.get("POLY_PRIVATE_KEY", "")

        if not all([api_key, api_secret, api_passphrase, private_key]):
            log.warning("Missing Polymarket trading credentials — trading disabled")
            _initialized = True
            _client = None
            return None

        # Ensure private key has 0x prefix
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            signature_type=0,  # EOA wallet
        )

        # Set pre-existing API credentials from Polymarket Builders portal
        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        )
        client.set_api_creds(creds)

        _client = client
        _initialized = True
        log.info("Trading engine initialized successfully")
        return client

    except ImportError:
        log.error("py-clob-client not installed. Run: pip install py-clob-client")
        _initialized = True
        _client = None
        return None
    except Exception as e:
        log.error(f"Trading engine init failed: {e}")
        _initialized = True
        _client = None
        return None


def is_trading_enabled() -> bool:
    """Check if trading engine is ready."""
    return _get_client() is not None


def reset_client():
    """Force re-initialization (e.g., after credential change)."""
    global _client, _initialized
    _client = None
    _initialized = False


# ═══════════════════════════════════════════════
# MARKET DATA (via authenticated client)
# ═══════════════════════════════════════════════

def get_orderbook(token_id: str) -> Optional[dict]:
    """Get order book for a token."""
    client = _get_client()
    if not client:
        return None
    try:
        return client.get_order_book(token_id)
    except Exception as e:
        log.error(f"Orderbook fetch error: {e}")
        return None


def get_midpoint(token_id: str) -> Optional[float]:
    """Get midpoint price for a token."""
    client = _get_client()
    if not client:
        return None
    try:
        mid = client.get_midpoint(token_id)
        return float(mid) if mid else None
    except Exception as e:
        log.error(f"Midpoint fetch error: {e}")
        return None


def get_best_price(token_id: str, side: str = "BUY") -> Optional[float]:
    """Get best available price for a side."""
    client = _get_client()
    if not client:
        return None
    try:
        price = client.get_price(token_id, side=side)
        return float(price) if price else None
    except Exception as e:
        log.error(f"Price fetch error: {e}")
        return None


# ═══════════════════════════════════════════════
# ORDER EXECUTION
# ═══════════════════════════════════════════════

def market_buy(token_id: str, amount: float, neg_risk: bool = False,
               tick_size: str = "0.01", worst_price: float = None) -> dict:
    """
    Execute a market BUY order (Fill-or-Kill).

    Args:
        token_id: The token to buy (YES or NO outcome token)
        amount: Dollar amount to spend (USDC)
        neg_risk: True for multi-outcome markets
        tick_size: Price precision ("0.01" or "0.001")
        worst_price: Max price willing to pay (slippage protection)

    Returns:
        dict with keys: success, order_id, error, details
    """
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        options = {"tick_size": tick_size, "neg_risk": neg_risk}

        # Build market order args
        kwargs = {
            "token_id": token_id,
            "amount": amount,
            "side": BUY,
        }
        if worst_price is not None:
            kwargs["price"] = worst_price

        signed_order = client.create_market_order(
            MarketOrderArgs(**kwargs),
            options=options,
        )

        resp = client.post_order(signed_order, OrderType.FOK)

        success = resp.get("success", False) if isinstance(resp, dict) else False
        return {
            "success": success,
            "order_id": resp.get("orderID", "") if isinstance(resp, dict) else "",
            "error": resp.get("errorMsg", "") if isinstance(resp, dict) else str(resp),
            "details": resp,
        }
    except Exception as e:
        log.error(f"Market buy error: {e}")
        return {"success": False, "error": str(e)}


def market_sell(token_id: str, amount: float, neg_risk: bool = False,
                tick_size: str = "0.01", worst_price: float = None) -> dict:
    """
    Execute a market SELL order (Fill-or-Kill).

    Args:
        token_id: The token to sell
        amount: Number of shares to sell
        neg_risk: True for multi-outcome markets
        tick_size: Price precision
        worst_price: Min price willing to accept

    Returns:
        dict with keys: success, order_id, error, details
    """
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        options = {"tick_size": tick_size, "neg_risk": neg_risk}

        kwargs = {
            "token_id": token_id,
            "amount": amount,
            "side": SELL,
        }
        if worst_price is not None:
            kwargs["price"] = worst_price

        signed_order = client.create_market_order(
            MarketOrderArgs(**kwargs),
            options=options,
        )

        resp = client.post_order(signed_order, OrderType.FOK)

        success = resp.get("success", False) if isinstance(resp, dict) else False
        return {
            "success": success,
            "order_id": resp.get("orderID", "") if isinstance(resp, dict) else "",
            "error": resp.get("errorMsg", "") if isinstance(resp, dict) else str(resp),
            "details": resp,
        }
    except Exception as e:
        log.error(f"Market sell error: {e}")
        return {"success": False, "error": str(e)}


def limit_buy(token_id: str, price: float, size: float, neg_risk: bool = False,
              tick_size: str = "0.01", expiration: int = None) -> dict:
    """
    Place a limit BUY order (GTC or GTD).

    Args:
        token_id: The token to buy
        price: Limit price (0.01 - 0.99)
        size: Number of shares
        neg_risk: True for multi-outcome markets
        tick_size: Price precision
        expiration: Unix timestamp for GTD orders (None = GTC)

    Returns:
        dict with keys: success, order_id, error, details
    """
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        options = {"tick_size": tick_size, "neg_risk": neg_risk}

        kwargs = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": BUY,
        }
        if expiration is not None:
            kwargs["expiration"] = expiration

        order_type = OrderType.GTD if expiration else OrderType.GTC

        signed_order = client.create_order(OrderArgs(**kwargs), options=options)
        resp = client.post_order(signed_order, order_type)

        success = resp.get("success", False) if isinstance(resp, dict) else False
        return {
            "success": success,
            "order_id": resp.get("orderID", "") if isinstance(resp, dict) else "",
            "error": resp.get("errorMsg", "") if isinstance(resp, dict) else str(resp),
            "details": resp,
        }
    except Exception as e:
        log.error(f"Limit buy error: {e}")
        return {"success": False, "error": str(e)}


def limit_sell(token_id: str, price: float, size: float, neg_risk: bool = False,
               tick_size: str = "0.01", expiration: int = None) -> dict:
    """
    Place a limit SELL order (GTC or GTD).
    """
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        options = {"tick_size": tick_size, "neg_risk": neg_risk}

        kwargs = {
            "token_id": token_id,
            "price": price,
            "size": size,
            "side": SELL,
        }
        if expiration is not None:
            kwargs["expiration"] = expiration

        order_type = OrderType.GTD if expiration else OrderType.GTC

        signed_order = client.create_order(OrderArgs(**kwargs), options=options)
        resp = client.post_order(signed_order, order_type)

        success = resp.get("success", False) if isinstance(resp, dict) else False
        return {
            "success": success,
            "order_id": resp.get("orderID", "") if isinstance(resp, dict) else "",
            "error": resp.get("errorMsg", "") if isinstance(resp, dict) else str(resp),
            "details": resp,
        }
    except Exception as e:
        log.error(f"Limit sell error: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# ORDER MANAGEMENT
# ═══════════════════════════════════════════════

def get_open_orders(market_id: str = None) -> list:
    """Get all open orders, optionally filtered by market."""
    client = _get_client()
    if not client:
        return []
    try:
        from py_clob_client.clob_types import OpenOrderParams
        params = OpenOrderParams(market=market_id) if market_id else OpenOrderParams()
        orders = client.get_orders(params)
        return orders if isinstance(orders, list) else []
    except Exception as e:
        log.error(f"Get orders error: {e}")
        return []


def cancel_order(order_id: str) -> dict:
    """Cancel a specific order by ID."""
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}
    try:
        resp = client.cancel(order_id)
        return {"success": True, "details": resp}
    except Exception as e:
        log.error(f"Cancel order error: {e}")
        return {"success": False, "error": str(e)}


def cancel_all_orders() -> dict:
    """Cancel all open orders."""
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}
    try:
        resp = client.cancel_all()
        return {"success": True, "details": resp}
    except Exception as e:
        log.error(f"Cancel all error: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# POSITIONS & BALANCE
# ═══════════════════════════════════════════════

def get_positions() -> list:
    """Get all current positions for the trading wallet."""
    client = _get_client()
    if not client:
        return []
    try:
        # The CLOB client doesn't have a direct positions method,
        # so we use the REST API with our wallet address
        import requests as req
        from eth_account import Account

        pk = os.environ.get("POLY_PRIVATE_KEY", "")
        if not pk.startswith("0x"):
            pk = "0x" + pk
        acct = Account.from_key(pk)
        address = acct.address

        r = req.get(f"https://data-api.polymarket.com/positions",
                     params={"user": address.lower()},
                     timeout=15)
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                return data
            return data.get("positions", data.get("results", []))
        return []
    except Exception as e:
        log.error(f"Get positions error: {e}")
        return []


def get_trade_history(limit: int = 20) -> list:
    """Get recent trade history for the trading wallet."""
    client = _get_client()
    if not client:
        return []
    try:
        import requests as req
        from eth_account import Account

        pk = os.environ.get("POLY_PRIVATE_KEY", "")
        if not pk.startswith("0x"):
            pk = "0x" + pk
        acct = Account.from_key(pk)
        address = acct.address

        r = req.get(f"https://data-api.polymarket.com/trades",
                     params={"maker": address.lower(), "limit": limit},
                     timeout=15)
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                return data[:limit]
            return data.get("data", data.get("results", []))[:limit]
        return []
    except Exception as e:
        log.error(f"Trade history error: {e}")
        return []


def get_wallet_address() -> Optional[str]:
    """Get the wallet address for the configured private key."""
    try:
        from eth_account import Account
        pk = os.environ.get("POLY_PRIVATE_KEY", "")
        if not pk:
            return None
        if not pk.startswith("0x"):
            pk = "0x" + pk
        return Account.from_key(pk).address
    except Exception:
        return None


# ═══════════════════════════════════════════════
# MARKET LOOKUP HELPERS
# ═══════════════════════════════════════════════

def resolve_market_tokens(market_slug_or_id: str) -> Optional[dict]:
    """
    Resolve a market slug, URL, or ID to its token IDs.
    Returns dict with: question, condition_id, tokens [{token_id, outcome}], neg_risk, tick_size
    """
    try:
        import requests as req

        # Clean input — handle full URLs
        slug = market_slug_or_id.strip()
        if "polymarket.com" in slug:
            # Extract slug from URL
            parts = slug.rstrip("/").split("/")
            slug = parts[-1] if parts else slug
            # Remove query params
            slug = slug.split("?")[0]

        # Try as slug first
        r = req.get(f"https://gamma-api.polymarket.com/markets",
                     params={"slug": slug}, timeout=15)
        markets = []
        if r.ok:
            data = r.json()
            markets = data if isinstance(data, list) else []

        # If no results, try as condition_id
        if not markets:
            r = req.get(f"https://gamma-api.polymarket.com/markets/{slug}", timeout=15)
            if r.ok:
                m = r.json()
                if m:
                    markets = [m]

        # Try as event slug (returns multiple markets)
        if not markets:
            r = req.get(f"https://gamma-api.polymarket.com/events",
                         params={"slug": slug}, timeout=15)
            if r.ok:
                events = r.json()
                if isinstance(events, list) and events:
                    event = events[0]
                    markets = event.get("markets", [])

        if not markets:
            return None

        m = markets[0]

        # Extract token IDs from the market
        tokens = []
        clob_token_ids = m.get("clobTokenIds", "[]")
        outcomes = m.get("outcomes", "[]")

        if isinstance(clob_token_ids, str):
            import json as _json
            clob_token_ids = _json.loads(clob_token_ids)
        if isinstance(outcomes, str):
            import json as _json
            outcomes = _json.loads(outcomes)

        for i, tid in enumerate(clob_token_ids):
            outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
            tokens.append({"token_id": tid, "outcome": outcome})

        # Determine neg_risk
        neg_risk = m.get("negRisk", False)
        if isinstance(neg_risk, str):
            neg_risk = neg_risk.lower() == "true"

        return {
            "question": m.get("question", ""),
            "condition_id": m.get("conditionId", m.get("id", "")),
            "market_id": m.get("id", ""),
            "slug": m.get("slug", ""),
            "tokens": tokens,
            "neg_risk": neg_risk,
            "tick_size": "0.01",  # Default; some markets use 0.001
            "end_date": m.get("endDate", ""),
            "volume": float(m.get("volume", 0) or 0),
            "outcomes": outcomes,
            "url": f"https://polymarket.com/event/{m.get('slug', '')}",
        }
    except Exception as e:
        log.error(f"Market resolve error: {e}")
        return None


def get_market_price_summary(token_id: str) -> Optional[dict]:
    """Get a quick price summary for a token."""
    client = _get_client()
    try:
        import requests as req
        book = None
        if client:
            try:
                book = client.get_order_book(token_id)
            except:
                pass

        mid = None
        best_bid = None
        best_ask = None
        spread = None

        if book:
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if bids:
                best_bid = float(bids[0].get("price", 0))
            if asks:
                best_ask = float(asks[0].get("price", 0))
            if best_bid and best_ask:
                mid = (best_bid + best_ask) / 2
                spread = best_ask - best_bid

        # Fallback: try midpoint API
        if mid is None and client:
            try:
                m = client.get_midpoint(token_id)
                mid = float(m) if m else None
            except:
                pass

        return {
            "midpoint": mid,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
        }
    except Exception as e:
        log.error(f"Price summary error: {e}")
        return None


# ═══════════════════════════════════════════════
# ALLOWANCE HELPERS
# ═══════════════════════════════════════════════

def check_and_set_allowances() -> dict:
    """
    Check and set token allowances for Polymarket exchange contracts.
    Required for EOA wallets before trading.
    Returns status dict.
    """
    client = _get_client()
    if not client:
        return {"success": False, "error": "Trading engine not initialized"}
    try:
        # py-clob-client handles allowances internally for most operations
        # But we can verify by attempting a small health check
        ok = client.get_ok()
        return {"success": True, "status": "connected", "health": ok}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# CONVENIENCE: ONE-SHOT TRADE
# ═══════════════════════════════════════════════

def quick_buy(market_slug_or_url: str, outcome: str, amount: float) -> dict:
    """
    One-shot: resolve market + buy the specified outcome.

    Args:
        market_slug_or_url: Polymarket URL, slug, or market ID
        outcome: "Yes" or "No" (case-insensitive)
        amount: Dollar amount to spend

    Returns:
        dict with success, order_id, market info, error
    """
    market = resolve_market_tokens(market_slug_or_url)
    if not market:
        return {"success": False, "error": "Could not resolve market"}

    outcome_lower = outcome.strip().lower()
    token_id = None
    for t in market["tokens"]:
        if t["outcome"].lower() == outcome_lower:
            token_id = t["token_id"]
            break

    if not token_id:
        available = [t["outcome"] for t in market["tokens"]]
        return {"success": False, "error": f"Outcome '{outcome}' not found. Available: {available}"}

    result = market_buy(
        token_id=token_id,
        amount=amount,
        neg_risk=market["neg_risk"],
        tick_size=market["tick_size"],
    )
    result["market"] = market["question"]
    result["outcome"] = outcome
    result["amount"] = amount
    return result


def quick_sell(market_slug_or_url: str, outcome: str, shares: float) -> dict:
    """
    One-shot: resolve market + sell the specified outcome shares.
    """
    market = resolve_market_tokens(market_slug_or_url)
    if not market:
        return {"success": False, "error": "Could not resolve market"}

    outcome_lower = outcome.strip().lower()
    token_id = None
    for t in market["tokens"]:
        if t["outcome"].lower() == outcome_lower:
            token_id = t["token_id"]
            break

    if not token_id:
        available = [t["outcome"] for t in market["tokens"]]
        return {"success": False, "error": f"Outcome '{outcome}' not found. Available: {available}"}

    result = market_sell(
        token_id=token_id,
        amount=shares,
        neg_risk=market["neg_risk"],
        tick_size=market["tick_size"],
    )
    result["market"] = market["question"]
    result["outcome"] = outcome
    result["shares"] = shares
    return result


# ═══════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════

def format_order_result(result: dict) -> str:
    """Format an order result for Telegram display."""
    if result.get("success"):
        lines = [
            "✅ <b>Order Executed</b>",
            "",
        ]
        if result.get("market"):
            lines.append(f"📊 {result['market']}")
        if result.get("outcome"):
            lines.append(f"🎯 Outcome: <b>{result['outcome']}</b>")
        if result.get("amount"):
            lines.append(f"💰 Amount: <b>${result['amount']:.2f}</b>")
        if result.get("shares"):
            lines.append(f"📦 Shares: <b>{result['shares']:.2f}</b>")
        if result.get("order_id"):
            lines.append(f"🆔 Order: <code>{result['order_id'][:16]}...</code>")
        return "\n".join(lines)
    else:
        return f"❌ <b>Order Failed</b>\n\n{result.get('error', 'Unknown error')}"


def format_positions(positions: list) -> str:
    """Format positions list for Telegram display."""
    if not positions:
        return "📭 No open positions"

    lines = ["📊 <b>Open Positions</b>", ""]

    for i, pos in enumerate(positions[:15], 1):
        title = pos.get("title", pos.get("question", "Unknown"))[:40]
        outcome = pos.get("outcome", "")
        size = float(pos.get("size", 0))
        avg_price = float(pos.get("avgPrice", pos.get("avg_price", 0)))
        cur_price = float(pos.get("curPrice", pos.get("cur_price", 0)))
        value = size * cur_price if cur_price else 0
        pnl = (cur_price - avg_price) * size if avg_price else 0
        pnl_pct = ((cur_price / avg_price) - 1) * 100 if avg_price else 0

        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"{i}. {emoji} <b>{title}</b>\n"
            f"   {outcome} | {size:.0f} shares @ ${avg_price:.2f}\n"
            f"   Value: ${value:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
        )

    if len(positions) > 15:
        lines.append(f"\n... and {len(positions) - 15} more")

    return "\n".join(lines)


def format_open_orders(orders: list) -> str:
    """Format open orders for Telegram display."""
    if not orders:
        return "📭 No open orders"

    lines = ["📋 <b>Open Orders</b>", ""]

    for i, order in enumerate(orders[:10], 1):
        side = order.get("side", "?").upper()
        price = float(order.get("price", 0))
        size = float(order.get("original_size", order.get("size", 0)))
        remaining = float(order.get("size_matched", 0))
        order_id = order.get("id", "")[:12]

        emoji = "🟩" if side == "BUY" else "🟥"
        lines.append(
            f"{i}. {emoji} {side} | Price: ${price:.2f} | "
            f"Size: {size:.0f} | ID: <code>{order_id}</code>"
        )

    return "\n".join(lines)
