import asyncio
import json

import api_server


def test_soi_report_endpoint_returns_latest_public_report(monkeypatch):
    async def fake_supabase(method, path, *, payload=None):
        assert method == "POST"
        assert path == "rpc/sniper_launch_list"
        return []

    monkeypatch.setattr(api_server, "_supabase_request", fake_supabase)
    response = asyncio.run(api_server.soi_public_report(None))

    assert response.status == 200
    body = json.loads(response.text)
    assert body["status"] == "partial"
    assert body["launches"] == []
    assert body["rolling_24h"]["summary"]["launches_detected"] == 0


def test_soi_report_endpoint_returns_503_when_sniper_data_is_unavailable(monkeypatch):
    async def unavailable_supabase(method, path, *, payload=None):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(api_server, "_supabase_request", unavailable_supabase)
    response = asyncio.run(api_server.soi_public_report(None))

    assert response.status == 503
