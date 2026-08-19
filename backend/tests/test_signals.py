"""전략별 시그널 계산 로직 유닛 테스트 (합성 OHLCV 데이터 사용)."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from app.signals.base import SignalType
from app.signals.bollinger import BollingerBandStrategy
from app.signals.macd import MACDStrategy
from app.signals.moving_average import MovingAverageCrossStrategy
from app.signals.rsi import RSIStrategy
from app.signals.volume_breakout import VolumeBreakoutStrategy


def make_df(closes, highs=None, lows=None, volumes=None, start="2023-01-02"):
    idx = pd.bdate_range(start=start, periods=len(closes))
    close = pd.Series([float(c) for c in closes], index=idx)
    high = pd.Series([float(h) for h in highs], index=idx) if highs is not None else close.copy()
    low = pd.Series([float(l) for l in lows], index=idx) if lows is not None else close.copy()
    open_ = close.shift(1).fillna(close.iloc[0])
    if volumes is not None:
        volume = pd.Series([float(v) for v in volumes], index=idx)
    else:
        volume = pd.Series([1_000_000.0] * len(closes), index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def scan_signals(strategy, df, symbol="TEST", name="테스트", market="KR"):
    """min_bars부터 끝까지 윈도우를 늘려가며 evaluate를 호출, 발생한 시그널을 모두 모은다."""
    signals = []
    for i in range(strategy.min_bars, len(df) + 1):
        sig = strategy.evaluate(symbol, name, market, df.iloc[:i])
        if sig is not None:
            signals.append(sig)
    return signals


# ---------------------------------------------------------------------------
# 이동평균 골든/데드크로스
# ---------------------------------------------------------------------------

def test_ma_golden_cross_triggers_buy():
    closes = [100.0] * 24 + [130.0]
    df = make_df(closes)
    strategy = MovingAverageCrossStrategy(short_window=5, long_window=20)
    sig = strategy.evaluate("TEST", "테스트", "KR", df)
    assert sig is not None
    assert sig.signal_type == SignalType.BUY
    assert sig.details["pattern"] == "golden_cross"


def test_ma_dead_cross_triggers_sell():
    closes = [100.0] * 24 + [70.0]
    df = make_df(closes)
    strategy = MovingAverageCrossStrategy(short_window=5, long_window=20)
    sig = strategy.evaluate("TEST", "테스트", "KR", df)
    assert sig is not None
    assert sig.signal_type == SignalType.SELL
    assert sig.details["pattern"] == "dead_cross"


def test_ma_flat_prices_no_signal():
    closes = [100.0] * 30
    df = make_df(closes)
    strategy = MovingAverageCrossStrategy()
    assert strategy.evaluate("TEST", "테스트", "KR", df) is None


def test_ma_insufficient_bars_returns_none():
    closes = [100.0] * 10
    df = make_df(closes)
    strategy = MovingAverageCrossStrategy()
    assert strategy.evaluate("TEST", "테스트", "KR", df) is None


# ---------------------------------------------------------------------------
# 볼린저 밴드
# ---------------------------------------------------------------------------

def test_bollinger_upper_break_triggers_sell():
    closes = [100.0] * 25 + [200.0]
    df = make_df(closes)
    strategy = BollingerBandStrategy(window=20, num_std=2.0)
    sig = strategy.evaluate("TEST", "테스트", "KR", df)
    assert sig is not None
    assert sig.signal_type == SignalType.SELL
    assert sig.details["pattern"] == "upper_band_break"
    assert sig.price == 200.0


def test_bollinger_lower_break_triggers_buy():
    closes = [100.0] * 25 + [20.0]
    df = make_df(closes)
    strategy = BollingerBandStrategy(window=20, num_std=2.0)
    sig = strategy.evaluate("TEST", "테스트", "KR", df)
    assert sig is not None
    assert sig.signal_type == SignalType.BUY
    assert sig.details["pattern"] == "lower_band_break"


def test_bollinger_flat_prices_no_signal():
    closes = [100.0] * 30
    df = make_df(closes)
    strategy = BollingerBandStrategy()
    assert strategy.evaluate("TEST", "테스트", "KR", df) is None


# ---------------------------------------------------------------------------
# 거래량 급증 + 가격 돌파
# ---------------------------------------------------------------------------

def test_volume_breakout_up_triggers_buy():
    closes = [100.0] * 25 + [150.0]
    volumes = [1_000_000.0] * 25 + [5_000_000.0]
    df = make_df(closes, volumes=volumes)
    strategy = VolumeBreakoutStrategy(volume_window=20, volume_multiplier=2.0, price_lookback=20)
    sig = strategy.evaluate("TEST", "테스트", "KR", df)
    assert sig is not None
    assert sig.signal_type == SignalType.BUY
    assert sig.details["pattern"] == "volume_breakout_up"
    assert sig.details["volume_ratio"] >= 2.0


def test_volume_breakdown_triggers_sell():
    closes = [100.0] * 25 + [50.0]
    volumes = [1_000_000.0] * 25 + [5_000_000.0]
    df = make_df(closes, volumes=volumes)
    strategy = VolumeBreakoutStrategy(volume_window=20, volume_multiplier=2.0, price_lookback=20)
    sig = strategy.evaluate("TEST", "테스트", "KR", df)
    assert sig is not None
    assert sig.signal_type == SignalType.SELL
    assert sig.details["pattern"] == "volume_breakdown"


def test_volume_breakout_without_volume_surge_no_signal():
    closes = [100.0] * 25 + [150.0]
    df = make_df(closes)  # 거래량 일정 (급증 없음)
    strategy = VolumeBreakoutStrategy()
    assert strategy.evaluate("TEST", "테스트", "KR", df) is None


# ---------------------------------------------------------------------------
# RSI 과매수/과매도 (오실레이션 후 추세전환 시나리오로 스캔)
# ---------------------------------------------------------------------------

def test_rsi_downtrend_eventually_triggers_oversold_buy():
    osc = [100.0 + 3 * math.sin(i) for i in range(20)]
    decline = [osc[-1] - j * 2 for j in range(1, 41)]
    closes = osc + decline
    df = make_df(closes)
    strategy = RSIStrategy(period=14, oversold=30, overbought=70)
    signals = scan_signals(strategy, df)
    buy_signals = [s for s in signals if s.signal_type == SignalType.BUY]
    assert buy_signals, "하락 추세에서 과매도 BUY 시그널이 한 번은 발생해야 한다"
    assert all(s.details["pattern"] == "oversold" for s in buy_signals)
    assert all(s.details["rsi"] <= 30 for s in buy_signals)
    assert not any(s.signal_type == SignalType.SELL for s in signals)


def test_rsi_uptrend_eventually_triggers_overbought_sell():
    osc = [100.0 + 3 * math.sin(i) for i in range(20)]
    rally = [osc[-1] + j * 2 for j in range(1, 41)]
    closes = osc + rally
    df = make_df(closes)
    strategy = RSIStrategy(period=14, oversold=30, overbought=70)
    signals = scan_signals(strategy, df)
    sell_signals = [s for s in signals if s.signal_type == SignalType.SELL]
    assert sell_signals, "상승 추세에서 과매수 SELL 시그널이 한 번은 발생해야 한다"
    assert all(s.details["pattern"] == "overbought" for s in sell_signals)
    assert all(s.details["rsi"] >= 70 for s in sell_signals)
    assert not any(s.signal_type == SignalType.BUY for s in signals)


def test_rsi_insufficient_bars_returns_none():
    closes = [100.0] * 10
    df = make_df(closes)
    strategy = RSIStrategy()
    assert strategy.evaluate("TEST", "테스트", "KR", df) is None


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def test_macd_reversal_up_eventually_triggers_golden_cross_buy():
    decline = [200.0 - i for i in range(45)]
    rally = [decline[-1] + j * 4 for j in range(1, 46)]
    closes = decline + rally
    df = make_df(closes)
    strategy = MACDStrategy()
    signals = scan_signals(strategy, df)
    buy_signals = [s for s in signals if s.signal_type == SignalType.BUY]
    assert buy_signals, "하락 후 강한 반등에서 MACD 골든크로스 BUY가 한 번은 발생해야 한다"
    assert all(s.details["pattern"] == "macd_golden_cross" for s in buy_signals)
    assert all(s.details["histogram"] > 0 for s in buy_signals)


def test_macd_reversal_down_eventually_triggers_dead_cross_sell():
    rally = [100.0 + i for i in range(45)]
    decline = [rally[-1] - j * 4 for j in range(1, 46)]
    closes = rally + decline
    df = make_df(closes)
    strategy = MACDStrategy()
    signals = scan_signals(strategy, df)
    sell_signals = [s for s in signals if s.signal_type == SignalType.SELL]
    assert sell_signals, "상승 후 강한 하락에서 MACD 데드크로스 SELL이 한 번은 발생해야 한다"
    assert all(s.details["pattern"] == "macd_dead_cross" for s in sell_signals)
    assert all(s.details["histogram"] < 0 for s in sell_signals)


def test_macd_insufficient_bars_returns_none():
    closes = [100.0] * 10
    df = make_df(closes)
    strategy = MACDStrategy()
    assert strategy.evaluate("TEST", "테스트", "KR", df) is None
