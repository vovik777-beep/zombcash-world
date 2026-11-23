"""Append-only observation store with durable PumpSwap pool resolution."""
import json
import sqlite3

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL, source_ref TEXT NOT NULL, observed_at TEXT NOT NULL,
 event_at TEXT, kind TEXT NOT NULL, mint TEXT, payload_json TEXT NOT NULL,
 confidence TEXT NOT NULL,
 UNIQUE(source, source_ref, kind)
);
CREATE INDEX IF NOT EXISTS idx_obs_mint_time ON observations(mint, event_at);
CREATE INDEX IF NOT EXISTS idx_obs_kind_time ON observations(kind, event_at);
CREATE TABLE IF NOT EXISTS collection_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at TEXT NOT NULL,
 completed_at TEXT, requested INTEGER NOT NULL DEFAULT 0, saved INTEGER NOT NULL DEFAULT 0,
 no_data INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS collection_results (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES collection_runs(id),
 mint TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, observed_at TEXT NOT NULL,
 UNIQUE(run_id, mint)
);
CREATE INDEX IF NOT EXISTS idx_collection_results_mint ON collection_results(mint, observed_at);
CREATE TABLE IF NOT EXISTS snapshot_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mint TEXT NOT NULL,
 launch_at TEXT NOT NULL,
 offset_minute INTEGER NOT NULL,
 due_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 claimed_at TEXT,
 completed_at TEXT,
 UNIQUE(mint, offset_minute)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_tasks_due ON snapshot_tasks(status, due_at);
