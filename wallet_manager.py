"""
POLYTRAGENT — Wallet Manager
Create, import, export Polygon wallets.
Send/receive USDC on Polygon network.
Check balances, manage multiple user wallets.

Storage: data/user_wallets.json
"""

import os, json, logging, time, secrets
from typing import Optional, Tuple
from datetime import datetime, timezone

log = logging.getLogger("polytragent.wallet")

# ═══════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════

POLYGON_RPC = os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
POLYGON_RPC_FALLBACK = "https://polygon.drpc.org"

# USDC on Polygon — there are TWO versions:
USDC_NATIVE_CONTRACT = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC (Circle, post-2023)
USDC_BRIDGED_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (bridged, legacy)
USDC_CONTRACT = USDC_NATIVE_CONTRACT  # Primary (most exchanges send this)
USDC_DECIMALS = 6

# ERC-20 Transfer ABI (minimal)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
]

# Allow override via env var so a Railway persistent volume path can be used.
# Set WALLETS_FILE=/railway/volume/user_wallets.json in Railway env vars if you have a volume mounted.
_wallets_file_override = os.environ.get("WALLETS_FILE")
if _wallets_file_override:
    WALLETS_FILE = _wallets_file_override
    WALLETS_DIR = os.path.dirname(WALLETS_FILE)
else:
    WALLETS_DIR = os.path.join(os.path.dirname(__file__), "data")
    WALLETS_FILE = os.path.join(WALLETS_DIR, "user_wallets.json")


# ═══════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════

def _load() -> dict:
    os.makedirs(WALLETS_DIR, exist_ok=True)
    if not os.path.exists(WALLETS_FILE):
        return {"wallets": {}}
    try:
        with open(WALLETS_FILE) as f:
            return json.load(f)
    except:
        return {"wallets": {}}


