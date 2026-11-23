from soi.storage import ObservationStore
from soi.models import Observation

def test_idempotent_store(tmp_path):
    s=ObservationStore(tmp_path/"test.sqlite")
    x=Observation("fixture","sig1","2026-01-01T00:00:00Z",None,"launch","Mint",{"x":1})
    assert s.add(x) is True
    assert s.add(x) is False
    assert s.count() == 1


def test_observations_can_filter_by_kind_and_mint_without_changing_default(tmp_path):
    store = ObservationStore(tmp_path / "filtered.sqlite")
    launch_a = Observation(
        "fixture", "launch-a", "2026-01-01T00:00:00Z", None,
        "token_created", "MintA", {"event": {"name": "A"}}
    )
    launch_b = Observation(
        "fixture", "launch-b", "2026-01-02T00:00:00Z", None,
        "token_created", "MintB", {"event": {"name": "B"}}
    )
    trade_a = Observation(
        "fixture", "trade-a", "2026-01-03T00:00:00Z", None,
        "trade_buy", "MintA", {"event": {"amount": 1}}
    )
    for item in (launch_a, launch_b, trade_a):
        assert store.add(item) is True

    # Existing no-argument and mint-only callers retain their prior behavior.
    assert len(store.observations()) == 3
    assert [item.kind for item in store.observations("MintA")] == [
        "token_created", "trade_buy"
    ]

    # New kind filtering composes with the existing mint filter.
    assert [item.mint for item in store.observations(kind="token_created")] == [
        "MintA", "MintB"
    ]
    selected = store.observations(mint="MintA", kind="token_created")
    assert len(selected) == 1
    assert selected[0].source_ref == "launch-a"
