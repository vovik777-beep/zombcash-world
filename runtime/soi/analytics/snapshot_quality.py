"""Measure scheduled first-hour snapshot coverage without imputing missing data."""
from __future__ import annotations
from datetime import datetime, timezone

EXPECTED = (1, 5, 15, 30, 60)


def _parse(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def build_snapshot_quality_report(store, mint=None):
    tasks = store.snapshot_tasks(mint)
    observations = [o for o in store.observations(mint) if o.kind == 'market_snapshot']
    by_mint = {}
    for task in tasks:
        by_mint.setdefault(task['mint'], {})[task['offset_minute']] = task
    snapshots = {}
    for obs in observations:
        event = (obs.payload or {}).get('event', {})
        offset = event.get('scheduled_offset_minute')
        if offset is not None:
            snapshots.setdefault(obs.mint, {})[int(offset)] = obs
    rows = []
    for token_mint, token_tasks in sorted(by_mint.items()):
        token_snaps = snapshots.get(token_mint, {})
        checkpoints = []
        for minute in EXPECTED:
            task = token_tasks.get(minute)
            snap = token_snaps.get(minute)
            checkpoints.append({
                'minute': minute,
                'status': 'observed' if snap else (task['status'] if task else 'not_scheduled'),
                'market_cap_usd': ((snap.payload or {}).get('event') or {}).get('market_cap_usd') if snap else None,
                'fdv_usd': ((snap.payload or {}).get('event') or {}).get('fdv_usd') if snap else None,
                'observed_at': snap.observed_at if snap else None,
                'last_error': task['last_error'] if task and not snap else None,
            })
        observed = sum(c['status'] == 'observed' for c in checkpoints)
        rows.append({'mint': token_mint, 'observed_checkpoints': observed,
                     'expected_checkpoints': len(EXPECTED),
                     'coverage_ratio': observed / len(EXPECTED),
                     'complete_first_hour': observed == len(EXPECTED),
                     'checkpoints': checkpoints})
    total = sum(x['observed_checkpoints'] for x in rows)
    expected = len(rows) * len(EXPECTED)
    return {'schema_version':'1.0', 'generated_at':datetime.now(timezone.utc).isoformat(),
            'tokens':len(rows), 'observed_checkpoints':total, 'expected_checkpoints':expected,
            'overall_coverage_ratio':total/expected if expected else None,
            'complete_tokens':sum(x['complete_first_hour'] for x in rows),
            'items':rows,
            'method':'Only snapshots tagged with a scheduled offset count. Missing observations remain missing; market cap and FDV are reported separately.'}
