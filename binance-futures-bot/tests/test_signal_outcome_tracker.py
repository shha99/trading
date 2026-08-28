"""app/signal_outcome_tracker.py 검증 - 합성 캔들 데이터, 네트워크 호출 없음
(fetch_klines를 monkeypatch)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import app.signal_outcome_tracker as tracker_module
from app.db import SessionLocal, SignalRecord


def make_df(closes, highs=None, lows=None, start="2026-01-01", freq="15min"):
    idx = pd.date_range(start, periods=len(closes), freq=freq)
    close = pd.Series([float(c) for c in closes], index=idx)
    high = pd.Series([float(h) for h in highs], index=idx) if highs is not None else close + 0.1
    low = pd.Series([float(l) for l in lows], index=idx) if lows is not None else close - 0.1
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def _add_signal(entry_time, entry_price=100.0, stop_price=95.0, target_price=110.0, time_stop_days=3):
    session = SessionLocal()
    try:
        row = SignalRecord(
            symbol="BTCUSDT", timeframe="15m", strategy="keltner_reclaim_200ema", signal_type="BUY",
            entry_price=entry_price, stop_price=stop_price, target_price=target_price,
            time_stop_at=entry_time + timedelta(days=time_stop_days), timestamp=entry_time,
            auto_traded="NO",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id
    finally:
        session.close()


def _get_signal(signal_id):
    session = SessionLocal()
    try:
        return session.query(SignalRecord).filter_by(id=signal_id).first()
    finally:
        session.close()


def test_resolves_to_tp_when_target_hit(monkeypatch):
    entry_time = pd.Timestamp("2026-01-01 00:00:00")
    closes = [100.0] * 1 + [101.0, 102.0, 111.0, 105.0]  # 4번째 봉 고가가 target(110) 넘김
    highs = [100.1, 101.1, 102.1, 111.5, 105.1]
    df = make_df([100.0, 101.0, 102.0, 111.0, 105.0], highs=highs, start=entry_time)
    monkeypatch.setattr(tracker_module, "fetch_klines", lambda *a, **k: df)

    sid = _add_signal(entry_time.to_pydatetime(), entry_price=100.0, stop_price=95.0, target_price=110.0)
    updated = tracker_module.check_signal_outcomes()

    assert updated == 1
    row = _get_signal(sid)
    assert row.virtual_status == "TP"
    assert row.virtual_exit_price == pytest.approx(110.0)
    assert row.virtual_pct_return == pytest.approx(10.0)


def test_resolves_to_sl_when_stop_hit(monkeypatch):
    entry_time = pd.Timestamp("2026-01-01 00:00:00")
    lows = [99.9, 98.0, 94.0, 90.0, 90.0]
    df = make_df([100.0, 98.5, 94.5, 90.5, 90.5], lows=lows, start=entry_time)
    monkeypatch.setattr(tracker_module, "fetch_klines", lambda *a, **k: df)

    sid = _add_signal(entry_time.to_pydatetime(), entry_price=100.0, stop_price=95.0, target_price=110.0)
    updated = tracker_module.check_signal_outcomes()

    assert updated == 1
    row = _get_signal(sid)
    assert row.virtual_status == "SL"
    assert row.virtual_exit_price == pytest.approx(95.0)
    assert row.virtual_pct_return == pytest.approx(-5.0)
    assert row.virtual_r_multiple == pytest.approx(-1.0)  # 손절 = 정확히 -1R


def test_stays_open_when_neither_stop_nor_target_hit_yet(monkeypatch):
    entry_time = pd.Timestamp("2026-01-01 00:00:00")
    # 손절(95)/익절(110) 사이에서만 움직이고, 시간손절(3일=288봉)에도 아직 못 미침
    df = make_df([100.0, 101.0, 102.0, 101.5, 102.5], start=entry_time)
    monkeypatch.setattr(tracker_module, "fetch_klines", lambda *a, **k: df)

    sid = _add_signal(entry_time.to_pydatetime(), entry_price=100.0, stop_price=95.0, target_price=110.0)
    updated = tracker_module.check_signal_outcomes()

    assert updated == 0
    row = _get_signal(sid)
    assert row.virtual_status == "OPEN"
    assert row.virtual_exit_price is None


def test_resolves_to_time_when_time_stop_genuinely_reached(monkeypatch):
    entry_time = pd.Timestamp("2026-01-01 00:00:00")
    # 3일치(15분봉) = 288봉 + 여유 - 손절/익절 안 닿고 시간손절 시점을 실제로 지나감
    n = 300
    closes = [100.0 + (i % 3) * 0.1 for i in range(n)]  # 95~110 사이에서만 잔잔하게 움직임
    df = make_df(closes, start=entry_time)
    monkeypatch.setattr(tracker_module, "fetch_klines", lambda *a, **k: df)

    sid = _add_signal(entry_time.to_pydatetime(), entry_price=100.0, stop_price=95.0, target_price=110.0, time_stop_days=3)
    updated = tracker_module.check_signal_outcomes()

    assert updated == 1
    row = _get_signal(sid)
    assert row.virtual_status == "TIME"
    assert row.virtual_exit_at is not None


def test_skips_signal_whose_entry_bar_is_out_of_fetch_range(monkeypatch):
    entry_time = pd.Timestamp("2020-01-01 00:00:00")  # fetch_klines가 주는 범위 밖(너무 오래됨)
    df = make_df([100.0, 101.0, 102.0], start="2026-01-01")
    monkeypatch.setattr(tracker_module, "fetch_klines", lambda *a, **k: df)

    sid = _add_signal(entry_time.to_pydatetime())
    updated = tracker_module.check_signal_outcomes()

    assert updated == 0
    row = _get_signal(sid)
    assert row.virtual_status == "OPEN"


def test_ignores_already_resolved_signals(monkeypatch):
    entry_time = pd.Timestamp("2026-01-01 00:00:00")
    highs = [100.1, 101.1, 102.1, 111.5, 105.1]
    df = make_df([100.0, 101.0, 102.0, 111.0, 105.0], highs=highs, start=entry_time)
    fetch_calls = []
    monkeypatch.setattr(tracker_module, "fetch_klines", lambda *a, **k: (fetch_calls.append(1), df)[1])

    sid = _add_signal(entry_time.to_pydatetime(), entry_price=100.0, stop_price=95.0, target_price=110.0)
    tracker_module.check_signal_outcomes()
    assert len(fetch_calls) == 1

    # 이미 TP로 확정됐으니 두 번째 호출에서는 아예 조회 대상에서 빠져야 한다
    updated_again = tracker_module.check_signal_outcomes()
    assert updated_again == 0
    assert len(fetch_calls) == 1


def test_check_signal_outcomes_survives_per_signal_exception(monkeypatch):
    entry_time = pd.Timestamp("2026-01-01 00:00:00")

    def boom(*a, **k):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(tracker_module, "fetch_klines", boom)
    sid = _add_signal(entry_time.to_pydatetime())

    updated = tracker_module.check_signal_outcomes()  # 예외를 삼키고 계속 진행해야 함 (raise 안 함)
    assert updated == 0
    row = _get_signal(sid)
    assert row.virtual_status == "OPEN"
