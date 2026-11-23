import pytest

from soi.scheduling import get_soi_poll_interval


def test_default_interval_is_five_minutes(monkeypatch):
    monkeypatch.delenv("SOI_POLL_SECONDS", raising=False)
    assert get_soi_poll_interval() == 300


@pytest.mark.parametrize("raw", ["60", "89", "119", "299", "3601", "invalid"])
def test_unsafe_or_invalid_interval_falls_back_to_five_minutes(monkeypatch, raw):
    monkeypatch.setenv("SOI_POLL_SECONDS", raw)
    assert get_soi_poll_interval() == 300


@pytest.mark.parametrize("raw,expected", [("300", 300), ("600", 600), ("3600", 3600)])
def test_valid_interval_is_preserved(monkeypatch, raw, expected):
    monkeypatch.setenv("SOI_POLL_SECONDS", raw)
    assert get_soi_poll_interval() == expected
