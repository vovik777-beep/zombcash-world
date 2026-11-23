"""Cross-check independently decoded mint supply against holder RPC snapshots."""
from __future__ import annotations


def audit_mint_supply(observations):
    """Compare raw total supply observations; never infer circulating supply."""
    mint_states = {}
    holder_states = {}
    for item in observations:
        event = (item.payload or {}).get("event", {})
        if not item.mint:
            continue
        if item.kind == "mint_supply_state":
            current = mint_states.get(item.mint)
            if current is None or item.observed_at > current.observed_at:
                mint_states[item.mint] = item
        elif item.kind == "holder_snapshot":
            supply = event.get("supply") or {}
            current = holder_states.get(item.mint)
            if current is None or item.observed_at > current["observed_at"]:
                holder_states[item.mint] = {
                    "observed_at": item.observed_at,
                    "amount_raw": supply.get("amount_raw"),
                    "decimals": supply.get("decimals"),
                }

    rows = []
    for mint in sorted(set(mint_states) | set(holder_states)):
        state = mint_states.get(mint)
        event = ((state.payload or {}).get("event", {}) if state else {})
        holder = holder_states.get(mint)
        mint_raw = event.get("supply_raw")
        mint_decimals = event.get("decimals")
        holder_raw = holder.get("amount_raw") if holder else None
        holder_decimals = holder.get("decimals") if holder else None
        comparable = (
            isinstance(mint_raw, int) and not isinstance(mint_raw, bool)
            and isinstance(holder_raw, str) and holder_raw.isdigit()
            and isinstance(mint_decimals, int) and not isinstance(mint_decimals, bool)
            and isinstance(holder_decimals, int) and not isinstance(holder_decimals, bool)
        )
        matches = (mint_raw == int(holder_raw) and mint_decimals == holder_decimals) if comparable else None
        risk_indicators = event.get("risk_indicators") or {}
        risk_signals = []
        if risk_indicators.get("mint_authority_present"):
            risk_signals.append("mint_authority_can_increase_supply")
        if risk_indicators.get("freeze_authority_present"):
            risk_signals.append("freeze_authority_can_freeze_accounts")
        if risk_indicators.get("permanent_delegate_present"):
            risk_signals.append("permanent_delegate_can_transfer_or_burn")
        if risk_indicators.get("non_transferable"):
            risk_signals.append("token_is_non_transferable")
        if risk_indicators.get("confidential_transfer_enabled"):
            risk_signals.append("confidential_transfers_supported")
        if risk_indicators.get("scaled_ui_amount_present"):
            risk_signals.append("scaled_ui_amount_changes_displayed_amount")
        if risk_indicators.get("interest_bearing_ui_amount"):
            risk_signals.append("interest_bearing_ui_amount_changes_displayed_balance")
        if risk_indicators.get("mint_close_authority_present"):
            risk_signals.append("mint_close_authority_present")
        if risk_indicators.get("transfer_hook_present"):
            risk_signals.append("transfer_hook_program_executes_on_transfers")
        if risk_indicators.get("transfer_hook_program_executable") is False:
            risk_signals.append("transfer_hook_program_not_executable_at_observation")
        if risk_indicators.get("transfer_hook_upgrade_authority_present"):
            risk_signals.append("transfer_hook_program_has_upgrade_authority")
        if risk_indicators.get("transfer_fee_configured"):
            risk_signals.append("transfer_fee_configured")
        if risk_indicators.get("default_account_state") == "frozen":
            risk_signals.append("new_token_accounts_default_to_frozen")
        if risk_indicators.get("paused") is True:
            risk_signals.append("token_operations_paused_at_observation")
        rows.append({
            "mint": mint,
            "status": "matched" if matches is True else (
                "mismatch" if matches is False else "not_compared"
            ),
            "mint_account_supply_raw": str(mint_raw) if mint_raw is not None else None,
            "mint_account_decimals": mint_decimals,
            "holder_rpc_supply_raw": holder_raw,
            "holder_rpc_decimals": holder_decimals,
            "mint_account_slot": event.get("mint_account_slot"),
            "mint_supply_matches_get_token_supply": event.get("supply_matches_get_token_supply"),
            "holder_supply_matches_mint_account": matches,
            "supply_semantics": "total_mint_supply",
            "circulating_supply_raw": None,
            "circulating_supply_status": "not_estimated",
            "risk_indicators": risk_indicators,
            "risk_signals": risk_signals,
        })
    return {
        "mints_checked": len(rows),
        "matched": sum(row["status"] == "matched" for row in rows),
        "mismatches": sum(row["status"] == "mismatch" for row in rows),
        "not_compared": sum(row["status"] == "not_compared" for row in rows),
        "circulating_supply_estimated": False,
        "risk_signal_counts": {
            signal: sum(signal in row["risk_signals"] for row in rows)
            for signal in (
                "mint_authority_can_increase_supply",
                "freeze_authority_can_freeze_accounts",
                "permanent_delegate_can_transfer_or_burn",
                "token_is_non_transferable",
                "confidential_transfers_supported",
                "scaled_ui_amount_changes_displayed_amount",
                "interest_bearing_ui_amount_changes_displayed_balance",
                "mint_close_authority_present",
                "transfer_hook_program_executes_on_transfers",
                "transfer_hook_program_not_executable_at_observation",
                "transfer_hook_program_has_upgrade_authority",
                "transfer_fee_configured",
                "new_token_accounts_default_to_frozen",
                "token_operations_paused_at_observation",
            )
        },
        "risk_signals_are_not_a_fraud_verdict": True,
        "rows": rows,
    }
