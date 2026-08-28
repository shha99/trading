"""4종목 동시 스크리닝 백테스트(app/multi_screen_backtest.py) 검증 -
실제 바이낸스 호출 없음(fetch_extended_history는 monkeypatch)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import app.multi_screen_backtest as msb


def _trade(entry, exit_, pct, combo="BTCUSDT 15m"):
    return {"entry_time": entry, "exit_time": exit_, "pct_return": pct, "combo": combo}


def test_take_trades_skips_overlapping_entries_while_position_open():
    trades = [
        _trade("2026-01-01 00:00:00", "2026-01-01 02:00:00", 1.0, "BTCUSDT 15m"),
        _trade("2026-01-01 01:00:00", "2026-01-01 03:00:00", 2.0, "ETHUSDT 5m"),  # 첫 거래 보유 중 - 건너뜀
        _trade("2026-01-01 02:30:00", "2026-01-01 04:00:00", 3.0, "ETHUSDT 15m"),  # 첫 거래 청산 후 - 채택
    ]
    taken = msb._take_trades(trades, start_ts=None)
    assert [t["pct_return"] for t in taken] == [1.0, 3.0]


def test_take_trades_respects_start_filter():
    trades = [
        _trade("2025-01-01 00:00:00", "2025-01-01 01:00:00", 5.0),
        _trade("2026-01-01 00:00:00", "2026-01-01 01:00:00", 1.0),
    ]
    taken = msb._take_trades(trades, start_ts=pd.Timestamp("2026-01-01"))
    assert len(taken) == 1
    assert taken[0]["pct_return"] == 1.0


def test_take_trades_back_to_back_no_gap_is_taken():
    """청산시각과 다음 진입시각이 정확히 같아도(공백 없이 이어짐) 채택돼야 한다."""
    trades = [
        _trade("2026-01-01 00:00:00", "2026-01-01 01:00:00", 1.0),
        _trade("2026-01-01 01:00:00", "2026-01-01 02:00:00", 2.0),
    ]
    taken = msb._take_trades(trades, start_ts=None)
    assert len(taken) == 2


@pytest.fixture
def seeded_trades_file(tmp_path, monkeypatch):
    trades = [
        _trade("2026-01-01 00:00:00", "2026-01-01 01:00:00", 2.0, "BTCUSDT 15m"),
        _trade("2026-01-01 01:00:00", "2026-01-01 02:00:00", -1.0, "ETHUSDT 5m"),
        _trade("2026-01-01 02:00:00", "2026-01-01 03:00:00", 1.5, "BTCUSDT 5m"),
        _trade("2026-01-01 03:00:00", "2026-01-01 04:00:00", -0.5, "ETHUSDT 15m"),
    ]
    path = tmp_path / "multi_screen_trades.json"
    path.write_text(json.dumps({"trades": trades, "_meta": {"worst_case_stress_test_pct": -43.8}}), encoding="utf-8")
    monkeypatch.setattr(msb, "MULTI_SCREEN_TRADES_FILE", path)
    return path


def test_compute_return_not_ready_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(msb, "MULTI_SCREEN_TRADES_FILE", tmp_path / "does_not_exist.json")
    result = msb.compute_return(20.0)
    assert result == {"ready": False}


def test_compute_return_compounds_with_bet_fraction(seeded_trades_file):
    result = msb.compute_return(bet_fraction_pct=100.0, principal=1_000_000.0)
    assert result["ready"] is True
    assert result["trades"] == 4
    expected_balance = 1_000_000.0
    for pct in (2.0, -1.0, 1.5, -0.5):
        expected_balance *= 1 + pct / 100.0
    assert result["final_balance"] == pytest.approx(expected_balance, rel=1e-6)
    assert result["win_rate"] == pytest.approx(50.0)


def test_compute_return_smaller_bet_fraction_shrinks_swings(seeded_trades_file):
    full = msb.compute_return(bet_fraction_pct=100.0)
    half = msb.compute_return(bet_fraction_pct=50.0)
    # 100% 대비 절반만 배팅하면 수익률의 절대값(등락폭)이 작아야 한다
    assert abs(half["return_pct"]) < abs(full["return_pct"])


def test_compute_return_worst_case_account_impact_scales_with_fraction(seeded_trades_file):
    result = msb.compute_return(bet_fraction_pct=20.0)
    assert result["worst_case_stress_test_pct"] == -43.8
    assert result["worst_case_account_impact_pct"] == pytest.approx(0.2 * 43.8, abs=1e-6)


def test_compute_return_no_trades_in_range_returns_zero(seeded_trades_file):
    result = msb.compute_return(bet_fraction_pct=20.0, start="2030-01-01")
    assert result["ready"] is True
    assert result["trades"] == 0
    assert result["return_pct"] == 0.0
    assert result["final_balance"] == msb.DEFAULT_PRINCIPAL


def random_walk_df(n=3000, seed=1, freq="15min"):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(1, 0.5, n))
    low = close - np.abs(rng.normal(1, 0.5, n))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    idx = pd.date_range("2023-01-01", periods=n, freq=freq)
    volume = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def test_build_merged_trades_tags_combo_and_sorts_chronologically(monkeypatch, tmp_path):
    dfs = {
        ("BTCUSDT", "15m"): random_walk_df(seed=1),
        ("BTCUSDT", "5m"): random_walk_df(seed=2),
        ("ETHUSDT", "15m"): random_walk_df(seed=3),
        ("ETHUSDT", "5m"): random_walk_df(seed=4),
    }
    monkeypatch.setattr(msb, "fetch_extended_history", lambda symbol, tf, bars: dfs[(symbol, tf)])
    monkeypatch.setattr(msb, "bars_needed_for_span", lambda tf: 3000)
    trades_file = tmp_path / "multi_screen_trades.json"
    monkeypatch.setattr(msb, "MULTI_SCREEN_TRADES_FILE", trades_file)

    result = msb.build_merged_trades()

    assert trades_file.exists()
    trades = result["trades"]
    assert len(trades) > 0
    assert all(t["combo"] in {"BTCUSDT 15m", "BTCUSDT 5m", "ETHUSDT 15m", "ETHUSDT 5m"} for t in trades)
    entry_times = [pd.Timestamp(t["entry_time"]) for t in trades]
    assert entry_times == sorted(entry_times)
    assert result["_meta"]["strategy"] == "bollinger_wick_breakeven_trail"


def test_build_merged_trades_handles_missing_combo_data(monkeypatch, tmp_path):
    def fake_fetch(symbol, tf, bars):
        if symbol == "BTCUSDT" and tf == "15m":
            return None
        return random_walk_df(seed=hash((symbol, tf)) % 1000)

    monkeypatch.setattr(msb, "fetch_extended_history", fake_fetch)
    monkeypatch.setattr(msb, "bars_needed_for_span", lambda tf: 3000)
    monkeypatch.setattr(msb, "MULTI_SCREEN_TRADES_FILE", tmp_path / "multi_screen_trades.json")

    result = msb.build_merged_trades()
    assert "error" in result["_meta"]["per_combo"]["BTCUSDT 15m"]
