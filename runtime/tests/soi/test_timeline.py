from soi.models import Observation
from soi.analytics.timeline import build_first_hour_timeline, WSOL
from soi.storage import ObservationStore

MINT = 'TokenMint111'
POOL = 'Pool111'
WALLET_A = 'WalletA'
WALLET_B = 'WalletB'
LAUNCH = '2026-11-17T10:00:00+00:00'


def obs(source, ref, kind, mint, at, event):
    return Observation(source, ref, at, at, kind, mint,
                       {'signature': ref.split(':')[0], 'event': event})


def test_combines_pumpfun_and_pumpswap_and_only_counts_confirmed_wsol():
    items = [
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH,
            {'mint': MINT}),
        obs('pumpfun_rpc', 'pfbuy:0', 'trade_buy', MINT,
            '2026-11-17T10:01:00+00:00',
            {'user': WALLET_A, 'sol_amount': 2_000_000_000, 'quote_mint': WSOL}),
        obs('pumpswap_rpc', 'pool:0', 'pool_created', MINT,
            '2026-11-17T10:20:00+00:00',
            {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL,
             'quote_mint_decimals': 9}),
        obs('pumpswap_rpc', 'amm-buy:0', 'trade_buy', MINT,
            '2026-11-17T10:21:00+00:00',
            {'pool': POOL, 'user': WALLET_B, 'quote_amount_in': 3_000_000_000}),
        obs('pumpswap_rpc', 'amm-sell:0', 'trade_sell', MINT,
            '2026-11-17T10:22:00+00:00',
            {'pool': POOL, 'user': WALLET_A, 'quote_amount_out': 500_000_000}),
    ]
    result = build_first_hour_timeline(items, MINT)
    assert result['migration_observed'] is False
    assert result['migration_status'] == 'pool_without_completion_event'
    assert result['event_count'] == 3
    assert result['unique_buyers'] == 2
    assert result['unique_sellers'] == 1
    assert result['sol_volume_total'] == 5.5
    assert result['venues']['pumpfun']['sol_volume'] == 2
    assert result['venues']['pumpswap']['sol_volume'] == 3.5


def test_excludes_non_wsol_quote_amounts():
    items = [
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT}),
        obs('pumpswap_rpc', 'pool:0', 'pool_created', MINT,
            '2026-11-17T10:10:00+00:00',
            {'pool': POOL, 'base_mint': MINT, 'quote_mint': 'USDCMint',
             'quote_mint_decimals': 6}),
        obs('pumpswap_rpc', 'buy:0', 'trade_buy', MINT,
            '2026-11-17T10:11:00+00:00',
            {'pool': POOL, 'user': WALLET_A, 'quote_amount_in': 1_000_000}),
    ]
    result = build_first_hour_timeline(items, MINT)
    assert result['sol_volume_total'] == 0
    assert result['non_sol_or_unresolved_quote_trades'] == 1


def test_pool_map_persists_and_resolves_trades_arriving_before_pool(tmp_path):
    store = ObservationStore(tmp_path / 'timeline.sqlite')
    trade = obs('pumpswap_rpc', 'trade:0', 'trade_buy', None,
        '2026-11-17T10:11:00+00:00', {'pool': POOL, 'quote_amount_in': 10})
    pool = obs('pumpswap_rpc', 'pool:0', 'pool_created', MINT,
        '2026-11-17T10:10:00+00:00',
        {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL})
    assert store.add(trade)
    assert store.observations()[0].mint is None
    assert store.add(pool)
    resolved = [o for o in store.observations() if o.kind == 'trade_buy'][0]
    assert resolved.mint == MINT
    assert store.resolve_pumpswap_pool_mints()[POOL] == MINT


def test_first_hour_checkpoint_metrics():
    items = [
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT}),
        obs('pumpfun_rpc', 'buy1:0', 'trade_buy', MINT,
            '2026-11-17T10:00:30+00:00', {'user': WALLET_A, 'sol_amount': 1_000_000_000, 'quote_mint': WSOL}),
        obs('pumpfun_rpc', 'buy2:0', 'trade_buy', MINT,
            '2026-11-17T10:04:00+00:00', {'user': WALLET_B, 'sol_amount': 2_000_000_000, 'quote_mint': WSOL}),
        obs('pumpfun_rpc', 'sell:0', 'trade_sell', MINT,
            '2026-11-17T10:16:00+00:00', {'user': WALLET_A, 'sol_amount': 500_000_000, 'quote_mint': WSOL}),
    ]
    result = build_first_hour_timeline(items, MINT)
    assert result['checkpoints_minutes']['1']['trade_count'] == 1
    assert result['checkpoints_minutes']['5']['trade_count'] == 2
    assert result['checkpoints_minutes']['15']['trade_count'] == 2
    assert result['checkpoints_minutes']['30']['trade_count'] == 3
    assert result['checkpoints_minutes']['60']['sol_volume'] == 3.5


