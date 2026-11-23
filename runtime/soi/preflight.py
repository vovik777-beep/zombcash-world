"""Read-only deployment preflight for the in-process SOI runtime's local filesystem setup."""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from pathlib import Path


def run_preflight() -> dict:
    data_dir = Path(os.getenv("SOI_DATA_DIR", "/data/soi"))
    db_path = Path(os.getenv("SOI_DB_PATH", str(data_dir / "soi.sqlite")))
    report_path = Path(os.getenv("SOI_REPORT_PATH", str(data_dir / "reports" / "latest-pipeline.json")))
    result = {"ok": False, "checks": {}, "paths": {"data_dir": str(data_dir), "db_path": str(db_path), "report_path": str(report_path)}}
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".soi-write-test"
        probe.write_text("ok", encoding="utf-8")
        result["checks"]["data_dir_writable"] = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink(missing_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS _soi_preflight (id INTEGER PRIMARY KEY, checked_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO _soi_preflight DEFAULT VALUES")
            conn.execute("DELETE FROM _soi_preflight")
        result["checks"]["sqlite_writable"] = db_path.exists()
        result["checks"]["paths_under_data_dir"] = all(
            p.resolve().is_relative_to(data_dir.resolve())
            for p in (db_path, report_path)
        )
        result["ok"] = all(result["checks"].values())
    except Exception as exc:  # report the operational error without exposing environment values
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    result = run_preflight()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
