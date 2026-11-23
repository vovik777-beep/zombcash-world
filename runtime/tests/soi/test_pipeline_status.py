from soi.pipeline import has_collection_errors, is_rate_limited_error


def test_detects_top_level_stage_error():
    assert has_collection_errors({"pumpfun_rpc": {"error": "RPC unavailable"}})


def test_detects_partial_rpc_transaction_failures():
    assert has_collection_errors({"pumpfun_rpc": {"transactions_scanned": 10, "failures": 2}})


def test_detects_dexscreener_endpoint_and_market_errors():
    assert has_collection_errors({
        "promotion": {"profiles": 0, "ads_error": "429", "order_errors": [{"mint": "x"}]},
        "market_snapshots": {"saved": 1, "errors": [{"mint": "y", "error": "timeout"}]},
    })


def test_detects_plural_order_errors_without_other_error_fields():
    assert has_collection_errors({"promotion": {"order_errors": [{"mint": "x", "error": "429"}]}})


def test_clean_or_empty_stages_are_successful():
    assert not has_collection_errors({
        "pumpfun_rpc": {"transactions_scanned": 5, "failures": 0},
        "promotion": {"profiles": 2, "ads": 1, "boosts": 3, "orders": 0},
        "market_snapshots": {"saved": 2, "errors": []},
        "snapshot_queue_seed": {"created": 10},
    })


def test_unparsed_provider_events_are_reported_as_partial_coverage():
    assert has_collection_errors({"pumpswap_rpc": {
        "transactions_scanned": 12, "failures": 0, "unparsed_events": 3,
    }})



def test_detects_http_429_rate_limit():
    assert is_rate_limited_error(RuntimeError("HTTP Error 429: Too Many Requests"))


def test_detects_provider_status_429():
    class RateLimited:
        status = 429

    assert is_rate_limited_error(RateLimited())


def test_does_not_misclassify_other_errors_as_rate_limits():
    assert not is_rate_limited_error(RuntimeError("connection timeout"))
