"""app/history.py의 캔들 스파이크 보정(sanitize_klines) 테스트.

바이낸스 테스트넷 과거 K라인에서 실제로 관측된 패턴(정상적인 시가/저가/종가 사이에
비정상적으로 큰 고가, 혹은 비정상적으로 작은 저가가 단일 캔들에 섞여 나오는 것)을
재현해 보정 로직을 검증한다.
"""
from __future__ import annotations

import pandas as pd

from app.history import sanitize_klines


def _df(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2021-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx)


def test_sanitize_klines_fixes_spike_high():
    df = _df([
        {"Open": 2537.20, "High": 2600.00, "Low": 2470.01, "Close": 2498.06, "Volume": 10},
        {"Open": 2537.20, "High": 104454.90, "Low": 2470.01, "Close": 2498.06, "Volume": 10},
    ])
    out = sanitize_klines(df)
    assert out.iloc[0]["High"] == 2600.00  # 정상 캔들은 그대로
    assert out.iloc[1]["High"] == 2537.20  # max(Open, Low, Close)로 보정
    assert out.iloc[1]["Low"] == 2470.01
    assert out.iloc[1]["Close"] == 2498.06


def test_sanitize_klines_fixes_spike_low():
    df = _df([
        {"Open": 56811.0, "High": 56900.0, "Low": 56000.0, "Close": 56500.0, "Volume": 10},
        {"Open": 56811.0, "High": 1000000.0, "Low": 150.0, "Close": 47070.0, "Volume": 10},
    ])
    out = sanitize_klines(df)
    assert out.iloc[1]["High"] == 56811.0  # max(Open, Low(원본), Close)
    assert out.iloc[1]["Low"] == 47070.0  # min(Open, High(원본), Close)


def test_sanitize_klines_leaves_normal_volatility_untouched():
    # 하루에 20% 넘게 움직인 정상 캔들 - 시가/종가 대비 고가/저가 차이가 커도
    # _WICK_FACTOR(1.5배)에는 한참 못 미치므로 그대로 유지되어야 한다.
    df = _df([
        {"Open": 1799.35, "High": 2149.99, "Low": 1733.93, "Close": 2143.66, "Volume": 10},
    ])
    out = sanitize_klines(df)
    pd.testing.assert_frame_equal(out, df)


def test_sanitize_klines_empty_df_returns_as_is():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    out = sanitize_klines(df)
    assert out.empty
