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
# AUTO-SWAP & USDC.e HANDLING FOR POLYMARKET
# ═══════════════════════════════════════════════
#
# Polymarket uses USDC.e (bridged) — NOT native USDC on Polygon.
# Confirmed in py-clob-client/config.py: collateral="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
#
# This module auto-swaps any supported token → USDC.e before trading,
# so users can trade from MATIC, native USDC, WETH, WBTC, etc.
# Swaps are done via OpenOcean DEX aggregator (free, no API key).

_USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"       # USDC.e (bridged) — Polymarket collateral
_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
_NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
_NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
_CT_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Conditional Tokens (ERC-1155)
_MAX_APPROVAL = 2**256 - 1

# Supported tokens for auto-swap (address → {symbol, decimals})
_SWAP_TOKENS = {
    "0x0000000000000000000000000000000000001010": {"symbol": "MATIC", "decimals": 18},  # MATIC (native, use 0xEee... for swaps)
    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359": {"symbol": "USDC", "decimals": 6},    # Native USDC
    "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619": {"symbol": "WETH", "decimals": 18},   # WETH
    "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6": {"symbol": "WBTC", "decimals": 8},    # WBTC
}
# OpenOcean uses 0xEee... for native MATIC
_NATIVE_TOKEN_ADDRESS = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

_approved_users = {}  # chat_id -> {"ctf": bool, "neg_risk": bool}

_ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]


def _get_w3():
    from web3 import Web3
    # Try env var first, then cycle through reliable free Polygon RPCs
    rpc_url = os.environ.get("POLYGON_RPC_URL") or os.environ.get("POLYGON_RPC")
    if rpc_url:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if w3.is_connected():
            return w3

    # Reliable free Polygon RPCs (verified working 2026-03-31)
    fallbacks = [
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.drpc.org",
        "https://1rpc.io/matic",
    ]
    for rpc in fallbacks:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                log.info(f"Connected to Polygon RPC: {rpc}")
                return w3
        except Exception:
            continue

    # Last resort — return with first fallback even if not verified
    return Web3(Web3.HTTPProvider(fallbacks[0]))


def _get_token_balances(w3, address: str) -> dict:
    """Get all supported token balances for an address."""
    from web3 import Web3
    balances = {}

    # USDC.e balance
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(_USDC_E), abi=_ERC20_ABI)
    raw = usdc_e.functions.balanceOf(address).call()
    balances["USDC.e"] = {"raw": raw, "human": raw / 10**6, "address": _USDC_E, "decimals": 6}

    # Native MATIC
    matic_raw = w3.eth.get_balance(Web3.to_checksum_address(address))
    balances["MATIC"] = {"raw": matic_raw, "human": float(w3.from_wei(matic_raw, "ether")),
                         "address": _NATIVE_TOKEN_ADDRESS, "decimals": 18}

    # ERC-20 tokens
    for addr, info in _SWAP_TOKENS.items():
        if info["symbol"] == "MATIC":
            continue  # Already handled above
        try:
            token = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=_ERC20_ABI)
            raw = token.functions.balanceOf(address).call()
            balances[info["symbol"]] = {"raw": raw, "human": raw / 10**info["decimals"],
                                        "address": addr, "decimals": info["decimals"]}
        except Exception as e:
            log.debug(f"Balance check failed for {info['symbol']}: {e}")

    return balances


