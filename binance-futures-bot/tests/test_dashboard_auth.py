"""server.py의 선택적 HTTP Basic Auth(DashboardAuthMiddleware) 검증.

DASHBOARD_USERNAME/PASSWORD를 둘 다 설정했을 때만 켜지고(opt-in), 기본값
(둘 중 하나라도 비어있음)에서는 지금까지와 완전히 동일하게 인증 없이
열려야 한다 - 기존 배포 동작을 절대 깨면 안 되는 부분이라 별도로 검증."""
from __future__ import annotations

import base64

from fastapi.testclient import TestClient

import server
from app.config import settings


def _basic_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_auth_disabled_by_default_allows_requests_through(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")

    client = TestClient(server.app)
    res = client.get("/api/health")

    assert res.status_code == 200  # 기존 동작 그대로 - 인증 없이 통과


def test_auth_disabled_when_only_username_set(monkeypatch):
    """하나만 채워지면(둘 다 채워야 켜짐) 여전히 꺼진 상태여야 한다."""
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "")

    client = TestClient(server.app)
    res = client.get("/api/health")

    assert res.status_code == 200


def test_auth_enabled_rejects_missing_credentials(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret123")

    client = TestClient(server.app)
    res = client.get("/api/health")

    assert res.status_code == 401
    assert "Basic" in res.headers.get("www-authenticate", "")


def test_auth_enabled_rejects_wrong_credentials(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret123")

    client = TestClient(server.app)
    res = client.get("/api/health", headers=_basic_header("admin", "wrong"))

    assert res.status_code == 401


def test_auth_enabled_accepts_correct_credentials(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret123")

    client = TestClient(server.app)
    res = client.get("/api/health", headers=_basic_header("admin", "secret123"))

    assert res.status_code == 200


def test_auth_enabled_protects_dashboard_page_too(monkeypatch):
    """API뿐 아니라 페이지(정적 파일 서빙 포함) 전체가 걸려야 한다."""
    monkeypatch.setattr(settings, "dashboard_username", "admin")
    monkeypatch.setattr(settings, "dashboard_password", "secret123")

    client = TestClient(server.app)
    assert client.get("/trading").status_code == 401
    assert client.get("/trading", headers=_basic_header("admin", "secret123")).status_code == 200