def test_rejects_conflicting_launch_signatures_instead_of_choosing_earliest():
    items = [
        obs('pumpfun_rpc', 'launch-a:0', 'token_created', MINT, LAUNCH,
            {'mint': MINT, 'creator': 'creator-a'}),
        obs('pumpfun_rpc', 'launch-b:0', 'token_created', MINT,
            '2026-11-17T10:01:00+00:00',
            {'mint': MINT, 'creator': 'creator-a'}),
    ]
    import pytest
    with pytest.raises(ValueError, match='Ambiguous launch evidence'):
        build_first_hour_timeline(items, MINT)


def test_accepts_exact_duplicate_launch_evidence():
    items = [
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH,
            {'mint': MINT, 'creator': 'creator-a'}),
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH,
            {'mint': MINT, 'creator': 'creator-a'}),
    ]
    result = build_first_hour_timeline(items, MINT)
    assert result['launch_at'] == LAUNCH


def test_rejects_conflicting_pool_quote_metadata():
    items = [
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH,
            {'mint': MINT}),
        obs('pumpswap_rpc', 'pool-a:0', 'pool_created', MINT,
            '2026-11-17T10:10:00+00:00',
            {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL}),
        obs('pumpswap_rpc', 'pool-b:0', 'pool_created', MINT,
            '2026-11-17T10:11:00+00:00',
            {'pool': POOL, 'base_mint': MINT, 'quote_mint': 'USDCMint'}),
    ]
    import pytest
    with pytest.raises(ValueError, match='Ambiguous PumpSwap pool metadata'):
        build_first_hour_timeline(items, MINT)


def test_rejects_pool_reassigned_to_a_different_base_mint():
    items = [
        obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH,
            {'mint': MINT}),
        obs('pumpswap_rpc', 'pool-a:0', 'pool_created', MINT,
            '2026-11-17T10:10:00+00:00',
            {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL}),
        obs('pumpswap_rpc', 'pool-b:0', 'pool_created', 'OtherMint',
            '2026-11-17T10:10:00+00:00',
            {'pool': POOL, 'base_mint': 'OtherMint', 'quote_mint': WSOL}),
    ]
    import pytest
    with pytest.raises(ValueError, match='Ambiguous PumpSwap pool metadata'):
        build_first_hour_timeline(items, MINT)


def test_timeline_uses_program_log_index_for_same_transaction_timestamp_ties():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH,
                 {'mint': MINT})
    first = obs('pumpfun_rpc', 'sig:20', 'trade_buy', MINT,
                '2026-11-17T10:00:10+00:00',
                {'user': WALLET_A, 'sol_amount': 1, 'quote_mint': WSOL})
    second = obs('pumpfun_rpc', 'sig:5', 'trade_sell', MINT,
                 '2026-11-17T10:00:10+00:00',
                 {'user': WALLET_B, 'sol_amount': 1, 'quote_mint': WSOL})
    for item, log_index in ((first, 20), (second, 5)):
        item.payload['slot'] = 100
        item.payload['log_index'] = log_index
    result = build_first_hour_timeline([launch, first, second], MINT)
    assert [item['log_index'] for item in result['events']] == [5, 20]
    assert result['ordering_quality']['within_transaction'] == 'program_log_index'


def migration_obs(signature, pool, quote_mint=WSOL, at='2026-11-17T10:20:00+00:00'):
    return Observation('pumpfun_rpc', f'{signature}:0', at, at, 'migration_completed', MINT,
        {'signature': signature, 'slot': 200, 'log_index': 9, 'event': {'mint': MINT, 'pool': pool, 'quote_mint': quote_mint}})

