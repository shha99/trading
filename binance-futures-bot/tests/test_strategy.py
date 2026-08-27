"""KeltnerReclaimStrategy 진입조건 검증 (합성 OHLCV).

기존 저장소 backend/tests/test_signals.py의 make_df 스타일을 그대로 따른다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.strategy import KeltnerReclaimStrategy, SignalType

WARMUP_BARS = 205  # min_bars(200EMA 기준) + 여유


def make_df(closes):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    close = pd.Series([float(c) for c in closes], index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1_000.0] * len(closes), index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def uptrend_then(dip: float, bounce: float) -> pd.DataFrame:
    """200봉 완만한 상승 추세(느린 200EMA 대비 종가가 항상 위) 뒤에
    켈트너 하단을 깨는 눌림목(dip) + 복귀(bounce) 2봉을 붙인다."""
    base = [100.0 + i for i in range(WARMUP_BARS)]
    closes = base + [base[-1] + dip, base[-1] + dip + bounce]
    return make_df(closes)


@pytest.fixture
def strategy() -> KeltnerReclaimStrategy:
    return KeltnerReclaimStrategy()


def test_pullback_then_reclaim_triggers_buy(strategy):
    df = uptrend_then(dip=-20.0, bounce=3.0)
    signal = strategy.evaluate("BTCUSDT", "1h", df)
    assert signal is not None
    assert signal.signal_type == SignalType.BUY
    assert signal.details["pattern"] == "keltner_lower_reclaim"
    assert signal.stop_price < signal.entry_price < signal.target_price


def test_no_pullback_no_signal(strategy):
    """눌림목 없이 계속 상승만 하면 반등할 "하단"이 없으므로 시그널이 없다."""
    base = [100.0 + i for i in range(WARMUP_BARS + 2)]
    df = make_df(base)
    assert strategy.evaluate("BTCUSDT", "1h", df) is None


def test_pullback_without_reclaim_no_signal(strategy):
    """눌림목은 발생했지만 아직 켈트너 하단을 다시 넘어서지 못했으면 시그널 없음."""
    df = uptrend_then(dip=-20.0, bounce=0.5)  # 하단 근처에서 머무름 (아직 미돌파)
    assert strategy.evaluate("BTCUSDT", "1h", df) is None


def test_insufficient_bars_returns_none(strategy):
    df = make_df([100.0] * 50)
    assert strategy.evaluate("BTCUSDT", "1h", df) is None


def test_sl_tp_time_stop_are_consistent(strategy):
    df = uptrend_then(dip=-20.0, bounce=3.0)
    signal = strategy.evaluate("BTCUSDT", "1h", df)
    assert signal is not None
    risk = signal.entry_price - signal.stop_price
    reward = signal.target_price - signal.entry_price
    assert risk > 0 and reward > 0
    assert reward == pytest.approx(risk * 2, rel=0.05)  # target_atr_mult(4) / stop_atr_mult(2) = 2배
    assert signal.time_stop_at > signal.timestamp


def test_condition_status_reports_not_enough_bars(strategy):
    status = strategy.condition_status(make_df([100.0] * 50))
    assert status["ready"] is False
    assert status["reason"] == "not_enough_bars"


def test_condition_status_all_met_matches_evaluate(strategy):
    df = uptrend_then(dip=-20.0, bounce=3.0)
    status = strategy.condition_status(df)
    assert status["ready"] is True
    assert status["all_met"] is True
    assert all(status["conditions"].values())
    assert strategy.evaluate("BTCUSDT", "1h", df) is not None


def test_condition_status_partial_met_when_no_pullback(strategy):
    base = [100.0 + i for i in range(WARMUP_BARS + 2)]
    status = strategy.condition_status(make_df(base))
    assert status["ready"] is True
    assert status["all_met"] is False
    assert status["conditions"]["trend_above_200ema"] is True
    assert status["conditions"]["prev_bar_pulled_back_below_keltner_lower"] is False