def _save(data: dict):
    os.makedirs(WALLETS_DIR, exist_ok=True)
    with open(WALLETS_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════════
# WALLET CREATION & MANAGEMENT
# ═══════════════════════════════════════════════

def create_wallet(chat_id: str) -> dict:
    """
    Create a new Polygon wallet for a user.
    Returns: {address, private_key, created}
    WARNING: Private key is shown ONCE to user then only stored encrypted.
    """
    try:
        from eth_account import Account

        # Generate new account
        acct = Account.create(extra_entropy=secrets.token_hex(32))
        address = acct.address
        private_key = acct.key.hex()

        # Store wallet (we store the encrypted key hash for recovery reference)
        data = _load()
        chat_str = str(chat_id)

        if chat_str not in data["wallets"]:
            data["wallets"][chat_str] = []

        wallet_entry = {
            "address": address,
            "private_key_encrypted": _encrypt_key(private_key, chat_str),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": f"Wallet {len(data['wallets'][chat_str]) + 1}",
            "is_primary": len(data["wallets"][chat_str]) == 0,
        }

        data["wallets"][chat_str].append(wallet_entry)
        _save(data)

        return {
            "success": True,
            "address": address,
            "private_key": private_key,  # Show ONCE
            "label": wallet_entry["label"],
        }
    except ImportError:
        return {"success": False, "error": "eth-account not installed"}
    except Exception as e:
        log.error(f"Wallet creation error: {e}")
        return {"success": False, "error": str(e)}


def ensure_wallet(chat_id: str) -> dict:
    """
    Ensure a user has a wallet. Auto-creates one if they don't.
    Returns: {success, address, existing, private_key (if new)}
    """
    wallets = get_wallets(chat_id)
    if wallets:
        # User already has wallets, return the primary one
        primary = get_primary_wallet(chat_id)
        return {
            "success": True,
            "address": primary["address"],
            "existing": True
        }
    else:
        # No wallets, create one
        result = create_wallet(chat_id)
        if result.get("success"):
            result["existing"] = False
        return result


def get_wallet_count(chat_id: str) -> int:
    """Get the count of wallets for a user."""
    return len(get_wallets(chat_id))


def import_wallet(chat_id: str, private_key: str, label: str = None) -> dict:
    """Import an existing wallet by private key."""
    try:
        from eth_account import Account

        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        acct = Account.from_key(private_key)
        address = acct.address

        data = _load()
        chat_str = str(chat_id)

        if chat_str not in data["wallets"]:
            data["wallets"][chat_str] = []

        # Check if already imported
        for w in data["wallets"][chat_str]:
            if w["address"].lower() == address.lower():
                return {"success": False, "error": "Wallet already imported"}

        wallet_entry = {
            "address": address,
            "private_key_encrypted": _encrypt_key(private_key, chat_str),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": label or f"Imported {len(data['wallets'][chat_str]) + 1}",
            "is_primary": len(data["wallets"][chat_str]) == 0,
        }

        data["wallets"][chat_str].append(wallet_entry)
        _save(data)

        return {"success": True, "address": address, "label": wallet_entry["label"]}
    except Exception as e:
        return {"success": False, "error": str(e)}


def export_wallet(chat_id: str, wallet_index: int = 0) -> dict:
    """Export wallet private key (user must confirm)."""
    data = _load()
    chat_str = str(chat_id)

    wallets = data["wallets"].get(chat_str, [])
    if not wallets:
        return {"success": False, "error": "No wallets found"}

    if wallet_index >= len(wallets):
        return {"success": False, "error": f"Wallet index {wallet_index} not found"}

    w = wallets[wallet_index]
    private_key = _decrypt_key(w["private_key_encrypted"], chat_str)

    return {
        "success": True,
        "address": w["address"],
        "private_key": private_key,
        "label": w.get("label", ""),
    }


def get_wallets(chat_id: str) -> list:
    """Get all wallets for a user (without private keys)."""
    data = _load()
    chat_str = str(chat_id)
    wallets = data["wallets"].get(chat_str, [])

    result = []
    for w in wallets:
        result.append({
            "address": w["address"],
            "label": w.get("label", ""),
            "is_primary": w.get("is_primary", False),
            "created_at": w.get("created_at", ""),
        })
    return result


def get_primary_wallet(chat_id: str) -> Optional[dict]:
    """Get the primary wallet for a user."""
    wallets = get_wallets(chat_id)
    for w in wallets:
        if w.get("is_primary"):
            return w
    return wallets[0] if wallets else None


def get_private_key(chat_id: str, wallet_index: int = 0) -> Optional[str]:
    """Get decrypted private key for internal use (e.g., auto-trading).
    Auto-migrates legacy XOR encryption to AES-256 Fernet on access."""
    data = _load()
    chat_str = str(chat_id)
    wallets = data["wallets"].get(chat_str, [])

    if wallet_index >= len(wallets):
        return None

    w = wallets[wallet_index]
    if not w.get("private_key_encrypted"):
        log.warning(f"No private_key_encrypted for user {chat_str} at index {wallet_index}")
        return None

    # Auto-migrate XOR → Fernet if needed
    if not w["private_key_encrypted"].startswith("fernet:"):
        if _migrate_wallet_encryption(chat_str, w):
            _save(data)

    return _decrypt_key(w["private_key_encrypted"], chat_str)


def get_primary_private_key(chat_id: str) -> Optional[str]:
    """
    Get decrypted private key for the PRIMARY wallet.
    Works for both created and imported wallets.
    Unlike get_private_key(chat_id, 0), this respects the is_primary flag
    so it correctly handles imported wallets that may not be at index 0.
    """
    data = _load()
    chat_str = str(chat_id)
    wallets = data["wallets"].get(chat_str, [])

    if not wallets:
        log.warning(f"No wallets found for user {chat_str}")
        return None

    # Find the primary wallet; fall back to index 0 if none is marked primary
    primary_wallet = None
    primary_idx = 0
    for i, w in enumerate(wallets):
        if w.get("is_primary"):
            primary_wallet = w
            primary_idx = i
            break
    if primary_wallet is None:
        primary_wallet = wallets[0]
        primary_idx = 0

    if not primary_wallet.get("private_key_encrypted"):
        log.warning(f"No private_key_encrypted for user {chat_str} primary wallet (idx {primary_idx})")
        return None

    log.debug(f"get_primary_private_key: user={chat_str} idx={primary_idx} addr={primary_wallet.get('address','')[:10]}")

    # Auto-migrate XOR → Fernet if needed
    if not primary_wallet["private_key_encrypted"].startswith("fernet:"):
        if _migrate_wallet_encryption(chat_str, primary_wallet):
            _save(data)

    return _decrypt_key(primary_wallet["private_key_encrypted"], chat_str)


def set_primary_wallet(chat_id: str, wallet_index: int) -> dict:
    """Set a wallet as primary."""
    data = _load()
    chat_str = str(chat_id)
    wallets = data["wallets"].get(chat_str, [])

    if wallet_index >= len(wallets):
        return {"success": False, "error": "Wallet not found"}

    for i, w in enumerate(wallets):
        w["is_primary"] = (i == wallet_index)

    _save(data)
    return {"success": True, "address": wallets[wallet_index]["address"]}


def delete_wallet(chat_id: str, wallet_index: int) -> dict:
    """Delete a wallet from storage."""
    data = _load()
    chat_str = str(chat_id)
    wallets = data["wallets"].get(chat_str, [])

    if wallet_index >= len(wallets):
        return {"success": False, "error": "Wallet not found"}

    removed = wallets.pop(wallet_index)

    # If we removed the primary, make first one primary
    if removed.get("is_primary") and wallets:
        wallets[0]["is_primary"] = True

    _save(data)
    return {"success": True, "address": removed["address"]}


# ═══════════════════════════════════════════════
# BALANCE CHECKING
# ═══════════════════════════════════════════════

def _get_web3() -> "Web3":
    """Get a Web3 instance, cycling through reliable RPCs until one connects."""
    from web3 import Web3

    rpcs = [POLYGON_RPC, POLYGON_RPC_FALLBACK,
            "https://1rpc.io/matic",
            "https://polygon-bor-rpc.publicnode.com",
            "https://polygon.drpc.org"]
    # Deduplicate while preserving order
    seen = set()
    unique_rpcs = []
    for r in rpcs:
        if r and r not in seen:
            seen.add(r)
            unique_rpcs.append(r)

    for rpc in unique_rpcs:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                return w3
        except Exception:
            log.warning(f"RPC {rpc} failed, trying next...")
            continue

    # Last resort
    log.error("All Polygon RPCs failed, using first as fallback")
    return Web3(Web3.HTTPProvider(unique_rpcs[0]))


def _query_erc20_balance(w3, contract_address: str, wallet_address: str) -> float:
    """Query ERC-20 balance for a given contract and wallet address."""
    contract = w3.eth.contract(
        address=w3.to_checksum_address(contract_address),
        abi=ERC20_ABI
    )
    raw = contract.functions.balanceOf(
        w3.to_checksum_address(wallet_address)
    ).call()
    return raw / (10 ** USDC_DECIMALS)


def get_usdc_balance(address: str) -> Optional[float]:
    """
    Get total USDC balance for an address on Polygon.
    Checks both native USDC (0x3c49...) and bridged USDC.e (0x2791...) and sums them.
    Most modern exchanges send native USDC; legacy balances may be in USDC.e.
    """
    try:
        from web3 import Web3
        w3 = _get_web3()

        native = 0.0
        bridged = 0.0

        try:
            native = _query_erc20_balance(w3, USDC_NATIVE_CONTRACT, address)
        except Exception as e:
            log.warning(f"Native USDC balance error for {address[:10]}: {e}")

        try:
            bridged = _query_erc20_balance(w3, USDC_BRIDGED_CONTRACT, address)
        except Exception as e:
            log.warning(f"Bridged USDC.e balance error for {address[:10]}: {e}")

        total = native + bridged
        log.debug(f"USDC balance {address[:10]}: native={native:.4f}, bridged={bridged:.4f}, total={total:.4f}")
        return total
    except Exception as e:
        log.error(f"Balance check error: {e}")
        return None


def get_usdc_balance_breakdown(address: str) -> dict:
    """Get native USDC and USDC.e balances separately."""
    native_ok = False
    bridged_ok = False
    native, bridged = 0.0, 0.0
    try:
        from web3 import Web3
        w3 = _get_web3()

        try:
            native = _query_erc20_balance(w3, USDC_NATIVE_CONTRACT, address)
            native_ok = True
        except Exception as e:
            log.warning(f"Native USDC query failed for {address[:10]}: {e}")

        try:
            bridged = _query_erc20_balance(w3, USDC_BRIDGED_CONTRACT, address)
            bridged_ok = True
        except Exception as e:
            log.warning(f"Bridged USDC.e query failed for {address[:10]}: {e}")

    except Exception as e:
        log.error(f"USDC breakdown error: {e}")

    return {
        "native": native,
        "bridged": bridged,
        "total": native + bridged,
        "error": not native_ok and not bridged_ok,  # True only if both failed
    }


def get_matic_balance(address: str) -> Optional[float]:
    """Get MATIC (POL) balance for gas fees."""
    try:
        from web3 import Web3
        w3 = _get_web3()
        balance_wei = w3.eth.get_balance(Web3.to_checksum_address(address))
        return float(w3.from_wei(balance_wei, "ether"))
    except Exception as e:
        log.error(f"MATIC balance error: {e}")
        return None


def get_full_balance(address: str) -> dict:
    """Get complete balance info for an address."""
    breakdown = get_usdc_balance_breakdown(address)
    # breakdown["error"] is True if both queries failed
    usdc = None if breakdown.get("error") else breakdown["total"]
    matic = get_matic_balance(address)

    return {
        "address": address,
        "usdc": usdc,
        "usdc_breakdown": breakdown,
        "matic": float(matic) if matic is not None else None,
        "has_gas": (matic or 0) > 0.01,  # Need some MATIC for gas
    }


# ═══════════════════════════════════════════════
# SEND / RECEIVE
# ═══════════════════════════════════════════════

def send_usdc(from_chat_id: str, to_address: str, amount: float,
              wallet_index: int = 0) -> dict:
    """
    Send USDC from user's wallet to another address.

    Args:
        from_chat_id: Sender's chat ID
        to_address: Recipient Polygon address
        amount: USDC amount to send
        wallet_index: Which wallet to send from

    Returns:
        dict with success, tx_hash, error
    """
    try:
        from web3 import Web3
        from eth_account import Account

        # Get sender's private key
        pk = get_private_key(from_chat_id, wallet_index)
        if not pk:
            return {"success": False, "error": "Wallet not found"}

        if not pk.startswith("0x"):
            pk = "0x" + pk

        w3 = _get_web3()
        acct = Account.from_key(pk)
        sender = acct.address

        # Validate recipient
        if not Web3.is_address(to_address):
            return {"success": False, "error": "Invalid recipient address"}

        # Check balance
        balance = get_usdc_balance(sender)
        if balance is None or balance < amount:
            return {"success": False, "error": f"Insufficient USDC balance ({balance or 0:.2f} < {amount:.2f})"}

        # Check gas
        matic_bal = get_matic_balance(sender)
        if not matic_bal or matic_bal < 0.005:
            return {"success": False, "error": f"Insufficient MATIC for gas ({matic_bal or 0:.4f}). Need ~0.005 MATIC."}

        # Build USDC transfer transaction
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_CONTRACT),
            abi=ERC20_ABI
        )

        amount_raw = int(amount * (10 ** USDC_DECIMALS))

        nonce = w3.eth.get_transaction_count(sender)

        tx = contract.functions.transfer(
            Web3.to_checksum_address(to_address),
            amount_raw
        ).build_transaction({
            "from": sender,
            "nonce": nonce,
            "gas": 100000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 137,
        })

        # Sign and send
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()

        log.info(f"USDC transfer sent: {amount} USDC from {sender[:10]} to {to_address[:10]} | tx: {tx_hex}")

        return {
            "success": True,
            "tx_hash": tx_hex,
            "amount": amount,
            "from": sender,
            "to": to_address,
            "explorer_url": f"https://polygonscan.com/tx/0x{tx_hex}" if not tx_hex.startswith("0x") else f"https://polygonscan.com/tx/{tx_hex}",
        }
    except Exception as e:
        log.error(f"USDC send error: {e}")
        return {"success": False, "error": str(e)}


