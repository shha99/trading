"""EMA/ATR/켈트너 하단 계산 검증 (합성 데이터)."""
from __future__ import annotations

import pandas as pd

from app.indicators import atr, ema, keltner_lower, rsi


def make_df(closes, highs=None, lows=None):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    close = pd.Series([float(c) for c in closes], index=idx)
    high = pd.Series([float(h) for h in highs], index=idx) if highs else close + 1.0
    low = pd.Series([float(l) for l in lows], index=idx) if lows else close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1_000.0] * len(closes), index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_ema_converges_to_constant_price():
    df = make_df([100.0] * 30)
    result = ema(df["Close"], 10)
    assert abs(result.iloc[-1] - 100.0) < 1e-6


def test_atr_is_nonnegative_and_reflects_range():
    df = make_df([100.0 + i for i in range(30)], highs=[105.0 + i for i in range(30)], lows=[95.0 + i for i in range(30)])
    result = atr(df, 10)
    assert (result.dropna() >= 0).all()
    assert result.iloc[-1] > 0


def test_keltner_lower_is_below_ema_midline():
    df = make_df([100.0] * 40)
    mid = ema(df["Close"], 20)
    lower = keltner_lower(df, ema_period=20, atr_period=10, mult=2.0)
    valid = mid.notna() & lower.notna()
    assert (lower[valid] <= mid[valid]).all()


def test_rsi_is_bounded_between_0_and_100():
    df = make_df([100.0 + i * 0.7 + (2 if i % 3 == 0 else -1) for i in range(60)])
    result = rsi(df["Close"], 14).dropna()
    assert (result >= 0).all() and (result <= 100).all()


def test_rsi_near_100_on_uninterrupted_uptrend():
    df = make_df([100.0 + i for i in range(40)])  # 매 봉 상승만 - 손실이 전혀 없음
    result = rsi(df["Close"], 14).dropna()
    assert (result > 95).all()


def test_rsi_near_0_on_uninterrupted_downtrend():
    df = make_df([100.0 - i for i in range(40)])  # 매 봉 하락만 - 이익이 전혀 없음
    result = rsi(df["Close"], 14).dropna()
    assert (result < 5).all()