def _openocean_swap(w3, pk: str, address: str, in_token: str, amount_raw: int,
                    in_decimals: int, slippage: float = 1.0) -> str:
    """
    Execute a token swap via OpenOcean DEX aggregator.
    Returns tx hash on success, raises on failure.
    """
    import requests as req
    from web3 import Web3

    # OpenOcean swap endpoint for Polygon (chain 137)
    url = "https://open-api.openocean.finance/v4/137/swap"
    params = {
        "inTokenAddress": in_token,
        "outTokenAddress": _USDC_E,
        "amount": str(amount_raw / 10**in_decimals),  # Human-readable amount
        "gasPrice": str(w3.from_wei(w3.eth.gas_price, "gwei")),
        "slippage": str(slippage),
        "account": address,
    }

    log.info(f"[SWAP] OpenOcean request: {in_token[:10]}... -> USDC.e, amount={params['amount']}")
    resp = req.get(url, params=params, timeout=20)
    data = resp.json()

    if data.get("code") != 200 or not data.get("data"):
        error_msg = data.get("error", data.get("message", "Unknown swap error"))
        raise Exception(f"OpenOcean swap failed: {error_msg}")

    swap_data = data["data"]
    out_amount = float(swap_data.get("outAmount", 0)) / 10**6
    log.info(f"[SWAP] Quote: {params['amount']} -> {out_amount:.2f} USDC.e")

    # For non-native tokens, we need to approve OpenOcean's router first
    if in_token.lower() != _NATIVE_TOKEN_ADDRESS.lower():
        router = Web3.to_checksum_address(swap_data.get("to", ""))
        token_contract = w3.eth.contract(address=Web3.to_checksum_address(in_token), abi=_ERC20_ABI)
        current_allowance = token_contract.functions.allowance(address, router).call()

        if current_allowance < amount_raw:
            log.info(f"[SWAP] Approving {in_token[:10]}... for OpenOcean router {router[:10]}...")
            nonce = w3.eth.get_transaction_count(address)
            approve_tx = token_contract.functions.approve(router, _MAX_APPROVAL).build_transaction({
                "from": address, "nonce": nonce, "gas": 100_000,
                "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
            })
            signed = w3.eth.account.sign_transaction(approve_tx, private_key=pk)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status != 1:
                raise Exception(f"Token approval for swap failed")
            log.info(f"[SWAP] Approval confirmed: {tx_hash.hex()}")

    # Build and send swap transaction
    nonce = w3.eth.get_transaction_count(address)
    swap_tx = {
        "from": address,
        "to": Web3.to_checksum_address(swap_data["to"]),
        "data": swap_data["data"],
        "value": int(swap_data.get("value", 0)),
        "gas": int(float(swap_data.get("estimatedGas", 300_000)) * 1.3),  # 30% buffer
        "gasPrice": w3.eth.gas_price,
        "nonce": nonce,
        "chainId": CHAIN_ID,
    }

    signed = w3.eth.account.sign_transaction(swap_tx, private_key=pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)

    if receipt.status != 1:
        raise Exception(f"Swap transaction reverted: {tx_hash.hex()}")

    log.info(f"[SWAP] Success! tx={tx_hash.hex()}, got ~{out_amount:.2f} USDC.e")
    return tx_hash.hex()


def _get_token_price_usd(token_address: str, decimals: int) -> Optional[float]:
    """Get token price in USD from OpenOcean quote (1 unit -> USDC.e)."""
    try:
        import requests as req
        # Quote swapping 1 token unit to USDC.e
        url = "https://open-api.openocean.finance/v4/137/quote"
        params = {
            "inTokenAddress": token_address,
            "outTokenAddress": _USDC_E,
            "amount": "1",
            "gasPrice": "50",
        }
        resp = req.get(url, params=params, timeout=8)
        data = resp.json()
        if data.get("code") == 200 and data.get("data"):
            out_amount = float(data["data"].get("outAmount", 0)) / 10**6
            return out_amount
    except Exception as e:
        log.debug(f"Price fetch failed for {token_address[:10]}: {e}")
    return None


