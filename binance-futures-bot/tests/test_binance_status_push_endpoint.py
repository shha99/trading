"""server.py::POST /api/binance/status/push + GET /api/binance/status가
그 캐시를 우선 쓰는지 검증 - app/binance_status_relay.py 수신측."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.binance_status_relay as relay
import server
from app.config import settings


def _reset_relay():
    relay._pushed_status = None
    relay._pushed_at = 0.0


def test_push_rejected_when_token_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "status_push_token", "")
    _reset_relay()
    client = TestClient(server.app)

    res = client.post("/api/binance/status/push", json={"ready": True})

    assert res.status_code == 403
    assert relay.get_pushed_status() is None


def test_push_rejected_with_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "status_push_token", "correct-token")
    _reset_relay()
    client = TestClient(server.app)

    res = client.post(
        "/api/binance/status/push", json={"ready": True},
        headers={"X-Status-Push-Token": "wrong-token"},
    )

    assert res.status_code == 403
    assert relay.get_pushed_status() is None


def test_push_accepted_with_correct_token_and_cached(monkeypatch):
    monkeypatch.setattr(settings, "status_push_token", "correct-token")
    _reset_relay()
    client = TestClient(server.app)
    payload = {"ready": True, "testnet": True, "account": {"total_wallet_balance": 1234.0},
               "open_positions": [], "recent_trades": []}

    res = client.post(
        "/api/binance/status/push", json=payload,
        headers={"X-Status-Push-Token": "correct-token"},
    )

    assert res.status_code == 200
    assert relay.get_pushed_status() == payload


def test_get_status_prefers_pushed_cache_over_direct_query(monkeypatch):
    monkeypatch.setattr(settings, "status_push_token", "correct-token")
    _reset_relay()
    pushed_payload = {"ready": True, "testnet": False, "account": {"total_wallet_balance": 999.0},
                       "open_positions": [], "recent_trades": []}
    relay.record_pushed_status(pushed_payload)

    def boom():
        raise AssertionError("캐시가 있으면 직접 조회를 호출하면 안 됨")

    monkeypatch.setattr(server, "get_binance_live_status", boom)
    client = TestClient(server.app)

    res = client.get("/api/binance/status")

    assert res.status_code == 200
    assert res.json() == pushed_payload


def test_get_status_falls_back_to_direct_query_when_no_cache(monkeypatch):
    _reset_relay()
    monkeypatch.setattr(server, "get_binance_live_status", lambda: {"ready": False, "reason": "설정 안 됨"})
    client = TestClient(server.app)

    res = client.get("/api/binance/status")

    assert res.status_code == 200
    assert res.json() == {"ready": False, "reason": "설정 안 됨"}
