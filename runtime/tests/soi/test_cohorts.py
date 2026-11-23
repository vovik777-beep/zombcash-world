from soi.models import Observation
from soi.analytics.cohorts import analyze_cohorts

MINT_A = 'MintA'
MINT_B = 'MintB'
BASE = '2026-11-17T10:00:00+00:00'

def ob(ref, kind, mint, at, event):
    return Observation('fixture', ref, at, at, kind, mint, {'event': event})

def test_cohort_milestones_and_unknowns_are_distinct():
    items = [
        ob('a0','token_created',MINT_A,BASE,{'mint':MINT_A}),
        ob('a1','market_snapshot',MINT_A,'2026-11-17T10:01:00+00:00',{'market_cap_usd':120000}),
        ob('a2','market_snapshot',MINT_A,'2026-11-17T10:10:00+00:00',{'market_cap_usd':510000}),
        ob('a3','market_snapshot',MINT_A,'2026-11-17T10:30:00+00:00',{'market_cap_usd':1100000}),
        ob('b0','token_created',MINT_B,'2026-11-17T11:00:00+00:00',{'mint':MINT_B}),
        ob('b1','market_snapshot',MINT_B,'2026-11-17T11:05:00+00:00',{'fdv_usd':9000000}),
    ]
    report = analyze_cohorts(items, '2026-11-17')
    assert report['token_count'] == 2
    assert report['milestones']['100000']['crossed_count'] == 1
    assert report['milestones']['100000']['unknown_no_market_cap_data'] == 1
    assert report['milestones']['500000']['median_time_to_seconds_among_crossers'] == 600
    assert report['milestones']['1000000']['crossed_count'] == 1
    assert report['tokens'][1]['market_cap_data_status'] == 'missing'

def test_cohort_trade_activity_and_mint_filter():
    items = [
        ob('a0','token_created',MINT_A,BASE,{}),
        ob('a1','trade_buy',MINT_A,'2026-11-17T10:00:30+00:00',{'user':'W1'}),
        ob('a2','trade_buy',MINT_A,'2026-11-17T10:01:00+00:00',{'user':'W1'}),
        ob('a3','trade_sell',MINT_A,'2026-11-17T10:02:00+00:00',{'user':'W2'}),
        ob('b0','token_created',MINT_B,'2026-11-17T10:00:00+00:00',{}),
        ob('b1','trade_buy',MINT_B,'2026-11-17T10:00:10+00:00',{'user':'W3'}),
    ]
    report = analyze_cohorts(items, '2026-11-17')
    a = next(t for t in report['tokens'] if t['mint'] == MINT_A)
    assert a['first_hour']['trade_count'] == 3
    assert a['first_hour']['unique_buyers'] == 1
    assert a['first_hour']['unique_sellers'] == 1

def test_snapshots_before_launch_and_negative_market_cap_ignored():
    items = [
        ob('a0','token_created',MINT_A,BASE,{}),
        ob('pre','market_snapshot',MINT_A,'2026-11-17T09:59:00+00:00',{'market_cap_usd':2000000}),
        ob('bad','market_snapshot',MINT_A,'2026-11-17T10:01:00+00:00',{'market_cap_usd':-1}),
    ]
    report = analyze_cohorts(items, '2026-11-17')
    assert report['milestones']['100000']['crossed_count'] == 0
    assert report['tokens'][0]['market_cap_snapshot_count'] == 0

def test_cohort_report_export_roundtrip(tmp_path):
    import json
    from soi.report import write_cohort_report
    report = analyze_cohorts([ob('a0','token_created',MINT_A,BASE,{})], '2026-11-17')
    path = write_cohort_report(tmp_path / 'reports' / 'cohort.json', report)
    loaded = json.loads(path.read_text(encoding='utf-8'))
    assert loaded['token_count'] == 1
    assert loaded['methodology']['fdv_substitution'] is False
