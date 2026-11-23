from soi.analytics.funding import extract_incoming_sol_transfers, summarize_funding_wallet

def test_extracts_only_parsed_direct_system_transfers_into_wallet():
    tx = {"blockTime": 1794910000, "transaction": {"signatures": ["sig-a"], "message": {"instructions": [
        {"program": "system", "parsed": {"type": "transfer", "info": {"source": "FunderA", "destination": "WalletA", "lamports": 2500000000}}},
        {"program": "system", "parsed": {"type": "transfer", "info": {"source": "WalletA", "destination": "WalletA", "lamports": 500}}},
        {"program": "spl-token", "parsed": {"type": "transfer", "info": {"source": "FunderB", "destination": "WalletA", "amount": "9"}}},
        {"program": "system", "parsed": {"type": "transfer", "info": {"source": "FunderC", "destination": "OtherWallet", "lamports": 10}}},
    ]}}, "meta": {"err": None, "innerInstructions": []}}
    result = extract_incoming_sol_transfers(tx, "WalletA")
    assert len(result) == 1
    assert result[0]["source_wallet"] == "FunderA"
    assert result[0]["sol"] == 2.5
    assert result[0]["signature"] == "sig-a"

def test_failed_transaction_is_not_funding_evidence():
    tx = {"meta": {"err": {"InstructionError": [0, "Custom"]}}, "transaction": {"message": {"instructions": []}}}
    assert extract_incoming_sol_transfers(tx, "WalletA") == []

def test_summary_keeps_direct_source_interpretation():
    result = summarize_funding_wallet("WalletA", "2026-11-17T10:00:00+00:00", [
        {"source_wallet": "FunderA", "sol": 1.0}, {"source_wallet": "FunderA", "sol": 2.0}])
    assert result["direct_incoming_sol_total"] == 3.0
    assert result["direct_sources"] == ["FunderA"]
    assert "no identity" in result["interpretation"]



def test_funding_scan_anchors_signature_history_before_first_buy():
    from soi.sources.funding import collect_wallet_funding

    class FakeRpc:
        def __init__(self):
            self.before = None
        def recent_signatures_for_address(self, wallet, limit, before=None):
            self.before = before
            return [{"signature": "funding-tx", "blockTime": 1794910000, "err": None}]
        def parsed_transaction(self, signature):
            return {"blockTime": 1794910000,
                "transaction": {"signatures": [signature], "message": {"instructions": [
                    {"program": "system", "parsed": {"type": "transfer", "info": {
                        "source": "FunderA", "destination": "WalletA", "lamports": 1000000000}}}
                ]}}, "meta": {"err": None, "innerInstructions": []}}

    rpc = FakeRpc()
    result = collect_wallet_funding(rpc, "WalletA", "2026-11-18T00:00:00+00:00",
        first_buy_signature="first-buy-signature")
    assert rpc.before == "first-buy-signature"
    assert result["direct_incoming_sol_total"] == 1.0
    assert result["coverage"]["anchor_signature"] == "first-buy-signature"


def test_funding_scan_reports_missing_first_buy_anchor_without_scanning():
    from soi.sources.funding import collect_wallet_funding

    class FakeRpc:
        def recent_signatures_for_address(self, *args, **kwargs):
            raise AssertionError("must not scan without an anchor")

    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-17T10:00:00+00:00")
    assert result["coverage"]["anchor_missing"] is True
    assert result["coverage"]["truncated_or_incomplete"] is True

def test_funding_scan_reports_page_exhaustion_and_oldest_timestamp():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            return [{"signature": "old", "blockTime": 1794910000, "err": None}]
        def parsed_transaction(self, signature):
            return {"blockTime": 1794910000, "transaction": {"signatures": [signature], "message": {"instructions": []}}, "meta": {"err": None}}
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00", first_buy_signature="buy-anchor", signatures_limit=10)
    coverage = result["coverage"]
    assert coverage["status"] == "complete_rpc_page_exhausted"
    assert coverage["coverage_complete_within_lookback"] is True
    assert coverage["oldest_scanned_signature_time"] is not None
    assert result["direct_incoming_transfer_count"] == 0


