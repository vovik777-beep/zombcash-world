from soi.sources.funding_provenance import collect_funder_provenance


class FakeRpc:
    def __init__(self):
        self.calls = []

    def recent_signatures_for_address(self, wallet, limit, before=None):
        self.calls.append({"wallet": wallet, "limit": limit, "before": before})
        return []


def test_provenance_anchors_before_earliest_outgoing_funding_transfer():
    rpc = FakeRpc()
    funding = [
        {"transfers": [
            {"source_wallet": "FunderA", "signature": "later-out", "block_time": 1795000000},
            {"source_wallet": "FunderA", "signature": "earlier-out", "block_time": 1794900000},
            {"source_wallet": "FunderB", "signature": "b-out", "block_time": 1794950000},
        ]}
    ]
    result = collect_funder_provenance(
        rpc, funding, source_limit=10, signatures_per_source=7, lookback_hours=12)
    assert rpc.calls == [
        {"wallet": "FunderA", "limit": 7, "before": "earlier-out"},
        {"wallet": "FunderB", "limit": 7, "before": "b-out"},
    ]
    assert result["depth"] == 1
    assert result["requested"] == 2
    assert result["errors"] == 0


def test_provenance_deduplicates_sources_and_reports_truncation():
    rpc = FakeRpc()
    funding = [{"transfers": [
        {"source_wallet": f"Funder{i}", "signature": f"sig{i}", "block_time": 1794900000+i}
        for i in range(4)
    ]}]
    result = collect_funder_provenance(rpc, funding, source_limit=2)
    assert result["unique_immediate_funders_seen"] == 4
    assert result["requested"] == 2
    assert result["truncated"] is True
    assert len(rpc.calls) == 2


def test_provenance_ignores_transfers_without_complete_anchor():
    rpc = FakeRpc()
    funding = [{"transfers": [
        {"source_wallet": "NoSignature", "block_time": 1794900000},
        {"source_wallet": "NoTime", "signature": "sig"},
    ]}]
    result = collect_funder_provenance(rpc, funding)
    assert result["requested"] == 0
    assert rpc.calls == []


def test_provenance_enforces_bounded_limits():
    rpc = FakeRpc()
    for kwargs in (
        {"source_limit": 21}, {"signatures_per_source": 26}, {"lookback_hours": 169}
    ):
        try:
            collect_funder_provenance(rpc, [], **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("expected bounded limit validation")

def test_provenance_quality_counts_complete_and_incomplete_lookbacks():
    class QualityRpc(FakeRpc):
        def recent_signatures_for_address(self, wallet, limit, before=None):
            if wallet == "Complete":
                return []
            self.times = {f"s{i}": 1794900000-i for i in range(limit)}
            return [{"signature": f"s{i}", "blockTime": self.times[f"s{i}"], "err": None} for i in range(limit)]
        def parsed_transaction(self, signature):
            return {"blockTime": self.times[signature], "transaction": {"signatures": [signature], "message": {"instructions": []}}, "meta": {"err": None}}
    funding = [{"transfers": [
        {"source_wallet": "Complete", "signature": "out-a", "block_time": 1794900000},
        {"source_wallet": "Limited", "signature": "out-b", "block_time": 1794900001},
    ]}]
    result = collect_funder_provenance(QualityRpc(), funding, source_limit=2, signatures_per_source=2)
    assert result["quality"]["complete_within_lookback"] == 1
    assert result["quality"]["incomplete_or_unknown"] == 1
    assert result["quality"]["status_counts"]["complete_rpc_page_exhausted"] == 1
    assert result["quality"]["status_counts"]["incomplete_signature_limit_reached"] == 1


def test_provenance_skips_conflicting_earliest_anchors():
    rpc = FakeRpc()
    funding = [{"transfers": [
        {"source_wallet": "Ambiguous", "signature": "sig-a", "block_time": 1794900000},
        {"source_wallet": "Ambiguous", "signature": "sig-b", "block_time": 1794900000},
        {"source_wallet": "Clear", "signature": "sig-c", "block_time": 1794900001},
    ]}]
    result = collect_funder_provenance(rpc, funding, source_limit=10)
    assert result["conflicting_earliest_anchors"] == 1
    assert result["skipped_conflicting_anchor_addresses"] == ["Ambiguous"]
    assert result["requested"] == 1
    assert rpc.calls == [{"wallet": "Clear", "limit": 10, "before": "sig-c"}]
