from soi.runtime import _safe_diagnostic, _stage_diagnostics


def test_diagnostic_redacts_urls_bearer_and_named_secrets():
    value = (
        "rpc https://rpc.example/path?api-key=private "
        "Bearer sk_private token=another-secret"
    )
    result = _safe_diagnostic(value)
    assert "rpc.example" not in result
    assert "sk_private" not in result
    assert "another-secret" not in result
    assert "[endpoint]" in result
    assert "[redacted]" in result


def test_stage_diagnostics_include_counts_but_not_rpc_cursor_identifiers():
    result = _stage_diagnostics({
        "pumpfun_rpc": {
            "signatures_returned": 5,
            "transactions_scanned": 4,
            "failed_signatures_skipped": 1,
            "failed_signature_error_groups": {"InstructionError/Custom:6001": 1},
            "failed_signature_program_samples": [{"error_group": "InstructionError/Custom:1", "program": "spl_token"}],
            "observations_inserted": 1,
            "unparsed_events": 2,
            "opaque_suffix_events": 3,
            "rate_limited": True,
            "sweep_complete": False,
            "bootstrap_status": "in_progress",
            "anchor": "signature-must-not-leak",
            "next_before": "another-signature",
        }
    })
    assert result["pumpfun_rpc"]["metrics"] == {
        "signatures_returned": 5,
        "transactions_scanned": 4,
        "failed_signatures_skipped": 1,
        "failed_signature_error_groups": {"InstructionError/Custom:6001": 1},
        "failed_signature_program_samples": [{"error_group": "InstructionError/Custom:1", "program": "spl_token"}],
        "observations_inserted": 1,
        "unparsed_events": 2,
        "opaque_suffix_events": 3,
        "rate_limited": True,
        "sweep_complete": False,
        "bootstrap_status": "in_progress",
    }
    assert "anchor" not in str(result)
    assert "signature-must-not-leak" not in str(result)


def test_default_rpc_catchup_limits_are_bounded():
    from soi.runtime import (
        DEFAULT_PUMPSWAP_LIMIT,
        DEFAULT_RPC_LIMIT,
        DEFAULT_RPC_PAUSE_SECONDS,
    )
    assert DEFAULT_RPC_LIMIT == 15
    assert DEFAULT_PUMPSWAP_LIMIT == 15
    assert DEFAULT_RPC_PAUSE_SECONDS == 1.1



def test_funding_provenance_error_details_are_redacted_and_address_free():
    result = _stage_diagnostics({
        "funding_provenance": {
            "requested": 2,
            "saved": 1,
            "errors": 1,
            "error_details": [
                {"error": "HTTP 429 from https://rpc.example/v1?api-key=private"}
            ],
        }
    })
    rendered = str(result["funding_provenance"])
    assert "429" in rendered
    assert "[endpoint]" in rendered
    assert "rpc.example" not in rendered
    assert "api-key=private" not in rendered
    assert "wallet" not in rendered.lower()


def test_public_collector_stages_distinguish_errors_partial_and_ok_without_leaking_details():
    from soi.runtime import _public_collector_stages

    result = _public_collector_stages({
        "pumpfun_rpc": {
            "metrics": {
                "signatures_returned": 15,
                "transactions_scanned": 1,
                "failed_signatures_skipped": 14,
                "sweep_complete": False,
                "opaque_suffix_events": 1,
            },
        },
        "pumpswap_pool_states": {
            "metrics": {"requested": 1, "saved": 1, "errors": 0},
        },
        "market_snapshots": {
            "metrics": {"requested": 10, "saved": 8, "errors": 2},
            "error_details": [{"error": "https://private-rpc.example/key?api-key=secret"}],
        },
        "_runtime": {"metrics": {"pumpfun_rpc_limit": 15}},
    })
    assert result["pumpfun_rpc"]["status"] == "partial"
    assert result["pumpfun_rpc"]["metrics"]["failed_signatures_skipped"] == 14
    assert result["pumpswap_pool_states"]["status"] == "ok"
    assert result["market_snapshots"]["status"] == "error"
    assert "private-rpc" not in str(result)
    assert "_runtime" not in result
