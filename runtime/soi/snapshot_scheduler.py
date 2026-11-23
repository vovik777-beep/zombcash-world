"""Durable first-hour snapshot scheduler with restart recovery and bounded retries."""
from __future__ import annotations
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from soi.sources.market_snapshots import MarketSnapshotCollector
from soi.storage import ObservationStore

OFFSETS = (1, 5, 15, 30, 60)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def discover_launches(store: ObservationStore):
    launches = {}
    for obs in store.observations():
        if obs.kind == 'token_created' and obs.mint and obs.event_at:
            previous = launches.get(obs.mint)
            if previous is None or obs.event_at < previous:
                launches[obs.mint] = obs.event_at
    return sorted(launches.items(), key=lambda x: (x[1], x[0]))


class SnapshotScheduler:
    def __init__(self, store, collector=None, max_tasks_per_cycle=25,
                 max_attempts=4, retry_backoff_seconds=30, sleep=time.sleep):
        self.store = store
        self.collector = collector or MarketSnapshotCollector(store)
        self.max_tasks_per_cycle = max(1, int(max_tasks_per_cycle))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(1, int(retry_backoff_seconds))
        self.sleep = sleep

    def sync_launches(self):
        return self.store.ensure_snapshot_tasks(discover_launches(self.store), OFFSETS)

    def run_cycle(self, now=None):
        now = now or utc_now()
        created = self.sync_launches()
        tasks = self.store.claim_due_snapshot_tasks(now, self.max_tasks_per_cycle)
        results = []
        for task in tasks:
            mint = task['mint']
            try:
                result = self.collector.collect([mint], task_metadata={mint: task})
                if result['saved']:
                    status, error = 'done', None
                elif result['no_pair']:
                    status, error = 'retry', 'No Solana pair available at scheduled checkpoint'
                else:
                    detail = result['errors'][0]['error'] if result['errors'] else 'Snapshot was not saved'
                    status, error = 'retry', detail
            except Exception as exc:  # one task must never stop the remaining queue
                status, error = 'retry', str(exc)[:300]
            final = self.store.finish_snapshot_task(task['id'], status, utc_now(), error,
                                                     max_attempts=self.max_attempts)
            if final == 'retry':
                # Make retries durable and progressively less aggressive.
                delay = self.retry_backoff_seconds * (2 ** max(0, task['attempts']))
                from datetime import timedelta
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                with self.store.connect() as db:
                    db.execute("UPDATE snapshot_tasks SET due_at=? WHERE id=?", (retry_at, task['id']))
            results.append({'task_id': task['id'], 'mint': mint,
                            'offset_minute': task['offset_minute'],
                            'status': final, 'error': error})
        return {'new_tasks': created, 'claimed': len(tasks), 'results': results,
                'queue': self.store.snapshot_task_summary()}

    def run_forever(self, poll_seconds=5, max_cycles=None):
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            self.run_cycle()
            cycles += 1
            self.sleep(max(1, poll_seconds))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run durable SOI first-hour snapshot scheduler')
    parser.add_argument('--db', required=True)
    parser.add_argument('--poll-seconds', type=int, default=5)
    parser.add_argument('--max-tasks-per-cycle', type=int, default=25)
    parser.add_argument('--max-attempts', type=int, default=4)
    parser.add_argument('--retry-backoff-seconds', type=int, default=30)
    parser.add_argument('--once', action='store_true', help='Run one queue cycle and exit')
    parser.add_argument('--max-cycles', type=int, default=None, help='Optional bounded daemon run')
    args = parser.parse_args(argv)
    if args.poll_seconds < 1 or args.max_tasks_per_cycle < 1 or args.max_attempts < 1:
        parser.error('poll/task/attempt limits must be positive')
    store = ObservationStore(Path(args.db))
    scheduler = SnapshotScheduler(store, max_tasks_per_cycle=args.max_tasks_per_cycle,
        max_attempts=args.max_attempts, retry_backoff_seconds=args.retry_backoff_seconds)
    if args.once:
        print(scheduler.run_cycle())
    else:
        scheduler.run_forever(args.poll_seconds, args.max_cycles)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
