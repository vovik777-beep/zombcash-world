"""Read-only Pump.fun CreateEvent decoder and low-volume JSON-RPC scanner.

The event layout and discriminator are pinned to the official Pump.fun IDL.
No signing, transaction submission, or wallet access is performed.
"""
from __future__ import annotations

import base64
import json
import os
import time
from collections import Counter
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable

from soi.models import Observation

PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
CREATE_EVENT_DISCRIMINATOR = bytes([27, 114, 169, 77, 222, 235, 99, 118])
TRADE_EVENT_DISCRIMINATOR = bytes([189, 219, 127, 211, 78, 230, 97, 238])
COMPLETE_EVENT_DISCRIMINATOR = bytes([95, 114, 97, 156, 212, 46, 152, 8])
COMPLETE_PUMP_AMM_MIGRATION_DISCRIMINATOR = bytes([189, 233, 93, 185, 92, 148, 234, 148])
DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"
DEFAULT_RPC_URLS = (
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
)


def _configured_rpc_urls(url: str | None = None) -> list[str]:
    """Resolve ordered RPC endpoints without logging or exposing credentials."""
    candidates = [url] if url else []
    candidates.extend(
        item.strip() for item in os.getenv("SOI_RPC_URLS", "").split(",")
        if item.strip()
    )
    # Keep provider-specific credentials additive: Alchemy is an optional
    # fallback and must never replace the existing ordered provider list.
    alchemy = os.getenv("SOI_ALCHEMY_RPC_URL", "").strip()
    if alchemy:
        candidates.append(alchemy)
    single = os.getenv("SOI_RPC_URL", "").strip()
    if single:
        candidates.append(single)
    if not candidates:
        candidates.extend(DEFAULT_RPC_URLS)
    return list(dict.fromkeys(candidates))

KNOWN_PROGRAM_LABELS = {
    PROGRAM_ID: "pumpfun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "pumpswap",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "spl_token",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": "token_2022",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "associated_token",
    "11111111111111111111111111111111": "system",
    "ComputeBudget111111111111111111111111111111": "compute_budget",
    "L2TExMFKdjpN9kozasaurPirfHy9P8sbXoAN1qA3S95": "lighthouse",
}


def _program_label(program_id: Any) -> str | None:
    if not isinstance(program_id, str) or not program_id:
        return None
    return KNOWN_PROGRAM_LABELS.get(program_id, "other_program")


def failed_transaction_program_diagnostic(tx: Any) -> dict[str, str]:
    """Return a safe category and, for unknown programs, a short lookup hint.

    Solana logs can report a failed nested CPI before the enclosing instruction
    fails. Prefer the instruction index in meta.err and resolve the top-level
    instruction's program ID, including v0 loaded addresses. For an unrecognized
    program, expose only its first eight base58 characters to distinguish
    repeated unknown programs; never return the full ID, signature, or raw logs.
    """
    if not isinstance(tx, dict):
        return {"program": "transaction_unavailable"}
    meta = tx.get("meta")
    program_id = None
    if isinstance(meta, dict):
        err = meta.get("err")
        instruction_error = err.get("InstructionError") if isinstance(err, dict) else None
        if (isinstance(instruction_error, (list, tuple))
                and len(instruction_error) >= 2
                and isinstance(instruction_error[0], int)
                and not isinstance(instruction_error[0], bool)):
            instruction_index = instruction_error[0]
            transaction = tx.get("transaction")
            message = transaction.get("message") if isinstance(transaction, dict) else None
            instructions = message.get("instructions") if isinstance(message, dict) else None
            if isinstance(instructions, list) and 0 <= instruction_index < len(instructions):
                instruction = instructions[instruction_index]
                if isinstance(instruction, dict):
                    direct_id = instruction.get("programId")
                    if isinstance(direct_id, str):
                        program_id = direct_id
                    else:
                        program_index = instruction.get("programIdIndex")
                        account_keys = message.get("accountKeys")
                        if (isinstance(program_index, int) and not isinstance(program_index, bool)
                                and isinstance(account_keys, list)):
                            keys = []
                            for key in account_keys:
                                if isinstance(key, str):
                                    keys.append(key)
                                elif isinstance(key, dict):
                                    keys.append(key.get("pubkey"))
                            loaded = meta.get("loadedAddresses")
                            if isinstance(loaded, dict):
                                keys.extend(key if isinstance(key, str) else None
                                            for key in (loaded.get("writable") or []))
                                keys.extend(key if isinstance(key, str) else None
                                            for key in (loaded.get("readonly") or []))
                            if 0 <= program_index < len(keys):
                                program_id = keys[program_index]

    # If transaction metadata did not resolve the failed top-level instruction,
    # use the first explicit failed-program log as a conservative fallback.
    if not isinstance(program_id, str):
        logs = meta.get("logMessages") if isinstance(meta, dict) else None
        if not isinstance(logs, list):
            return {"program": "logs_unavailable"}
        for line in logs:
            if isinstance(line, str) and line.startswith("Program ") and " failed:" in line:
                program_id = line[len("Program "):].split(" ", 1)[0]
                break
    if not isinstance(program_id, str):
        return {"program": "failure_program_unidentified"}
    label = _program_label(program_id) or "other_program"
    result = {"program": label}
    if label == "other_program":
        result["program_hint"] = program_id[:8]
    return result


