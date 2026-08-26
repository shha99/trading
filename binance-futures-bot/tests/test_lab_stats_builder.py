"""lab_stats_builder.build_all() 파이프라인 검증 (fetch_extended_history를
합성 데이터로 monkeypatch - 실제 바이낸스 호출 없음)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import app.lab_stats_builder as lab_stats_builder_module
from app.lab_stats_builder import build_all
from app.strategy import KeltnerReclaimStrategy


def synthetic_df(n=3000, seed=1) -> pd.DataFrame:
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
    monkeypatch.setattr(lab_stats_builder_module, "fetch_extended_history", lambda symbol, tf, bars: df)
    stats_file = tmp_path / "lab_stats.json"
    monkeypatch.setattr(lab_stats_builder_module, "LAB_STATS_FILE", stats_file)
    return stats_file


def test_build_all_includes_all_12_strategies(patched_history):
    result = build_all(symbols=["TESTUSDT"], timeframes=["1h"])
    assert len(result["catalog"]) == 12
    assert result["catalog"][0]["key"] == KeltnerReclaimStrategy.key

    by_strategy = result["stats"]["TESTUSDT"]["1h"]
    for entry in result["catalog"]:
        assert entry["key"] in by_strategy
        assert "error" not in by_strategy[entry["key"]]


def test_build_all_reports_pct_based_metrics_for_every_strategy(patched_history):
    result = build_all(symbols=["TESTUSDT"], timeframes=["1h"])
    by_strategy = result["stats"]["TESTUSDT"]["1h"]
    for entry in result["catalog"]:
        summary = by_strategy[entry["key"]]
        if summary.get("trades", 0) > 0:
            assert "avg_pct_per_trade" in summary
            assert "recent_trades" in summary


def test_build_all_writes_valid_json_file(patched_history):
    build_all(symbols=["TESTUSDT"], timeframes=["1h"])
    assert patched_history.exists()
    on_disk = json.loads(patched_history.read_text(encoding="utf-8"))
    assert "TESTUSDT" in on_disk["stats"]


def test_build_all_handles_missing_data_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(lab_stats_builder_module, "fetch_extended_history", lambda symbol, tf, bars: None)
    monkeypatch.setattr(lab_stats_builder_module, "LAB_STATS_FILE", tmp_path / "lab_stats.json")

    result = build_all(symbols=["NODATA"], timeframes=["1h"])

    by_strategy = result["stats"]["NODATA"]["1h"]
    for entry in result["catalog"]:
        assert "error" in by_strategy[entry["key"]]
