import json

from soi import runtime


def test_run_cycle_uses_bounded_defaults_and_publishes_partial_report(tmp_path, monkeypatch):
    data_dir = tmp_path / "soi-data"
    monkeypatch.setenv("SOI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SOI_RPC_LIMIT", "2")
    monkeypatch.setenv("SOI_MAX_TASKS_PER_CYCLE", "4")

    captured = {}

    class FakeStore:
        def __init__(self, path):
            captured["db_path"] = str(path)

        def observations(self):
            return []

    class FakeScheduler:
        def __init__(self, store, max_tasks_per_cycle):
            captured["max_tasks"] = max_tasks_per_cycle

        def run_cycle(self):
            return {"claimed": 0, "queue": {"total": 0}}

    def fake_pipeline(args):
        captured["args"] = args
        report_path = args[args.index("--report") + 1]
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump({"collection_status": "partial_or_failed", "stages": {"pumpfun_rpc": {"error": "offline"}}}, handle)
        return 2

    def fake_public_report(observations):
        captured["observation_count"] = len(observations)
        return {"status": "ok", "coverage": {}, "errors": [], "summary": {"launches_detected": 0}, "rolling_24h": {"summary": {"launches_detected": 0}}, "daily_history": [], "launches": []}

    monkeypatch.setattr(runtime, "ObservationStore", FakeStore)
    monkeypatch.setattr(runtime, "SnapshotScheduler", FakeScheduler)
    monkeypatch.setattr(runtime, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(runtime, "build_live_report", fake_public_report)

    result = runtime.run_cycle()

    assert result["pipeline_exit"] == 2
    assert result["collection_status"] == "partial_or_failed"
    assert result["launches_detected"] == 0
    assert captured["max_tasks"] == 4
    assert captured["observation_count"] == 0
    assert captured["db_path"] == str(data_dir / "soi.sqlite")
    assert captured["args"][captured["args"].index("--rpc-limit") + 1] == "2"

    public_path = data_dir / "daily-report.json"
    report = json.loads(public_path.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["coverage"]["pipeline_collection_status"] == "partial_or_failed"
    assert report["coverage"]["stages_with_collection_issues"] == ["pumpfun_rpc"]
    assert report["coverage"]["collector_stages"]["pumpfun_rpc"]["status"] == "error"
    assert report["collection"]["status"] == "partial"
    assert report["collection"]["queue"]["claimed"] == 0
    assert "results" not in report["collection"]["queue"]
    assert report["errors"][0]["stage"] == "collection_pipeline"


def test_run_snapshot_cycle_uses_the_shared_database_and_task_limit(tmp_path, monkeypatch):
    data_dir = tmp_path / "soi-data"
    monkeypatch.setenv("SOI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SOI_MAX_TASKS_PER_CYCLE", "7")
    captured = {}

    class FakeStore:
        def __init__(self, path):
            captured["db_path"] = str(path)

    class FakeScheduler:
        def __init__(self, store, max_tasks_per_cycle):
            captured["max_tasks"] = max_tasks_per_cycle
        def run_cycle(self):
            return {"claimed": 2, "queue": {"total": 3}}

    monkeypatch.setattr(runtime, "ObservationStore", FakeStore)
    monkeypatch.setattr(runtime, "SnapshotScheduler", FakeScheduler)

    result = runtime.run_snapshot_cycle()

    assert captured["db_path"] == str(data_dir / "soi.sqlite")
    assert captured["max_tasks"] == 7
    assert result["claimed"] == 2
