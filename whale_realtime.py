"""
WHALE REALTIME — On-chain whale trade listener via Polygon WebSocket.

Listens to OrderFilled events on Polymarket's CTF Exchange contracts,
filters for tracked whale wallets, and dispatches instant signals.

Replaces the 5-minute polling loop with ~2 second detection latency.
"""

import os, json, time, threading, traceback
from datetime import datetime, timezone
from web3 import Web3

import copy_trading as ct
import copy_signals
import config

# ═══════════════════════════════════════════════
# POLYGON WEBSOCKET + RPC CONFIGURATION
# ═══════════════════════════════════════════════

# Free public WSS endpoints for Polygon (fallback chain)
_WSS_ENDPOINTS = [
    os.environ.get("POLYGON_WSS_URL", ""),
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon.drpc.org",
]

# Polymarket Exchange Contracts on Polygon
CTF_EXCHANGE = Web3.to_checksum_address("0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E")
NEG_RISK_CTF_EXCHANGE = Web3.to_checksum_address("0xC5d563A36AE78145C45a50134d48A1215220f80a")

# OrderFilled event signature
# event OrderFilled(bytes32 indexed orderHash, address indexed maker,
#   address indexed taker, uint256 makerAssetId, uint256 takerAssetId,
#   uint256 makerAmountFilled, uint256 takerAmountFilled, uint256 fee)
ORDER_FILLED_TOPIC = Web3.keccak(
    text="OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
).hex()

# Minimal ABI for decoding OrderFilled events
CTF_ABI = json.loads("""[{
    "anonymous": false,
    "inputs": [
        {"indexed": true, "name": "orderHash", "type": "bytes32"},
        {"indexed": true, "name": "maker", "type": "address"},
        {"indexed": true, "name": "taker", "type": "address"},
        {"indexed": false, "name": "makerAssetId", "type": "uint256"},
        {"indexed": false, "name": "takerAssetId", "type": "uint256"},
        {"indexed": false, "name": "makerAmountFilled", "type": "uint256"},
        {"indexed": false, "name": "takerAmountFilled", "type": "uint256"},
        {"indexed": false, "name": "fee", "type": "uint256"}
    ],
    "name": "OrderFilled",
    "type": "event"
}]""")

# USDC has 6 decimals on Polygon
USDC_DECIMALS = 6

# Minimum trade size (USDC) to trigger an alert
MIN_ALERT_USD = float(os.environ.get("WHALE_RT_MIN_USD", "500"))

# ═══════════════════════════════════════════════
# TRANSACTION LOG — persists to whale_realtime_log.json
# ═══════════════════════════════════════════════

_LOG_FILE = os.path.join(os.path.dirname(__file__), "whale_realtime_log.json")
_LOG_LOCK = threading.Lock()
_MAX_LOG_ENTRIES = 1000

