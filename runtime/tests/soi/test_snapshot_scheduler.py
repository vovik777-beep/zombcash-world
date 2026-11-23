from soi.models import Observation
from soi.snapshot_scheduler import SnapshotScheduler
from soi.storage import ObservationStore

MINT='MintSchedule'
LAUNCH='2026-11-17T10:00:00+00:00'


def add_launch(store):
    store.add(Observation('pumpfun_rpc','sig-launch',LAUNCH,LAUNCH,'token_created',MINT,{'event':{'mint':MINT}}))


def test_scheduler_creates_five_durable_checkpoints(tmp_path):
    store=ObservationStore(tmp_path/'scheduler.db')
    add_launch(store)
    scheduler=SnapshotScheduler(store, collector=type('C',(),{'collect':lambda *a,**k:{'saved':0,'no_pair':[],'errors':[]}})())
    assert scheduler.sync_launches()==5
    tasks=store.snapshot_tasks(MINT)
    assert [x['offset_minute'] for x in tasks]==[1,5,15,30,60]
    assert scheduler.sync_launches()==0


def test_scheduler_retries_missing_pair_and_eventually_marks_failed(tmp_path):
    store=ObservationStore(tmp_path/'retry.db')
    add_launch(store)
    scheduler=SnapshotScheduler(store,
        collector=type('C',(),{'collect':lambda *a,**k:{'saved':0,'no_pair':[MINT],'errors':[]}})(),
        max_attempts=1)
    scheduler.sync_launches()
    task=store.snapshot_tasks(MINT)[0]
    result=scheduler.run_cycle(now='2026-11-17T10:02:00+00:00')
    assert result['claimed']==1
    assert store.snapshot_tasks(MINT)[0]['status']=='failed'


def test_scheduler_persists_successful_checkpoint_and_metadata(tmp_path):
    store=ObservationStore(tmp_path/'success.db')
    add_launch(store)
    class Collector:
        def collect(self, mints, task_metadata=None):
            assert task_metadata[MINT]['offset_minute']==1
            return {'saved':1,'no_pair':[],'errors':[]}
    scheduler=SnapshotScheduler(store, collector=Collector())
    scheduler.sync_launches()
    scheduler.run_cycle(now='2026-11-17T10:02:00+00:00')
    assert store.snapshot_tasks(MINT)[0]['status']=='done'
    assert store.snapshot_task_summary()['by_status']['done']==1


def test_stale_running_tasks_are_recovered(tmp_path):
    store=ObservationStore(tmp_path/'lease.db')
    add_launch(store)
    store.ensure_snapshot_tasks([(MINT,LAUNCH)])
    store.claim_due_snapshot_tasks('2026-11-17T10:02:00+00:00')
    reclaimed=store.claim_due_snapshot_tasks('2026-11-17T10:10:00+00:00',lease_seconds=60)
    assert reclaimed and reclaimed[0]['mint']==MINT
    assert reclaimed[0]['attempts']==2


def test_snapshot_quality_reports_missing_checkpoints_as_missing(tmp_path):
    from soi.analytics.snapshot_quality import build_snapshot_quality_report
    from soi.models import Observation
    store=ObservationStore(tmp_path/'quality.db')
    add_launch(store)
    store.ensure_snapshot_tasks([(MINT,LAUNCH)])
    store.add(Observation('dexscreener','pair:now','2026-11-17T10:01:10+00:00',
        '2026-11-17T10:01:10+00:00','market_snapshot',MINT,
        {'event':{'scheduled_offset_minute':1,'market_cap_usd':120000,'fdv_usd':250000}}))
    report=build_snapshot_quality_report(store)
    item=report['items'][0]
    assert item['observed_checkpoints']==1
    assert item['checkpoints'][0]['market_cap_usd']==120000
    assert item['checkpoints'][1]['status']=='pending'
    assert item['complete_first_hour'] is False


def test_scheduler_records_real_snapshot_with_checkpoint_tag(tmp_path):
    from soi.sources.market_snapshots import MarketSnapshotCollector
    store=ObservationStore(tmp_path/'integration.db')
    add_launch(store)
    class Client:
        def token_pairs(self, mint):
            return [{'chainId':'solana','pairAddress':'pair-x','dexId':'raydium',
                'baseToken':{'address':mint},'quoteToken':{'address':'So11111111111111111111111111111111111111112'},
                'priceUsd':'0.02','marketCap':150000,'fdv':300000,'liquidity':{'usd':25000}}]
    collector=MarketSnapshotCollector(store,Client(),sleep=lambda _:None,min_interval_seconds=0)
    scheduler=SnapshotScheduler(store,collector=collector)
    scheduler.sync_launches()
    scheduler.run_cycle(now='2026-11-17T10:02:00+00:00')
    snapshots=[o for o in store.observations(MINT) if o.kind=='market_snapshot']
    assert len(snapshots)==1
    event=snapshots[0].payload['event']
    assert event['scheduled_offset_minute']==1
    assert event['scheduled_for']=='2026-11-17T10:01:00+00:00'
    assert event['market_cap_usd']==150000
