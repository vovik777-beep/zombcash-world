"""Cross-stage temporal consistency checks for the bounded funding evidence chain."""
from __future__ import annotations
from datetime import datetime, timezone
import math


def _timestamp(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            result = float(value)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result = parsed.timestamp()
        else:
            return None
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def validate_funding_temporal_chain(direct_funding: list[dict], provenance: dict) -> dict:
    """Check direct funding precedes first buy and upstream funding precedes its outgoing transfer."""
    violations, missing_times, duplicate_events = [], 0, 0
    seen, earliest_outgoing = set(), {}
    for buyer in direct_funding if isinstance(direct_funding, list) else []:
        if not isinstance(buyer, dict):
            continue
        buy_at = _timestamp(buyer.get("first_observed_buy_at"))
        for transfer in buyer.get("transfers", []) if isinstance(buyer.get("transfers"), list) else []:
            if not isinstance(transfer, dict):
                continue
            event_key = (transfer.get("signature"), transfer.get("instruction_index"),
                         transfer.get("source_wallet"), transfer.get("destination_wallet"))
            if event_key in seen:
                duplicate_events += 1
                violations.append({"type": "duplicate_direct_transfer_event", "event": list(event_key)})
                continue
            seen.add(event_key)
            transfer_at = _timestamp(transfer.get("block_time"))
            if buy_at is None or transfer_at is None:
                missing_times += 1
                continue
            if transfer_at > buy_at:
                violations.append({"type": "direct_funding_after_first_buy",
                    "wallet": buyer.get("wallet"), "signature": transfer.get("signature")})
            source = transfer.get("source_wallet")
            if source:
                current = earliest_outgoing.get(source)
                candidate = {"timestamp": transfer_at, "signature": transfer.get("signature")}
                if current is None or candidate["timestamp"] < current["timestamp"]:
                    earliest_outgoing[source] = candidate
                elif candidate["timestamp"] == current["timestamp"] and candidate["signature"] != current["signature"]:
                    current["conflict"] = True

    provenance_rows = provenance.get("funders", []) if isinstance(provenance, dict) else []
    checked_provenance = 0
    for row in provenance_rows:
        if not isinstance(row, dict) or row.get("error"):
            continue
        wallet = row.get("wallet")
        anchor_at = _timestamp(row.get("first_observed_buy_at"))
        direct_anchor = earliest_outgoing.get(wallet)
        if direct_anchor is None or direct_anchor.get("conflict"):
            continue
        if anchor_at is None:
            missing_times += 1
            continue
        checked_provenance += 1
        if anchor_at > direct_anchor["timestamp"]:
            violations.append({"type": "upstream_funding_after_outgoing_transfer",
                "wallet": wallet, "anchor_signature": row.get("coverage", {}).get("anchor_signature")
                    if isinstance(row.get("coverage"), dict) else None})

    return {
        "status": "consistent" if not violations and not missing_times else "anomalies_or_gaps",
        "direct_wallets_checked": len(direct_funding) if isinstance(direct_funding, list) else 0,
        "provenance_wallets_temporally_checked": checked_provenance,
        "duplicate_direct_transfer_events": duplicate_events,
        "missing_or_invalid_timestamps": missing_times,
        "violations": violations,
        "interpretation": "Checks observed timestamps only. Missing or bounded RPC observations do not prove absence of funding or complete history."
    }
