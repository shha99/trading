"""run_trading_bot.py(헤드리스 매매 루프) 검증.

스케줄러를 실제로 돌리지 않고, 각 헬퍼 함수를 monkeypatch해서 호출 여부/
예외 격리만 확인한다 - 실제 바이낸스 호출/블로킹 스케줄러 기동 없음."""
from __future__ import annotations

import run_trading_bot as bot


def test_run_initial_scan_calls_all_three_engines(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "run_signal_once", lambda: calls.append("keltner"))
    monkeypatch.setattr(bot, "run_wick_signal_once", lambda: calls.append("wick"))
    monkeypatch.setattr(bot, "run_paper_trading_once", lambda: calls.append("paper"))

    bot._run_initial_scan()

    assert calls == ["keltner", "wick", "paper"]


def test_run_initial_scan_isolates_failures_between_engines(monkeypatch):
    """한 엔진의 최초 실행이 실패해도 (raise 없이) 나머지 엔진은 그대로 실행돼야 한다."""
    calls = []
    monkeypatch.setattr(bot, "run_signal_once", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(bot, "run_wick_signal_once", lambda: calls.append("wick"))
    monkeypatch.setattr(bot, "run_paper_trading_once", lambda: calls.append("paper"))

    bot._run_initial_scan()  # 예외를 삼키고 계속 진행해야 함 (raise 안 함)

    assert calls == ["wick", "paper"]


def test_position_watch_tick_calls_all_four_checks(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "check_time_stops", lambda: calls.append("time_stops"))
    monkeypatch.setattr(bot, "reconcile_open_positions", lambda: calls.append("reconcile"))
    monkeypatch.setattr(bot, "check_signal_outcomes", lambda: calls.append("outcomes"))
    monkeypatch.setattr(bot, "manage_wick_positions", lambda: calls.append("wick_positions"))

    bot._position_watch_tick()

    assert calls == ["time_stops", "reconcile", "outcomes", "wick_positions"]


def test_log_startup_banner_does_not_raise():
    bot._log_startup_banner()  # 로그만 남기는 smoke test - 예외 없이 끝나면 통과
