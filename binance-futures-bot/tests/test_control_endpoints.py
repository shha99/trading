"""server.py::/api/control/status 검증 - 원격 킬스위치 조회/변경 엔드포인트.

GET은 기본값(둘 다 켜짐)과 변경된 값을 확인하고, POST는 대시보드 인증이
꺼져있으면 거부되는지(URL만 알면 아무나 봇을 못 끄게)와, 켜져있을 때 정상
반영되는지, 잘못된 engine 값은 400이 되는지 확인한다."""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient

import server
from app.config import settings


def _basic_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_control_status_defaults_to_both_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")
    monkeypatch.setattr(server, "CONTROL_STATE_FILE", tmp_path / "control_state.json")

    client = TestClient(server.app)
    res = client.get("/api/control/status")

    assert res.status_code == 200
    assert res.json() == {"wick_enabled": True, "keltner_enabled": True}


def test_post_rejected_when_dashboard_auth_not_configured(monkeypatch, tmp_path):
    """URL만 알면 아무나 봇을 끄고 켤 수 있으면 안 되므로, 대시보드 인증을
    먼저 켜둬야만 이 엔드포인트가 동작해야 한다."""
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")
    monkeypatch.setattr(server, "CONTROL_STATE_FILE", tmp_path / "control_state.json")

    client = TestClient(server.app)
    res = client.post("/api/control/status", json={"engine": "wick", "enabled": False})

    assert res.status_code == 403


def test_post_updates_state_when_auth_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret123")
    monkeypatch.setattr(server, "CONTROL_STATE_FILE", tmp_path / "control_state.json")
    auth = _basic_header("admin", "secret123")

    client = TestClient(server.app)
    res = client.post("/api/control/status", json={"engine": "wick", "enabled": False}, headers=auth)

    assert res.status_code == 200
    assert res.json() == {"wick_enabled": False, "keltner_enabled": True}

    # 조회해도 반영된 상태가 그대로 보여야 함
    res2 = client.get("/api/control/status", headers=auth)
    assert res2.json() == {"wick_enabled": False, "keltner_enabled": True}


def test_post_rejects_invalid_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret123")
    monkeypatch.setattr(server, "CONTROL_STATE_FILE", tmp_path / "control_state.json")
    auth = _basic_header("admin", "secret123")

    client = TestClient(server.app)
    res = client.post("/api/control/status", json={"engine": "nope", "enabled": True}, headers=auth)

    assert res.status_code == 400
