from soi.models import Observation
from soi.public_report import build_daily_report

MINT = "Mint111"


def obs(kind, at, event, source="pumpfun_rpc"):
    return Observation(source, kind + at, at, at, kind, MINT, {"event": event}, "observed")


def test_public_report_keeps_unchecked_socials_unknown_and_exports_launch():
    rows = [
        obs("token_created", "2026-11-18T10:00:00+00:00", {"name": "Test", "symbol": "TST"}),
        obs("market_snapshot", "2026-11-18T10:10:00+00:00",
            {"market_cap_usd": 120000, "fdv_usd": 900000, "liquidity_usd": 5000}, "dexscreener"),
    ]
    report = build_daily_report(rows, "2026-11-18")
    item = report["launches"][0]
    assert item["name"] == "Test"
    assert item["socials"]["observed"] is False
    assert report["summary"]["without_social_links"] == 0
    assert item["market_cap_usd"] == 120000


def test_explicit_empty_social_links_and_boost_are_reported():
    rows = [
        obs("token_created", "2026-11-18T10:00:00+00:00", {"name": "Test"}),
        obs("profile_observed", "2026-11-18T10:01:00+00:00", {"links": []}, "dexscreener"),
        obs("boost_observed", "2026-11-18T10:02:00+00:00", {"amount": 10}, "dexscreener"),
        obs("trade_buy", "2026-11-17 23:59:00 UTC", {"amount": "1"}, "old_source"),
    ]
    report = build_daily_report(rows, "2026-11-18")
    item = report["launches"][0]
    assert item["socials"]["observed"] is True
    assert report["summary"]["without_social_links"] == 1
    assert item["promotion"]["boosted"] is True
    assert item["promoted"] is True


def test_profile_observation_alone_is_not_paid_promotion():
    rows = [
        obs("token_created", "2026-11-18T10:00:00+00:00", {"name": "Test"}),
        obs("profile_observed", "2026-11-18T10:01:00+00:00", {"links": []}, "dexscreener"),
    ]
    report = build_daily_report(rows, "2026-11-18")
    item = report["launches"][0]
    assert item["promotion"]["observed"] is False
    assert item["promoted"] is False
    assert report["summary"]["promoted_launches"] == 0


def test_sources_observed_includes_collectors_even_when_no_launches_were_found():
    rows = [
        obs("trade_buy", "2026-11-18T10:00:00+00:00", {"amount": "100"}, "pumpfun_rpc"),
        obs("pool_created", "2026-11-18T10:01:00+00:00", {"pool": "Pool111"}, "pumpswap_rpc"),
        obs("boost_observed", "2026-11-18T10:02:00+00:00", {"amount": 10}, "dexscreener"),
    ]
    report = build_daily_report(rows, "2026-11-18")
    assert report["summary"]["launches_detected"] == 0
    assert report["coverage"]["sources_observed"] == [
        "dexscreener", "pumpfun_rpc", "pumpswap_rpc",
    ]


def test_legacy_utc_timestamp_is_parsed_without_crashing_daily_report():
    from datetime import datetime, timezone
    from soi.public_report import _dt
    assert _dt("2026-11-17 23:42:26 UTC") == datetime(2026, 11, 17, 23, 42, 26, tzinfo=timezone.utc)
    assert _dt("not-a-date") is None



def test_live_report_uses_rolling_24h_across_midnight_and_keeps_daily_history():
    from datetime import datetime, timezone
    from soi.public_report import build_live_report

    rows = [
        Observation("pumpfun_rpc", "launch-old", "2026-11-17T13:00:00+00:00",
                    "2026-11-17T13:00:00+00:00", "token_created", "MintOld",
                    {"event": {"name": "Yesterday", "symbol": "YDAY"}}, "observed"),
        Observation("pumpfun_rpc", "launch-new", "2026-11-18T10:00:00+00:00",
                    "2026-11-18T10:00:00+00:00", "token_created", "MintNew",
                    {"event": {"name": "Today", "symbol": "TDAY"}}, "observed"),
        Observation("pumpfun_rpc", "buy-new", "2026-11-18T10:05:00+00:00",
                    "2026-11-18T10:05:00+00:00", "trade_buy", "MintNew",
                    {"event": {"user": "WalletA"}}, "observed"),
        Observation("pumpswap_rpc", "pool-new", "2026-11-18T10:06:00+00:00",
                    "2026-11-18T10:06:00+00:00", "pool_created", "MintNew",
                    {"event": {"pool": "PoolA"}}, "observed"),
        Observation("pumpfun_rpc", "launch-too-old", "2026-11-17T10:00:00+00:00",
                    "2026-11-17T10:00:00+00:00", "token_created", "MintTooOld",
                    {"event": {"name": "Old", "symbol": "OLD"}}, "observed"),
    ]
    report = build_live_report(
        rows, now=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
        history_days=3,
    )
    rolling = report["rolling_24h"]
    assert rolling["summary"]["launches_detected"] == 2
    assert rolling["summary"]["buys"] == 1
    assert rolling["summary"]["pools_created"] == 1
    assert {x["mint"] for x in rolling["launches"]} == {"MintOld", "MintNew"}
    assert [x["date"] for x in report["daily_history"]] == ["2026-11-17", "2026-11-18"]
    assert [x["launches_detected"] for x in report["daily_history"]] == [2, 1]
    assert report["history"]["timezone"] == "UTC"


def test_first_hour_report_exposes_scheduled_snapshot_coverage():
    rows = [
        obs("token_created", "2026-11-18T10:00:00+00:00", {"name": "Test"}),
        obs("market_snapshot", "2026-11-18T10:01:00+00:00", {
            "market_cap_usd": 100000, "liquidity_usd": 5000,
            "scheduled_offset_minute": 1,
        }, "dexscreener"),
        obs("market_snapshot", "2026-11-18T10:05:00+00:00", {
            "market_cap_usd": 150000, "liquidity_usd": 7000,
            "scheduled_offset_minute": 5,
        }, "dexscreener"),
    ]
    report = build_daily_report(rows, "2026-11-18")
    launch = report["launches"][0]

    assert launch["market_snapshot_count"] == 2
    assert launch["market_snapshot_observed_at"] == "2026-11-18T10:05:00+00:00"
    assert launch["first_hour"]["market_snapshot_count"] == 2
    assert launch["first_hour"]["scheduled_snapshot_offsets"] == [1, 5]
    assert launch["first_hour"]["expected_snapshot_offsets"] == [1, 5, 15, 30, 60]
    assert launch["first_hour"]["snapshot_coverage_ratio"] == 0.4
    assert launch["first_hour"]["peak_market_cap_usd"] == 150000
