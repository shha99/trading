"""커스텀 지표 5종(VWAP/Supertrend/Ichimoku/Donchian/Keltner) 공식 검증."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.custom_indicators import donchian_channel, ichimoku, keltner, supertrend, vwap


def make_df(closes, highs=None, lows=None, volumes=None, freq="h", start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq=freq)
    close = pd.Series([float(c) for c in closes], index=idx)
    high = pd.Series([float(h) for h in highs], index=idx) if highs is not None else close + 1.0
    low = pd.Series([float(l) for l in lows], index=idx) if lows is not None else close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([float(v) for v in volumes], index=idx) if volumes is not None else pd.Series(1_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_vwap_equals_price_when_flat():
    df = make_df([100.0] * 30)
    out = vwap(df)
    assert out["vwap"].dropna().apply(lambda v: v == pytest.approx(100.0)).all()


def test_vwap_resets_each_utc_day():
    # 하루차(24봉, 1시간봉)로 이틀 구성 - 둘째날 VWAP은 둘째날 값만 반영해야 함
    day1 = [100.0] * 24
    day2 = [200.0] * 24
    df = make_df(day1 + day2)
    out = vwap(df)["vwap"]
    assert out.iloc[23] == pytest.approx(100.0)
    assert out.iloc[24] == pytest.approx(200.0)  # 새 날 시작 -> 누적 초기화


def test_supertrend_flips_direction_on_strong_move():
    # 완만한 하락 후 강한 상승 -> 추세가 -1에서 1로 바뀌어야 함
    closes = [200.0 - i for i in range(30)] + [170.0 + i * 5 for i in range(15)]
    df = make_df(closes)
    out = supertrend(df, period=10, multiplier=3.0)
    trend = out["trend"].dropna()
    assert trend.iloc[-1] == 1
    assert (trend == -1).any()  # 초반 하락 구간에서는 -1이었어야 함


def test_supertrend_value_is_below_close_in_uptrend():
    closes = [100.0 + i for i in range(60)]
    df = make_df(closes)
    out = supertrend(df, period=10, multiplier=3.0)
    tail = out["trend"].iloc[-10:]
    assert (tail == 1).all()
    assert (out["supertrend"].iloc[-10:] < df["Close"].iloc[-10:]).all()


def test_donchian_matches_rolling_high_low():
    highs = [100.0, 105.0, 103.0, 110.0, 102.0, 108.0]
    lows = [95.0, 96.0, 97.0, 98.0, 99.0, 100.0]
    closes = [98.0, 100.0, 100.0, 104.0, 100.0, 104.0]
    df = make_df(closes, highs=highs, lows=lows)
    out = donchian_channel(df, period=3)
    # 마지막 3봉: highs[3:6]=[110,102,108], lows[3:6]=[98,99,100]
    assert out["upper"].iloc[-1] == pytest.approx(110.0)
    assert out["lower"].iloc[-1] == pytest.approx(98.0)
    assert out["middle"].iloc[-1] == pytest.approx((110.0 + 98.0) / 2)


def test_keltner_channel_ordering():
    df = make_df([100.0 + np.sin(i / 3) * 5 for i in range(60)])
    out = keltner(df, ema_period=20, atr_period=10, mult=2.0)
    valid = out["upper"].notna() & out["middle"].notna() & out["lower"].notna()
    assert (out["upper"][valid] >= out["middle"][valid]).all()
    assert (out["middle"][valid] >= out["lower"][valid]).all()


def test_ichimoku_flat_price_lines_converge():
    df = make_df([100.0] * 120)
    out = ichimoku(df, tenkan_period=9, kijun_period=26, senkou_b_period=52, displacement=26)
    tail_valid = slice(-10, None)
    for key in ("tenkan", "kijun"):
        assert out[key].iloc[tail_valid].dropna().apply(lambda v: v == pytest.approx(100.0)).all()
    # 선행스팬은 displacement만큼 미래로 밀려있어야 하므로 앞쪽은 NaN
    assert out["senkou_a"].iloc[:26].isna().all()


def test_ichimoku_chikou_is_close_shifted_back():
    closes = list(range(100, 160))
    df = make_df([float(c) for c in closes])
    out = ichimoku(df, displacement=26)
    chikou = out["chikou"]
    # chikou[i] = close[i+26] (미래로 26칸 당겨서 과거에 그림)
    assert chikou.iloc[0] == pytest.approx(df["Close"].iloc[26])
    assert chikou.iloc[-26:].isna().all()
