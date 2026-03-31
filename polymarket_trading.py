"""
POLYTRAGENT — Trading Engine
Executes real trades on Polymarket via py-clob-client SDK.
Supports market orders, limit orders, position management.
"""

import os, json, time, logging
from typing import Optional, Tuple
from datetime import datetime, timezone
from polymarket_api import gamma_get
import config as _cfg

log = logging.getLogger("polytragent.trading")

# ═══════════════════════════════════════════════
# CLOB API CONNECTION
# Railway is geoblocked by Polymarket for trading. We use the EU
# proxy at 13.49.25.66 to bypass. The proxy nginx config MUST have
# 'underscores_in_headers on;' to pass POLY_* auth headers.
# ECDSA signing uses the real host, then we switch to proxy for HTTP.
# ═══════════════════════════════════════════════

CLOB_HOST = _cfg.CLOB_BASE        # EU proxy URL for HTTP requests
CLOB_AUTH_HOST = _cfg.CLOB_AUTH_HOST  # Real Polymarket host for signing
CHAIN_ID = 137  # Polygon mainnet

log.info(f"CLOB routing: sign={CLOB_AUTH_HOST} send={CLOB_HOST}")

# ═══════════════════════════════════════════════
# FEE CALCULATION & TRACKING
# ═══════════════════════════════════════════════

def calculate_fee(amount: float, side: str = "BUY") -> Tuple[float, float]:
    """
    Calculate fee and net amount. Returns (net_amount, fee_amount).

    Args:
        amount: The gross amount (in dollars for BUY, in shares for SELL)
        side: "BUY" or "SELL" to determine which fee rate to use

    Returns:
        Tuple of (net_amount, fee_amount)
    """
    fee_key = "TRADE_FEE_BUY" if side == "BUY" else "TRADE_FEE_SELL"
    fee_rate = float(os.environ.get(fee_key, "0.01"))
    fee = round(amount * fee_rate, 6)
    net = round(amount - fee, 6)
    return net, fee


def record_fee(chat_id: str, amount: float, side: str, market: str) -> None:
    """Record a fee collection for tracking."""
    try:
        os.makedirs("data", exist_ok=True)
        fees_file = "data/fees.json"

        fees_data = {}
        if os.path.exists(fees_file):
            with open(fees_file, "r") as f:
                fees_data = json.load(f) if f else {}

        if "total" not in fees_data:
            fees_data["total"] = 0.0
        if "trades" not in fees_data:
            fees_data["trades"] = []

        _, fee_amount = calculate_fee(amount, side)
        fees_data["total"] = round(fees_data["total"] + fee_amount, 6)

        fees_data["trades"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chat_id": chat_id,
            "side": side,
            "amount": amount,
            "fee": fee_amount,
            "market": market,
        })

        with open(fees_file, "w") as f:
            json.dump(fees_data, f, indent=2)
    except Exception as e:
        log.error(f"Failed to record fee: {e}")


def get_total_fees_collected() -> float:
    """Get total fees collected (stored in data/fees.json)."""
    try:
        fees_file = "data/fees.json"
        if os.path.exists(fees_file):
            with open(fees_file, "r") as f:
                data = json.load(f)
                return float(data.get("total", 0.0))
        return 0.0
    except Exception as e:
        log.error(f"Failed to get total fees: {e}")
        return 0.0

# ═══════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════

# ═══════════════════════════════════════════════
# PER-USER CLIENT MANAGEMENT
# Each user trades from their own wallet.
# API keys are auto-derived from the user's private key
# via py-clob-client's create_or_derive_api_creds().
# No manual API key setup needed per user.
# ═══════════════════════════════════════════════

# Cache: chat_id -> {"client": ClobClient, "address": str, "ts": float}
_user_clients = {}
_USER_CLIENT_TTL = 3600  # Re-derive API creds every 1 hour

# Legacy admin client (for read-only operations like orderbook, midpoint)
_admin_client = None
_admin_initialized = False