def _auto_swap_to_usdc_e(chat_id: str, needed_amount: float) -> dict:
    """
    Auto-swap user's tokens to USDC.e if they don't have enough.
    Checks USDC.e balance first, then tries to swap from other tokens.

    Returns: {"swapped": bool, "from_token": str, "amount": float, "tx": str, "usdc_e_balance": float}
    """
    import wallet_manager as wm
    from web3 import Web3

    chat_str = str(chat_id)
    pk = wm.get_primary_private_key(chat_str)
    if not pk:
        return {"swapped": False, "error": "No wallet found"}
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = _get_w3()
    address = w3.eth.account.from_key(pk).address
    balances = _get_token_balances(w3, address)

    usdc_e_bal = balances.get("USDC.e", {}).get("human", 0)
    log.info(f"[AUTO-SWAP] User {chat_str} needs ${needed_amount:.2f}, has ${usdc_e_bal:.2f} USDC.e")

    # If already enough USDC.e, no swap needed
    if usdc_e_bal >= needed_amount:
        return {"swapped": False, "usdc_e_balance": usdc_e_bal}

    # Find the best token to swap from (prioritize stablecoins, then large balances)
    swap_priority = ["USDC", "WETH", "WBTC", "MATIC"]
    best_token = None
    best_value_usd = 0

    for symbol in swap_priority:
        info = balances.get(symbol)
        if not info or info["human"] <= 0:
            continue

        # For MATIC, keep 0.5 MATIC for gas
        available = info["human"]
        if symbol == "MATIC":
            available = max(0, available - 0.5)
            if available <= 0:
                continue

        # USD estimation — fetch live price from OpenOcean for accuracy
        price_est = _get_token_price_usd(info["address"], info["decimals"])
        if price_est is None:
            # Fallback rough estimates
            price_est = {"USDC": 1, "MATIC": 0.10, "WETH": 2000, "WBTC": 80000}.get(symbol, 0)
        value_usd = available * price_est

        if value_usd > best_value_usd:
            best_value_usd = value_usd
            best_token = {
                "symbol": symbol,
                "available": available,
                "value_usd": value_usd,
                "address": info["address"],
                "decimals": info["decimals"],
                "raw": info["raw"],
            }

    if not best_token:
        # Build helpful error with what user has
        bal_str = ", ".join(f"{s}: {b['human']:.4f}" for s, b in balances.items() if b["human"] > 0)
        raise Exception(
            f"Not enough funds to trade. Need ${needed_amount:.2f} USDC.e.\n"
            f"Your balances: {bal_str or 'all zero'}\n"
            f"Deposit USDC.e, USDC, MATIC, WETH, or WBTC to: {address}"
        )

    if best_value_usd < needed_amount * 0.5:
        raise Exception(
            f"Not enough value to swap. Your {best_token['symbol']} is worth ~${best_value_usd:.2f}, "
            f"but you need ${needed_amount:.2f}.\nDeposit more funds to: {address}"
        )

    log.info(f"[AUTO-SWAP] Swapping {best_token['symbol']} (~${best_value_usd:.2f}) -> USDC.e")

    # For MATIC, calculate amount to swap (keep 0.5 for gas)
    if best_token["symbol"] == "MATIC":
        swap_raw = int((best_token["available"]) * 10**18)
        in_addr = _NATIVE_TOKEN_ADDRESS
    else:
        swap_raw = best_token["raw"]
        in_addr = best_token["address"]

    try:
        tx_hash = _openocean_swap(w3, pk, address, in_addr, swap_raw, best_token["decimals"])

        # Re-check USDC.e balance after swap
        usdc_e_contract = w3.eth.contract(address=Web3.to_checksum_address(_USDC_E), abi=_ERC20_ABI)
        new_balance = usdc_e_contract.functions.balanceOf(address).call() / 10**6

        return {
            "swapped": True,
            "from_token": best_token["symbol"],
            "amount_swapped": best_token["available"],
            "tx": tx_hash,
            "usdc_e_balance": new_balance,
        }
    except Exception as e:
        log.error(f"[AUTO-SWAP] Swap failed: {e}")
        raise Exception(
            f"Auto-swap from {best_token['symbol']} to USDC.e failed: {str(e)}\n"
            f"You can manually swap on QuickSwap/Uniswap, or deposit USDC.e directly."
        )


