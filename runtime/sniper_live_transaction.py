"""Strict parsing of confirmed Solana transaction fills for Live accounting."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any


LAMPORTS_PER_SOL = Decimal("1000000000")


def _account_key(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        key = value.get("pubkey")
        return key if isinstance(key, str) else None
    return None


def _token_amount(entry: dict) -> Decimal | None:
    ui = entry.get("uiTokenAmount")
    if not isinstance(ui, dict):
        return None
    raw = ui.get("uiAmountString")
    if raw not in (None, ""):
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            return None
    else:
        raw_amount = ui.get("amount")
        decimals = ui.get("decimals")
        if raw_amount is None or not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 18:
            return None
        try:
            value = Decimal(str(raw_amount)) / (Decimal(10) ** decimals)
        except (InvalidOperation, ValueError):
            return None
    return value if value.is_finite() and value >= 0 else None


def extract_live_fill(transaction: Any, *, wallet_public_key: str, mint: str, side: str) -> dict | None:
    """Extract a wallet-owned token delta and SOL trade delta from a confirmed tx.

    The wallet's native SOL delta is adjusted for the transaction fee and the
    lamport delta of the traded token accounts (ATA rent creation/closure).
    This is intentionally fail-closed when owner or account-index evidence is
    missing; callers must not synthesize a fill from an estimated market price.
    """
    if side not in {"buy", "sell"} or not wallet_public_key or not mint:
        return None
    if not isinstance(transaction, dict):
        return None
    meta = transaction.get("meta")
    tx = transaction.get("transaction")
    if not isinstance(meta, dict) or meta.get("err") is not None or not isinstance(tx, dict):
        return None
    message = tx.get("message")
    if not isinstance(message, dict):
        return None

    account_keys = [_account_key(item) for item in (message.get("accountKeys") or [])]
    if any(not key for key in account_keys):
        return None
    loaded = meta.get("loadedAddresses") or {}
    if isinstance(loaded, dict):
        account_keys.extend([key for key in loaded.get("writable") or [] if isinstance(key, str)])
        account_keys.extend([key for key in loaded.get("readonly") or [] if isinstance(key, str)])
    try:
        wallet_index = account_keys.index(wallet_public_key)
    except ValueError:
        return None

    pre_balances = meta.get("preBalances")
    post_balances = meta.get("postBalances")
    fee_lamports = meta.get("fee")
    if (
        not isinstance(pre_balances, list)
        or not isinstance(post_balances, list)
        or wallet_index >= len(pre_balances)
        or wallet_index >= len(post_balances)
        or not isinstance(fee_lamports, int)
        or isinstance(fee_lamports, bool)
        or fee_lamports < 0
    ):
        return None
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in pre_balances + post_balances):
        return None

    pre_entries = meta.get("preTokenBalances") or []
    post_entries = meta.get("postTokenBalances") or []
    if not isinstance(pre_entries, list) or not isinstance(post_entries, list):
        return None

    def owned_amounts(entries):
        total = Decimal(0)
        indexes = set()
        found_owner_entry = False
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("mint") != mint:
                continue
            if entry.get("owner") != wallet_public_key:
                continue
            found_owner_entry = True
            amount = _token_amount(entry)
            index = entry.get("accountIndex")
            if amount is None or not isinstance(index, int) or isinstance(index, bool) or index < 0:
                return None, set(), False
            total += amount
            indexes.add(index)
        return total, indexes, found_owner_entry

    pre_tokens, pre_indexes, pre_found = owned_amounts(pre_entries)
    post_tokens, post_indexes, post_found = owned_amounts(post_entries)
    if pre_tokens is None or post_tokens is None or not (pre_found or post_found):
        return None

    token_delta = post_tokens - pre_tokens
    if side == "buy" and token_delta <= 0:
        return None
    if side == "sell" and token_delta >= 0:
        return None
    filled_tokens = abs(token_delta)

    token_account_indexes = pre_indexes | post_indexes
    token_accounts_lamport_delta = 0
    for index in token_account_indexes:
        if index >= len(pre_balances) or index >= len(post_balances):
            return None
        token_accounts_lamport_delta += post_balances[index] - pre_balances[index]

    wallet_delta = post_balances[wallet_index] - pre_balances[wallet_index]
    # wallet_delta includes tx fee and ATA rent. Add those back to isolate the
    # SOL transfer exchanged for the token; fees are stored separately.
    trade_delta_lamports = wallet_delta + token_accounts_lamport_delta + fee_lamports
    if side == "buy" and trade_delta_lamports >= 0:
        return None
    if side == "sell" and trade_delta_lamports <= 0:
        return None

    filled_sol = Decimal(abs(trade_delta_lamports)) / LAMPORTS_PER_SOL
    price_sol = filled_sol / filled_tokens
    if not filled_sol.is_finite() or not price_sol.is_finite() or filled_sol <= 0 or price_sol <= 0:
        return None

    slot = transaction.get("slot")
    block_time = transaction.get("blockTime")
    if slot is not None and (not isinstance(slot, int) or isinstance(slot, bool) or slot < 0):
        slot = None
    if block_time is not None and (not isinstance(block_time, int) or isinstance(block_time, bool) or block_time < 0):
        block_time = None

    return {
        "mint": mint,
        "side": side,
        "tokenAmount": str(filled_tokens),
        "solAmount": str(filled_sol),
        "priceSol": str(price_sol),
        "feeSol": str(Decimal(fee_lamports) / LAMPORTS_PER_SOL),
        "feeLamports": fee_lamports,
        "walletNetLamports": wallet_delta,
        "tokenAccountLamportsDelta": token_accounts_lamport_delta,
        "slot": slot,
        "blockTime": block_time,
    }