def failed_transaction_program_label(tx: Any) -> str:
    """Compatibility wrapper returning only the allow-listed program category."""
    return failed_transaction_program_diagnostic(tx)["program"]


class DecodeError(ValueError):
    pass


def classify_signature_error(err: Any) -> str:
    """Return a stable, credential-free class for getSignaturesForAddress err values.

    Instruction indexes and all transaction/address identifiers are deliberately
    omitted. Custom program error codes are retained because they are useful for
    distinguishing repeated on-chain failure causes.
    """
    import re

    safe = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
    if not isinstance(err, dict):
        return "unknown_error_shape"

    instruction_error = err.get("InstructionError")
    if isinstance(instruction_error, (list, tuple)) and len(instruction_error) >= 2:
        detail = instruction_error[1]
        if isinstance(detail, dict) and detail:
            variant, value = next(iter(detail.items()))
            if not isinstance(variant, str) or not safe.fullmatch(variant):
                return "InstructionError/unknown"
            if variant == "Custom" and isinstance(value, int) and not isinstance(value, bool):
                return f"InstructionError/Custom:{value}"
            return f"InstructionError/{variant}"
        if isinstance(detail, str) and safe.fullmatch(detail):
            return f"InstructionError/{detail}"
        return "InstructionError/unknown"

    if len(err) == 1:
        variant = next(iter(err))
        if isinstance(variant, str) and safe.fullmatch(variant):
            return variant
    return "unknown_error_object"