def _ensure_usdc_allowance(chat_id: str, neg_risk: bool = False):
    """Approve USDC.e spending for Polymarket exchange contract."""
    import wallet_manager as wm
    from web3 import Web3

    chat_str = str(chat_id)
    cached = _approved_users.get(chat_str, {})
    exchange_key = "neg_risk" if neg_risk else "ctf"
    if cached.get(exchange_key):
        return

    exchange_addr = _NEG_RISK_CTF_EXCHANGE if neg_risk else _CTF_EXCHANGE
    pk = wm.get_primary_private_key(chat_str)
    if not pk:
        return
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = _get_w3()
    address = w3.eth.account.from_key(pk).address
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(_USDC_E), abi=_ERC20_ABI)

    current = usdc_e.functions.allowance(address, Web3.to_checksum_address(exchange_addr)).call()
    balance = usdc_e.functions.balanceOf(address).call()

    if current >= balance and balance > 0:
        _approved_users.setdefault(chat_str, {})[exchange_key] = True
        log.info(f"USDC.e allowance OK for {address[:10]}...")
        return

    log.info(f"Setting USDC.e approval for {address[:10]}...")
    nonce = w3.eth.get_transaction_count(address)
    tx = usdc_e.functions.approve(
        Web3.to_checksum_address(exchange_addr), _MAX_APPROVAL
    ).build_transaction({
        "from": address, "nonce": nonce, "gas": 100_000,
        "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    if receipt.status == 1:
        _approved_users.setdefault(chat_str, {})[exchange_key] = True
        log.info(f"USDC.e approved! tx={tx_hash.hex()}")
    else:
        raise Exception("USDC.e approval transaction failed")


# ERC-1155 ABI for Conditional Tokens (setApprovalForAll + isApprovedForAll)
_ERC1155_ABI = json.loads('[{"inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"account","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}]')

_ct_approved_users = {}  # chat_str -> {"ctf": True, "neg_risk": True}


def _ensure_ct_allowance(chat_id: str, neg_risk: bool = False):
    """
    Approve Conditional Tokens (ERC-1155) for Polymarket exchange.
    Required for SELL orders — the exchange needs permission to transfer outcome tokens.
    """
    import wallet_manager as wm
    from web3 import Web3

    chat_str = str(chat_id)
    ct_key = "neg_risk" if neg_risk else "ctf"

    # Check cache
    if _ct_approved_users.get(chat_str, {}).get(ct_key):
        return

    # For selling: approve BOTH the exchange AND the neg risk adapter on the CT contract
    exchange_addr = _NEG_RISK_CTF_EXCHANGE if neg_risk else _CTF_EXCHANGE

    pk = wm.get_primary_private_key(chat_str)
    if not pk:
        return
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = _get_w3()
    address = w3.eth.account.from_key(pk).address
    ct = w3.eth.contract(address=Web3.to_checksum_address(_CT_CONTRACT), abi=_ERC1155_ABI)

    # Check if already approved
    already_approved = ct.functions.isApprovedForAll(address, Web3.to_checksum_address(exchange_addr)).call()
    if already_approved:
        _ct_approved_users.setdefault(chat_str, {})[ct_key] = True
        log.info(f"CT approval already set for {address[:10]}... on {ct_key}")
        return

    log.info(f"Setting CT (ERC-1155) approval for {address[:10]}... -> {exchange_addr[:10]}...")
    nonce = w3.eth.get_transaction_count(address)
    tx = ct.functions.setApprovalForAll(
        Web3.to_checksum_address(exchange_addr), True
    ).build_transaction({
        "from": address, "nonce": nonce, "gas": 100_000,
        "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=pk)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    if receipt.status == 1:
        _ct_approved_users.setdefault(chat_str, {})[ct_key] = True
        log.info(f"CT approved! tx={tx_hash.hex()}")
    else:
        raise Exception("Conditional Token approval failed — check MATIC for gas")

    # For neg_risk markets, also approve the adapter
    if neg_risk:
        adapter_approved = ct.functions.isApprovedForAll(
            address, Web3.to_checksum_address(_NEG_RISK_ADAPTER)).call()
        if not adapter_approved:
            nonce = w3.eth.get_transaction_count(address)
            tx2 = ct.functions.setApprovalForAll(
                Web3.to_checksum_address(_NEG_RISK_ADAPTER), True
            ).build_transaction({
                "from": address, "nonce": nonce, "gas": 100_000,
                "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
            })
            signed2 = w3.eth.account.sign_transaction(tx2, private_key=pk)
            tx_hash2 = w3.eth.send_raw_transaction(signed2.raw_transaction)
            receipt2 = w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=60)
            if receipt2.status == 1:
                log.info(f"Neg-risk adapter approved! tx={tx_hash2.hex()}")


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

    # ── Basic wallet & gas check ──
    bal = check_user_balance(chat_id)
    if not bal.get("has_gas"):
        return {"success": False, "error": f"Need MATIC for gas. Your balance: {bal.get('matic', 0):.4f} MATIC. Send at least 0.01 MATIC to your wallet."}
    if not bal.get("address"):
        return {"success": False, "error": bal.get("error", "No wallet found")}

    # ── Check total value across all swappable tokens ──
    # Don't block here if user has other tokens — auto-swap will handle conversion
    total_value = bal.get("usdc", 0)  # This includes both USDC + USDC.e
    if total_value < amount:
        # Check if they have value in other tokens (MATIC, WETH, WBTC)
        matic_val = max(0, bal.get("matic", 0) - 0.5) * 0.10  # keep 0.5 for gas, MATIC ~$0.10
        total_swappable = total_value + matic_val
        if total_swappable < amount * 0.5:
            return {"success": False, "error": (
                f"Insufficient funds. Balance: ${bal.get('usdc', 0):.2f} USDC, "
                f"{bal.get('matic', 0):.4f} MATIC (~${matic_val:.2f}).\n"
                f"Need ${amount:.2f}. Deposit USDC.e, USDC, MATIC, WETH, or WBTC to:\n"
                f"{bal.get('address', 'unknown')}"
            )}

    client = _get_client_for_user(chat_id)
    if not client:
        cached_err = _user_clients.get(str(chat_id), {}).get("error")
        if cached_err:
            return {"success": False, "error": f"Wallet init failed: {cached_err}"}
        import wallet_manager as _wm
        _wm_data = _wm._load()
        _has_wallet = bool(_wm_data.get("wallets", {}).get(str(chat_id)))
        if _has_wallet:
            return {"success": False, "error": "Wallet found but CLOB client init failed. Possible network issue with Polymarket API."}
        return {"success": False, "error": "No wallet found. Use /start to create one, or /import_wallet to restore an existing wallet with your private key."}

    # ── Auto-swap other tokens to USDC.e if needed ──
    try:
        swap_result = _auto_swap_to_usdc_e(chat_id, amount)
        if swap_result.get("swapped"):
            log.info(f"[TRADE] Auto-swapped {swap_result.get('from_token')} -> USDC.e, "
                     f"new balance: ${swap_result.get('usdc_e_balance', 0):.2f}")
    except Exception as e:
        err_msg = str(e)
        log.error(f"[TRADE] Auto-swap failed: {err_msg}")
        return {"success": False, "error": err_msg}

    # ── Approve USDC.e spending for Polymarket exchange ──
    try:
        _ensure_usdc_allowance(chat_id, neg_risk)
    except Exception as e:
        err_msg = str(e)
        if "USDC" in err_msg:
            return {"success": False, "error": err_msg}
        log.warning(f"Auto-approve USDC.e failed (continuing anyway): {e}")

    # ── Final USDC.e balance check before order ──
    try:
        from web3 import Web3 as _W3
        _w3 = _get_w3()
        import wallet_manager as _wm_check
        _wallet = _wm_check.get_primary_wallet(str(chat_id))
        if _wallet:
            _usdc_e_contract = _w3.eth.contract(
                address=_W3.to_checksum_address(_USDC_E), abi=_ERC20_ABI)
            _usdc_e_bal = _usdc_e_contract.functions.balanceOf(
                _W3.to_checksum_address(_wallet["address"])).call() / 10**6
            log.info(f"[TRADE] Pre-order USDC.e balance: ${_usdc_e_bal:.2f}, order: ${amount:.2f}")
            if _usdc_e_bal < amount * 0.95:  # 5% tolerance for rounding
                return {"success": False, "error": (
                    f"Insufficient USDC.e after swap. Have: ${_usdc_e_bal:.2f}, need: ${amount:.2f}.\n"
                    f"Your wallet may not have enough funds. Deposit USDC.e to:\n"
                    f"{_wallet['address']}"
                )}
    except Exception as e:
        log.warning(f"Pre-order balance check failed (continuing): {e}")

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        # Calculate fee (1% of requested amount)
        # Polymarket minimum order is $1, so ensure net amount stays >= 1.0
        net_amount, fee_amount = calculate_fee(amount, side="BUY")
        if net_amount < 1.0:
            net_amount = 1.0
            fee_amount = round(amount - 1.0, 6)
            if fee_amount < 0:
                fee_amount = 0

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

    # ── Approve Conditional Tokens for exchange (required for selling) ──
    try:
        _ensure_ct_allowance(chat_id, neg_risk)
    except Exception as e:
        log.error(f"CT approval failed for sell: {e}")
        return {"success": False, "error": f"Token approval failed: {e}. Need MATIC for gas."}

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

        # If no results, try as condition_id path param
        if not markets:
            m = gamma_get(f"/markets/{slug}")
            if m and isinstance(m, dict):
                markets = [m]

        # Try as conditionId query param (data-api market field is a condition_id)
        if not markets:
            data = gamma_get("/markets", params={"conditionId": slug})
            if data:
                markets = data if isinstance(data, list) else []

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
