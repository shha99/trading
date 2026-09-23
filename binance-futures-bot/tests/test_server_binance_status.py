"""server.py::/api/binance/status 검증 - app/binance_account.py를 그대로
호출하는 얇은 엔드포인트이므로, "설정 안 됨" 경로만 서버 레벨로 확인한다
(나머지 상세 로직은 tests/test_binance_account.py에서 이미 커버됨)."""
from __future__ import annotations

from fastapi.testclient import TestClient

import server
from app.config import settings


def test_binance_status_endpoint_reports_not_ready_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_username", "")
    monkeypatch.setattr(settings, "dashboard_password", "")
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")

    client = TestClient(server.app)
    res = client.get("/api/binance/status")

    assert res.status_code == 200
    assert res.json() == {"ready": False, "reason": "BINANCE_API_KEY/SECRET이 설정되지 않음"}