def _load_log() -> list:
    try:
        with open(_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def _save_log(entries: list):
    with _LOG_LOCK:
        with open(_LOG_FILE, "w") as f:
            json.dump(entries[-_MAX_LOG_ENTRIES:], f)

def log_whale_tx(entry: dict):
    """Append a whale transaction to the persistent log."""
    entries = _load_log()
    entries.append(entry)
    _save_log(entries)

def get_recent_whale_txs(limit: int = 50) -> list:
    """Get recent whale transactions (for admin dashboard)."""
    entries = _load_log()
    return entries[-limit:]

def get_realtime_stats() -> dict:
    """Get stats for admin dashboard."""
    entries = _load_log()
    now = datetime.now(timezone.utc)
    last_24h = [e for e in entries if _age_hours(e.get("timestamp", "")) < 24]
    last_1h = [e for e in entries if _age_hours(e.get("timestamp", "")) < 1]
    return {
        "total_detected": len(entries),
        "last_24h": len(last_24h),
        "last_1h": len(last_1h),
        "volume_24h": sum(e.get("value_usd", 0) for e in last_24h),
        "volume_1h": sum(e.get("value_usd", 0) for e in last_1h),
        "last_tx": entries[-1] if entries else None,
        "listener_status": "running" if _listener_running else "stopped",
    }

def _age_hours(ts: str) -> float:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 9999

# ═══════════════════════════════════════════════
# WHALE ADDRESS SET — rebuilt periodically from copy_trading data
# ═══════════════════════════════════════════════

_whale_addresses = set()  # lowercase addresses of tracked whales
_addr_to_alias = {}       # address -> display name
_addr_lock = threading.Lock()

def refresh_whale_set():
    """Rebuild the set of whale addresses from copy_trading storage."""
    global _whale_addresses, _addr_to_alias
    data = ct._load()
    addrs = set()
    aliases = {}
    for wid, w in data.get("wallets", {}).items():
        addr = w.get("address", "").lower()
        if addr:
            addrs.add(addr)
            aliases[addr] = w.get("alias", addr[:10])
    with _addr_lock:
        _whale_addresses = addrs
        _addr_to_alias = aliases
    print(f"[WHALE-RT] Tracking {len(addrs)} whale addresses on-chain")

def is_tracked_whale(address: str) -> bool:
    """Check if an address is a tracked whale."""
    return address.lower() in _whale_addresses

def get_whale_alias(address: str) -> str:
    return _addr_to_alias.get(address.lower(), address[:10])

# ═══════════════════════════════════════════════
# EVENT PROCESSING — decode OrderFilled and create signals
# ═══════════════════════════════════════════════

def _resolve_market_title(asset_id: int) -> str:
    """Try to resolve a conditional token asset ID to a market title."""
    try:
        import polymarket_api as api
        # asset_id is the condition token ID — try gamma API
        r = api._get(f"{api.GAMMA_BASE}/markets", params={"clob_token_ids": str(asset_id)})
        if r and isinstance(r, list) and len(r) > 0:
            return r[0].get("question", f"Token #{asset_id}")[:60]
    except Exception:
        pass
    return f"Token #{asset_id}"

_market_title_cache = {}

def _get_market_title(asset_id: int) -> str:
    """Cached market title lookup."""
    if asset_id in _market_title_cache:
        return _market_title_cache[asset_id]
    title = _resolve_market_title(asset_id)
    _market_title_cache[asset_id] = title
    # Cap cache at 500 entries
    if len(_market_title_cache) > 500:
        oldest = list(_market_title_cache.keys())[:100]
        for k in oldest:
            _market_title_cache.pop(k, None)
    return title


def process_order_filled(event, contract_address: str):
    """Process an OrderFilled event — check if maker or taker is a tracked whale."""
    try:
        args = event.get("args", {})
        maker = args.get("maker", "").lower()
        taker = args.get("taker", "").lower()
        tx_hash = event.get("transactionHash", b"").hex() if isinstance(event.get("transactionHash"), bytes) else str(event.get("transactionHash", ""))

        # Check if either maker or taker is a tracked whale
        whale_addr = None
        role = None
        if is_tracked_whale(maker):
            whale_addr = maker
            role = "maker"
        elif is_tracked_whale(taker):
            whale_addr = taker
            role = "taker"
        else:
            return  # Not a tracked whale

        # Decode amounts (USDC = 6 decimals, outcome tokens also 6 decimals in Polymarket)
        maker_amount = args.get("makerAmountFilled", 0) / (10 ** USDC_DECIMALS)
        taker_amount = args.get("takerAmountFilled", 0) / (10 ** USDC_DECIMALS)
        fee = args.get("fee", 0) / (10 ** USDC_DECIMALS)

        maker_asset_id = args.get("makerAssetId", 0)
        taker_asset_id = args.get("takerAssetId", 0)

        # Determine trade value in USD
        # If whale is maker: they're selling makerAssetId for takerAssetId
        # If whale is taker: they're buying takerAssetId for makerAssetId
        if role == "maker":
            value_usd = maker_amount
            asset_id = maker_asset_id
            side = "SELL"
        else:
            value_usd = taker_amount
            asset_id = taker_asset_id
            side = "BUY"

        # Skip small trades
        if value_usd < MIN_ALERT_USD:
            return

        alias = get_whale_alias(whale_addr)
        market_title = _get_market_title(asset_id)
        timestamp = datetime.now(timezone.utc).isoformat()

        # Log the transaction
        tx_entry = {
            "timestamp": timestamp,
            "whale": whale_addr,
            "alias": alias,
            "role": role,
            "side": side,
            "value_usd": round(value_usd, 2),
            "maker_amount": round(maker_amount, 2),
            "taker_amount": round(taker_amount, 2),
            "fee": round(fee, 2),
            "asset_id": str(asset_id),
            "market_title": market_title,
            "tx_hash": tx_hash,
            "contract": contract_address,
        }
        log_whale_tx(tx_entry)
        print(f"[WHALE-RT] 🐋 {alias} {side} ${value_usd:.0f} on {market_title[:40]} (tx: {tx_hash[:16]}...)")

        # Build signal compatible with copy_signals.dispatch_signals()
        signal = {
            "type": "NEW_POSITION" if side == "BUY" else "CLOSED",
            "wallet": whale_addr,
            "alias": alias,
            "market_id": str(asset_id),
            "market_title": market_title,
            "title": market_title,
            "side": "yes" if side == "BUY" else "no",
            "outcome": "Yes" if side == "BUY" else "No",
            "size": value_usd,
            "avg_price": 0.50,  # Approximate — real price requires order book context
            "value_usd": value_usd,
            "timestamp": timestamp,
            "source": "realtime",  # Flag to distinguish from polling signals
            "tx_hash": tx_hash,
        }

        # Dispatch to followers immediately
        threading.Thread(
            target=_safe_dispatch, args=(signal,), daemon=True
        ).start()

    except Exception as e:
        print(f"[WHALE-RT] Error processing event: {e}")
        traceback.print_exc()


def _safe_dispatch(signal: dict):
    """Dispatch a signal in a separate thread to not block the listener."""
    try:
        sent = copy_signals.dispatch_signals([signal])
        alias = signal.get("alias", "?")
        value = signal.get("value_usd", 0)
        print(f"[WHALE-RT] Dispatched signal: {alias} ${value:.0f} → {sent} notifications")
    except Exception as e:
        print(f"[WHALE-RT] Dispatch error: {e}")


# ═══════════════════════════════════════════════
# WEBSOCKET LISTENER — connects to Polygon WSS and subscribes to events
# ═══════════════════════════════════════════════

_listener_running = False
_listener_thread = None
_stop_event = threading.Event()

def _get_wss_url() -> str:
    """Get the best available WSS endpoint."""
    for url in _WSS_ENDPOINTS:
        if url and url.startswith("wss://"):
            return url
    return "wss://polygon-bor-rpc.publicnode.com"


def _run_listener():
    """Main listener loop — connects via WebSocket and processes events."""
    global _listener_running

    _listener_running = True
    refresh_whale_set()
    reconnect_delay = 5
    whale_refresh_interval = 300  # Refresh whale set every 5 min
    last_whale_refresh = time.time()

    print(f"[WHALE-RT] Starting real-time listener...")
    print(f"[WHALE-RT] Contracts: CTF={CTF_EXCHANGE[:10]}..., NegRisk={NEG_RISK_CTF_EXCHANGE[:10]}...")

    while not _stop_event.is_set():
        wss_url = _get_wss_url()
        print(f"[WHALE-RT] Connecting to {wss_url}...")

        try:
            w3 = Web3(Web3.WebsocketProvider(
                wss_url,
                websocket_timeout=60,
                websocket_kwargs={"ping_interval": 30, "ping_timeout": 10}
            ))

            if not w3.is_connected():
                raise ConnectionError("WebSocket connection failed")

            chain_id = w3.eth.chain_id
            print(f"[WHALE-RT] Connected to Polygon (chain {chain_id})")

            # Create contract instances for event decoding
            ctf_contract = w3.eth.contract(address=CTF_EXCHANGE, abi=CTF_ABI)
            neg_risk_contract = w3.eth.contract(address=NEG_RISK_CTF_EXCHANGE, abi=CTF_ABI)

            # Create event filters for OrderFilled
            ctf_filter = ctf_contract.events.OrderFilled.create_filter(fromBlock="latest")
            neg_risk_filter = neg_risk_contract.events.OrderFilled.create_filter(fromBlock="latest")

            reconnect_delay = 5  # Reset on successful connection

            print(f"[WHALE-RT] ✅ Listening for OrderFilled events on both exchanges...")

            while not _stop_event.is_set():
                # Poll for new events
                try:
                    for event in ctf_filter.get_new_entries():
                        process_order_filled(event, CTF_EXCHANGE)

                    for event in neg_risk_filter.get_new_entries():
                        process_order_filled(event, NEG_RISK_CTF_EXCHANGE)

                except Exception as poll_err:
                    err_str = str(poll_err)
                    if "filter not found" in err_str.lower():
                        print("[WHALE-RT] Filter expired, reconnecting...")
                        break
                    raise

                # Periodically refresh whale address set
                if time.time() - last_whale_refresh > whale_refresh_interval:
                    refresh_whale_set()
                    last_whale_refresh = time.time()

                # Small sleep to avoid burning CPU (events are still near-instant)
                time.sleep(1)

        except Exception as e:
            print(f"[WHALE-RT] Connection error: {e}")
            if not _stop_event.is_set():
                print(f"[WHALE-RT] Reconnecting in {reconnect_delay}s...")
                _stop_event.wait(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 120)  # Exponential backoff, max 2 min

    _listener_running = False
    print("[WHALE-RT] Listener stopped.")


# ═══════════════════════════════════════════════
# FALLBACK: HTTP POLLING (if WebSocket unavailable)
# ═══════════════════════════════════════════════

def _run_http_fallback():
    """Fallback: poll via HTTP RPC every 15 seconds if WebSocket fails repeatedly."""
    global _listener_running

    _listener_running = True
    refresh_whale_set()
    rpc_url = config.POLYGON_RPC_URL
    print(f"[WHALE-RT] Starting HTTP fallback listener (RPC: {rpc_url})")

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if not w3.is_connected():
            print("[WHALE-RT] HTTP RPC not connected, falling back to scheduled polling")
            _listener_running = False
            return

        ctf_contract = w3.eth.contract(address=CTF_EXCHANGE, abi=CTF_ABI)
        neg_risk_contract = w3.eth.contract(address=NEG_RISK_CTF_EXCHANGE, abi=CTF_ABI)
        last_block = w3.eth.block_number

        print(f"[WHALE-RT] HTTP fallback started at block {last_block}")

        while not _stop_event.is_set():
            try:
                current_block = w3.eth.block_number
                if current_block > last_block:
                    # Fetch events from last_block+1 to current_block
                    from_block = last_block + 1
                    to_block = min(current_block, from_block + 100)  # Max 100 blocks per query

                    for contract, addr in [(ctf_contract, CTF_EXCHANGE), (neg_risk_contract, NEG_RISK_CTF_EXCHANGE)]:
                        try:
                            events = contract.events.OrderFilled.get_logs(
                                fromBlock=from_block, toBlock=to_block
                            )
                            for event in events:
                                process_order_filled(event, addr)
                        except Exception as e:
                            print(f"[WHALE-RT] HTTP poll error for {addr[:10]}: {e}")

                    last_block = to_block

                # Refresh whale set periodically
                refresh_whale_set()

            except Exception as e:
                print(f"[WHALE-RT] HTTP poll error: {e}")

            _stop_event.wait(15)  # Poll every 15 seconds

    except Exception as e:
        print(f"[WHALE-RT] HTTP fallback failed: {e}")

    _listener_running = False


# ═══════════════════════════════════════════════
# PUBLIC API — start / stop / status
# ═══════════════════════════════════════════════

def start_listener(use_http_fallback: bool = False):
    """Start the real-time whale listener in a background thread."""
    global _listener_thread, _stop_event

    if _listener_running:
        print("[WHALE-RT] Listener already running")
        return

    _stop_event = threading.Event()

    if use_http_fallback:
        _listener_thread = threading.Thread(target=_run_http_fallback, daemon=True, name="whale-rt-http")
    else:
        _listener_thread = threading.Thread(target=_run_listener, daemon=True, name="whale-rt-ws")

    _listener_thread.start()
    print(f"[WHALE-RT] Listener thread started (mode: {'HTTP' if use_http_fallback else 'WebSocket'})")


def stop_listener():
    """Stop the real-time whale listener."""
    global _listener_running

    if not _listener_running:
        return

    _stop_event.set()
    print("[WHALE-RT] Stop signal sent to listener")


def is_running() -> bool:
    return _listener_running
