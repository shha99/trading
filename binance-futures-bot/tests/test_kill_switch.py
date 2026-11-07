"""app/risk.py::is_kill_switch_active 검증 - 고정금액제(DAILY_LOSS_LIMIT_USDT)
vs 정률제(DAILY_LOSS_LIMIT_PCT) 전환, 잔고 조회 실패/0 이하 시 고정금액제로
폴백. 실제 바이낸스 호출 없음 - 가짜 broker 주입 + DB에 직접 TradeRecord를
심어서 "오늘 실현손익"을 통제한다."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.db import SessionLocal, TradeRecord, init_db
from app.risk import is_kill_switch_active


class FakeBroker:
    def __init__(self, balance=None, raises=False):
        self._balance = balance
        self._raises = raises

    def get_available_balance_usdt(self) -> float:
        if self._raises:
            raise RuntimeError("네트워크 오류")
        return self._balance


def _seed_realized_pnl(amount: float) -> None:
    init_db()
    session = SessionLocal()
    try:
        session.add(
            TradeRecord(
                symbol="BTCUSDT", timeframe="15m", side="BUY", status="CLOSED_SL",
                strategy="bollinger_wick_breakeven_trail",
                realized_pnl_usdt=amount, closed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_trade_records():
    init_db()
    session = SessionLocal()
    try:
        session.query(TradeRecord).delete()
        session.commit()
    finally:
        session.close()
    yield


def test_fixed_mode_triggers_when_loss_exceeds_limit(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 50.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 0.0)
    _seed_realized_pnl(-60.0)

    assert is_kill_switch_active() is True


def test_fixed_mode_not_triggered_when_within_limit(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 50.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 0.0)
    _seed_realized_pnl(-30.0)

    assert is_kill_switch_active() is False


def test_zero_or_below_limit_disables_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 0.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 0.0)
    _seed_realized_pnl(-1_000_000.0)  # 얼마를 잃어도 꺼져있으면 무조건 통과

    assert is_kill_switch_active() is False


def test_percent_mode_computes_limit_from_live_balance(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 50.0)  # 정률제가 우선해야 함(무시돼야 함)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 20.0)
    broker = FakeBroker(balance=1000.0)  # 20% = 200
    _seed_realized_pnl(-150.0)  # 한도(200) 안쪽 -> 아직 안 걸림

    assert is_kill_switch_active(broker) is False

    _seed_realized_pnl(-60.0)  # 누적 -210 -> 한도(200) 초과

    assert is_kill_switch_active(broker) is True


def test_percent_mode_falls_back_to_fixed_without_broker(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 50.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 20.0)
    _seed_realized_pnl(-60.0)  # 정률제 계산 불가 -> 고정 50으로 폴백, -60은 초과

    assert is_kill_switch_active(broker=None) is True


def test_percent_mode_falls_back_to_fixed_on_broker_exception(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 1_000_000.0)  # 폴백되면 절대 안 걸리게
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 20.0)
    broker = FakeBroker(raises=True)
    _seed_realized_pnl(-60.0)

    assert is_kill_switch_active(broker) is False


def test_percent_mode_falls_back_to_fixed_on_zero_balance(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 1_000_000.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 20.0)
    broker = FakeBroker(balance=0.0)
    _seed_realized_pnl(-60.0)

    assert is_kill_switch_active(broker) is False


def test_todays_pnl_excludes_yesterdays_trades(monkeypatch):
    monkeypatch.setattr(settings, "daily_loss_limit_usdt", 50.0)
    monkeypatch.setattr(settings, "daily_loss_limit_pct", 0.0)
    init_db()
    session = SessionLocal()
    try:
        session.add(
            TradeRecord(
                symbol="BTCUSDT", timeframe="15m", side="BUY", status="CLOSED_SL",
                strategy="bollinger_wick_breakeven_trail",
                realized_pnl_usdt=-500.0, closed_at=datetime(2000, 1, 1),
            )
        )
        session.commit()
    finally:
        session.close()

    assert is_kill_switch_active() is False