_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    zeros = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, rem = divmod(number, 58)
        out = _ALPHABET[rem] + out
    return "1" * zeros + (out or ("" if zeros else "1"))


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise DecodeError("CreateEvent payload is truncated")
        part = self.data[self.offset:self.offset + size]
        self.offset += size
        return part

    def integer(self, size: int, signed: bool = False) -> int:
        return int.from_bytes(self.take(size), "little", signed=signed)

    def boolean(self) -> bool:
        value = self.integer(1)
        if value not in (0, 1):
            raise DecodeError("Invalid Borsh boolean")
        return bool(value)

    def string(self) -> str:
        length = self.integer(4)
        try:
            return self.take(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecodeError("Invalid UTF-8 in CreateEvent string") from exc

    def pubkey(self) -> str:
        return b58encode(self.take(32))

    def shareholders(self) -> list[dict[str, Any]]:
        count = self.integer(4)
        if count > 1000:
            raise DecodeError("Unreasonable shareholder vector length")
        return [{"address": self.pubkey(), "share_bps": self.integer(2)}
                for _ in range(count)]


def decode_create_event(encoded: str) -> dict[str, Any] | None:
    """Decode one base64 `Program data:` log; return None for other events."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 event data") from exc
    if not raw.startswith(CREATE_EVENT_DISCRIMINATOR):
        return None

    r = _Reader(raw[len(CREATE_EVENT_DISCRIMINATOR):])
    event = {
        "name": r.string(),
        "symbol": r.string(),
        "uri": r.string(),
        "mint": r.pubkey(),
        "bonding_curve": r.pubkey(),
        "user": r.pubkey(),
        "creator": r.pubkey(),
        "timestamp": r.integer(8, signed=True),
        "virtual_token_reserves": r.integer(8),
        "virtual_sol_reserves": r.integer(8),
        "real_token_reserves": r.integer(8),
        "token_total_supply": r.integer(8),
        "token_program": r.pubkey(),
        "is_mayhem_mode": r.boolean(),
        "is_cashback_enabled": r.boolean(),
        "quote_mint": r.pubkey(),
        "virtual_quote_reserves": r.integer(8),
        "creator_fee_bps": r.integer(8),
        "is_holder_reward": r.boolean(),
        "depth": r.integer(1),
    }
    if r.offset != len(r.data):
        raise DecodeError("Unexpected trailing bytes in CreateEvent")
    return event



def decode_trade_event(encoded: str) -> dict[str, Any] | None:
    """Decode the official Pump.fun TradeEvent from a base64 Program data log."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 event data") from exc
    if not raw.startswith(TRADE_EVENT_DISCRIMINATOR):
        return None

    r = _Reader(raw[len(TRADE_EVENT_DISCRIMINATOR):])
    event = {
        "mint": r.pubkey(),
        "sol_amount": r.integer(8),
        "token_amount": r.integer(8),
        "is_buy": r.boolean(),
        "user": r.pubkey(),
        "timestamp": r.integer(8, signed=True),
        "virtual_sol_reserves": r.integer(8),
        "virtual_token_reserves": r.integer(8),
        "real_sol_reserves": r.integer(8),
        "real_token_reserves": r.integer(8),
        "fee_recipient": r.pubkey(),
        "fee_basis_points": r.integer(8),
        "fee": r.integer(8),
        "creator": r.pubkey(),
        "creator_fee_basis_points": r.integer(8),
        "creator_fee": r.integer(8),
        "track_volume": r.boolean(),
        "total_unclaimed_tokens": r.integer(8),
        "total_claimed_tokens": r.integer(8),
        "current_sol_volume": r.integer(8),
        "last_update_timestamp": r.integer(8, signed=True),
        "ix_name": r.string(),
        "mayhem_mode": r.boolean(),
        "cashback_fee_basis_points": r.integer(8),
        "cashback": r.integer(8),
        "buyback_fee_basis_points": r.integer(8),
        "buyback_fee": r.integer(8),
        "shareholders": r.shareholders(),
        "quote_mint": r.pubkey(),
        "quote_amount": r.integer(8),
        "virtual_quote_reserves": r.integer(8),
        "real_quote_reserves": r.integer(8),
        "holder_rewards_bps": r.integer(8),
        "holder_rewards": r.integer(8),
        "creator_fee_unclaimed": r.integer(8),
    }
    trailing = r.data[r.offset:]
    if trailing:
        if len(trailing) != 8:
            raise DecodeError("Unexpected trailing bytes in TradeEvent")
        # Preserve the undocumented suffix length only; never interpret it.
        event["unparsed_trailing_bytes_length"] = len(trailing)
    return event


def decode_complete_event(encoded: str) -> dict[str, Any] | None:
    """Decode Pump.fun CompleteEvent: curve completion, not AMM migration."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 CompleteEvent data") from exc
    if not raw.startswith(COMPLETE_EVENT_DISCRIMINATOR):
        return None
    r = _Reader(raw[len(COMPLETE_EVENT_DISCRIMINATOR):])
    event = {"user": r.pubkey(), "mint": r.pubkey(),
             "bonding_curve": r.pubkey(), "timestamp": r.integer(8, signed=True),
             "quote_mint": r.pubkey()}
    if r.offset != len(r.data):
        raise DecodeError("Unexpected trailing bytes in CompleteEvent")
    return event


def decode_migration_event(encoded: str) -> dict[str, Any] | None:
    """Decode the official CompletePumpAmmMigrationEvent from the Pump IDL."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 migration event data") from exc
    if not raw.startswith(COMPLETE_PUMP_AMM_MIGRATION_DISCRIMINATOR):
        return None
    r = _Reader(raw[len(COMPLETE_PUMP_AMM_MIGRATION_DISCRIMINATOR):])
    event = {
        "user": r.pubkey(), "mint": r.pubkey(),
        "mint_amount": r.integer(8), "sol_amount": r.integer(8),
        "pool_migration_fee": r.integer(8), "bonding_curve": r.pubkey(),
        "timestamp": r.integer(8, signed=True), "pool": r.pubkey(),
        "quote_mint": r.pubkey(),
    }
    if r.offset != len(r.data):
        raise DecodeError("Unexpected trailing bytes in CompletePumpAmmMigrationEvent")
    return event


