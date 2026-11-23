"""Vault-backed managed Solana wallet provisioning for registered ZombieCash users.

The generated 64-byte Solana keypair is submitted only to the server-side Supabase
RPC that encrypts it with Supabase Vault. This module never returns or logs secrets.
"""
from __future__ import annotations

import logging
import re

from nacl.signing import SigningKey
from solders.keypair import Keypair

log = logging.getLogger("zombcash.sniper.wallets")
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PUBLIC_KEY_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _base58_encode(raw: bytes) -> str:
    zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    return ("1" * zeroes) + (encoded or ("" if zeroes else "1"))


def _generate_solana_keypair() -> tuple[str, str]:
    signing_key = SigningKey.generate()
    seed = bytes(signing_key)
    public_key = bytes(signing_key.verify_key)
    secret_key = _base58_encode(seed + public_key)
    address = _base58_encode(public_key)
    if len(seed) != 32 or len(public_key) != 32 or not PUBLIC_KEY_RE.fullmatch(address):
        raise RuntimeError("Generated Solana keypair failed validation")
    try:
        validated = Keypair.from_base58_string(secret_key)
    except Exception as exc:
        raise RuntimeError("Generated Solana secret is not a valid 64-byte keypair") from exc
    if str(validated.pubkey()) != address:
        raise RuntimeError("Generated Solana keypair public address mismatch")
    return address, secret_key


async def ensure_managed_wallet(telegram_id: int, supabase_request) -> dict:
    """Return/create one managed wallet for an already registered Telegram user."""
    if not isinstance(telegram_id, int) or telegram_id <= 0:
        raise ValueError("registered_profile_required")

    existing = await supabase_request(
        "POST", "rpc/sniper_wallet_get", payload={"p_telegram_id": telegram_id}
    )
    if isinstance(existing, dict) and existing.get("publicKey"):
        return {
            "walletId": existing.get("walletId"),
            "publicKey": existing.get("publicKey"),
            "status": existing.get("status", "active"),
            "custodyMode": existing.get("custodyMode", "managed"),
            "created": False,
        }

    public_key, secret_key = _generate_solana_keypair()
    try:
        result = await supabase_request(
            "POST",
            "rpc/sniper_wallet_provision",
            payload={
                "p_telegram_id": telegram_id,
                "p_public_key": public_key,
                "p_secret_key": secret_key,
            },
        )
    finally:
        # Python strings are immutable; do not retain the secret in module state,
        # logs, exceptions, or API responses. The RPC request is sent over HTTPS.
        secret_key = None

    if not isinstance(result, dict) or not result.get("publicKey"):
        raise RuntimeError("wallet_provisioning_incomplete")
    if result.get("publicKey") != public_key and result.get("created"):
        raise RuntimeError("wallet_provisioning_address_mismatch")
    if not PUBLIC_KEY_RE.fullmatch(str(result.get("publicKey") or "")):
        raise RuntimeError("wallet_provisioning_returned_invalid_address")
    return {
        "walletId": result.get("walletId"),
        "publicKey": result.get("publicKey"),
        "status": result.get("status", "active"),
        "custodyMode": "managed",
        "created": bool(result.get("created")),
    }