def _get_admin_client():
    """
    Lazy-init an admin CLOB client for read-only market data.
    Uses env var credentials (POLY_API_KEY etc.) if available,
    otherwise returns None (market data fetched via REST fallback).
    """
    global _admin_client, _admin_initialized
    if _admin_initialized:
        return _admin_client

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        api_key = os.environ.get("POLY_API_KEY", "")
        api_secret = os.environ.get("POLY_API_SECRET", "")
        api_passphrase = os.environ.get("POLY_API_PASSPHRASE", "")
        private_key = os.environ.get("POLY_PRIVATE_KEY", "")

        if not all([api_key, api_secret, api_passphrase, private_key]):
            log.info("No admin CLOB credentials — market data via REST only")
            _admin_initialized = True
            _admin_client = None
            return None

        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        # Init with real host for signing, then switch to proxy for HTTP
        client = ClobClient(
            CLOB_AUTH_HOST, key=private_key, chain_id=CHAIN_ID, signature_type=0,
        )
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
        client.set_api_creds(creds)
        client.host = CLOB_HOST  # Switch to EU proxy for actual requests

        _admin_client = client
        _admin_initialized = True
        log.info("Admin CLOB client initialized (read-only market data)")
        return client

    except Exception as e:
        log.error(f"Admin client init failed: {e}")
        _admin_initialized = True
        _admin_client = None
        return None


def _get_client():
    """Legacy alias — returns admin client for read-only operations."""
    return _get_admin_client()


def _get_client_for_user(chat_id: str):
    """
    Get or create a ClobClient for a specific user.
    Loads the user's private key from wallet_manager,
    then auto-derives Polymarket CLOB API keys.

    Args:
        chat_id: The user's Telegram chat ID

    Returns:
        ClobClient instance or None if user has no wallet
    """
    import wallet_manager

    chat_str = str(chat_id)

    # Check cache
    cached = _user_clients.get(chat_str)
    if cached and (time.time() - cached["ts"]) < _USER_CLIENT_TTL:
        return cached["client"]

    # Get the primary wallet's private key (works for both created and imported wallets)
    pk = wallet_manager.get_primary_private_key(chat_str)
    if not pk:
        all_wallets = wallet_manager._load().get("wallets", {}).get(chat_str, [])
        log.warning(f"No wallet/key for user {chat_str} — found {len(all_wallets)} wallet(s): "
                    f"{[{'addr': w.get('address','?')[:10], 'primary': w.get('is_primary'), 'has_key': bool(w.get('private_key_encrypted'))} for w in all_wallets]}")
        return None

    if not pk.startswith("0x"):
        pk = "0x" + pk

    try:
        from py_clob_client.client import ClobClient

        # Init with real Polymarket host for ECDSA signing + API key derivation
        client = ClobClient(
            CLOB_AUTH_HOST, key=pk, chain_id=CHAIN_ID, signature_type=0,
        )

        # Derive API credentials using real host (L1 auth)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)

        # Switch to EU proxy for all subsequent trade requests (bypass geoblock)
        client.host = CLOB_HOST

        # Get wallet address for logging
        from eth_account import Account
        address = Account.from_key(pk).address

        # Cache the client
        _user_clients[chat_str] = {
            "client": client,
            "address": address,
            "ts": time.time(),
        }

        log.info(f"User client ready: {chat_str} -> {address[:10]}...")
        return client

    except ImportError:
        log.error("py-clob-client not installed. Run: pip install py-clob-client")
        return None
    except Exception as e:
        log.error(f"User client init failed for {chat_str}: {e}", exc_info=True)
        # Store the error so market_buy can surface it to the user
        _user_clients[chat_str] = {"client": None, "error": str(e), "ts": time.time()}
        return None


def get_user_address(chat_id: str) -> Optional[str]:
    """Get cached wallet address for a user, or derive it."""
    chat_str = str(chat_id)
    cached = _user_clients.get(chat_str)
    if cached:
        return cached.get("address")

    # Derive from wallet_manager
    import wallet_manager
    wallet = wallet_manager.get_primary_wallet(chat_str)
    return wallet["address"] if wallet else None