def _program_data_lines(log_messages: Iterable[str], program_id: str):
    """Yield (log index, base64) only for data emitted in this program's invocation.

    Bare Program-data fixtures remain supported when no invocation markers exist.
    Nested CPI output is attributed to the currently executing child program.
    """
    logs = list(log_messages or [])
    has_invocations = any(
        line.startswith("Program ") and " invoke [" in line for line in logs
    )
    stack = []
    for index, line in enumerate(logs):
        if line.startswith("Program ") and " invoke [" in line:
            invoked = line[len("Program "):].split(" invoke [", 1)[0]
            stack.append(invoked)
            continue
        if line.startswith("Program ") and (
            line.endswith(" success") or " failed:" in line
        ):
            finished = line[len("Program "):].split(" ", 1)[0]
            if stack and stack[-1] == finished:
                stack.pop()
            elif finished in stack:
                del stack[len(stack) - 1 - stack[::-1].index(finished):]
            continue
        if line.startswith("Program data: ") and (
            not has_invocations or (stack and stack[-1] == program_id)
        ):
            yield index, line.partition("Program data: ")[2].strip()


def extract_trade_events(log_messages: Iterable[str]) -> list[dict[str, Any]]:
    found = []
    for _, encoded in _program_data_lines(log_messages, PROGRAM_ID):
        event = decode_trade_event(encoded)
        if event is not None:
            found.append(event)
    return found


def extract_create_events(log_messages: Iterable[str]) -> list[dict[str, Any]]:
    found = []
    for _, encoded in _program_data_lines(log_messages, PROGRAM_ID):
        event = decode_create_event(encoded)
        if event is not None:
            found.append(event)
    return found


