"""server.py의 백테스트 성적표 정기 갱신 로직(_refresh_all_stats) 검증.

서버 전체를 띄우지 않고, 네 build_* 함수를 monkeypatch해서 호출 여부/순서와
겹쳐 돌지 않는지(락)만 확인한다 - 실제 바이낸스 호출/FastAPI 기동 없음.
"""
from __future__ import annotations

import threading
import time

import server


def test_refresh_all_stats_calls_all_four_builders(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "build_strategy_stats", lambda: calls.append("strategy"))
    monkeypatch.setattr(server, "build_lab_stats", lambda: calls.append("lab"))
    monkeypatch.setattr(server, "build_validated_lab_stats", lambda: calls.append("validated"))
    monkeypatch.setattr(server, "build_multi_screen_trades", lambda: calls.append("multi_screen"))

    server._refresh_all_stats()

    assert calls == ["strategy", "lab", "validated", "multi_screen"]


def test_refresh_all_stats_releases_lock_even_on_failure(monkeypatch):
    monkeypatch.setattr(server, "build_strategy_stats", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(server, "build_lab_stats", lambda: None)
    monkeypatch.setattr(server, "build_validated_lab_stats", lambda: None)
    monkeypatch.setattr(server, "build_multi_screen_trades", lambda: None)

    server._refresh_all_stats()  # 예외를 삼키고 로그만 남겨야 함 (raise 안 함)

    assert server._stats_refresh_lock.acquire(blocking=False)  # 락이 풀려있어야 함
    server._stats_refresh_lock.release()


def test_refresh_all_stats_skips_when_already_running(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_build():
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(server, "build_strategy_stats", slow_build)
    monkeypatch.setattr(server, "build_lab_stats", lambda: calls.append("lab"))
    monkeypatch.setattr(server, "build_validated_lab_stats", lambda: calls.append("validated"))
    monkeypatch.setattr(server, "build_multi_screen_trades", lambda: calls.append("multi_screen"))

    t = threading.Thread(target=server._refresh_all_stats)
    t.start()
    assert started.wait(timeout=2)  # 첫 번째 갱신이 build_strategy_stats 안에서 멈춰있는 동안

    server._refresh_all_stats()  # 두 번째 호출 - 락을 못 얻으니 즉시 건너뛰어야 함
    assert calls == []  # lab/validated/multi_screen은 아예 호출 안 됐어야 함

    release.set()
    t.join(timeout=2)
