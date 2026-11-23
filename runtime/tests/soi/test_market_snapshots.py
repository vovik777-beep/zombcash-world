from soi.sources.market_snapshots import choose_primary_pair, normalize_pair_snapshot, MarketSnapshotCollector
from soi.storage import ObservationStore

MINT = 'Mint111'

def pair(address, liquidity, market_cap=250000, fdv=900000):
    return {'chainId':'solana','pairAddress':address,'dexId':'raydium',
            'baseToken':{'address':MINT},'quoteToken':{'address':'So111'},
            'priceUsd':'0.01','marketCap':market_cap,'fdv':fdv,
            'liquidity':{'usd':liquidity},'volume':{'h24':1000},'txns':{'h24':{'buys':10,'sells':4}}}

def test_primary_pair_is_most_liquid_and_chain_mint_filtered():
    chosen = choose_primary_pair([
        pair('low', 100), pair('high', 900),
        {'chainId':'ethereum','pairAddress':'wrong','baseToken':{'address':MINT},'liquidity':{'usd':99999}},
        {'chainId':'solana','pairAddress':'othermint','baseToken':{'address':'Other'},'liquidity':{'usd':99999}},
    ], MINT)
    assert chosen['pairAddress'] == 'high'

def test_market_cap_and_fdv_are_not_interchanged():
    obs = normalize_pair_snapshot(pair('p1', 500, market_cap=None, fdv=800000), MINT,
                                  '2026-11-17T12:00:00+00:00')
    event = obs.payload['event']
    assert event['market_cap_usd'] is None
    assert event['fdv_usd'] == 800000
    assert obs.kind == 'market_snapshot'

def test_collector_persists_snapshot_and_continues_after_missing_pair(tmp_path):
    class Client:
        def token_pairs(self, mint):
            return [pair('pair-'+mint, 200)] if mint == MINT else []
    store = ObservationStore(tmp_path/'soi.db')
    collector = MarketSnapshotCollector(store, Client(), sleep=lambda _: None, min_interval_seconds=0)
    result = collector.collect([MINT, 'NoPair'])
    assert result['saved'] == 1
    assert result['no_pair'] == ['NoPair']
    assert store.observations(MINT)[0].payload['event']['market_cap_usd'] == 250000


def test_collection_run_tracks_saved_missing_and_errors(tmp_path):
    class Client:
        def token_pairs(self, mint):
            if mint == 'Broken':
                raise RuntimeError('temporary provider failure')
            return [pair('pair-'+mint, 200)] if mint == MINT else []
    store = ObservationStore(tmp_path/'runs.db')
    collector = MarketSnapshotCollector(store, Client(), sleep=lambda _: None, min_interval_seconds=0)
    result = collector.collect([MINT, 'NoPair', 'Broken'])
    assert result['coverage'] == {'run_id': 1, 'saved': 1, 'no_data': 1, 'errors': 1}
    history = store.collection_history()
    assert history[0]['requested'] == 3
    assert history[0]['status'] == 'completed'
    assert history[0]['errors'] == 1


def test_latest_snapshot_coverage_keeps_missing_as_unknown(tmp_path):
    store = ObservationStore(tmp_path/'coverage.db')
    collector = MarketSnapshotCollector(store, type('Client', (), {
        'token_pairs': lambda self, mint: [pair('pair-'+mint, 200)] if mint == MINT else []
    })(), sleep=lambda _: None, min_interval_seconds=0)
    collector.collect([MINT])
    report = store.latest_snapshot_coverage([MINT, 'NeverSeen'])
    assert report['requested'] == 2
    assert report['covered'] == 1
    assert report['missing'] == 1
    assert report['coverage_ratio'] == 0.5
    assert report['items'][1]['has_snapshot'] is False