def completion_observations_from_transaction(signature: str, tx: dict[str, Any],
                                                 observed_at: str | None = None) -> list[Observation]:
    """Record canonical bonding-curve completion independently of migration."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    block_time = tx.get("blockTime")
    output = []
    for log_index, encoded in _program_data_lines(meta.get("logMessages") or [], PROGRAM_ID):
        event = decode_complete_event(encoded)
        if event is None:
            continue
        event_index = len(output)
        output.append(Observation(
            source="pumpfun_rpc", source_ref=f"{signature}:{event_index}",
            observed_at=observed,
            event_at=_utc_from_unix(event.get("timestamp")) or _utc_from_unix(block_time),
            kind="bonding_curve_completed", mint=event["mint"],
            payload={"signature": signature, "slot": tx.get("slot"),
                     "block_time": block_time, "log_index": log_index, "event": event},
            confidence="observed",
        ))
    return output


def migration_observations_from_transaction(signature: str, tx: dict[str, Any],
                                                observed_at: str | None = None) -> list[Observation]:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    block_time = tx.get("blockTime")
    output = []
    for log_index, encoded in _program_data_lines(meta.get("logMessages") or [], PROGRAM_ID):
        event = decode_migration_event(encoded)
        if event is None:
            continue
        event_index = len(output)
        output.append(Observation(
            source="pumpfun_rpc", source_ref=f"{signature}:{event_index}",
            observed_at=observed,
            event_at=_utc_from_unix(event.get("timestamp")) or _utc_from_unix(block_time),
            kind="migration_completed", mint=event["mint"],
            payload={"signature": signature, "slot": tx.get("slot"),
                     "block_time": block_time, "log_index": log_index, "event": event},
            confidence="observed",
        ))
    return output


def _utc_from_unix(value: int | float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class SolanaRpcClient:
    def __init__(self, url: str | None = None, timeout: int = 20):
        self.urls = _configured_rpc_urls(url)
        self.url = self.urls[0]
        self.timeout = timeout
        self._request_id = 0
        self.last_provider_index = 0
        self.failover_count = 0

    def call(self, method: str, params: list[Any]) -> Any:
        """Call ordered RPC providers, failing over only on transport/provider errors."""
        self._request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._request_id,
                           "method": method, "params": params}).encode()
        errors: list[str] = []
        for index, endpoint in enumerate(self.urls):
            req = urllib.request.Request(
                endpoint, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("invalid JSON-RPC response")
                if "error" in result:
                    raise RuntimeError(f"JSON-RPC error: {result['error']}")
                self.url = endpoint
                self.last_provider_index = index
                if index:
                    self.failover_count += 1
                return result.get("result")
            except Exception as exc:
                # Never include the endpoint URL: provider URLs may contain API keys.
                errors.append(f"provider_{index + 1}:{type(exc).__name__}")
        raise RuntimeError(
            f"All {len(self.urls)} Solana RPC providers failed for {method}: "
            + ", ".join(errors)
        )

    def recent_signatures_for_address(self, address: str, limit: int = 100, before: str | None = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        options: dict[str, Any] = {"limit": limit, "commitment": "confirmed"}
        if before:
            options["before"] = before
        return self.call("getSignaturesForAddress", [address, options]) or []

    def recent_signatures(self, limit: int = 100, before: str | None = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        options: dict[str, Any] = {"limit": limit, "commitment": "confirmed"}
        if before:
            options["before"] = before
        return self.call("getSignaturesForAddress", [PROGRAM_ID, options]) or []

    def parsed_transaction(self, signature: str) -> dict[str, Any] | None:
        """Fetch parsed instructions for read-only transaction analysis."""
        return self.call("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "confirmed",
            "maxSupportedTransactionVersion": 1,
        }])

    def token_supply(self, mint: str) -> dict[str, Any] | None:
        """Return the current SPL token supply response for a mint."""
        return self.call("getTokenSupply", [mint, {"commitment": "confirmed"}])

    def epoch_info(self) -> dict[str, Any] | None:
        """Return the current confirmed Solana epoch information."""
        return self.call("getEpochInfo", [{"commitment": "confirmed"}])

    def largest_token_accounts(self, mint: str) -> dict[str, Any] | None:
        """Return Solana's current top-20 token-account snapshot for a mint."""
        return self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])

    def parsed_accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        """Fetch parsed account metadata in one bounded RPC request."""
        if len(addresses) > 100:
            raise ValueError("getMultipleAccounts accepts at most 100 addresses")
        result = self.call("getMultipleAccounts", [addresses, {
            "encoding": "jsonParsed", "commitment": "confirmed", "withContext": True
        }])
        self.last_context_slot = (result or {}).get("context", {}).get("slot")
        return (result or {}).get("value", [])

    def account_info(self, address: str) -> dict[str, Any] | None:
        """Return one account's base64 data and metadata at the current confirmed slot."""
        return self.call("getAccountInfo", [address, {
            "encoding": "base64", "commitment": "confirmed", "withContext": True
        }])

    def transaction(self, signature: str) -> dict[str, Any] | None:
        return self.call("getTransaction", [signature, {
            "encoding": "json",
            "commitment": "confirmed",
            "maxSupportedTransactionVersion": 1,
        }])


def observations_from_transaction(signature: str, tx: dict[str, Any],
                                   observed_at: str | None = None) -> list[Observation]:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    block_time = tx.get("blockTime")
    event_time = _utc_from_unix(block_time)
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    logs = meta.get("logMessages") or []
    output = []
    for log_index, encoded in _program_data_lines(logs, PROGRAM_ID):
        event_index = len(output)
        event = decode_create_event(encoded)
        if event is None:
            continue
        # The signature + event index is a stable, idempotent event reference.
        output.append(Observation(
            source="pumpfun_rpc",
            source_ref=f"{signature}:{event_index}",
            observed_at=observed,
            event_at=_utc_from_unix(event.get("timestamp")) or event_time,
            kind="token_created",
            mint=event["mint"],
            payload={"signature": signature, "slot": tx.get("slot"),
                     "block_time": block_time, "log_index": log_index, "event": event},
            confidence="observed",
        ))
    return output


