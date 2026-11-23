from types import SimpleNamespace

from soi.analytics.mint_supply_audit import audit_mint_supply


def _obs(kind, observed_at, event, mint="MintA"):
    return SimpleNamespace(
        kind=kind, mint=mint, observed_at=observed_at,
        payload={"event": event},
    )


def test_audit_matches_mint_account_total_supply_to_holder_rpc_snapshot():
    observations = [
        _obs("mint_supply_state", "2026-11-17T12:00:00+00:00", {
            "supply_raw": 1000000, "decimals": 6, "mint_account_slot": 88,
            "supply_matches_get_token_supply": True,
        }),
        _obs("holder_snapshot", "2026-11-17T12:01:00+00:00", {
            "supply": {"amount_raw": "1000000", "decimals": 6},
        }),
    ]
    result = audit_mint_supply(observations)
    assert result["matched"] == 1
    assert result["rows"][0]["status"] == "matched"
    assert result["rows"][0]["supply_semantics"] == "total_mint_supply"
    assert result["rows"][0]["circulating_supply_raw"] is None
    assert result["circulating_supply_estimated"] is False


def test_audit_keeps_supply_mismatch_visible():
    observations = [
        _obs("mint_supply_state", "2026-11-17T12:00:00+00:00", {
            "supply_raw": 1000000, "decimals": 6,
        }),
        _obs("holder_snapshot", "2026-11-17T12:01:00+00:00", {
            "supply": {"amount_raw": "999999", "decimals": 6},
        }),
    ]
    result = audit_mint_supply(observations)
    assert result["mismatches"] == 1
    assert result["rows"][0]["status"] == "mismatch"


def test_audit_reports_missing_comparison_as_unknown_not_zero():
    result = audit_mint_supply([
        _obs("mint_supply_state", "2026-11-17T12:00:00+00:00", {
            "supply_raw": 1000000, "decimals": 6,
        })
    ])
    assert result["not_compared"] == 1
    assert result["rows"][0]["holder_supply_matches_mint_account"] is None


def test_audit_surfaces_control_settings_as_signals_not_fraud_verdict():
    observations = [
        _obs("mint_supply_state", "2026-11-17T12:00:00+00:00", {
            "supply_raw": 1000000, "decimals": 6,
            "risk_indicators": {
                "mint_authority_present": True,
                "freeze_authority_present": True,
                "mint_close_authority_present": True,
                "permanent_delegate_present": True,
                "non_transferable": True,
                "confidential_transfer_enabled": True,
                "scaled_ui_amount_present": True,
                "interest_bearing_ui_amount": True,
                "transfer_hook_present": True,
                "transfer_hook_program_executable": True,
                "transfer_hook_upgrade_authority_present": True,
                "newer_transfer_fee_basis_points": 250,
                "transfer_fee_configured": True,
                "default_account_state": "frozen",
                "paused": False,
            },
        })
    ]
    result = audit_mint_supply(observations)
    row = result["rows"][0]
    assert "mint_authority_can_increase_supply" in row["risk_signals"]
    assert "freeze_authority_can_freeze_accounts" in row["risk_signals"]
    assert "permanent_delegate_can_transfer_or_burn" in row["risk_signals"]
    assert "token_is_non_transferable" in row["risk_signals"]
    assert "confidential_transfers_supported" in row["risk_signals"]
    assert "scaled_ui_amount_changes_displayed_amount" in row["risk_signals"]
    assert "interest_bearing_ui_amount_changes_displayed_balance" in row["risk_signals"]
    assert "mint_close_authority_present" in row["risk_signals"]
    assert "transfer_hook_program_executes_on_transfers" in row["risk_signals"]
    assert "transfer_hook_program_has_upgrade_authority" in row["risk_signals"]
    assert "transfer_fee_configured" in row["risk_signals"]
    assert "new_token_accounts_default_to_frozen" in row["risk_signals"]
    assert "token_operations_paused_at_observation" not in row["risk_signals"]
    assert result["risk_signals_are_not_a_fraud_verdict"] is True
