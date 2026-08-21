"""stats_builder.build_all() 파이프라인 검증 (fetch_extended_history를
합성 데이터로 monkeypatch - 실제 바이낸스 호출 없음)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

import app.stats_builder as stats_builder_module
from app.config import settings
from app.stats_builder import STATS_FILE, build_all

WARMUP_BARS = 205


def synthetic_uptrend_with_periodic_signals(n_cycles: int, cycle_len: int = 60) -> pd.DataFrame:
    """워밍업 이후 "완만한 상승 -> 눌림목 -> 복귀"를 여러 번 반복해서
    학습/검증 구간에 걸쳐 트레이드가 여러 건 나오게 만든다."""
    closes = [100.0 + i * 0.1 for i in range(WARMUP_BARS)]
    for _ in range(n_cycles):
        base = closes[-1]
        # 완만한 상승
        closes += [base + i for i in range(1, cycle_len)]
        # 눌림목 + 복귀
        top = closes[-1]
        closes += [top - 20.0, top - 17.0]
        # 익절/시간손절 여유를 위한 후속 흐름
        closes += [closes[-1] + 0.5 * i for i in range(1, 20)]

    idx = pd.date_range("2022-01-01", periods=len(closes), freq="h")
    close = pd.Series(closes, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


@pytest.fixture
def patched_history(monkeypatch, tmp_path):
    df = synthetic_uptrend_with_periodic_signals(n_cycles=8)
    monkeypatch.setattr(stats_builder_module, "fetch_extended_history", lambda symbol, tf, bars: df)
    # 합성 데이터의 날짜 범위(2022~) 중간 어디쯤을 학습/검증 경계로 잡는다.
    mid_point = df.index[len(df) // 2]
    monkeypatch.setattr(settings, "backtest_train_start", str(df.index[0].date()))
    monkeypatch.setattr(settings, "backtest_train_end", str(mid_point.date()))
    monkeypatch.setattr(settings, "backtest_validation_end", str(df.index[-1].date()))
    stats_file = tmp_path / "strategy_stats.json"
    monkeypatch.setattr(stats_builder_module, "STATS_FILE", stats_file)
    return stats_file


def test_build_all_produces_expected_structure(patched_history):
    stats = build_all(symbols=["TESTUSDT"], timeframes=["1h"])

    assert "TESTUSDT" in stats and "1h" in stats["TESTUSDT"]
    result = stats["TESTUSDT"]["1h"]
    assert set(result.keys()) >= {"overall", "train", "validation", "yearly", "recent_trades", "bars", "range"}
    assert result["overall"]["trades"] > 0
    assert "_meta" in stats and stats["_meta"]["strategy"] == "keltner_reclaim_200ema"


def test_build_all_train_validation_split_is_exhaustive(patched_history):
    stats = build_all(symbols=["TESTUSDT"], timeframes=["1h"])
    result = stats["TESTUSDT"]["1h"]
    assert result["train"]["trades"] + result["validation"]["trades"] == result["overall"]["trades"]


def test_build_all_yearly_counts_sum_to_overall(patched_history):
    stats = build_all(symbols=["TESTUSDT"], timeframes=["1h"])
    result = stats["TESTUSDT"]["1h"]
    yearly_sum = sum(y["trades"] for y in result["yearly"].values())
    assert yearly_sum == result["overall"]["trades"]


def test_build_all_writes_valid_json_file(patched_history):
    build_all(symbols=["TESTUSDT"], timeframes=["1h"])
    assert patched_history.exists()
    on_disk = json.loads(patched_history.read_text(encoding="utf-8"))
    assert "TESTUSDT" in on_disk


def test_build_all_handles_missing_data_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(stats_builder_module, "fetch_extended_history", lambda symbol, tf, bars: None)
    monkeypatch.setattr(stats_builder_module, "STATS_FILE", tmp_path / "strategy_stats.json")

    stats = build_all(symbols=["NODATA"], timeframes=["1h"])

    assert "error" in stats["NODATA"]["1h"]
