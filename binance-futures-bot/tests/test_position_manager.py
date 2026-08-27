"""시간손절 청산 + SL/TP 체결 반영(reconcile) 검증 (바이낸스 실호출 없음)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal, TradeRecord
from app.position_manager import check_time_stops, reconcile_open_positions


class FakeBrokerForTimeStop:
    def __init__(self, fill_price: float = 105.0):
        self.fill_price = fill_price
        self.calls = []

    def close_position_market(self, symbol, quantity, side="SELL"):
        self.calls.append((symbol, quantity, side))
        return {"order_id": "fake-close-1", "price": self.fill_price}


class FakeBrokerForReconcile:
    def __init__(self, order_statuses: dict[str, dict]):
        self.order_statuses = order_statuses
        self.cancelled = []

    def get_order_status(self, symbol, order_id):
        return self.order_statuses.get(order_id)

    def cancel_order(self, symbol, order_id):
        self.cancelled.append((symbol, order_id))


def _add_open_trade(**overrides) -> int:
    session = SessionLocal()
    try:
        trade = TradeRecord(
            symbol="BTCUSDT", timeframe="1h", side="BUY", status="OPEN",
            quantity=0.01, entry_price=100.0, sl_order_id="sl-1", tp_order_id="tp-1",
            time_stop_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
        )
        for k, v in overrides.items():
            setattr(trade, k, v)
        session.add(trade)
        session.commit()
        return trade.id
    finally:
        session.close()


def test_check_time_stops_closes_due_position_and_computes_pnl():
    _add_open_trade()
    broker = FakeBrokerForTimeStop(fill_price=110.0)

    closed = check_time_stops(broker=broker)

    assert closed == 1
    assert broker.calls == [("BTCUSDT", 0.01, "SELL")]
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "CLOSED_TIME"
        assert trade.exit_price == 110.0
        assert trade.realized_pnl_usdt == pytest.approx((110.0 - 100.0) * 0.01)
    finally:
        session.close()


def test_check_time_stops_ignores_not_yet_due_position():
    _add_open_trade(time_stop_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1))
    broker = FakeBrokerForTimeStop()

    assert check_time_stops(broker=broker) == 0
    assert broker.calls == []


def test_reconcile_marks_sl_filled_and_cancels_tp():
    _add_open_trade()
    broker = FakeBrokerForReconcile({"sl-1": {"status": "FILLED", "avgPrice": "98.0"}})

    updated = reconcile_open_positions(broker=broker)

    assert updated == 1
    assert broker.cancelled == [("BTCUSDT", "tp-1")]
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "CLOSED_SL"
        assert trade.exit_price == 98.0
        assert trade.realized_pnl_usdt == pytest.approx((98.0 - 100.0) * 0.01)
    finally:
        session.close()


def test_reconcile_marks_tp_filled_and_cancels_sl():
    _add_open_trade()
    broker = FakeBrokerForReconcile({
        "sl-1": {"status": "NEW"},
        "tp-1": {"status": "FILLED", "avgPrice": "112.0"},
    })

    updated = reconcile_open_positions(broker=broker)

    assert updated == 1
    assert broker.cancelled == [("BTCUSDT", "sl-1")]
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "CLOSED_TP"
        assert trade.exit_price == 112.0
    finally:
        session.close()


def test_reconcile_leaves_still_open_position_untouched():
    _add_open_trade()
    broker = FakeBrokerForReconcile({"sl-1": {"status": "NEW"}, "tp-1": {"status": "NEW"}})

    assert reconcile_open_positions(broker=broker) == 0
    assert broker.cancelled == []
    session = SessionLocal()
    try:
        assert session.query(TradeRecord).one().status == "OPEN"
    finally:
        session.close()