def test_migration_requires_canonical_completion_event_and_matching_pool_creation():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    pool = obs('pumpswap_rpc', 'migrateSig:0', 'pool_created', MINT,
        '2026-11-17T10:20:00+00:00', {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL})
    pool.payload['signature'] = 'migrateSig'
    pool.payload['log_index'] = 5
    result = build_first_hour_timeline([launch, pool, migration_obs('migrateSig', POOL)], MINT)
    assert result['migration_observed'] is True
    assert result['migration_status'] == 'confirmed'
    assert result['migration_pool'] == POOL
    assert result['migration_pool_creation_matched'] is True
    assert result['migration_pool_order_verified'] is True

def test_pool_creation_alone_is_not_migration_confirmation():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    pool = obs('pumpswap_rpc', 'external:0', 'pool_created', MINT,
        '2026-11-17T10:20:00+00:00', {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL})
    result = build_first_hour_timeline([launch, pool], MINT)
    assert result['migration_observed'] is False
    assert result['migration_status'] == 'pool_without_completion_event'
    assert result['migration_at'] is None

def test_migration_event_without_matching_pool_is_reported_separately():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    result = build_first_hour_timeline([launch, migration_obs('migrateSig', POOL)], MINT)
    assert result['migration_status'] == 'migration_event_only'
    assert result['migration_observed'] is True
    assert result['migration_pool_creation_matched'] is False

def test_migration_pool_quote_mint_mismatch_is_not_confirmed():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    pool = obs('pumpswap_rpc', 'migrateSig:0', 'pool_created', MINT,
        '2026-11-17T10:20:00+00:00', {'pool': POOL, 'base_mint': MINT, 'quote_mint': 'OtherQuote'})
    pool.payload['signature'] = 'migrateSig'
    pool.payload['log_index'] = 5
    result = build_first_hour_timeline([launch, pool, migration_obs('migrateSig', POOL)], MINT)
    assert result['migration_status'] == 'migration_pool_metadata_or_order_mismatch'
    assert result['migration_pool_creation_matched'] is False

def curve_complete_obs(signature='completeSig', at='2026-11-17T10:19:00+00:00', log_index=4):
    return Observation('pumpfun_rpc', f'{signature}:0', at, at, 'bonding_curve_completed', MINT,
        {'signature': signature, 'slot': 200, 'log_index': log_index,
         'event': {'mint': MINT, 'bonding_curve': 'BondingCurve111', 'quote_mint': WSOL}})


def test_curve_completion_is_not_mistaken_for_amm_migration():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    complete = curve_complete_obs()
    result = build_first_hour_timeline([launch, complete], MINT)
    assert result['bonding_curve_completed'] is True
    assert result['bonding_curve_completion_evidence'] == 'official_pump_complete_event'
    assert result['migration_observed'] is False
    assert result['migration_status'] == 'not_observed'


def test_migration_must_not_precede_curve_completion_in_same_transaction():
    import pytest
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    complete = curve_complete_obs(signature='sameSig', log_index=10)
    pool = obs('pumpswap_rpc', 'sameSig:0', 'pool_created', MINT,
        '2026-11-17T10:20:00+00:00', {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL})
    pool.payload.update({'signature': 'sameSig', 'log_index': 5})
    migration = migration_obs('sameSig', POOL)
    with pytest.raises(ValueError, match='migration precedes bonding-curve completion'):
        build_first_hour_timeline([launch, complete, pool, migration], MINT)


def test_curve_completion_ambiguity_is_not_silently_resolved():
    import pytest
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    with pytest.raises(ValueError, match='Ambiguous Pump bonding-curve completion'):
        build_first_hour_timeline([launch, curve_complete_obs('completeA'),
            curve_complete_obs('completeB', at='2026-11-17T10:21:00+00:00')], MINT)

def test_valid_curve_completion_then_migration_sequence_is_accepted():
    launch = obs('pumpfun_rpc', 'launch:0', 'token_created', MINT, LAUNCH, {'mint': MINT})
    complete = curve_complete_obs(signature='sameSig', at='2026-11-17T10:19:00+00:00', log_index=4)
    pool = obs('pumpswap_rpc', 'sameSig:0', 'pool_created', MINT,
        '2026-11-17T10:20:00+00:00', {'pool': POOL, 'base_mint': MINT, 'quote_mint': WSOL})
    pool.payload.update({'signature': 'sameSig', 'log_index': 5})
    migration = migration_obs('sameSig', POOL, at='2026-11-17T10:20:00+00:00')
    migration.payload['log_index'] = 6
    result = build_first_hour_timeline([launch, complete, pool, migration], MINT)
    assert result['bonding_curve_completed'] is True
    assert result['migration_observed'] is True
    assert result['migration_status'] == 'confirmed'
