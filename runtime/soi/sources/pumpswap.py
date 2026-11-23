"""Read-only PumpSwap AMM event decoder based on the official Pump IDL."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Iterable

from soi.models import Observation
from collections import Counter

from soi.sources.pumpfun import (DecodeError, _Reader, _utc_from_unix,
                                 _program_data_lines, classify_signature_error,
                                 failed_transaction_program_diagnostic)

PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
CREATE_POOL_EVENT = bytes([177, 49, 12, 210, 160, 118, 167, 116])
BUY_EVENT = bytes([103, 244, 82, 31, 44, 245, 119, 119])
SELL_EVENT = bytes([62, 47, 55, 10, 165, 3, 220, 42])


class _PumpSwapReader(_Reader):
    def integer128(self, signed: bool = False) -> int:
        return int.from_bytes(self.take(16), "little", signed=signed)

    def byte(self) -> int:
        return self.integer(1)


def _decode(encoded: str) -> tuple[bytes, bytes]:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 PumpSwap event") from exc
    return raw[:8], raw[8:]


def decode_pumpswap_event(encoded: str) -> tuple[str, dict[str, Any]] | None:
    discriminator, payload = _decode(encoded)
    if discriminator not in (CREATE_POOL_EVENT, BUY_EVENT, SELL_EVENT):
        return None
    r = _PumpSwapReader(payload)
    if discriminator == CREATE_POOL_EVENT:
        kind = "pool_created"
        event = {
            "timestamp": r.integer(8, signed=True), "index": r.integer(2),
            "creator": r.pubkey(), "base_mint": r.pubkey(), "quote_mint": r.pubkey(),
            "base_mint_decimals": r.byte(), "quote_mint_decimals": r.byte(),
            "base_amount_in": r.integer(8), "quote_amount_in": r.integer(8),
            "pool_base_amount": r.integer(8), "pool_quote_amount": r.integer(8),
            "minimum_liquidity": r.integer(8), "initial_liquidity": r.integer(8),
            "lp_token_amount_out": r.integer(8), "pool_bump": r.byte(),
            "pool": r.pubkey(), "lp_mint": r.pubkey(),
            "user_base_token_account": r.pubkey(), "user_quote_token_account": r.pubkey(),
            "coin_creator": r.pubkey(), "is_mayhem_mode": r.boolean(),
            "creator_fee_bps": r.integer(8), "can_edit_creator_fee": r.boolean(),
            "is_holder_reward": r.boolean(),
        }
    elif discriminator == BUY_EVENT:
        kind = "trade_buy"
        event = {
            "timestamp": r.integer(8, signed=True),
            "base_amount_out": r.integer(8), "max_quote_amount_in": r.integer(8),
            "user_base_token_reserves": r.integer(8), "user_quote_token_reserves": r.integer(8),
            "pool_base_token_reserves": r.integer(8), "pool_quote_token_reserves": r.integer(8),
            "quote_amount_in": r.integer(8), "lp_fee_basis_points": r.integer(8),
            "lp_fee": r.integer(8), "protocol_fee_basis_points": r.integer(8),
            "protocol_fee": r.integer(8), "quote_amount_in_with_lp_fee": r.integer(8),
            "user_quote_amount_in": r.integer(8), "pool": r.pubkey(), "user": r.pubkey(),
            "user_base_token_account": r.pubkey(), "user_quote_token_account": r.pubkey(),
            "protocol_fee_recipient": r.pubkey(), "protocol_fee_recipient_token_account": r.pubkey(),
            "coin_creator": r.pubkey(), "coin_creator_fee_basis_points": r.integer(8),
            "coin_creator_fee": r.integer(8), "track_volume": r.boolean(),
            "total_unclaimed_tokens": r.integer(8), "total_claimed_tokens": r.integer(8),
            "current_sol_volume": r.integer(8), "last_update_timestamp": r.integer(8, signed=True),
            "min_base_amount_out": r.integer(8), "ix_name": r.string(),
            "cashback_fee_basis_points": r.integer(8), "cashback": r.integer(8),
            "buyback_fee_basis_points": r.integer(8), "buyback_fee": r.integer(8),
            "virtual_quote_reserves": r.integer128(signed=True), "can_boost": r.boolean(),
            "base_supply": r.integer(8), "holder_rewards_bps": r.integer(8),
            "holder_rewards": r.integer(8), "creator_fee_unclaimed": r.integer(8),
        }
    else:
        kind = "trade_sell"
        event = {
            "timestamp": r.integer(8, signed=True),
            "base_amount_in": r.integer(8), "min_quote_amount_out": r.integer(8),
            "user_base_token_reserves": r.integer(8), "user_quote_token_reserves": r.integer(8),
            "pool_base_token_reserves": r.integer(8), "pool_quote_token_reserves": r.integer(8),
            "quote_amount_out": r.integer(8), "lp_fee_basis_points": r.integer(8),
            "lp_fee": r.integer(8), "protocol_fee_basis_points": r.integer(8),
            "protocol_fee": r.integer(8), "quote_amount_out_without_lp_fee": r.integer(8),
            "user_quote_amount_out": r.integer(8), "pool": r.pubkey(), "user": r.pubkey(),
            "user_base_token_account": r.pubkey(), "user_quote_token_account": r.pubkey(),
            "protocol_fee_recipient": r.pubkey(), "protocol_fee_recipient_token_account": r.pubkey(),
            "coin_creator": r.pubkey(), "coin_creator_fee_basis_points": r.integer(8),
            "coin_creator_fee": r.integer(8), "cashback_fee_basis_points": r.integer(8),
            "cashback": r.integer(8), "buyback_fee_basis_points": r.integer(8),
            "buyback_fee": r.integer(8), "virtual_quote_reserves": r.integer128(signed=True),
            "can_boost": r.boolean(), "base_supply": r.integer(8),
            "holder_rewards_bps": r.integer(8), "holder_rewards": r.integer(8),
            "creator_fee_unclaimed": r.integer(8),
        }
    trailing = r.data[r.offset:]
    if trailing:
        if len(trailing) != 8:
            raise DecodeError(f"Unexpected trailing bytes in PumpSwap {kind}")
        # Keep the undocumented suffix opaque; only the official IDL prefix is decoded.
        event["unparsed_trailing_bytes_length"] = len(trailing)
    return kind, event


def extract_pumpswap_events(log_messages: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for _, encoded in _program_data_lines(log_messages, PROGRAM_ID):
        event = decode_pumpswap_event(encoded)
        if event is not None:
            found.append(event)
    return found


def observations_from_transaction(signature: str, tx: dict[str, Any],
                                   observed_at: str | None = None) -> list[Observation]:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    block_time = tx.get("blockTime")
    output = []
    logs = meta.get("logMessages") or []
    for log_index, encoded in _program_data_lines(logs, PROGRAM_ID):
        try:
            decoded = decode_pumpswap_event(encoded)
        except DecodeError as exc:
            # Preserve exact bytes without guessing the undocumented mainnet
            # BuyEvent/SellEvent layout. This record is explicitly not a trade.
            raw = base64.b64decode(encoded, validate=True)
            event_index = len(output)
            output.append(Observation(
                source="pumpswap_rpc", source_ref=f"{signature}:{event_index}",
                observed_at=observed, event_at=_utc_from_unix(block_time),
                kind="unparsed_event", mint=None,
                payload={"signature": signature, "slot": tx.get("slot"),
                         "block_time": block_time, "log_index": log_index,
                         "event_discriminator_hex": raw[:8].hex(),
                         "raw_event_base64": encoded,
                         "decoder_error": str(exc)[:200]},
                confidence="unverified",
            ))
            continue
        if decoded is None:
            continue
        event_index = len(output)
        kind, event = decoded
        mint = event.get("base_mint")
        output.append(Observation(
            source="pumpswap_rpc", source_ref=f"{signature}:{event_index}",
            observed_at=observed,
            event_at=_utc_from_unix(event.get("timestamp")) or _utc_from_unix(block_time),
            kind=kind, mint=mint,
            payload={"signature": signature, "slot": tx.get("slot"),
                     "block_time": block_time, "log_index": log_index, "event": event},
            confidence="observed",
        ))
    return output


def resolve_pool_mints(observations: Iterable[Observation]) -> dict[str, str]:
    """Build pool->mint mapping only from observed CreatePoolEvent records."""
    result: dict[str, str] = {}
    for obs in observations:
        if obs.kind == "pool_created":
            event = (obs.payload or {}).get("event", {})
            if event.get("pool") and event.get("base_mint"):
                result[event["pool"]] = event["base_mint"]
    return result


def resolve_pool_metadata(observations: Iterable[Observation]) -> dict[str, dict[str, Any]]:
    """Build pool metadata only from observed CreatePoolEvent records."""
    result: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if obs.kind == "pool_created":
            event = (obs.payload or {}).get("event", {})
            pool = event.get("pool")
            mint = event.get("base_mint")
            if pool and mint:
                result[pool] = {
                    "base_mint": mint,
                    "quote_mint": event.get("quote_mint"),
                    "base_decimals": event.get("base_mint_decimals"),
                    "quote_decimals": event.get("quote_mint_decimals"),
                }
    return result


def normalize_trade_amounts(observation: Observation, pool: dict[str, Any]) -> None:
    """Add token-unit amounts while retaining all raw integer values unchanged."""
    event = (observation.payload or {}).get("event", {})
    quote_decimals = pool.get("quote_decimals")
    base_decimals = pool.get("base_decimals")
    observation.mint = pool.get("base_mint")
    event["quote_mint"] = pool.get("quote_mint")
    event["quote_decimals"] = quote_decimals
    event["base_decimals"] = base_decimals
    if observation.kind == "trade_buy":
        raw_quote = event.get("quote_amount_in")
        raw_base = event.get("base_amount_out")
    else:
        raw_quote = event.get("quote_amount_out")
        raw_base = event.get("base_amount_in")
    event["raw_quote_amount"] = raw_quote
    event["raw_base_amount"] = raw_base
    event["quote_amount_ui"] = (raw_quote / (10 ** quote_decimals)
                                if raw_quote is not None and isinstance(quote_decimals, int) else None)
    event["base_amount_ui"] = (raw_base / (10 ** base_decimals)
                               if raw_base is not None and isinstance(base_decimals, int) else None)
    event["quote_amount_usd"] = None
    event["fee_quote_units"] = {
        "lp_fee_raw": event.get("lp_fee"),
        "protocol_fee_raw": event.get("protocol_fee"),
        "creator_fee_raw": event.get("coin_creator_fee"),
        "denomination": "quote_token_raw_units_unverified",
    }
    observation.payload["event"] = event


def attach_pool_mints(observations: Iterable[Observation]) -> list[Observation]:
    """Return trade observations with mint resolved from observed pool creation."""
    items = list(observations)
    pool_metadata = resolve_pool_metadata(items)
    for obs in items:
        if obs.kind in ("trade_buy", "trade_sell") and not obs.mint:
            pool = (obs.payload or {}).get("event", {}).get("pool")
            if pool in pool_metadata:
                normalize_trade_amounts(obs, pool_metadata[pool])
    return items


def scan_recent(rpc: Any, store: Any, limit: int = 100,
                pause_seconds: float = 0.15, bootstrap_max_pages: int = 10) -> dict[str, Any]:
    """Durably scan PumpSwap signatures; resolve trade mints from observed pool events."""
    import time
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if bootstrap_max_pages < 1:
        raise ValueError("bootstrap_max_pages must be at least 1")

    watermark = store.get_collector_state("pumpswap_watermark")
    anchor = store.get_collector_state("pumpswap_anchor")
    before = store.get_collector_state("pumpswap_before")
    bootstrap = watermark is None
    pages_done = int(store.get_collector_state("pumpswap_bootstrap_pages", "0") or 0)

    if anchor is None:
        signatures = rpc.recent_signatures_for_address(PROGRAM_ID, limit=limit) if hasattr(rpc, "recent_signatures_for_address") else (
            rpc.call("getSignaturesForAddress", [PROGRAM_ID, {"limit": limit, "commitment": "confirmed"}]) or [])
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
        store.set_collector_state("pumpswap_anchor", anchor)
        before = None
    else:
        signatures = (rpc.recent_signatures_for_address(PROGRAM_ID, limit=limit, before=before) if hasattr(rpc, "recent_signatures_for_address")
                      else rpc.call("getSignaturesForAddress", [PROGRAM_ID, {
                          "limit": limit, "commitment": "confirmed", **({"before": before} if before else {})}]) or [])

    scanned = inserted = failures = missing = unparsed_events = opaque_suffix_events = 0
    failed_signatures_skipped = 0
    failed_signature_errors: Counter[str] = Counter()
    failed_signature_program_samples: list[dict[str, str]] = []
    sampled_error_groups: set[str] = set()
    failure_reasons: list[str] = []
    rate_limited = False
    last_signature = None
    reached_watermark = False
    pool_metadata = resolve_pool_metadata(store.observations())
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
                observations = observations_from_transaction(signature, tx)
                unparsed_events += sum(item.kind == "unparsed_event" for item in observations)
                opaque_suffix_events += sum(
                    bool((item.payload or {}).get("event", {}).get("unparsed_trailing_bytes_length"))
                    for item in observations
                )
                pool_metadata.update(resolve_pool_metadata(observations))
                for observation in observations:
                    if observation.kind in ("trade_buy", "trade_sell") and not observation.mint:
                        pool = (observation.payload or {}).get("event", {}).get("pool")
                        if pool in pool_metadata:
                            normalize_trade_amounts(observation, pool_metadata[pool])
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
    cap_reached = bootstrap and next_pages_done >= bootstrap_max_pages
    complete = ((page_exhausted or cap_reached) if bootstrap else
                (reached_watermark or page_exhausted)) and failures == 0

    bootstrap_status = "not_applicable"
    if bootstrap:
        if complete:
            truncated = not page_exhausted
            store.set_collector_state("pumpswap_bootstrap_truncated", "true" if truncated else "false")
            bootstrap_status = "truncated" if truncated else "complete"
        else:
            bootstrap_status = "in_progress"

    if complete:
        store.set_collector_state("pumpswap_watermark", anchor)
        store.set_collector_state("pumpswap_anchor", None)
        store.set_collector_state("pumpswap_before", None)
        store.set_collector_state("pumpswap_bootstrap_pages", None)
    elif failures == 0 and last_signature:
        store.set_collector_state("pumpswap_before", last_signature)
        if bootstrap:
            store.set_collector_state("pumpswap_bootstrap_pages", next_pages_done)

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
