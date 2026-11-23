"""Run one bounded SOI collection cycle inside the existing ZombieCash bot process.

This is a library entry point, not a daemon or an additional process.
The Telegram bot schedules run_cycle through its existing JobQueue.
"""
from __future__ import annotations

import json
import os
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import time

from soi.pipeline import main as run_pipeline
from soi.public_report import build_live_report
from soi.snapshot_scheduler import SnapshotScheduler
from soi.storage import ObservationStore

# Bounded catch-up throughput: keep combined Pump.fun/PumpSwap reads below
# the documented public-RPC per-endpoint request ceiling while draining cursors.
DEFAULT_RPC_LIMIT = 15
DEFAULT_PUMPSWAP_LIMIT = 15
DEFAULT_RPC_PAUSE_SECONDS = 1.1


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)

def _safe_diagnostic(value, depth: int = 0):
    """Bound and redact nested provider diagnostics before writing them to logs."""
    import re

    if depth >= 3:
        return "[truncated]"
    if isinstance(value, str):
        message = value[:240]
        message = re.sub(r"https?://\S+", "[endpoint]", message)
        message = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", message)
        message = re.sub(
            r"(?i)(api[_-]?key|token|secret|authorization)(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[redacted]",
            message,
        )
        return message
    if isinstance(value, dict):
        return {
            str(key)[:80]: _safe_diagnostic(item, depth + 1)
            for key, item in list(value.items())[:10]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_diagnostic(item, depth + 1) for item in list(value)[:10]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(type(value).__name__)


def _stage_diagnostics(stages: dict) -> dict:
    """Return bounded, credential-safe collector metrics without cursor identifiers."""
    fields = (
        "signatures_returned", "transactions_scanned", "failed_signatures_skipped", "failed_signature_error_groups", "failed_signature_program_samples", "observations_inserted",
        "failures", "missing_transactions", "unparsed_events", "opaque_suffix_events", "rate_limited", "sweep_complete",
        "bootstrap_status", "bootstrap_pages_completed", "bootstrap_max_pages",
        "bootstrap_truncated", "requested", "saved", "errors", "deferred",
        "cooldown_deferred", "rate_limited_deferred",
        "cooldown_seconds_remaining_max", "candidates_considered",
    )
    diagnostics = {}
    if not isinstance(stages, dict):
        return diagnostics
    for name, stage in stages.items():
        if not isinstance(stage, dict):
            continue
        metrics = {key: stage[key] for key in fields if key in stage}
        details = {}
        if stage.get("error"):
            details["error"] = _safe_diagnostic(stage["error"])
        if stage.get("failure_reasons"):
            details["failure_reasons"] = _safe_diagnostic(stage["failure_reasons"])
        if stage.get("error_details"):
            details["error_details"] = _safe_diagnostic(stage["error_details"])
        if stage.get("unparsed_events"):
            details["unparsed_events"] = stage["unparsed_events"]
        if metrics or details:
            diagnostics[name] = {"metrics": metrics, **details}
    return diagnostics


def _public_collector_stages(stage_diagnostics: dict) -> dict:
    """Convert internal diagnostics to a compact, credential-safe public status map."""
    result = {}
    if not isinstance(stage_diagnostics, dict):
        return result
    scalar_types = (str, int, float, bool)
    for name, stage in stage_diagnostics.items():
        if name == "_runtime" or not isinstance(stage, dict):
            continue
        raw_metrics = stage.get("metrics", {})
        if not isinstance(raw_metrics, dict):
            raw_metrics = {}
        metrics = {
            key: value for key, value in raw_metrics.items()
            if isinstance(value, scalar_types)
            and not (isinstance(value, str) and len(value) > 100)
        }
        errors = metrics.get("errors", 0)
        has_error = bool(stage.get("error") or stage.get("error_details") or
                         stage.get("failure_reasons") or errors)
        incomplete = (
            metrics.get("sweep_complete") is False
            or metrics.get("bootstrap_status") in ("in_progress", "truncated")
            or metrics.get("bootstrap_truncated") is True
        )
        result[str(name)] = {
            "status": "error" if has_error else ("partial" if incomplete else "ok"),
            "metrics": metrics,
        }
    return result


def run_snapshot_cycle() -> dict:
    """Process due first-hour checkpoints from the shared SOI database."""
    data_dir = Path(os.getenv("SOI_DATA_DIR", "/data/soi"))
    db_path = Path(os.getenv("SOI_DB_PATH", str(data_dir / "soi.sqlite")))
    max_tasks = _int_env("SOI_MAX_TASKS_PER_CYCLE", 10, 1, 250)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = ObservationStore(db_path)
    return SnapshotScheduler(store, max_tasks_per_cycle=max_tasks).run_cycle()


def run_cycle() -> dict:
    """Collect one bounded SOI cycle and atomically refresh the public JSON report."""
    started_monotonic = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    data_dir = Path(os.getenv("SOI_DATA_DIR", "/data/soi"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(os.getenv("SOI_DB_PATH", str(data_dir / "soi.sqlite")))
    pipeline_report_path = Path(os.getenv(
        "SOI_REPORT_PATH", str(data_dir / "reports" / "latest-pipeline.json")
    ))
    public_report_path = Path(os.getenv(
        "SOI_PUBLIC_REPORT_PATH", str(data_dir / "daily-report.json")
    ))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    pipeline_report_path.parent.mkdir(parents=True, exist_ok=True)
    public_report_path.parent.mkdir(parents=True, exist_ok=True)

    rpc_limit = _int_env("SOI_RPC_LIMIT", DEFAULT_RPC_LIMIT, 1, 100)
    rpc_bootstrap_pages = _int_env("SOI_RPC_BOOTSTRAP_PAGES", 2, 1, 1000)
    pumpswap_limit = _int_env("SOI_PUMPSWAP_LIMIT", DEFAULT_PUMPSWAP_LIMIT, 1, 100)
    pumpswap_bootstrap_pages = _int_env("SOI_PUMPSWAP_BOOTSTRAP_PAGES", 2, 1, 1000)
    pool_state_limit = _int_env("SOI_POOL_STATE_LIMIT", 3, 1, 25)
    market_limit = _int_env("SOI_MARKET_LIMIT", 10, 1, 50)
    holder_limit = _int_env("SOI_HOLDER_LIMIT", 3, 1, 25)
    funding_wallet_limit = _int_env("SOI_FUNDING_WALLET_LIMIT", 2, 1, 25)
    funding_signatures = _int_env("SOI_FUNDING_SIGNATURES_PER_WALLET", 10, 1, 25)
    funding_lookback_hours = _int_env("SOI_FUNDING_LOOKBACK_HOURS", 24, 1, 168)
    funding_source_account_limit = _int_env("SOI_FUNDING_SOURCE_ACCOUNT_LIMIT", 20, 1, 100)
    funding_provenance_source_limit = _int_env("SOI_FUNDING_PROVENANCE_SOURCE_LIMIT", 10, 1, 20)
    funding_provenance_signatures = _int_env("SOI_FUNDING_PROVENANCE_SIGNATURES", 10, 1, 25)
    funding_provenance_lookback_hours = _int_env("SOI_FUNDING_PROVENANCE_LOOKBACK_HOURS", 24, 1, 168)
    max_tasks = _int_env("SOI_MAX_TASKS_PER_CYCLE", 5, 1, 250)
    rpc_pause = _float_env("SOI_RPC_PAUSE_SECONDS", DEFAULT_RPC_PAUSE_SECONDS, 0.1)

    args = [
        "--db", str(db_path),
        "--rpc-limit", str(rpc_limit),
        "--rpc-bootstrap-pages", str(rpc_bootstrap_pages),
        "--pumpswap-limit", str(pumpswap_limit),
        "--pumpswap-bootstrap-pages", str(pumpswap_bootstrap_pages),
        "--pool-state-limit", str(pool_state_limit),
        "--rpc-pause", str(rpc_pause),
        "--market-limit", str(market_limit),
        "--holder-limit", str(holder_limit),
        "--funding-wallet-limit", str(funding_wallet_limit),
        "--funding-signatures-per-wallet", str(funding_signatures),
        "--funding-lookback-hours", str(funding_lookback_hours),
        "--funding-source-account-limit", str(funding_source_account_limit),
        "--funding-provenance-source-limit", str(funding_provenance_source_limit),
        "--funding-provenance-signatures", str(funding_provenance_signatures),
        "--funding-provenance-lookback-hours", str(funding_provenance_lookback_hours),
        "--report", str(pipeline_report_path),
    ]

    # Keep the large machine-readable pipeline dump out of the Telegram bot log.
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink):
        pipeline_exit = run_pipeline(args)

    store = ObservationStore(db_path)
    queue_result = SnapshotScheduler(
        store, max_tasks_per_cycle=max_tasks
    ).run_cycle()

    if pipeline_exit not in (0, 2):
        raise RuntimeError(f"SOI pipeline returned unexpected exit code {pipeline_exit}")

    pipeline_report = {}
    try:
        pipeline_report = json.loads(pipeline_report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        pipeline_report = {}

    report_date = datetime.now(timezone.utc).date().isoformat()
    public_report = build_live_report(store.observations())
    collection_status = pipeline_report.get("collection_status", "unknown")
    if pipeline_exit != 0 or collection_status != "ok":
        public_report["status"] = "partial"
        public_report.setdefault("coverage", {})["pipeline_collection_status"] = collection_status
        stages = pipeline_report.get("stages", {})
        issue_stages = []
        if isinstance(stages, dict):
            for name, stage in stages.items():
                if not isinstance(stage, dict):
                    continue
                if stage.get("error") or stage.get("errors") or stage.get("failures"):
                    issue_stages.append(name)
        public_report["coverage"]["stages_with_collection_issues"] = sorted(issue_stages)
        public_report.setdefault("errors", []).append({
            "stage": "collection_pipeline",
            "error": "One or more collection stages reported incomplete data; see the internal pipeline report.",
        })

    stage_diagnostics = _stage_diagnostics(pipeline_report.get("stages", {}))
    stage_diagnostics["_runtime"] = {
        "pumpfun_rpc_limit": rpc_limit,
        "pumpswap_rpc_limit": pumpswap_limit,
        "rpc_pause_seconds": rpc_pause,
    }
    collector_stages = _public_collector_stages(stage_diagnostics)
    stage_issues = {
        name: stage["status"] for name, stage in collector_stages.items()
        if stage["status"] != "ok"
    }
    completed_at = datetime.now(timezone.utc).isoformat()
    cycle_duration_seconds = round(max(0.0, time.monotonic() - started_monotonic), 3)
    overall_status = "partial" if pipeline_exit != 0 or collection_status != "ok" or stage_issues else "ok"
    public_report["status"] = overall_status
    coverage = public_report.setdefault("coverage", {})
    coverage["pipeline_collection_status"] = collection_status
    coverage["collector_stages"] = collector_stages
    coverage["stages_with_collection_issues"] = sorted(stage_issues)
    safe_queue = {
        "new_tasks": int(queue_result.get("new_tasks", 0) or 0),
        "claimed": int(queue_result.get("claimed", 0) or 0),
        "queue": queue_result.get("queue", {}) if isinstance(queue_result.get("queue"), dict) else {},
    }
    public_report["collection"] = {
        "status": overall_status,
        "pipeline_status": collection_status,
        "pipeline_exit": pipeline_exit,
        "cycle_started_at": started_at,
        "cycle_completed_at": completed_at,
        "cycle_duration_seconds": cycle_duration_seconds,
        "queue": safe_queue,
        "stages": collector_stages,
    }
    _atomic_json(public_report_path, public_report)
    return {
        "pipeline_exit": pipeline_exit,
        "collection_status": collection_status,
        "cycle_duration_seconds": cycle_duration_seconds,
        "launches_detected": public_report.get("rolling_24h", {}).get("summary", {}).get("launches_detected", public_report.get("summary", {}).get("launches_detected", 0)),
        "public_report": str(public_report_path),
        "queue": queue_result,
        "stage_diagnostics": stage_diagnostics,
    }
