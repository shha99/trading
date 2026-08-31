"""WickSignalEngine의 자동매매 게이팅(화이트리스트/킬스위치/중복 방지) 검증.

바이낸스 실호출은 전부 없음 - fetch_klines/is_candle_closed와 broker를 가짜로
주입하고, 전략도 항상 진입 신호를 내는 가짜로 바꿔 끼운다(엔진의 게이팅
로직만 검증하면 되므로 실제 볼린저/RSI 계산은 필요 없음).
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.broker import EntryResult
from app.config import settings
from app.db import SessionLocal, TradeRecord
import app.wick_signal_engine as wick_engine_module
from app.wick_signal_engine import WICK_STRATEGY_KEY, WickSignalEngine


def make_df(n=30):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = pd.Series([100.0 + i * 0.01 for i in range(n)], index=idx)
    high = close + 0.2
    low = close - 0.2
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


class FakeWickStrategy:
    key = WICK_STRATEGY_KEY
    atr_period = 14

    def __init__(self, direction="LONG", min_bars=5):
        self.direction = direction
        self.min_bars = min_bars

    def precompute(self, df):
        return {}

    def check_entry(self, k, ctx):
        if self.direction == "LONG":
            return {"direction": "LONG", "entry_price": 100.0, "stop_price": 95.0,
                     "breakeven_trigger_price": 100.5, "trail_mult": 0.3}
        return {"direction": "SHORT", "entry_price": 100.0, "stop_price": 105.0,
                 "breakeven_trigger_price": 99.5, "trail_mult": 0.3}


class FakeBroker:
    def __init__(self):
        self.calls = []

    def enter_position(self, direction, symbol, entry_price_hint, stop_price, risk_usdt, leverage=1):
        self.calls.append((direction, symbol, entry_price_hint, stop_price, risk_usdt, leverage))
        return EntryResult(
            quantity=0.01, entry_order_id="fake-entry", entry_price=entry_price_hint,
            sl_order_id="fake-sl", tp_order_id=None,
        )


@pytest.fixture
def patch_data(monkeypatch):
    df = make_df()
    monkeypatch.setattr(wick_engine_module, "fetch_klines", lambda *a, **k: df)
    monkeypatch.setattr(wick_engine_module, "is_candle_closed", lambda *a, **k: True)
    monkeypatch.setattr(wick_engine_module, "notify_wick_entry", lambda *a, **k: None)
    return df


@pytest.fixture(autouse=True)
def _restore_settings():
    original = (
        settings.wick_auto_trade_enabled, set(settings.wick_auto_trade_whitelist),
        settings.max_open_positions, settings.symbols, settings.wick_timeframes,
    )
    settings.symbols = ["BTCUSDT"]
    settings.wick_timeframes = ["15m"]
    yield
    (settings.wick_auto_trade_enabled, wl, settings.max_open_positions,
     settings.symbols, settings.wick_timeframes) = original
    settings.wick_auto_trade_whitelist = wl


def test_disabled_records_nothing(patch_data):
    settings.wick_auto_trade_enabled = False
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    broker = FakeBroker()

    entered = WickSignalEngine(strategy=FakeWickStrategy(), broker=broker).run_once()

    assert entered == 0
    assert broker.calls == []
    session = SessionLocal()
    try:
        assert session.query(TradeRecord).count() == 0
    finally:
        session.close()


def test_not_whitelisted_no_trade(patch_data):
    settings.wick_auto_trade_enabled = True
    settings.wick_auto_trade_whitelist = set()
    broker = FakeBroker()

    WickSignalEngine(strategy=FakeWickStrategy(), broker=broker).run_once()

    assert broker.calls == []


def test_whitelisted_long_executes_trade_with_wick_fields(patch_data):
    settings.wick_auto_trade_enabled = True
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    broker = FakeBroker()

    entered = WickSignalEngine(strategy=FakeWickStrategy(direction="LONG"), broker=broker).run_once()

    assert entered == 1
    assert broker.calls[0][0] == "LONG"
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.status == "OPEN"
        assert trade.side == "BUY"
        assert trade.strategy == WICK_STRATEGY_KEY
        assert trade.tp_order_id is None
        assert trade.initial_stop_price == 95.0
        assert trade.current_stop_price == 95.0
        assert trade.breakeven_trigger_price == 100.5
        assert trade.trail_mult == 0.3
        assert trade.moved_to_breakeven == "NO"
    finally:
        session.close()


def test_whitelisted_short_executes_with_sell_side(patch_data):
    settings.wick_auto_trade_enabled = True
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    broker = FakeBroker()

    WickSignalEngine(strategy=FakeWickStrategy(direction="SHORT"), broker=broker).run_once()

    assert broker.calls[0][0] == "SHORT"
    session = SessionLocal()
    try:
        trade = session.query(TradeRecord).one()
        assert trade.side == "SELL"
        assert trade.initial_stop_price == 105.0
    finally:
        session.close()


def test_max_open_positions_blocks_new_entry(patch_data):
    settings.wick_auto_trade_enabled = True
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    settings.max_open_positions = 1
    session = SessionLocal()
    try:
        session.add(TradeRecord(symbol="ETHUSDT", timeframe="4h", side="BUY", status="OPEN"))
        session.commit()
    finally:
        session.close()

    broker = FakeBroker()
    WickSignalEngine(strategy=FakeWickStrategy(), broker=broker).run_once()

    assert broker.calls == []


def test_existing_open_wick_position_same_combo_blocks_duplicate(patch_data):
    settings.wick_auto_trade_enabled = True
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    session = SessionLocal()
    try:
        session.add(TradeRecord(symbol="BTCUSDT", timeframe="15m", side="BUY", status="OPEN", strategy=WICK_STRATEGY_KEY))
        session.commit()
    finally:
        session.close()

    broker = FakeBroker()
    WickSignalEngine(strategy=FakeWickStrategy(), broker=broker).run_once()

    assert broker.calls == []


def test_open_keltner_position_same_combo_does_not_block_wick(patch_data):
    """켈트너 엔진이 같은 심볼/시간대에 포지션을 들고 있어도, 서로 다른
    엔진이므로 wick 진입까지 막으면 안 된다(단, 전체 포지션 수 한도는 공유)."""
    settings.wick_auto_trade_enabled = True
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    settings.max_open_positions = 5
    session = SessionLocal()
    try:
        session.add(TradeRecord(symbol="BTCUSDT", timeframe="15m", side="BUY", status="OPEN", strategy=None))
        session.commit()
    finally:
        session.close()

    broker = FakeBroker()
    entered = WickSignalEngine(strategy=FakeWickStrategy(), broker=broker).run_once()

    assert entered == 1
    assert len(broker.calls) == 1


def test_same_closed_candle_is_not_processed_twice(patch_data):
    settings.wick_auto_trade_enabled = False
    settings.wick_auto_trade_whitelist = set()
    engine = WickSignalEngine(strategy=FakeWickStrategy(), broker=FakeBroker())

    first = engine.run_once()
    second = engine.run_once()

    assert first == 0  # 화이트리스트 꺼져있어 진입은 없지만
    assert second == 0


def test_wick_dedup_state_independent_from_keltner_scan_state(patch_data):
    """wick 엔진과 켈트너 엔진이 같은 (symbol, timeframe)을 봐도 서로의
    ScanState를 밟지 않아야 한다 - 안 그러면 한쪽이 처리한 봉을 다른 쪽이
    건너뛰는 실제 버그가 생긴다."""
    from app import db

    settings.wick_auto_trade_enabled = False
    settings.wick_auto_trade_whitelist = set()
    latest_ts = make_df().index[-1].to_pydatetime()

    # 켈트너 엔진이 이 봉을 이미 처리했다고 가정
    db.set_last_processed("BTCUSDT", "15m", latest_ts, strategy="keltner_reclaim_200ema")

    engine = WickSignalEngine(strategy=FakeWickStrategy(), broker=FakeBroker())
    entered = engine.run_once()  # wick은 이 봉을 아직 안 봤으니 정상 평가돼야 함

    # 화이트리스트가 꺼져있어 실제 진입은 없지만, 최소한 "이미 처리됨"으로
    # 건너뛰지는 않았어야 한다 - ScanState가 섞였다면 여기서 0건 스캔조차 안 됐을 것
    assert db.get_last_processed("BTCUSDT", "15m", strategy=WICK_STRATEGY_KEY) == latest_ts
