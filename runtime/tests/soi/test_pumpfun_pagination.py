from soi.sources.pumpfun import scan_recent


class MemoryStore:
    def __init__(self):
        self.state = {}
        self.items = []

    def get_collector_state(self, key, default=None):
        return self.state.get(key, default)

    def set_collector_state(self, key, value):
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = str(value)

    def add(self, item):
        self.items.append(item)
        return True


class FakeRpc:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def recent_signatures(self, limit=100, before=None):
        self.calls.append(before)
        return self.pages[before][:limit]

    def transaction(self, signature):
        return {"meta": {"err": None, "logMessages": []}, "blockTime": 1800000000}


def sig(value):
    return {"signature": value, "err": None}


def test_pumpfun_scan_paginates_backlog_until_previous_watermark():
    store = MemoryStore()
    bootstrap_rpc = FakeRpc({None: [sig("s5"), sig("s4")]})
    first = scan_recent(bootstrap_rpc, store, limit=2, pause_seconds=0, bootstrap_max_pages=1)
    assert first["sweep_complete"] is True
    assert store.state["pumpfun_watermark"] == "s5"

    pages = {
        None: [sig("s7"), sig("s6")],
        "s6": [sig("s5"), sig("s4")],
    }
    rpc = FakeRpc(pages)
    page_one = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert page_one["sweep_complete"] is False
    assert store.state["pumpfun_anchor"] == "s7"
    assert store.state["pumpfun_before"] == "s6"

    page_two = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert page_two["sweep_complete"] is True
    assert store.state["pumpfun_watermark"] == "s7"
    assert "pumpfun_anchor" not in store.state
    assert "pumpfun_before" not in store.state


def test_pumpfun_scan_does_not_advance_checkpoint_on_missing_transaction():
    store = MemoryStore()
    store.state["pumpfun_watermark"] = "old"
    rpc = FakeRpc({None: [sig("new"), sig("old")]})
    rpc.transaction = lambda signature: None if signature == "new" else {
        "meta": {"err": None, "logMessages": []}, "blockTime": 1800000000
    }
    result = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert result["failures"] == 1
    assert result["missing_transactions"] == 1
    assert result["sweep_complete"] is False
    assert store.state["pumpfun_watermark"] == "old"
    assert store.state["pumpfun_anchor"] == "new"



def test_fresh_bootstrap_backfills_multiple_pages_across_cycles():
    store = MemoryStore()
    rpc = FakeRpc({
        None: [sig("s5"), sig("s4")],
        "s4": [sig("s3")],
    })
    first = scan_recent(rpc, store, limit=2, pause_seconds=0, bootstrap_max_pages=3)
    assert first["sweep_complete"] is False
    assert first["bootstrap_status"] == "in_progress"
    assert store.state["pumpfun_anchor"] == "s5"
    assert store.state["pumpfun_before"] == "s4"
    assert store.state["pumpfun_bootstrap_pages"] == "1"
    assert "pumpfun_watermark" not in store.state

    second = scan_recent(rpc, store, limit=2, pause_seconds=0, bootstrap_max_pages=3)
    assert second["sweep_complete"] is True
    assert second["bootstrap_status"] == "complete"
    assert second["bootstrap_truncated"] is False
    assert store.state["pumpfun_watermark"] == "s5"
    assert store.state["pumpfun_bootstrap_truncated"] == "false"
    assert "pumpfun_anchor" not in store.state
    assert "pumpfun_before" not in store.state


def test_bootstrap_page_cap_marks_history_as_truncated():
    store = MemoryStore()
    rpc = FakeRpc({None: [sig("s5"), sig("s4")]})
    result = scan_recent(rpc, store, limit=2, pause_seconds=0, bootstrap_max_pages=1)
    assert result["sweep_complete"] is True
    assert result["bootstrap_status"] == "truncated"
    assert result["bootstrap_truncated"] is True
    assert store.state["pumpfun_bootstrap_truncated"] == "true"
    assert store.state["pumpfun_watermark"] == "s5"
