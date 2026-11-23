from soi.preflight import run_preflight


def test_preflight_uses_configured_persistent_directory(tmp_path, monkeypatch):
    data = tmp_path / "persist"
    monkeypatch.setenv("SOI_DATA_DIR", str(data))
    monkeypatch.delenv("SOI_DB_PATH", raising=False)
    monkeypatch.delenv("SOI_REPORT_PATH", raising=False)
    result = run_preflight()
    assert result["ok"] is True
    assert all(result["checks"].values())
    assert (data / "soi.sqlite").exists()
    assert (data / "reports").is_dir()


def test_preflight_detects_paths_outside_persistent_directory(tmp_path, monkeypatch):
    data = tmp_path / "persist"
    monkeypatch.setenv("SOI_DATA_DIR", str(data))
    monkeypatch.setenv("SOI_DB_PATH", str(tmp_path / "outside.sqlite"))
    monkeypatch.delenv("SOI_REPORT_PATH", raising=False)
    result = run_preflight()
    assert result["ok"] is False
    assert result["checks"]["paths_under_data_dir"] is False