def is_trading_enabled() -> bool:
    """Check if trading is possible (py-clob-client installed)."""
    try:
        from py_clob_client.client import ClobClient
        return True
    except ImportError:
        return False


def is_user_trading_ready(chat_id: str) -> bool:
    """Check if a specific user can trade (has wallet)."""
    import wallet_manager
    return wallet_manager.get_private_key(str(chat_id)) is not None


def check_user_balance(chat_id: str) -> dict:
    """
    Check if user has sufficient USDC + MATIC for trading.
    Returns: {ready, usdc, matic, has_gas, error}
    """
    import wallet_manager

    wallet = wallet_manager.get_primary_wallet(str(chat_id))
    if not wallet:
        return {"ready": False, "error": "No wallet found"}

    balance = wallet_manager.get_full_balance(wallet["address"])
    usdc = balance.get("usdc") or 0
    matic = float(balance.get("matic") or 0)

    return {
        "ready": usdc > 0.5 and matic > 0.005,
        "usdc": usdc,
        "matic": matic,
        "has_gas": matic > 0.005,
        "address": wallet["address"],
        "error": None if usdc > 0.5 else "Insufficient USDC balance",
    }


def reset_client():
    """Force re-initialization of admin client."""
    global _admin_client, _admin_initialized
    _admin_client = None
    _admin_initialized = False


def reset_user_client(chat_id: str):
    """Force re-initialization of a user's client."""
    _user_clients.pop(str(chat_id), None)


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
# USDC APPROVAL FOR POLYMARKET EXCHANGE
# ═══════════════════════════════════════════════

# Polymarket exchange contract addresses on Polygon
_USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
_NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
_MAX_APPROVAL = 2**256 - 1  # Unlimited approval

# Track which users we've already approved for (avoid redundant txns)
_approved_users = {}  # chat_id -> {"ctf": bool, "neg_risk": bool}

_ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]


def _ensure_usdc_allowance(chat_id: str, neg_risk: bool = False):
    """
    Check USDC allowance for Polymarket exchange contracts and approve if needed.
    Must be called before placing any trade.
    """
    import wallet_manager as wm
    from web3 import Web3

    chat_str = str(chat_id)

    # Check in-memory cache first
    cached = _approved_users.get(chat_str, {})
    exchange_key = "neg_risk" if neg_risk else "ctf"
    if cached.get(exchange_key):
        return  # Already approved this session

    exchange_addr = _NEG_RISK_CTF_EXCHANGE if neg_risk else _CTF_EXCHANGE

    pk = wm.get_primary_private_key(chat_str)
    if not pk:
        return
    if not pk.startswith("0x"):
        pk = "0x" + pk

    rpc_url = os.environ.get("POLYGON_RPC", "https://polygon-rpc.com")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    account = w3.eth.account.from_key(pk)
    address = account.address

    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(_USDC_ADDRESS),
        abi=_ERC20_ABI
    )

    # Check current allowance
    current = usdc.functions.allowance(address, Web3.to_checksum_address(exchange_addr)).call()
    min_required = 1_000_000 * 10**6  # $1M in USDC (plenty of headroom)

    if current >= min_required:
        # Already approved
        _approved_users.setdefault(chat_str, {})[exchange_key] = True
        log.info(f"USDC allowance OK for {address[:10]}... on {'neg_risk' if neg_risk else 'ctf'} exchange")
        return

    # Need to approve — send approval transaction
    log.info(f"Setting USDC approval for {address[:10]}... on {'neg_risk' if neg_risk else 'ctf'} exchange")

    nonce = w3.eth.get_transaction_count(address)
    tx = usdc.functions.approve(
        Web3.to_checksum_address(exchange_addr),
        _MAX_APPROVAL
    ).build_transaction({
        "from": address,
        "nonce": nonce,
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID,
    })

    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    if receipt.status == 1:
        _approved_users.setdefault(chat_str, {})[exchange_key] = True
        log.info(f"USDC approved! tx={tx_hash.hex()}")
    else:
        log.error(f"USDC approval tx failed: {tx_hash.hex()}")
        raise Exception(f"USDC approval transaction failed: {tx_hash.hex()}")