CREATE TABLE IF NOT EXISTS collector_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class ObservationStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        return sqlite3.connect(self.path)

    def add(self, item):
        payload = json.dumps(item.payload, sort_keys=True)
        with self.connect() as db:
            cur = db.execute("""INSERT OR IGNORE INTO observations
            (source,source_ref,observed_at,event_at,kind,mint,payload_json,confidence)
            VALUES (?,?,?,?,?,?,?,?)""",
            (item.source,item.source_ref,item.observed_at,item.event_at,item.kind,
             item.mint,payload,item.confidence))
            inserted = cur.rowcount == 1
            # A pool-creation event may arrive after its trades. Re-resolve all
            # unresolved PumpSwap trades from the durable pool->mint evidence.
            if item.kind == "pool_created":
                event = (item.payload or {}).get("event", {})
                pool, mint = event.get("pool"), event.get("base_mint")
                if pool and mint:
                    db.execute("""UPDATE observations SET mint=?
                        WHERE source='pumpswap_rpc' AND mint IS NULL
                        AND json_extract(payload_json, '$.event.pool')=?
                        AND kind IN ('trade_buy','trade_sell')""", (mint, pool))
            return inserted

    def add_many(self, items):
        return sum(int(self.add(item)) for item in items)

    def resolve_pumpswap_pool_mints(self):
        """Rebuild trade mint links from observed CreatePoolEvent records."""
        with self.connect() as db:
            rows = db.execute("""SELECT payload_json FROM observations
                WHERE source='pumpswap_rpc' AND kind='pool_created'""").fetchall()
            mapping = {}
            for (raw,) in rows:
                event = json.loads(raw).get("event", {})
                if event.get("pool") and event.get("base_mint"):
                    mapping[event["pool"]] = event["base_mint"]
            for pool, mint in mapping.items():
                db.execute("""UPDATE observations SET mint=?
                    WHERE source='pumpswap_rpc' AND mint IS NULL
                    AND json_extract(payload_json, '$.event.pool')=?
                    AND kind IN ('trade_buy','trade_sell')""", (mint, pool))
        return mapping

    def observations(self, mint=None, kind=None):
        """Load observations, optionally narrowed by mint and/or event kind."""
        query = "SELECT source,source_ref,observed_at,event_at,kind,mint,payload_json,confidence FROM observations"
        filters = []
        args = []
        if mint is not None:
            filters.append("mint=?")
            args.append(mint)
        if kind is not None:
            filters.append("kind=?")
            args.append(kind)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY COALESCE(event_at, observed_at), id"
        from soi.models import Observation
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [Observation(source=r[0], source_ref=r[1], observed_at=r[2],
            event_at=r[3], kind=r[4], mint=r[5], payload=json.loads(r[6]),
            confidence=r[7]) for r in rows]


    def start_collection_run(self, source, started_at, requested):
        with self.connect() as db:
            cur = db.execute("INSERT INTO collection_runs(source,started_at,requested) VALUES(?,?,?)",
                             (source, started_at, requested))
            return cur.lastrowid

    def record_collection_result(self, run_id, mint, status, observed_at, detail=None):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO collection_results(run_id,mint,status,detail,observed_at) VALUES(?,?,?,?,?)",
                       (run_id, mint, status, detail, observed_at))

    def finish_collection_run(self, run_id, completed_at):
        with self.connect() as db:
            counts = dict(db.execute("SELECT status,COUNT(*) FROM collection_results WHERE run_id=? GROUP BY status", (run_id,)).fetchall())
            saved, no_data, errors = counts.get("saved", 0), counts.get("no_pair", 0), counts.get("error", 0)
            db.execute("UPDATE collection_runs SET completed_at=?,saved=?,no_data=?,errors=?,status='completed' WHERE id=?",
                       (completed_at, saved, no_data, errors, run_id))
        return {"run_id": run_id, "saved": saved, "no_data": no_data, "errors": errors}

    def collection_history(self, limit=20):
        with self.connect() as db:
            rows = db.execute("SELECT id,source,started_at,completed_at,requested,saved,no_data,errors,status FROM collection_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ("run_id", "source", "started_at", "completed_at", "requested", "saved", "no_data", "errors", "status")
        return [dict(zip(keys, row)) for row in rows]

    def latest_snapshot_coverage(self, mints):
        """Missing snapshots remain unknown; they are never treated as zero activity."""
        result = []
        with self.connect() as db:
            for mint in dict.fromkeys(mints):
                row = db.execute("SELECT observed_at FROM observations WHERE kind='market_snapshot' AND mint=? ORDER BY observed_at DESC,id DESC LIMIT 1", (mint,)).fetchone()
                result.append({"mint": mint, "has_snapshot": row is not None, "last_snapshot_at": row[0] if row else None})
        total = len(result)
        covered = sum(1 for item in result if item["has_snapshot"])
        return {"requested": total, "covered": covered, "missing": total-covered,
                "coverage_ratio": covered/total if total else None, "items": result}

    def ensure_snapshot_tasks(self, launches, offsets=(1, 5, 15, 30, 60)):
        """Create durable first-hour snapshot tasks; existing tasks are untouched."""
        created = 0
        with self.connect() as db:
            for mint, launch_at in launches:
                launch_dt = __import__("datetime").datetime.fromisoformat(launch_at.replace("Z", "+00:00"))
                if launch_dt.tzinfo is None:
                    launch_dt = launch_dt.replace(tzinfo=__import__("datetime").timezone.utc)
                for minute in offsets:
                    due = (launch_dt + __import__("datetime").timedelta(minutes=int(minute))).isoformat()
                    cur = db.execute("""INSERT OR IGNORE INTO snapshot_tasks
                        (mint,launch_at,offset_minute,due_at) VALUES(?,?,?,?)""",
                        (mint, launch_at, int(minute), due))
                    created += int(cur.rowcount == 1)
        return created

    def claim_due_snapshot_tasks(self, now, limit=50, lease_seconds=300):
        """Claim due tasks atomically and recover stale claims after process death."""
        from datetime import datetime, timedelta
        now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        stale_before = (now_dt - timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE snapshot_tasks SET status='retry', claimed_at=NULL, last_error='stale worker lease expired' WHERE status='running' AND claimed_at<?", (stale_before,))
            rows = db.execute("""SELECT id,mint,launch_at,offset_minute,due_at,attempts
                FROM snapshot_tasks WHERE status IN ('pending','retry') AND due_at<=?
                ORDER BY due_at,id LIMIT ?""", (now, limit)).fetchall()
            for row in rows:
                db.execute("UPDATE snapshot_tasks SET status='running',claimed_at=?,attempts=attempts+1 WHERE id=?", (now, row[0]))
            db.commit()
        keys = ('id','mint','launch_at','offset_minute','due_at','attempts')
        return [dict(zip(keys, row[:5] + (row[5] + 1,))) for row in rows]

    def finish_snapshot_task(self, task_id, status, completed_at, error=None, max_attempts=4):
        if status not in ('done','no_pair','retry','failed'):
            raise ValueError(f'Unsupported snapshot task status: {status}')
        with self.connect() as db:
            row = db.execute("SELECT attempts FROM snapshot_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            attempts = row[0]
            final_status = 'failed' if status == 'retry' and attempts >= max_attempts else status
            db.execute("""UPDATE snapshot_tasks SET status=?,last_error=?,claimed_at=NULL,
                completed_at=? WHERE id=?""", (final_status, error, completed_at if final_status in ('done','no_pair','failed') else None, task_id))
        return final_status

    def snapshot_task_summary(self):
        with self.connect() as db:
            counts = dict(db.execute("SELECT status,COUNT(*) FROM snapshot_tasks GROUP BY status").fetchall())
            total = sum(counts.values())
            return {'total': total, 'by_status': counts,
                    'completed_ratio': (counts.get('done',0) / total) if total else None}

    def snapshot_tasks(self, mint=None):
        query = "SELECT id,mint,launch_at,offset_minute,due_at,status,attempts,last_error,completed_at FROM snapshot_tasks"
        args = ()
        if mint:
            query += " WHERE mint=?"
            args = (mint,)
        query += " ORDER BY launch_at,offset_minute"
        keys = ('id','mint','launch_at','offset_minute','due_at','status','attempts','last_error','completed_at')
        with self.connect() as db:
            return [dict(zip(keys,row)) for row in db.execute(query,args).fetchall()]

    def get_collector_state(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_collector_state(self, key, value):
        with self.connect() as db:
            if value is None:
                db.execute("DELETE FROM collector_state WHERE key=?", (key,))
            else:
                db.execute("INSERT INTO collector_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
