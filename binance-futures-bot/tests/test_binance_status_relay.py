"""app/binance_status_relay.py 검증 - Render 등 지역차단 우회용 상태 릴레이.

송신측(push_status_once)은 실제 바이낸스 호출 없이 get_live_status를 가짜로
바꿔 통제하고, requests.post 자체도 가짜로 바꿔 네트워크 호출 없이 검증한다.
"""
from __future__ import annotations

import time

import pytest

import app.binance_status_relay as relay
from app.config import settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    original_url = settings.remote_control_url
    original_token = settings.status_push_token
    relay._pushed_status = None
    relay._pushed_at = 0.0
    yield
    settings.remote_control_url = original_url
    settings.status_push_token = original_token
    relay._pushed_status = None
    relay._pushed_at = 0.0


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_push_does_nothing_when_url_not_configured(monkeypatch):
    settings.remote_control_url = ""
    settings.status_push_token = "secret"
    called = {"n": 0}
    monkeypatch.setattr(relay.requests, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    result = relay.push_status_once(broker=object())

    assert result is False
    assert called["n"] == 0


def test_push_does_nothing_when_token_not_configured(monkeypatch):
    settings.remote_control_url = "https://dashboard.example.com"
    settings.status_push_token = ""
    called = {"n": 0}
    monkeypatch.setattr(relay.requests, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    result = relay.push_status_once(broker=object())

    assert result is False
    assert called["n"] == 0


def test_push_skips_when_local_status_not_ready(monkeypatch):
    settings.remote_control_url = "https://dashboard.example.com"
    settings.status_push_token = "secret"
    monkeypatch.setattr(relay, "get_live_status", lambda broker: {"ready": False, "reason": "키 없음"})
    called = {"n": 0}
    monkeypatch.setattr(relay.requests, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    result = relay.push_status_once(broker=object())

    assert result is False
    assert called["n"] == 0


def test_push_sends_ready_status_with_token_header(monkeypatch):
    settings.remote_control_url = "https://dashboard.example.com"
    settings.status_push_token = "secret-token"
    fake_status = {"ready": True, "testnet": True, "account": {}, "open_positions": [], "recent_trades": []}
    monkeypatch.setattr(relay, "get_live_status", lambda broker: fake_status)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse(200)

    monkeypatch.setattr(relay.requests, "post", fake_post)

    result = relay.push_status_once(broker=object())

    assert result is True
    assert captured["url"] == "https://dashboard.example.com/api/binance/status/push"
    assert captured["json"] == fake_status
    assert captured["headers"] == {"X-Status-Push-Token": "secret-token"}


def test_push_returns_false_on_network_failure(monkeypatch):
    settings.remote_control_url = "https://dashboard.example.com"
    settings.status_push_token = "secret"
    monkeypatch.setattr(relay, "get_live_status", lambda broker: {"ready": True})

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(relay.requests, "post", boom)

    result = relay.push_status_once(broker=object())

    assert result is False


def test_get_pushed_status_returns_none_when_nothing_recorded():
    assert relay.get_pushed_status() is None


def test_record_and_get_pushed_status_roundtrip():
    status = {"ready": True, "account": {"total_wallet_balance": 1000.0}}

    relay.record_pushed_status(status)

    assert relay.get_pushed_status() == status


def test_get_pushed_status_returns_none_when_stale(monkeypatch):
    relay.record_pushed_status({"ready": True})
    future = time.time() + relay.STALE_AFTER_SECONDS + 1
    monkeypatch.setattr(relay.time, "time", lambda: future)

    assert relay.get_pushed_status() is None