# ═══════════════════════════════════════════════
# ORDER EXECUTION
# ═══════════════════════════════════════════════

def market_buy(token_id: str, amount: float, neg_risk: bool = False,
               tick_size: str = "0.01", worst_price: float = None, chat_id: str = None,
               market_question: str = "") -> dict:
    """
    Execute a market BUY order (Fill-or-Kill) from the user's own wallet.

    Args:
        token_id: The token to buy (YES or NO outcome token)
        amount: Dollar amount to spend (USDC) — fee taken from this amount
        neg_risk: True for multi-outcome markets
        tick_size: Price precision ("0.01" or "0.001")
        worst_price: Max price willing to pay (slippage protection)
        chat_id: User's chat ID — REQUIRED for per-user trading
        market_question: Optional market question for fee tracking

    Returns:
        dict with keys: success, order_id, error, details, fee, net_amount
    """
    if not chat_id:
        return {"success": False, "error": "chat_id required for trading"}

    # Check user balance first
    bal = check_user_balance(chat_id)
    if not bal["ready"]:
        if not bal.get("has_gas"):
            return {"success": False, "error": f"Need MATIC for gas. Your balance: {bal.get('matic', 0):.4f} MATIC. Send at least 0.01 MATIC to your wallet."}
        return {"success": False, "error": f"Insufficient USDC. Balance: ${bal.get('usdc', 0):.2f}, needed: ${amount:.2f}"}
    if bal["usdc"] < amount:
        return {"success": False, "error": f"Insufficient USDC. Balance: ${bal['usdc']:.2f}, needed: ${amount:.2f}"}

    client = _get_client_for_user(chat_id)
    if not client:
        # Check if there was a specific init error
        cached_err = _user_clients.get(str(chat_id), {}).get("error")
        if cached_err:
            return {"success": False, "error": f"Wallet init failed: {cached_err}"}
        import wallet_manager as _wm
        _wm_data = _wm._load()
        _has_wallet = bool(_wm_data.get("wallets", {}).get(str(chat_id)))
        if _has_wallet:
            return {"success": False, "error": "Wallet found but CLOB client init failed. Possible network issue with Polymarket API."}
        return {"success": False, "error": "No wallet found. Use /start to create one, or /import_wallet to restore an existing wallet with your private key."}

    # ── Auto-approve USDC for Polymarket exchange contracts ──
    try:
        _ensure_usdc_allowance(chat_id, neg_risk)
    except Exception as e:
        log.warning(f"Auto-approve USDC failed (continuing anyway): {e}")

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        # Calculate fee (1% of requested amount)
        net_amount, fee_amount = calculate_fee(amount, side="BUY")

        from py_clob_client.clob_types import PartialCreateOrderOptions
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

        # Build market order args with net amount
        kwargs = {
            "token_id": token_id,
            "amount": net_amount,
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

        # Record fee if trade was successful
        if success and chat_id:
            record_fee(chat_id, amount, "BUY", market_question)

        return {
            "success": success,
            "order_id": resp.get("orderID", "") if isinstance(resp, dict) else "",
            "error": resp.get("errorMsg", "") if isinstance(resp, dict) else str(resp),
            "details": resp,
            "fee": fee_amount,
            "net_amount": net_amount,
        }
    except Exception as e:
        log.error(f"Market buy error: {e}")
        return {"success": False, "error": str(e)}


def market_sell(token_id: str, amount: float, neg_risk: bool = False,
                tick_size: str = "0.01", worst_price: float = None, chat_id: str = None,
                market_question: str = "") -> dict:
    """
    Execute a market SELL order (Fill-or-Kill) from the user's own wallet.

    Args:
        token_id: The token to sell
        amount: Number of shares to sell
        neg_risk: True for multi-outcome markets
        tick_size: Price precision
        worst_price: Min price willing to accept
        chat_id: User's chat ID — REQUIRED for per-user trading
        market_question: Optional market question for fee tracking

    Returns:
        dict with keys: success, order_id, error, details, fee, proceeds
    """
    if not chat_id:
        return {"success": False, "error": "chat_id required for trading"}

    client = _get_client_for_user(chat_id)
    if not client:
        return {"success": False, "error": "No wallet found. Use /start to create one, or /import_wallet to restore an existing wallet with your private key."}

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        from py_clob_client.clob_types import PartialCreateOrderOptions
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

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

        # For SELL, fee is calculated on the proceeds
        # We estimate proceeds as shares * best_bid (from order book)
        # For now, we'll calculate fee after receiving actual proceeds
        fee_amount = 0.0
        if success and chat_id:
            # Fee will be ~1% of the gross sale proceeds
            # Actual proceeds depend on execution price
            record_fee(chat_id, amount, "SELL", market_question)
            _, fee_amount = calculate_fee(amount, side="SELL")

        return {
            "success": success,
            "order_id": resp.get("orderID", "") if isinstance(resp, dict) else "",
            "error": resp.get("errorMsg", "") if isinstance(resp, dict) else str(resp),
            "details": resp,
            "fee": fee_amount,
            "proceeds": round(amount - fee_amount, 6),
        }
    except Exception as e:
        log.error(f"Market sell error: {e}")
        return {"success": False, "error": str(e)}


def limit_buy(token_id: str, price: float, size: float, neg_risk: bool = False,
              tick_size: str = "0.01", expiration: int = None, chat_id: str = None) -> dict:
    """
    Place a limit BUY order (GTC or GTD) from user's wallet.

    Args:
        token_id: The token to buy
        price: Limit price (0.01 - 0.99)
        size: Number of shares
        neg_risk: True for multi-outcome markets
        tick_size: Price precision
        expiration: Unix timestamp for GTD orders (None = GTC)
        chat_id: User's chat ID — REQUIRED

    Returns:
        dict with keys: success, order_id, error, details
    """
    if not chat_id:
        return {"success": False, "error": "chat_id required for trading"}

    client = _get_client_for_user(chat_id)
    if not client:
        return {"success": False, "error": "No wallet found. Use /start to create one, or /import_wallet to restore an existing wallet with your private key."}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        from py_clob_client.clob_types import PartialCreateOrderOptions
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

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
               tick_size: str = "0.01", expiration: int = None, chat_id: str = None) -> dict:
    """
    Place a limit SELL order (GTC or GTD) from user's wallet.
    """
    if not chat_id:
        return {"success": False, "error": "chat_id required for trading"}

    client = _get_client_for_user(chat_id)
    if not client:
        return {"success": False, "error": "No wallet found. Use /start to create one, or /import_wallet to restore an existing wallet with your private key."}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        from py_clob_client.clob_types import PartialCreateOrderOptions
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

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

