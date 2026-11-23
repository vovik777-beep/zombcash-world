import asyncio
import json

import api_server


def test_soi_report_endpoint_returns_latest_public_report(tmp_path, monkeypatch):
    path = tmp_path / "daily-report.json"
    expected = {"schema_version": "1.0", "launches": [], "summary": {"launches_detected": 0}}
    path.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(api_server, "SOI_PUBLIC_REPORT_FILE", str(path))

    response = asyncio.run(api_server.soi_public_report(None))

    assert response.status == 200
    assert json.loads(response.text) == expected


def test_soi_report_endpoint_returns_503_before_first_report(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SOI_PUBLIC_REPORT_FILE", str(tmp_path / "missing.json"))

    response = asyncio.run(api_server.soi_public_report(None))

    assert response.status == 503
