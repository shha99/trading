"""validated_lab_stats_builder.build_all() 파이프라인 검증 (fetch_extended_history를
합성 데이터로 monkeypatch - 실제 바이낸스 호출 없음)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

import app.validated_lab_stats_builder as builder_module
from app.config import settings
from app.lab_strategies import LabStrategy
from app.validated_lab_stats_builder import build_all


class _FixedEntryLabStrategy(LabStrategy):
    """일정 간격마다 항상 진입 신호를 내는 단순 전략(테스트 전용) - 실제
    후보 전략의 세부 규칙과 무관하게 파이프라인(학습/검증/연도별 분리, JSON
    저장)만 검증하면 되므로."""

    key = "fixed_entry_test"
    min_bars = 1

    def __init__(self, every=50):
        self.every = every

    def precompute(self, df):
        return {"atr": [1.0] * len(df)}

    def check_entry(self, k, ctx):
        if k > 0 and k % self.every == 0:
            close = ctx["close"][k]
            return {
                "direction": "LONG", "entry_price": close,
                "stop_price": close - 2.0, "breakeven_trigger_price": close + 0.5,
                "trail_mult": 0.5, "breakeven_trail": True,
            }
        return None


def synthetic_df(n=3000, seed=1) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(1, 0.5, n))
    low = close - np.abs(rng.normal(1, 0.5, n))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    idx = pd.date_range("2022-01-01", periods=n, freq="h")
    volume = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


@pytest.fixture
def patched_history(monkeypatch, tmp_path):
    df = synthetic_df()
    monkeypatch.setattr(builder_module, "fetch_extended_history", lambda symbol, tf, bars: df)
    mid_point = df.index[len(df) // 2]
    monkeypatch.setattr(settings, "backtest_train_start", str(df.index[0].date()))
    monkeypatch.setattr(settings, "backtest_train_end", str(mid_point.date()))
    monkeypatch.setattr(settings, "backtest_validation_end", str(df.index[-1].date()))
    stats_file = tmp_path / "validated_lab_stats.json"
    monkeypatch.setattr(builder_module, "VALIDATED_STATS_FILE", stats_file)
    return stats_file


def _test_specs():
    return [{"strategy": _FixedEntryLabStrategy(), "symbols": ["TESTUSDT"], "timeframes": ["1h"]}]


def test_build_all_produces_expected_structure(patched_history):
    stats = build_all(specs=_test_specs())
    key = _FixedEntryLabStrategy().key
    assert key in stats
    result = stats[key]["TESTUSDT"]["1h"]
    assert set(result.keys()) >= {"overall", "train", "validation", "yearly", "recent_trades", "bars", "range"}
    assert result["overall"]["trades"] > 0
    assert "_meta" in stats


def test_build_all_train_validation_split_is_exhaustive(patched_history):
    stats = build_all(specs=_test_specs())
    key = _FixedEntryLabStrategy().key
    result = stats[key]["TESTUSDT"]["1h"]
    assert result["train"]["trades"] + result["validation"]["trades"] == result["overall"]["trades"]


def test_build_all_yearly_counts_sum_to_overall(patched_history):
    stats = build_all(specs=_test_specs())
    key = _FixedEntryLabStrategy().key
    result = stats[key]["TESTUSDT"]["1h"]
    yearly_sum = sum(y["trades"] for y in result["yearly"].values())
    assert yearly_sum == result["overall"]["trades"]


def test_build_all_writes_valid_json_file(patched_history):
    build_all(specs=_test_specs())
    assert patched_history.exists()
    on_disk = json.loads(patched_history.read_text(encoding="utf-8"))
    key = _FixedEntryLabStrategy().key
    assert key in on_disk


def test_build_all_handles_missing_data_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(builder_module, "fetch_extended_history", lambda symbol, tf, bars: None)
    monkeypatch.setattr(builder_module, "VALIDATED_STATS_FILE", tmp_path / "validated_lab_stats.json")

    stats = build_all(specs=_test_specs())
    key = _FixedEntryLabStrategy().key
    assert "error" in stats[key]["TESTUSDT"]["1h"]


def test_default_specs_cover_both_promoted_strategies():
    from app.validated_lab_stats_builder import _default_specs

    keys = {spec["strategy"].key for spec in _default_specs()}
    assert keys == {"big_candle_bollinger_confluence", "bollinger_wick_breakeven_trail"}
