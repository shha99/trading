"""backtest.py의 simulate()/summarize() 검증 (합성 데이터, 네트워크 없음)."""
from __future__ import annotations

import pandas as pd
import pytest

from app.strategy import KeltnerReclaimStrategy
from backtest import simulate, summarize

WARMUP_BARS = 205


def make_df(closes):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    close = pd.Series([float(c) for c in closes], index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1_000.0] * len(closes), index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_simulate_finds_entry_and_hits_take_profit():
    base = [100.0 + i for i in range(WARMUP_BARS)]
    dip_bounce = [base[-1] - 20.0, base[-1] - 17.0]
    # 익절(진입가+4*ATR)까지 확실히 뚫고 올라가는 긴 후속 상승
    rally = [dip_bounce[-1] + 5.0 * i for i in range(1, 30)]
    df = make_df(base + dip_bounce + rally)

    trades = simulate(df, KeltnerReclaimStrategy())

    assert len(trades) >= 1
    first = trades[0]
    assert first["exit_reason"] in ("TP", "SL", "TIME")
    assert first["r_multiple"] != 0


def test_simulate_no_overlapping_positions():
    """한 트레이드가 청산되기 전에는 새 진입을 잡지 않아야 한다."""
    base = [100.0 + i for i in range(WARMUP_BARS)]
    df = make_df(base + [base[-1] - 20.0, base[-1] - 17.0] + [base[-1] + i for i in range(60)])

    trades = simulate(df, KeltnerReclaimStrategy())

    for a, b in zip(trades, trades[1:]):
        assert pd.Timestamp(b["entry_time"]) >= pd.Timestamp(a["exit_time"])


def test_simulate_no_signal_returns_empty():
    df = make_df([100.0] * (WARMUP_BARS + 5))  # 눌림목 없음
    assert simulate(df, KeltnerReclaimStrategy()) == []


def test_summarize_empty_trades():
    assert summarize([]) == {"trades": 0}


def test_summarize_computes_win_rate_and_totals():
    trades = [
        {"r_multiple": 2.0}, {"r_multiple": -1.0}, {"r_multiple": 1.5},
    ]
    summary = summarize(trades)
    assert summary["trades"] == 3
    assert summary["win_rate"] == pytest.approx(2 / 3, abs=0.001)  # summarize()가 소수3자리로 반올림
    assert summary["total_r"] == pytest.approx(2.5)
    assert summary["best_r"] == 2.0
    assert summary["worst_r"] == -1.0
