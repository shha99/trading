"""app/wick_position_manager.py 검증 - compute_trailing_state(순수 함수) +
manage_wick_positions(합성 캔들, 가짜 브로커 - 실제 바이낸스 호출 없음)."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

import app.wick_position_manager as wpm
from app.db import SessionLocal, TradeRecord
from app.wick_position_manager import WICK_STRATEGY_KEY, compute_trailing_state, manage_wick_positions


# ---------------------------------------------------------------------------
# compute_trailing_state - 순수 함수, app/lab_backtest.py의 상태 전이 로직과 동일
# ---------------------------------------------------------------------------

def test_compute_trailing_state_long_stays_at_initial_stop_before_trigger():
    state = compute_trailing_state(
        "LONG", entry_price=100.0, initial_stop=95.0, trigger_price=100.5, trail_mult=0.3,
        highs=[100.2, 100.4], lows=[99.8, 99.9], atrs=[1.0, 1.0],
    )
    assert state["moved_to_breakeven"] is False
    assert state["stop_price"] == pytest.approx(95.0)  # 트리거 전이라 그대로


def test_compute_trailing_state_long_moves_to_breakeven_then_trails():
    state = compute_trailing_state(
        "LONG", entry_price=100.0, initial_stop=95.0, trigger_price=100.5, trail_mult=0.3,
        highs=[100.6, 102.0], lows=[100.0, 101.0], atrs=[1.0, 1.0],
    )
    assert state["moved_to_breakeven"] is True
    # 2번째 봉 고점 102.0 - 0.3*1.0 = 101.7 > 본전(100.0) 이므로 트레일이 더 유리
    assert state["stop_price"] == pytest.approx(101.7)


def test_compute_trailing_state_short_mirrors_long():
    state = compute_trailing_state(
        "SHORT", entry_price=100.0, initial_stop=105.0, trigger_price=99.5, trail_mult=0.3,
        highs=[100.0, 99.0], lows=[99.4, 98.0], atrs=[1.0, 1.0],
    )
    assert state["moved_to_breakeven"] is True
    assert state["stop_price"] == pytest.approx(98.3)  # 저점 98.0 + 0.3


def test_compute_trailing_state_stop_never_regresses():
    """한번 유리해진 손절선은 다시 나빠지는 쪽으로 안 움직여야 한다."""
    state = compute_trailing_state(
        "LONG", entry_price=100.0, initial_stop=95.0, trigger_price=100.5, trail_mult=0.3,
        highs=[103.0, 100.1], lows=[102.0, 99.0], atrs=[1.0, 1.0],  # 2번째 봉은 크게 하락
        )
    # 최고점 103에서 트레일 = 102.7, 이후 하락해도 최고점은 안 낮아짐 -> 스탑도 안 낮아짐
    assert state["stop_price"] == pytest.approx(102.7)


def test_compute_trailing_state_handles_nan_atr_safely():
    state = compute_trailing_state(
        "LONG", entry_price=100.0, initial_stop=95.0, trigger_price=100.5, trail_mult=0.3,
        highs=[100.6], lows=[100.0], atrs=[float("nan")],
    )
    assert state["moved_to_breakeven"] is True
    assert state["stop_price"] == pytest.approx(100.0)  # ATR이 NaN이면 트레일 계산을 건너뛰고 본전 유지


# ---------------------------------------------------------------------------
# manage_wick_positions - DB + 가짜 브로커
# ---------------------------------------------------------------------------

class FakeBroker:
    def __init__(self, order_status=None):
        self.order_status = order_status or {}
        self.replace_calls = []
        self.cancelled = []

    def get_order_status(self, symbol, order_id):
        return self.order_status.get(order_id)

    def replace_stop_order(self, symbol, direction, quantity, new_stop_price, old_order_id):
        self.replace_calls.append((symbol, direction, quantity, new_stop_price, old_order_id))
        return f"new-{len(self.replace_calls)}"

    def cancel_order(self, symbol, order_id):
        self.cancelled.append((symbol, order_id))


def make_df(closes, highs=None, lows=None, start="2026-01-01", freq="15min", periods=None):
    n = periods or len(closes)
    idx = pd.date_range(start, periods=n, freq=freq)
    close = pd.Series([float(c) for c in closes], index=idx)
    high = pd.Series([float(h) for h in highs], index=idx) if highs is not None else close + 0.2
    low = pd.Series([float(l) for l in lows], index=idx) if lows is not None else close - 0.2
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def _add_open_wick_trade(**overrides) -> int:
    session = SessionLocal()
    try:
        trade = TradeRecord(
            symbol="BTCUSDT", timeframe="15m", side="BUY", status="OPEN",
            quantity=0.01, entry_price=100.0, sl_order_id="sl-1", tp_order_id=None,
            strategy=WICK_STRATEGY_KEY,
            initial_stop_price=95.0, current_stop_price=95.0,
            breakeven_trigger_price=100.5, trail_mult=0.3, atr_period=14,
            moved_to_breakeven="NO",
            opened_at=datetime(2026, 1, 1, 0, 0, 0),
        )
        for k, v in overrides.items():
            setattr(trade, k, v)
        session.add(trade)
        session.commit()
        return trade.id
    finally:
        session.close()


def test_manage_wick_positions_closes_when_stop_order_filled(monkeypatch):
    _add_open_wick_trade()
    broker = FakeBroker(order_status={"sl-1": {"status": "FILLED", "avgPrice": "94.5"}})

    updated = manage_wick_positions(broker=broker)

    assert updated == 1
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "CLOSED_SL"  # moved_to_breakeven=NO였으니 SL로 기록
        assert trade.exit_price == 94.5
        assert trade.realized_pnl_usdt == pytest.approx((94.5 - 100.0) * 0.01)
    finally:
        session.close()


def test_manage_wick_positions_labels_trail_exit_when_already_at_breakeven(monkeypatch):
    _add_open_wick_trade(moved_to_breakeven="YES", current_stop_price=101.0)
    broker = FakeBroker(order_status={"sl-1": {"status": "FILLED", "avgPrice": "101.5"}})

    manage_wick_positions(broker=broker)

    session = SessionLocal()
    try:
        assert session.query(TradeRecord).one().status == "CLOSED_TRAIL"
    finally:
        session.close()


def test_manage_wick_positions_updates_stop_when_price_moved_favorably(monkeypatch):
    _add_open_wick_trade()
    # 진입 이후 완결봉들 - 고점이 트리거(100.5)를 넘어 본전 이동 + 트레일링 발생
    df = make_df(
        closes=[100.0, 100.7, 102.0, 101.5],
        highs=[100.2, 100.8, 102.2, 101.8],
        lows=[99.8, 100.5, 101.5, 101.0],
        start="2026-01-01", freq="15min",
    )
    monkeypatch.setattr(wpm, "fetch_klines", lambda *a, **k: df)
    monkeypatch.setattr(wpm, "is_candle_closed", lambda *a, **k: True)
    broker = FakeBroker(order_status={"sl-1": {"status": "NEW"}})

    updated = manage_wick_positions(broker=broker)

    assert updated == 1
    assert len(broker.replace_calls) == 1
    symbol, direction, qty, new_stop, old_id = broker.replace_calls[0]
    assert symbol == "BTCUSDT" and direction == "LONG" and old_id == "sl-1"
    assert new_stop > 95.0  # 본전 이상으로 유리하게 이동했어야 함

    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "OPEN"  # 아직 청산 안 됨
        assert trade.moved_to_breakeven == "YES"
        assert trade.sl_order_id == "new-1"
        assert trade.current_stop_price == pytest.approx(new_stop)
    finally:
        session.close()


def test_manage_wick_positions_no_change_when_stop_unchanged(monkeypatch):
    _add_open_wick_trade()
    # 트리거(100.5) 근처도 못 감 - 스탑이 그대로여야 함
    df = make_df(closes=[100.0, 100.1, 100.2], highs=[100.1, 100.2, 100.3], lows=[99.9, 100.0, 100.1])
    monkeypatch.setattr(wpm, "fetch_klines", lambda *a, **k: df)
    monkeypatch.setattr(wpm, "is_candle_closed", lambda *a, **k: True)
    broker = FakeBroker(order_status={"sl-1": {"status": "NEW"}})

    updated = manage_wick_positions(broker=broker)

    assert updated == 0
    assert broker.replace_calls == []


def test_manage_wick_positions_ignores_keltner_trades(monkeypatch):
    """strategy가 wick이 아닌(=켈트너) 열린 포지션은 절대 건드리면 안 된다."""
    _add_open_wick_trade(strategy=None, sl_order_id="keltner-sl")
    broker = FakeBroker(order_status={"keltner-sl": {"status": "FILLED", "avgPrice": "90.0"}})

    updated = manage_wick_positions(broker=broker)

    assert updated == 0
    session = SessionLocal()
    try:
        assert session.query(TradeRecord).one().status == "OPEN"
    finally:
        session.close()


def test_manage_wick_positions_survives_per_trade_exception(monkeypatch):
    _add_open_wick_trade()

    def boom(*a, **k):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(wpm, "fetch_klines", boom)
    broker = FakeBroker(order_status={"sl-1": {"status": "NEW"}})

    updated = manage_wick_positions(broker=broker)  # 예외를 삼키고 계속 진행해야 함 (raise 안 함)
    assert updated == 0
