from soi.analytics.first_hour import summarize_snapshots
from soi.analytics.classify import social_presence, promotion_state

def test_first_hour():
    r=summarize_snapshots([
      {"minute":0,"market_cap_usd":100000,"volume_usd":5000,"txns":10},
      {"minute":15,"market_cap_usd":1100000,"volume_usd":40000,"txns":80},
      {"minute":60,"market_cap_usd":600000,"volume_usd":20000,"txns":40}])
    assert r["crossed_1m_market_cap"] is True
    assert r["latest_snapshot_transactions"] == 40
    assert r["latest_snapshot_volume_usd"] == 20000
    assert "not summed" in r["counter_method"]
    assert r["peak_market_cap_usd"] == 1100000

def test_unknown_is_not_false():
    assert social_presence(None) is None
    assert social_presence([]) is False
    assert promotion_state(None,None,None) == (None,None)
