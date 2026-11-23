"""Bounded read-only scan for direct SOL funding before first observed buy."""
from __future__ import annotations
from datetime import datetime, timezone
from soi.analytics.funding import extract_incoming_sol_transfers, summarize_funding_wallet

def collect_wallet_funding(rpc, wallet: str, first_buy_at: str, first_buy_signature: str | None = None, signatures_limit: int = 10, lookback_seconds: int = 86400, first_buy_anchor_conflict: bool = False) -> dict:
    if not 1 <= signatures_limit <= 25:
        raise ValueError("signatures_limit must be between 1 and 25")
    first_buy = datetime.fromisoformat(first_buy_at.replace("Z", "+00:00"))
    if first_buy.tzinfo is None:
        first_buy = first_buy.replace(tzinfo=timezone.utc)
    first_buy = first_buy.astimezone(timezone.utc)
    earliest = first_buy.timestamp() - lookback_seconds
    window_start = datetime.fromtimestamp(earliest, timezone.utc).isoformat()
    if first_buy_anchor_conflict:
        result = summarize_funding_wallet(wallet, first_buy_at, [])
        result["coverage"] = {
            "status": "incomplete_anchor_ambiguous", "anchor_ambiguous": True,
            "truncated_or_incomplete": True, "coverage_complete_within_lookback": False,
            "window_start": window_start, "anchor_time": first_buy.isoformat(),
            "errors": ["multiple first-buy signatures share the earliest observed timestamp"],
        }
        return result
    if not first_buy_signature:
        result = summarize_funding_wallet(wallet, first_buy_at, [])
        result["coverage"] = {
            "status": "incomplete_anchor_missing", "anchor_missing": True,
            "truncated_or_incomplete": True, "coverage_complete_within_lookback": False,
            "window_start": window_start, "anchor_time": first_buy.isoformat(),
            "errors": ["first observed buy signature unavailable"],
        }
        return result

    signatures = rpc.recent_signatures_for_address(
        wallet, limit=signatures_limit, before=first_buy_signature)
    transfers, scanned, errors = [], 0, []
    missing_metadata = 0
    invalid_transaction_time = 0
    transfers_outside_window = 0
    future_skipped = 0
    duplicate_signatures = 0
    timestamp_mismatches = 0
    duplicate_transfer_events = 0
    seen_signatures = set()
    seen_transfer_events = set()
    previous_signature_time = None
    signature_order_anomalies = 0
    oldest_scanned = None
    lookback_boundary_reached = False
    for item in signatures:
        block_time, signature = item.get("blockTime"), item.get("signature")
        if block_time is None or not signature:
            missing_metadata += 1
            continue
        try:
            block_time = float(block_time)
        except (TypeError, ValueError):
            missing_metadata += 1
            continue
        if signature in seen_signatures:
            duplicate_signatures += 1
            continue
        seen_signatures.add(signature)
        if previous_signature_time is not None and block_time > previous_signature_time:
            signature_order_anomalies += 1
        previous_signature_time = block_time
        if block_time > first_buy.timestamp():
            future_skipped += 1
            continue
        if block_time < earliest:
            lookback_boundary_reached = True
            break
        oldest_scanned = block_time if oldest_scanned is None else min(oldest_scanned, block_time)
        if item.get("err") is not None:
            continue
        scanned += 1
        try:
            tx = rpc.parsed_transaction(signature)
            if tx:
                tx_time = tx.get("blockTime")
                try:
                    tx_time = float(tx_time)
                except (TypeError, ValueError):
                    invalid_transaction_time += 1
                    continue
                if tx_time != block_time:
                    timestamp_mismatches += 1
                    continue
                if tx_time > first_buy.timestamp() or tx_time < earliest:
                    transfers_outside_window += 1
                    continue
                for transfer in extract_incoming_sol_transfers(tx, wallet):
                    event_key = (transfer.get("signature"), transfer.get("instruction_index"),
                                 transfer.get("source_wallet"), transfer.get("destination_wallet"))
                    if event_key in seen_transfer_events:
                        duplicate_transfer_events += 1
                        continue
                    seen_transfer_events.add(event_key)
                    transfers.append(transfer)
            else:
                errors.append({"signature": signature, "error": "parsed transaction unavailable"})
        except Exception as exc:
            errors.append({"signature": signature, "error": str(exc)[:160]})

    page_exhausted = len(signatures) < signatures_limit and missing_metadata == 0
    complete = (lookback_boundary_reached or page_exhausted) and not errors and missing_metadata == 0 and invalid_transaction_time == 0 and transfers_outside_window == 0 and timestamp_mismatches == 0 and duplicate_signatures == 0 and signature_order_anomalies == 0 and duplicate_transfer_events == 0
    if errors:
        status = "incomplete_transaction_errors"
    elif invalid_transaction_time:
        status = "incomplete_transaction_time_missing"
    elif timestamp_mismatches:
        status = "incomplete_signature_transaction_time_mismatch"
    elif transfers_outside_window:
        status = "incomplete_transaction_time_mismatch"
    elif duplicate_signatures or duplicate_transfer_events:
        status = "incomplete_duplicate_evidence"
    elif signature_order_anomalies:
        status = "incomplete_signature_order"
    elif missing_metadata:
        status = "incomplete_signature_metadata"
    elif lookback_boundary_reached:
        status = "complete_lookback_boundary_reached"
    elif page_exhausted:
        status = "complete_rpc_page_exhausted"
    else:
        status = "incomplete_signature_limit_reached"
    result = summarize_funding_wallet(wallet, first_buy_at, transfers)
    result["coverage"] = {
        "status": status, "signatures_returned": len(signatures),
        "transactions_scanned": scanned, "lookback_seconds": lookback_seconds,
        "signature_limit": signatures_limit, "anchor_signature": first_buy_signature,
        "anchor_time": first_buy.isoformat(), "window_start": window_start,
        "oldest_scanned_signature_time": (
            datetime.fromtimestamp(oldest_scanned, timezone.utc).isoformat()
            if oldest_scanned is not None else None),
        "lookback_boundary_reached": lookback_boundary_reached,
        "rpc_page_exhausted": page_exhausted,
        "future_signatures_skipped": future_skipped,
        "duplicate_signature_rows": duplicate_signatures,
        "signature_order_anomalies": signature_order_anomalies,
        "signature_transaction_timestamp_mismatches": timestamp_mismatches,
        "duplicate_transfer_events": duplicate_transfer_events,
        "transactions_missing_block_time": invalid_transaction_time,
        "transactions_outside_lookback_or_anchor": transfers_outside_window,
        "signatures_missing_metadata": missing_metadata,
        "coverage_complete_within_lookback": complete,
        "truncated_or_incomplete": not complete, "errors": errors,
    }
    return result