def get_deposit_info(chat_id: str) -> dict:
    """Get deposit address and instructions for a user."""
    wallet = get_primary_wallet(chat_id)
    if not wallet:
        return {"success": False, "error": "No wallet found. Create one first with /create_wallet"}

    return {
        "success": True,
        "address": wallet["address"],
        "network": "Polygon (MATIC)",
        "token": "USDC",
        "instructions": (
            f"Send <b>USDC</b> on the <b>Polygon</b> network to:\n\n"
            f"<code>{wallet['address']}</code>\n\n"
            f"⚠️ Only send <b>USDC on Polygon</b>. Sending other tokens or using other networks will result in loss of funds.\n\n"
            f"💡 You also need a small amount of <b>MATIC (POL)</b> for gas fees (~0.01 MATIC)."
        ),
    }


# ═══════════════════════════════════════════════
# ENCRYPTION — AES-256 via Fernet (upgraded from XOR)
# Master key stored in WALLET_ENCRYPTION_KEY env var.
# Per-wallet key = PBKDF2(master_key, salt=chat_id, 480000 iterations)
# Old XOR-encrypted keys auto-migrate on first decrypt.
# ═══════════════════════════════════════════════

import base64, hashlib, os as _os

def _get_master_key() -> bytes:
    """Get the master encryption key from environment.
    Falls back to a derived key if env var is missing (NOT recommended for production)."""
    mk = _os.environ.get("WALLET_ENCRYPTION_KEY", "")
    if mk:
        return mk.encode()
    # Fallback: derive from a combination of available secrets (better than nothing)
    fallback_seed = _os.environ.get("TELEGRAM_TOKEN", "") + _os.environ.get("STRIPE_SECRET_KEY", "") + "polytragent_fallback_v2"
    log.warning("[WALLET] WALLET_ENCRYPTION_KEY not set! Using fallback derivation. Set this env var for production!")
    return hashlib.sha256(fallback_seed.encode()).digest()


