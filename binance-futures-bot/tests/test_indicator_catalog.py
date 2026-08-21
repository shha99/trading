"""지표 카탈로그(TA-Lib 160 + 커스텀 5 = 165종, 그 중 캔들패턴 61종) 검증."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicator_catalog import build_catalog, compute_indicator, get_indicator_meta


def synthetic_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(1, 0.5, n))
    low = close - np.abs(rng.normal(1, 0.5, n))
    open_ = close + rng.normal(0, 0.3, n)
    volume = np.abs(rng.normal(1000, 200, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx
    )


def test_catalog_total_is_165_with_61_patterns():
    catalog = build_catalog()
    assert len(catalog) == 165
    assert sum(1 for e in catalog if e["is_pattern"]) == 61
    assert sum(1 for e in catalog if e["source"] == "custom") == 5


def test_mavp_excluded():
    assert not any(e["id"] == "MAVP" for e in build_catalog())


def test_every_indicator_computes_without_error():
    """165종 전부를 합성 데이터로 계산해 예외 없이 df 길이와 정렬되는지 확인."""
    df = synthetic_df()
    for entry in build_catalog():
        out = compute_indicator(entry["id"], df, {})
        assert set(out.keys()) == set(entry["output_names"])
        for series in out.values():
            assert len(series) == len(df)
            assert series.index.equals(df.index)


def test_rsi_single_output_range():
    df = synthetic_df()
    out = compute_indicator("RSI", df, {"timeperiod": 14})
    valid = out["real"].dropna()
    assert ((valid >= 0) & (valid <= 100)).all()


def test_bbands_multi_output_ordering():
    df = synthetic_df()
    out = compute_indicator("BBANDS", df, {})
    valid_idx = out["upperband"].notna() & out["lowerband"].notna()
    assert (out["upperband"][valid_idx] >= out["lowerband"][valid_idx]).all()


def test_candle_pattern_is_marked_and_bounded():
    meta = get_indicator_meta("CDLHAMMER")
    assert meta["is_pattern"] is True
    assert meta["pane"] == "subpane"
    df = synthetic_df()
    out = compute_indicator("CDLHAMMER", df, {})
    values = out["integer"].dropna().unique()
    assert set(values).issubset({-200, -100, 0, 100, 200})


def test_overlay_indicators_share_price_scale_shape():
    meta = get_indicator_meta("EMA")
    assert meta["pane"] == "overlay"


def test_unknown_indicator_raises():
    with pytest.raises(KeyError):
        get_indicator_meta("NOT_A_REAL_INDICATOR")
