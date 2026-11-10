"""app/emergency_stop.py 검증 - 계좌 전체 긴급 서킷브레이커.

바이낸스 실호출 없음 - get_account_snapshot/broker를 가짜로 바꿔 통제한다.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import app.emergency_stop as es
from app.config import settings
from app.db import SessionLocal, TradeRecord


class FakeBroker:
    def __init__(self):
        self.close_calls = []
        self.fail_symbols: set[str] = set()

    def close_position_market(self, symbol, quantity, side="SELL"):
        self.close_calls.append((symbol, quantity, side))
        if symbol in self.fail_symbols:
            from app.broker import BrokerError
            raise BrokerError(f"{symbol} 청산 실패(가짜)")
        return {"order_id": f"close-{symbol}", "price": 90.0}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "EMERGENCY_HALT_FILE", tmp_path / "emergency_halt.json")
    monkeypatch.setattr(es, "EMERGENCY_REFERENCE_FILE", tmp_path / "emergency_reference.json")
    original_pct = settings.emergency_stop_pct
    yield
    settings.emergency_stop_pct = original_pct


def _snapshot(balance: float) -> dict:
    return {
        "total_wallet_balance": balance,
        "total_unrealized_profit": 0.0,
        "total_margin_balance": balance,
        "available_balance": balance,
        "starting_balance_usdt": None,
        "cumulative_return_pct": None,
    }


def _add_open_trade(**overrides) -> int:
    session = SessionLocal()
    try:
        trade = TradeRecord(
            symbol="BTCUSDT", timeframe="15m", side="BUY", status="OPEN",
            quantity=0.01, entry_price=100.0,
            opened_at=datetime(2026, 1, 1),
        )
        for k, v in overrides.items():
            setattr(trade, k, v)
        session.add(trade)
        session.commit()
        return trade.id
    finally:
        session.close()


def test_disabled_by_default_does_nothing(monkeypatch):
    settings.emergency_stop_pct = 0
    monkeypatch.setattr(es, "get_account_snapshot", lambda broker: _snapshot(900_000))

    triggered = es.check_emergency_stop(broker=FakeBroker())

    assert triggered is False
    assert not es.is_emergency_halted()


def test_no_trigger_when_within_threshold(monkeypatch):
    settings.emergency_stop_pct = 5.0
    monkeypatch.setattr(es, "get_account_snapshot", lambda broker: _snapshot(980_000))  # -2%, 5% 기준 안쪽
    # 기준점(1,000,000)을 먼저 심어둔다
    es._todays_reference_balance(1_000_000)

    triggered = es.check_emergency_stop(broker=FakeBroker())

    assert triggered is False
    assert not es.is_emergency_halted()


def test_triggers_and_closes_all_open_positions(monkeypatch):
    settings.emergency_stop_pct = 5.0
    es._todays_reference_balance(1_000_000)
    monkeypatch.setattr(es, "get_account_snapshot", lambda broker: _snapshot(940_000))  # -6%, 기준 초과
    _add_open_trade(symbol="BTCUSDT", timeframe="15m", side="BUY", quantity=0.01, entry_price=100.0)
    _add_open_trade(symbol="ETHUSDT", timeframe="5m", side="SELL", quantity=1.0, entry_price=2000.0)
    broker = FakeBroker()

    triggered = es.check_emergency_stop(broker=broker)

    assert triggered is True
    assert es.is_emergency_halted()
    assert len(broker.close_calls) == 2
    session = SessionLocal()
    try:
        trades = session.query(TradeRecord).all()
        assert all(t.status == "CLOSED_MANUAL" for t in trades)
        assert all(t.exit_price == 90.0 for t in trades)
        assert all(t.realized_pnl_usdt is not None for t in trades)
    finally:
        session.close()


def test_does_not_recheck_when_already_halted(monkeypatch):
    settings.emergency_stop_pct = 5.0
    es.EMERGENCY_HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    es.EMERGENCY_HALT_FILE.write_text("{}")
    called = {"n": 0}

    def boom(broker):
        called["n"] += 1
        raise AssertionError("이미 halted인데 계좌 조회를 또 하면 안 됨")

    monkeypatch.setattr(es, "get_account_snapshot", boom)

    triggered = es.check_emergency_stop(broker=FakeBroker())

    assert triggered is False
    assert called["n"] == 0


def test_clear_emergency_halt_removes_file():
    es.EMERGENCY_HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    es.EMERGENCY_HALT_FILE.write_text("{}")
    assert es.is_emergency_halted()

    es.clear_emergency_halt()

    assert not es.is_emergency_halted()


def test_broker_query_failure_is_safe(monkeypatch):
    from app.broker import BrokerError

    settings.emergency_stop_pct = 5.0

    def boom(broker):
        raise BrokerError("조회 실패(가짜)")

    monkeypatch.setattr(es, "get_account_snapshot", boom)

    triggered = es.check_emergency_stop(broker=FakeBroker())

    assert triggered is False
    assert not es.is_emergency_halted()


def test_reference_balance_fixed_for_the_day_even_if_queried_again(monkeypatch):
    settings.emergency_stop_pct = 5.0
    monkeypatch.setattr(es, "get_account_snapshot", lambda broker: _snapshot(1_000_000))
    es.check_emergency_stop(broker=FakeBroker())  # 오늘 첫 호출 - 기준점 1,000,000으로 고정됨

    # 그 다음 호출부터 잔고가 950,000(-5%, 기준 이상)이면 최초 기준(1,000,000) 대비로 판정돼야 함
    monkeypatch.setattr(es, "get_account_snapshot", lambda broker: _snapshot(949_000))  # -5.1%

    triggered = es.check_emergency_stop(broker=FakeBroker())

    assert triggered is True


def test_close_all_continues_after_one_broker_failure(monkeypatch):
    settings.emergency_stop_pct = 5.0
    es._todays_reference_balance(1_000_000)
    monkeypatch.setattr(es, "get_account_snapshot", lambda broker: _snapshot(900_000))
    _add_open_trade(symbol="BTCUSDT", timeframe="15m", side="BUY", quantity=0.01, entry_price=100.0)
    _add_open_trade(symbol="ETHUSDT", timeframe="5m", side="SELL", quantity=1.0, entry_price=2000.0)
    broker = FakeBroker()
    broker.fail_symbols = {"BTCUSDT"}

    triggered = es.check_emergency_stop(broker=broker)

    assert triggered is True  # 전부 실패해도 halt 자체는 걸려야 함
    session = SessionLocal()
    try:
        btc = session.query(TradeRecord).filter_by(symbol="BTCUSDT").one()
        eth = session.query(TradeRecord).filter_by(symbol="ETHUSDT").one()
        assert btc.status == "OPEN"  # 청산 실패 - 수동 확인 필요한 채로 남음
        assert eth.status == "CLOSED_MANUAL"  # 다른 하나는 정상 청산됨
    finally:
        session.close()
