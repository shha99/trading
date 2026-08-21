"""SignalEngine의 자동매매 게이팅(화이트리스트/킬스위치/중복 방지) 검증.

바이낸스 실호출은 전부 없음 — fetch_klines/is_candle_closed와 broker를
가짜로 주입한다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.broker import EntryResult
from app.config import settings
from app.db import SessionLocal, SignalRecord, TradeRecord
import app.signal_engine as signal_engine_module
from app.signal_engine import SignalEngine

WARMUP_BARS = 205


def make_df(closes):
    idx = pd.bdate_range("2024-01-01", periods=len(closes), freq="h")
    close = pd.Series([float(c) for c in closes], index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1_000.0] * len(closes), index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def signal_triggering_df() -> pd.DataFrame:
    base = [100.0 + i for i in range(WARMUP_BARS)]
    closes = base + [base[-1] - 20.0, base[-1] - 20.0 + 3.0]
    return make_df(closes)


class FakeBroker:
    def __init__(self):
        self.calls = []

    def enter_long(self, symbol, entry_price_hint, stop_price, target_price, risk_usdt, leverage=1):
        self.calls.append((symbol, entry_price_hint, stop_price, target_price, risk_usdt, leverage))
        return EntryResult(
            quantity=0.01, entry_order_id="fake-1", entry_price=entry_price_hint,
            sl_order_id="fake-sl", tp_order_id="fake-tp",
        )


@pytest.fixture
def patch_data(monkeypatch):
    df = signal_triggering_df()
    monkeypatch.setattr(signal_engine_module, "fetch_klines", lambda *a, **k: df)
    monkeypatch.setattr(signal_engine_module, "is_candle_closed", lambda *a, **k: True)
    monkeypatch.setattr(signal_engine_module, "notify_signal", lambda *a, **k: None)
    return df


@pytest.fixture(autouse=True)
def _restore_settings():
    original = (
        settings.auto_trade_enabled,
        set(settings.auto_trade_whitelist),
        settings.max_open_positions,
        settings.symbols,
        settings.timeframes,
    )
    settings.symbols = ["BTCUSDT"]
    settings.timeframes = ["1h"]
    yield
    settings.auto_trade_enabled, wl, settings.max_open_positions, settings.symbols, settings.timeframes = original
    settings.auto_trade_whitelist = wl


def test_auto_trade_disabled_records_signal_but_no_trade(patch_data):
    settings.auto_trade_enabled = False
    settings.auto_trade_whitelist = {("BTCUSDT", "1h")}
    engine = SignalEngine(broker=FakeBroker())

    signals = engine.run_once()

    assert len(signals) == 1
    session = SessionLocal()
    try:
        assert session.query(SignalRecord).count() == 1
        assert session.query(TradeRecord).count() == 0
        assert session.query(SignalRecord).first().auto_traded == "NO"
    finally:
        session.close()
    assert engine.broker.calls == []


def test_not_whitelisted_no_trade(patch_data):
    settings.auto_trade_enabled = True
    settings.auto_trade_whitelist = set()  # BTCUSDT:1h 없음
    engine = SignalEngine(broker=FakeBroker())

    engine.run_once()

    session = SessionLocal()
    try:
        assert session.query(TradeRecord).count() == 0
    finally:
        session.close()


def test_whitelisted_executes_trade(patch_data):
    settings.auto_trade_enabled = True
    settings.auto_trade_whitelist = {("BTCUSDT", "1h")}
    broker = FakeBroker()
    engine = SignalEngine(broker=broker)

    engine.run_once()

    assert len(broker.calls) == 1
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "OPEN"
        assert trade.symbol == "BTCUSDT"
        assert session.query(SignalRecord).first().auto_traded == "YES"
    finally:
        session.close()


def test_max_open_positions_blocks_new_entry(patch_data):
    settings.auto_trade_enabled = True
    settings.auto_trade_whitelist = {("BTCUSDT", "1h")}
    settings.max_open_positions = 1
    session = SessionLocal()
    try:
        session.add(TradeRecord(symbol="ETHUSDT", timeframe="4h", side="BUY", status="OPEN"))
        session.commit()
    finally:
        session.close()

    broker = FakeBroker()
    SignalEngine(broker=broker).run_once()

    assert broker.calls == []


def test_existing_open_position_same_symbol_blocks_duplicate(patch_data):
    settings.auto_trade_enabled = True
    settings.auto_trade_whitelist = {("BTCUSDT", "1h")}
    session = SessionLocal()
    try:
        session.add(TradeRecord(symbol="BTCUSDT", timeframe="1h", side="BUY", status="OPEN"))
        session.commit()
    finally:
        session.close()

    broker = FakeBroker()
    SignalEngine(broker=broker).run_once()

    assert broker.calls == []


def test_same_closed_candle_is_not_processed_twice(patch_data):
    settings.auto_trade_enabled = False
    settings.auto_trade_whitelist = set()
    engine = SignalEngine(broker=FakeBroker())

    first = engine.run_once()
    second = engine.run_once()

    assert len(first) == 1
    assert len(second) == 0  # 같은 완결봉 -> 중복 처리 안 됨
    session = SessionLocal()
    try:
        assert session.query(SignalRecord).count() == 1
    finally:
        session.close()
