from soi.analytics.temporal_chain import validate_funding_temporal_chain


def test_temporal_chain_accepts_direct_and_upstream_funding_before_anchors():
    direct = [{
        "wallet": "Buyer",
        "first_observed_buy_at": "2026-11-17T10:00:10+00:00",
        "transfers": [{
            "source_wallet": "Funder", "destination_wallet": "Buyer",
            "signature": "funding", "instruction_index": 0, "block_time": 1794909600
        }]
    }]
    provenance = {"funders": [{
        "wallet": "Funder", "first_observed_buy_at": "2026-11-17T09:59:00+00:00",
        "coverage": {"anchor_signature": "funding"}
    }]}
    result = validate_funding_temporal_chain(direct, provenance)
    assert result["status"] == "consistent"
    assert result["provenance_wallets_temporally_checked"] == 1


def test_temporal_chain_flags_direct_funding_after_first_buy():
    direct = [{
        "wallet": "Buyer", "first_observed_buy_at": "2026-11-17T10:00:10+00:00",
        "transfers": [{
            "source_wallet": "Funder", "destination_wallet": "Buyer",
            "signature": "late", "instruction_index": 0, "block_time": 1794909620
        }]
    }]
    result = validate_funding_temporal_chain(direct, {"funders": []})
    assert result["status"] == "anomalies_or_gaps"
    assert result["violations"][0]["type"] == "direct_funding_after_first_buy"


def test_temporal_chain_flags_upstream_funding_after_outgoing_transfer():
    direct = [{
        "wallet": "Buyer", "first_observed_buy_at": "2026-11-17T10:00:10+00:00",
        "transfers": [{
            "source_wallet": "Funder", "destination_wallet": "Buyer",
            "signature": "outgoing", "instruction_index": 0, "block_time": 1794909600
        }]
    }]
    provenance = {"funders": [{
        "wallet": "Funder", "first_observed_buy_at": "2026-11-17T10:01:00+00:00"
    }]}
    result = validate_funding_temporal_chain(direct, provenance)
    assert result["status"] == "anomalies_or_gaps"
    assert result["violations"][0]["type"] == "upstream_funding_after_outgoing_transfer"


def test_temporal_chain_detects_duplicate_direct_transfer_events():
    event = {"source_wallet": "Funder", "destination_wallet": "Buyer",
             "signature": "same", "instruction_index": 0, "block_time": 1794909600}
    direct = [{"wallet": "Buyer", "first_observed_buy_at": "2026-11-17T10:00:10+00:00",
               "transfers": [event, dict(event)]}]
    result = validate_funding_temporal_chain(direct, {"funders": []})
    assert result["duplicate_direct_transfer_events"] == 1
    assert result["status"] == "anomalies_or_gaps"


def test_temporal_chain_reports_malformed_and_non_finite_times_as_gaps():
    direct = [{
        "wallet": "Buyer", "first_observed_buy_at": "not-a-time",
        "transfers": [{
            "source_wallet": "Funder", "destination_wallet": "Buyer",
            "signature": "bad-time", "instruction_index": 0, "block_time": float("nan")
        }]
    }]
    result = validate_funding_temporal_chain(direct, {"funders": []})
    assert result["status"] == "anomalies_or_gaps"
    assert result["missing_or_invalid_timestamps"] == 1
    assert result["violations"] == []