def test_funding_scan_marks_full_page_as_incomplete_even_without_transfers():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            self.times = {f"s{i}": 1794910000-i for i in range(limit)}
            return [{"signature": f"s{i}", "blockTime": self.times[f"s{i}"], "err": None} for i in range(limit)]
        def parsed_transaction(self, signature):
            return {"blockTime": self.times[signature], "transaction": {"signatures": [signature], "message": {"instructions": []}}, "meta": {"err": None}}
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00", first_buy_signature="buy-anchor", signatures_limit=2)
    assert result["coverage"]["status"] == "incomplete_signature_limit_reached"
    assert result["coverage"]["coverage_complete_within_lookback"] is False


def test_funding_scan_marks_missing_signature_metadata_incomplete():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            return [{"signature": None, "blockTime": 1794910000}]
        def parsed_transaction(self, signature):
            raise AssertionError("invalid signature must not be fetched")
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00", first_buy_signature="buy-anchor")
    assert result["coverage"]["status"] == "incomplete_signature_metadata"
    assert result["coverage"]["signatures_missing_metadata"] == 1


def test_funding_scan_rejects_parsed_transaction_after_anchor_time():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            return [{"signature": "future-tx", "blockTime": 1794910000, "err": None}]
        def parsed_transaction(self, signature):
            return {"blockTime": 1795000000, "transaction": {"signatures": [signature],
                "message": {"instructions": [{
                    "program": "system", "parsed": {"type": "transfer", "info": {
                        "source": "FunderA", "destination": "WalletA", "lamports": 1000000000}}
                }]}}, "meta": {"err": None}}
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00",
        first_buy_signature="buy-anchor")
    assert result["direct_incoming_transfer_count"] == 0
    assert result["coverage"]["signature_transaction_timestamp_mismatches"] == 1


def test_funding_scan_detects_duplicate_signature_rows():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            return [
                {"signature": "same", "blockTime": 1794910000, "err": None},
                {"signature": "same", "blockTime": 1794910000, "err": None},
            ]
        def parsed_transaction(self, signature):
            return {"blockTime": 1794910000, "transaction": {"signatures": [signature],
                "message": {"instructions": []}}, "meta": {"err": None}}
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00",
        first_buy_signature="buy-anchor", signatures_limit=2)
    assert result["coverage"]["duplicate_signature_rows"] == 1
    assert result["coverage"]["status"] == "incomplete_duplicate_evidence"
    assert result["coverage"]["coverage_complete_within_lookback"] is False


def test_funding_scan_rejects_signature_transaction_timestamp_mismatch():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            return [{"signature": "sig-a", "blockTime": 1794910000, "err": None}]
        def parsed_transaction(self, signature):
            return {"blockTime": 1794910001, "transaction": {"signatures": [signature],
                "message": {"instructions": []}}, "meta": {"err": None}}
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00",
        first_buy_signature="buy-anchor")
    assert result["coverage"]["signature_transaction_timestamp_mismatches"] == 1
    assert result["coverage"]["status"] == "incomplete_signature_transaction_time_mismatch"
    assert result["coverage"]["coverage_complete_within_lookback"] is False


def test_funding_scan_detects_non_descending_signature_page():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, wallet, limit, before=None):
            return [
                {"signature": "newer", "blockTime": 1794910001, "err": None},
                {"signature": "older", "blockTime": 1794910000, "err": None},
                {"signature": "out-of-order", "blockTime": 1794910002, "err": None},
            ]
        def parsed_transaction(self, signature):
            times = {"newer": 1794910001, "older": 1794910000, "out-of-order": 1794910002}
            return {"blockTime": times[signature], "transaction": {"signatures": [signature],
                "message": {"instructions": []}}, "meta": {"err": None}}
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-18T00:00:00+00:00",
        first_buy_signature="buy-anchor", signatures_limit=3)
    assert result["coverage"]["signature_order_anomalies"] == 1
    assert result["coverage"]["status"] == "incomplete_signature_order"


def test_funding_scan_skips_ambiguous_first_buy_anchor():
    from soi.sources.funding import collect_wallet_funding
    class FakeRpc:
        def recent_signatures_for_address(self, *args, **kwargs):
            raise AssertionError("must not scan with ambiguous first-buy anchor")
    result = collect_wallet_funding(FakeRpc(), "WalletA", "2026-11-17T10:00:00+00:00",
        first_buy_signature="one-of-two", first_buy_anchor_conflict=True)
    assert result["coverage"]["status"] == "incomplete_anchor_ambiguous"
    assert result["coverage"]["coverage_complete_within_lookback"] is False