def get_open_orders(market_id: str = None, chat_id: str = None) -> list:
    """Get all open orders for a user, optionally filtered by market."""
    client = _get_client_for_user(chat_id) if chat_id else _get_client()
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


def cancel_order(order_id: str, chat_id: str = None) -> dict:
    """Cancel a specific order by ID."""
    client = _get_client_for_user(chat_id) if chat_id else _get_client()
    if not client:
        return {"success": False, "error": "Wallet not ready"}
    try:
        resp = client.cancel(order_id)
        return {"success": True, "details": resp}
    except Exception as e:
        log.error(f"Cancel order error: {e}")
        return {"success": False, "error": str(e)}


def cancel_all_orders(chat_id: str = None) -> dict:
    """Cancel all open orders for a user."""
    client = _get_client_for_user(chat_id) if chat_id else _get_client()
    if not client:
        return {"success": False, "error": "Wallet not ready"}
    try:
        resp = client.cancel_all()
        return {"success": True, "details": resp}
    except Exception as e:
        log.error(f"Cancel all error: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# POSITIONS & BALANCE
# ═══════════════════════════════════════════════

def get_positions(chat_id: str = None) -> list:
    """Get all current positions for a user's wallet."""
    address = get_user_address(chat_id) if chat_id else None

    # Fallback to admin wallet if no chat_id
    if not address:
        try:
            from eth_account import Account
            pk = os.environ.get("POLY_PRIVATE_KEY", "")
            if pk:
                if not pk.startswith("0x"):
                    pk = "0x" + pk
                address = Account.from_key(pk).address
        except:
            pass

    if not address:
        return []

    try:
        import requests as req
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


def get_trade_history(limit: int = 20, chat_id: str = None) -> list:
    """Get recent trade history for a user's wallet."""
    address = get_user_address(chat_id) if chat_id else None

    if not address:
        try:
            from eth_account import Account
            pk = os.environ.get("POLY_PRIVATE_KEY", "")
            if pk:
                if not pk.startswith("0x"):
                    pk = "0x" + pk
                address = Account.from_key(pk).address
        except:
            pass

    if not address:
        return []

    try:
        import requests as req
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


def get_wallet_address(chat_id: str = None) -> Optional[str]:
    """Get wallet address for a user, or the admin wallet."""
    if chat_id:
        return get_user_address(chat_id)
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
        # Clean input — handle full URLs
        slug = market_slug_or_id.strip()
        if "polymarket.com" in slug:
            # Extract slug from URL
            parts = slug.rstrip("/").split("/")
            slug = parts[-1] if parts else slug
            # Remove query params
            slug = slug.split("?")[0]

        # Try as slug first
        data = gamma_get("/markets", params={"slug": slug})
        markets = []
        if data:
            markets = data if isinstance(data, list) else []

        # If no results, try as condition_id
        if not markets:
            m = gamma_get(f"/markets/{slug}")
            if m:
                markets = [m]

        # Try as event slug (returns multiple markets)
        if not markets:
            events = gamma_get("/events", params={"slug": slug})
            if events:
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

def check_and_set_allowances(chat_id: str = None) -> dict:
    """
    Check and set token allowances for Polymarket exchange contracts.
    Required for EOA wallets before trading.
    Returns status dict.
    """
    client = _get_client_for_user(chat_id) if chat_id else _get_client()
    if not client:
        return {"success": False, "error": "Wallet not ready"}
    try:
        ok = client.get_ok()
        return {"success": True, "status": "connected", "health": ok}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# CONVENIENCE: ONE-SHOT TRADE
# ═══════════════════════════════════════════════

def quick_buy(market_slug_or_url: str, outcome: str, amount: float, chat_id: str = None) -> dict:
    """
    One-shot: resolve market + buy the specified outcome.

    Args:
        market_slug_or_url: Polymarket URL, slug, or market ID
        outcome: "Yes" or "No" (case-insensitive)
        amount: Dollar amount to spend (fee taken from this amount)
        chat_id: Optional chat ID for fee tracking

    Returns:
        dict with success, order_id, market info, error, fee, net_amount
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
        chat_id=chat_id,
        market_question=market["question"],
    )
    result["market"] = market["question"]
    result["outcome"] = outcome
    result["amount"] = amount
    return result


def quick_sell(market_slug_or_url: str, outcome: str, shares: float, chat_id: str = None) -> dict:
    """
    One-shot: resolve market + sell the specified outcome shares.

    Args:
        market_slug_or_url: Polymarket URL, slug, or market ID
        outcome: "Yes" or "No" (case-insensitive)
        shares: Number of shares to sell
        chat_id: Optional chat ID for fee tracking

    Returns:
        dict with success, order_id, market info, error, fee, proceeds
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
        chat_id=chat_id,
        market_question=market["question"],
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
        if result.get("fee") is not None:
            lines.append(f"💸 Fee (1%): <b>${result['fee']:.2f}</b>")
        if result.get("net_amount") is not None:
            lines.append(f"📦 Executed: <b>${result['net_amount']:.2f}</b>")
        if result.get("shares"):
            lines.append(f"📦 Shares: <b>{result['shares']:.2f}</b>")
        if result.get("proceeds") is not None:
            lines.append(f"💵 Proceeds (after fee): <b>${result['proceeds']:.2f}</b>")
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