def trade_observations_from_transaction(signature: str, tx: dict[str, Any],
                                        observed_at: str | None = None) -> list[Observation]:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    output = []
    for log_index, encoded in _program_data_lines(meta.get("logMessages") or [], PROGRAM_ID):
        event_index = len(output)
        try:
            event = decode_trade_event(encoded)
        except DecodeError as exc:
            # The deployed Pump TradeEvent can contain an undocumented suffix.
            # Preserve the exact record as opaque data; never infer trade fields.
            raw = base64.b64decode(encoded, validate=True)
            output.append(Observation(
                source="pumpfun_rpc", source_ref=f"{signature}:{event_index}",
                observed_at=observed, event_at=_utc_from_unix(tx.get("blockTime")),
                kind="unparsed_event", mint=None,
                payload={"signature": signature, "slot": tx.get("slot"),
                         "block_time": tx.get("blockTime"), "log_index": log_index,
                         "event_discriminator_hex": raw[:8].hex(),
                         "raw_event_base64": encoded,
                         "decoder_error": str(exc)[:200]},
                confidence="unverified",
            ))
            continue
        if event is None:
            continue
        event_time = _utc_from_unix(event.get("timestamp")) or _utc_from_unix(tx.get("blockTime"))
        side = "buy" if event["is_buy"] else "sell"
        output.append(Observation(
            source="pumpfun_rpc", source_ref=f"{signature}:{event_index}",
            observed_at=observed, event_at=event_time, kind=f"trade_{side}",
            mint=event["mint"],
            payload={"signature": signature, "slot": tx.get("slot"),
                     "block_time": tx.get("blockTime"), "log_index": log_index, "event": event},
            confidence="observed",
        ))
    return output


