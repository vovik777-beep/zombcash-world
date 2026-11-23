from types import SimpleNamespace

from soi.analytics.event_integrity import audit_event_integrity


def obs(kind, mint, ref, signature, event_at, block_time, event=None, slot=1):
    return SimpleNamespace(
        source="pumpfun_rpc", source_ref=ref, kind=kind, mint=mint,
        event_at=event_at,
        payload={"signature": signature, "slot": slot, "block_time": block_time,
                 "event": event or {}},
    )


def test_clean_single_launch_and_trade_has_no_internal_anomalies():
    rows = [
        obs("token_created", "mint-a", "s1:0", "s1",
            "2026-11-17T10:00:00+00:00", 1794909_600),
        obs("trade_buy", "mint-a", "s2:0", "s2",
            "2026-11-17T10:00:20+00:00", 1794909_620),
    ]
    report = audit_event_integrity(rows)
    assert report["status"] == "no_internal_anomalies_observed"
    assert report["conflicting_launch_mints"] == 0


def test_detects_multiple_conflicting_launch_records_for_same_mint():
    rows = [
        obs("token_created", "mint-a", "s1:0", "s1",
            "2026-11-17T10:00:00+00:00", 1794909_600),
        obs("token_created", "mint-a", "s2:0", "s2",
            "2026-11-17T10:01:00+00:00", 1794909_660),
    ]
    report = audit_event_integrity(rows)
    assert report["conflicting_launch_mints"] == 1
    assert any(x["type"] == "multiple_launch_observations" for x in report["findings"])


def test_detects_pool_mapping_and_transaction_metadata_conflicts():
    rows = [
        obs("pool_created", "mint-a", "p1:0", "p1",
            "2026-11-17T10:00:00+00:00", 1794909_600,
            {"pool": "pool-x", "base_mint": "mint-a", "quote_mint": "quote-a"}),
        obs("pool_created", "mint-b", "p2:0", "p2",
            "2026-11-17T10:00:00+00:00", 1794909_600,
            {"pool": "pool-x", "base_mint": "mint-b", "quote_mint": "quote-b"}),
        obs("trade_buy", "mint-a", "p1:1", "p1",
            "2026-11-17T10:00:10+00:00", 1794909_610, slot=2),
    ]
    report = audit_event_integrity(rows)
    assert report["conflicting_pools"] == 1
    assert report["conflicting_transaction_metadata"] == 1


def test_detects_bad_timestamps_and_block_time_mismatch():
    rows = [
        obs("trade_buy", "mint-a", "s1:0", "s1", "not-a-time", 1794909_600),
        obs("trade_sell", "mint-a", "s2:0", "s2",
            "2026-11-17T10:10:00+00:00", 1794909_600),
    ]
    report = audit_event_integrity(rows)
    assert report["invalid_event_timestamps"] == 1
    assert report["event_block_time_mismatches_over_120s"] == 1


def test_creator_conflict_is_a_launch_conflict_even_when_signature_and_time_match():
    rows = [
        obs("token_created", "mint-a", "s1:0", "s1",
            "2026-11-17T10:00:00+00:00", 1794909600,
            {"creator": "creator-a"}),
        obs("token_created", "mint-a", "s1:1", "s1",
            "2026-11-17T10:00:00+00:00", 1794909600,
            {"creator": "creator-b"}),
    ]
    report = audit_event_integrity(rows)
    assert report["conflicting_launch_mints"] == 1
    finding = next(x for x in report["findings"]
                   if x["type"] == "multiple_launch_observations")
    assert finding["distinct_creators"] == 2
