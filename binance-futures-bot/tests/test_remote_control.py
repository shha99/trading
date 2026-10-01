"""app/remote_control.py 검증 - 원격 킬스위치의 opt-in 기본값·네트워크 실패
시 마지막 값 유지·프로세스 재시작 후에도 캐시 파일로 상태 유지. 실제
HTTP 호출 없음 - requests.get을 가짜로 주입."""
from __future__ import annotations

import json

import pytest

import app.remote_control as rc
from app.config import settings


@pytest.fixture(autouse=True)
def _reset_module_cache(tmp_path, monkeypatch):
    """각 테스트가 독립된 캐시 파일/메모리 상태로 시작하도록 격리."""
    monkeypatch.setattr(rc, "_CACHE_FILE", tmp_path / "remote_control_cache.json")
    monkeypatch.setattr(rc, "_cache", None)
    yield


class FakeResponse:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_disabled_by_default_always_returns_true(monkeypatch):
    monkeypatch.setattr(settings, "remote_control_url", "")

    assert rc.is_remotely_enabled("wick") is True
    assert rc.is_remotely_enabled("keltner") is True


def test_fetches_and_caches_fresh_value(monkeypatch):
    monkeypatch.setattr(settings, "remote_control_url", "http://dashboard.example")
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: FakeResponse({"wick_enabled": False, "keltner_enabled": True}))

    assert rc.is_remotely_enabled("wick") is False
    assert rc.is_remotely_enabled("keltner") is True


def test_falls_back_to_last_cached_value_on_network_failure(monkeypatch):
    monkeypatch.setattr(settings, "remote_control_url", "http://dashboard.example")
    calls = {"n": 0}

    def flaky_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse({"wick_enabled": False, "keltner_enabled": True})
        raise RuntimeError("네트워크 끊김")

    monkeypatch.setattr(rc.requests, "get", flaky_get)

    assert rc.is_remotely_enabled("wick") is False  # 첫 호출 성공 - False로 캐싱
    assert rc.is_remotely_enabled("wick") is False  # 두번째는 실패하지만 캐시된 False 유지


def test_defaults_to_true_when_never_cached_and_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "remote_control_url", "http://dashboard.example")
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("연결 실패")))

    assert rc.is_remotely_enabled("wick") is True  # 한 번도 성공한 적 없으면 안전하게 True


def test_cache_persists_to_file_across_process_restarts(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "remote_control_url", "http://dashboard.example")
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: FakeResponse({"wick_enabled": False, "keltner_enabled": True}))

    rc.is_remotely_enabled("wick")  # 성공적으로 조회 -> 파일에 저장됨

    # "프로세스 재시작"을 흉내: 메모리 캐시만 지우고, 네트워크는 계속 끊긴 상태로 재조회
    monkeypatch.setattr(rc, "_cache", None)
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("끊김")))

    assert rc.is_remotely_enabled("wick") is False  # 파일에서 복원된 값 유지