def _derive_fernet_key(salt: str) -> bytes:
    """Derive a per-wallet Fernet key using PBKDF2 with the master key."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    master = _get_master_key()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=f"polytragent_wallet_{salt}".encode(),
        iterations=480_000,  # OWASP recommended minimum
    )
    derived = kdf.derive(master)
    return base64.urlsafe_b64encode(derived)


def _encrypt_key(private_key: str, salt: str) -> str:
    """Encrypt private key with AES-256 via Fernet.
    Output is prefixed with 'fernet:' to distinguish from old XOR format."""
    from cryptography.fernet import Fernet
    fernet_key = _derive_fernet_key(salt)
    f = Fernet(fernet_key)
    encrypted = f.encrypt(private_key.encode())
    return "fernet:" + encrypted.decode()


def _decrypt_key(encrypted: str, salt: str) -> str:
    """Decrypt private key. Auto-detects Fernet vs legacy XOR format."""
    if encrypted.startswith("fernet:"):
        # New AES-256 Fernet format
        from cryptography.fernet import Fernet
        fernet_key = _derive_fernet_key(salt)
        f = Fernet(fernet_key)
        token = encrypted[len("fernet:"):].encode()
        return f.decrypt(token).decode()
    else:
        # Legacy XOR format — decrypt and flag for migration
        log.warning(f"[WALLET] Legacy XOR encryption detected for salt={salt[:8]}... — will migrate on next save")
        return _decrypt_key_legacy_xor(encrypted, salt)


def _decrypt_key_legacy_xor(encrypted: str, salt: str) -> str:
    """Decrypt old XOR-encrypted keys (backward compatibility only)."""
    encrypted_bytes = base64.b64decode(encrypted)
    salt_hash = hashlib.sha256(f"polytragent_{salt}_v1".encode()).digest()
    decrypted = bytes(b ^ salt_hash[i % len(salt_hash)] for i, b in enumerate(encrypted_bytes))
    return decrypted.decode()


def _migrate_wallet_encryption(chat_id: str, wallet_entry: dict) -> bool:
    """Re-encrypt a wallet from XOR to Fernet. Returns True if migrated."""
    enc = wallet_entry.get("private_key_encrypted", "")
    if not enc or enc.startswith("fernet:"):
        return False  # Already new format or empty
    try:
        # Decrypt with old XOR
        plaintext = _decrypt_key_legacy_xor(enc, str(chat_id))
        # Re-encrypt with Fernet
        wallet_entry["private_key_encrypted"] = _encrypt_key(plaintext, str(chat_id))
        wallet_entry["encryption_version"] = "fernet_v1"
        log.info(f"[WALLET] Migrated encryption for user {str(chat_id)[:8]}...")
        return True
    except Exception as e:
        log.error(f"[WALLET] Migration failed for {str(chat_id)[:8]}: {e}")
        return False


# ═══════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═══════════════════════════════════════════════

def format_wallets(wallets: list) -> str:
    """Format wallet list for Telegram."""
    if not wallets:
        return "📭 No wallets. Use /create_wallet to create one."

    lines = ["👛 <b>Your Wallets</b>", ""]

    for i, w in enumerate(wallets):
        primary = " ⭐" if w.get("is_primary") else ""
        addr = w["address"]
        short_addr = f"{addr[:6]}...{addr[-4:]}"
        lines.append(f"{i+1}. <b>{w.get('label', 'Wallet')}</b>{primary}")
        lines.append(f"   <code>{addr}</code>")

    return "\n".join(lines)


def format_balance(balance_info: dict) -> str:
    """Format balance info for Telegram."""
    addr = balance_info["address"]
    short = f"{addr[:6]}...{addr[-4:]}"
    usdc = balance_info.get("usdc")
    matic = balance_info.get("matic")
    breakdown = balance_info.get("usdc_breakdown")

    lines = [
        f"💰 <b>Balance</b> — {short}",
        "",
    ]

    if usdc is not None:
        lines.append(f"💵 USDC: <b>${usdc:.2f}</b>")
        # Show breakdown if both types are present
        if breakdown and breakdown.get("native", 0) > 0 and breakdown.get("bridged", 0) > 0:
            lines.append(f"  ↳ Native USDC: ${breakdown['native']:.2f}")
            lines.append(f"  ↳ USDC.e (bridged): ${breakdown['bridged']:.2f}")
        elif breakdown and breakdown.get("bridged", 0) > 0 and breakdown.get("native", 0) == 0:
            lines.append(f"  ↳ USDC.e (bridged)")
    else:
        lines.append("💵 USDC: <i>unavailable</i>")

    lines.append(f"⛽ MATIC: <b>{matic:.4f}</b>" if matic is not None else "⛽ MATIC: <i>unavailable</i>")

    if not balance_info.get("has_gas"):
        lines.append("\n⚠️ Low MATIC — you need gas to trade!")

    return "\n".join(lines)


def format_send_result(result: dict) -> str:
    """Format send result for Telegram."""
    if result.get("success"):
        return (
            f"✅ <b>USDC Sent</b>\n\n"
            f"💰 Amount: <b>${result['amount']:.2f} USDC</b>\n"
            f"📤 From: <code>{result['from'][:10]}...</code>\n"
            f"📥 To: <code>{result['to'][:10]}...</code>\n"
            f"🔗 <a href=\"{result['explorer_url']}\">View on PolygonScan</a>"
        )
    else:
        return f"❌ <b>Send Failed</b>\n\n{result.get('error', 'Unknown error')}"
