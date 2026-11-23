from soi import pipeline


class FakeStore:
    def __init__(self):
        self.state = {}
        self.saved = []

    def get_collector_state(self, key, default=None):
        return self.state.get(key, default)

    def set_collector_state(self, key, value):
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = str(value)

    def add(self, observation):
        self.saved.append(observation)
        return True


def test_bonding_curve_429_sets_persistent_global_cooldown(monkeypatch):
    store = FakeStore()
    calls = []

    def collect(_rpc, mint, address):
        calls.append((mint, address))
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(pipeline, "collect_bonding_curve_snapshot", collect)
    selected = [("MintA", "CurveA"), ("MintB", "CurveB"), ("MintC", "CurveC")]

    first = pipeline.collect_bonding_curve_stage(
        object(), store, selected, pause_seconds=0, retry_seconds=300,
        now=1000, sleep=lambda _seconds: None)

    assert first["requested"] == 3
    assert first["errors"] == 1
    assert first["deferred"] == 2
    assert first["rate_limited"] is True
    assert store.get_collector_state("bonding_curve_snapshot_global_retry_after") == "1300"
    assert calls == [("MintA", "CurveA")]

    second = pipeline.collect_bonding_curve_stage(
        object(), store, selected, pause_seconds=0, retry_seconds=300,
        now=1100, sleep=lambda _seconds: None)

    assert second["errors"] == 0
    assert second["deferred"] == 3
    assert second["backoff_seconds_remaining"] == 200
    assert calls == [("MintA", "CurveA")]


def test_bonding_curve_collection_continues_after_non_rate_limit_error(monkeypatch):
    store = FakeStore()
    calls = []

    def collect(_rpc, mint, address):
        calls.append(mint)
        if mint == "MintA":
            raise RuntimeError("account data unavailable")
        return {"mint": mint, "address": address}

    monkeypatch.setattr(pipeline, "collect_bonding_curve_snapshot", collect)
    stage = pipeline.collect_bonding_curve_stage(
        object(), store, [("MintA", "CurveA"), ("MintB", "CurveB")],
        pause_seconds=0, now=2000, sleep=lambda _seconds: None)

    assert stage["errors"] == 1
    assert stage["saved"] == 1
    assert stage["deferred"] == 0
    assert calls == ["MintA", "MintB"]
    assert len(store.saved) == 1
    assert store.get_collector_state("bonding_curve_snapshot_global_retry_after") is None