def scan_recent(rpc: SolanaRpcClient, store: Any, limit: int = 100,
                pause_seconds: float = 0.15, bootstrap_max_pages: int = 10) -> dict[str, Any]:
    """Scan one durable page of Pump.fun signatures.

    Fresh databases perform a bounded, resumable initial backfill. The initial
    head is pinned as an anchor; pages are drained over successive cycles.
    If the configured page cap is reached before RPC history is exhausted, the
    collector records an explicit truncated-coverage flag rather than claiming
    a complete historical archive.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if bootstrap_max_pages < 1:
        raise ValueError("bootstrap_max_pages must be at least 1")

    watermark = store.get_collector_state("pumpfun_watermark")
    anchor = store.get_collector_state("pumpfun_anchor")
    before = store.get_collector_state("pumpfun_before")
    bootstrap = watermark is None
    pages_done = int(store.get_collector_state("pumpfun_bootstrap_pages", "0") or 0)

    if anchor is None:
        signatures = rpc.recent_signatures(limit=limit)
        if not signatures:
            return {"signatures_returned": 0, "transactions_scanned": 0,
                    "observations_inserted": 0, "failures": 0, "missing_transactions": 0,
                    "sweep_complete": True, "bootstrap_status": "empty"}
        anchor = signatures[0].get("signature")
        if not anchor:
            raise RuntimeError("Solana RPC returned a signature without a signature field")
        if not bootstrap and anchor == watermark:
            return {"signatures_returned": len(signatures), "transactions_scanned": 0,
                    "observations_inserted": 0, "failures": 0, "missing_transactions": 0,
                    "sweep_complete": True, "bootstrap_status": "not_applicable"}
        store.set_collector_state("pumpfun_anchor", anchor)
        before = None
    else:
        signatures = rpc.recent_signatures(limit=limit, before=before)

    scanned = inserted = failures = missing = unparsed_events = opaque_suffix_events = 0
    failed_signatures_skipped = 0
    failed_signature_errors: Counter[str] = Counter()
    failed_signature_program_samples: list[dict[str, str]] = []
    sampled_error_groups: set[str] = set()
    failure_reasons: list[str] = []
    rate_limited = False
    reached_watermark = False
    last_signature = None
    for item in signatures:
        signature = item.get("signature")
        if not signature:
            failures += 1
            failure_reasons.append("signature record missing signature field")
            continue
        if not bootstrap and signature == watermark:
            reached_watermark = True
            break
        last_signature = signature
        if item.get("err") is not None:
            failed_signatures_skipped += 1
            error_group = classify_signature_error(item.get("err"))
            failed_signature_errors[error_group] += 1
            # Bounded read-only attribution: sample at most three distinct error
            # groups per cycle. Diagnostic failures never affect scan counters,
            # cursors, watermark, or observation storage.
            if len(failed_signature_program_samples) < 3 and error_group not in sampled_error_groups:
                sampled_error_groups.add(error_group)
                try:
                    failed_tx = rpc.transaction(signature)
                    program_diagnostic = failed_transaction_program_diagnostic(failed_tx)
                except Exception:
                    program_diagnostic = {"program": "diagnostic_fetch_failed"}
                failed_signature_program_samples.append({
                    "error_group": error_group, **program_diagnostic
                })
                if pause_seconds:
                    time.sleep(pause_seconds)
            continue
        scanned += 1
        try:
            tx = rpc.transaction(signature)
            if tx is None:
                missing += 1
                failures += 1
                failure_reasons.append("getTransaction returned null")
            else:
                observations = (observations_from_transaction(signature, tx) +
                                trade_observations_from_transaction(signature, tx) +
                                completion_observations_from_transaction(signature, tx) +
                                migration_observations_from_transaction(signature, tx))
                unparsed_events += sum(item.kind == "unparsed_event" for item in observations)
                opaque_suffix_events += sum(
                    bool((item.payload or {}).get("event", {}).get("unparsed_trailing_bytes_length"))
                    for item in observations
                )
                for observation in observations:
                    inserted += int(store.add(observation))
        except Exception as exc:
            failures += 1
            failure_reasons.append(f"{type(exc).__name__}: {str(exc)[:180]}")
            rate_limited = (
                getattr(exc, "code", None) == 429
                or "HTTP Error 429" in str(exc)
                or "HTTP 429" in str(exc)
            )
        if pause_seconds:
            time.sleep(pause_seconds)
        if rate_limited:
            break

    page_exhausted = len(signatures) < limit
    next_pages_done = pages_done + 1 if bootstrap and failures == 0 else pages_done
    bootstrap_cap_reached = bootstrap and next_pages_done >= bootstrap_max_pages
    if bootstrap:
        complete = (page_exhausted or bootstrap_cap_reached) and failures == 0
    else:
        complete = (reached_watermark or page_exhausted) and failures == 0

    bootstrap_status = "not_applicable"
    if bootstrap:
        if complete:
            truncated = not page_exhausted
            store.set_collector_state("pumpfun_bootstrap_truncated", "true" if truncated else "false")
            bootstrap_status = "truncated" if truncated else "complete"
        else:
            bootstrap_status = "in_progress"

    if complete:
        store.set_collector_state("pumpfun_watermark", anchor)
        store.set_collector_state("pumpfun_anchor", None)
        store.set_collector_state("pumpfun_before", None)
        store.set_collector_state("pumpfun_bootstrap_pages", None)
    elif failures == 0 and last_signature:
        store.set_collector_state("pumpfun_before", last_signature)
        if bootstrap:
            store.set_collector_state("pumpfun_bootstrap_pages", next_pages_done)

    return {"signatures_returned": len(signatures), "transactions_scanned": scanned,
            "failed_signatures_skipped": failed_signatures_skipped,
            "failed_signature_error_groups": dict(sorted(failed_signature_errors.items())),
            "failed_signature_program_samples": failed_signature_program_samples,
            "observations_inserted": inserted, "failures": failures,
            "rate_limited": rate_limited,
            "unparsed_events": unparsed_events,
            "opaque_suffix_events": opaque_suffix_events,
            "failure_reasons": failure_reasons[:5],
            "missing_transactions": missing, "sweep_complete": complete,
            "watermark": watermark, "anchor": anchor,
            "next_before": None if complete else last_signature,
            "bootstrap_status": bootstrap_status,
            "bootstrap_pages_completed": next_pages_done if bootstrap else None,
            "bootstrap_max_pages": bootstrap_max_pages if bootstrap else None,
            "bootstrap_truncated": (bootstrap_status == "truncated") if bootstrap else None}
