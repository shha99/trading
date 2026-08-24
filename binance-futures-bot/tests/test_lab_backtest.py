"""lab_backtest.py의 공용 청산 로직(고정 손절/익절, 동적 밴드 익절,
트레일링 스탑, 시간손절) 자체를 - 실제 전략과 무관하게 - 가짜 전략으로
직접 검증한다."""
from __future__ import annotations

import pandas as pd
import pytest

from app.lab_backtest import simulate_lab, summarize_lab
from app.lab_strategies import LabStrategy


def make_df(n=30, start_price=100.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    close = pd.Series([start_price] * n, index=idx)
    return pd.DataFrame({
        "Open": close, "High": close + 0.1, "Low": close - 0.1, "Close": close,
        "Volume": pd.Series(1_000.0, index=idx),
    })


class _FixedEntryStrategy(LabStrategy):
    """지정한 봉 인덱스 하나에서만 딱 한 번 진입 신호를 낸다 (테스트 전용)."""

    min_bars = 1

    def __init__(self, entry_idx, entry):
        self.entry_idx = entry_idx
        self.entry = entry

    def precompute(self, df):
        return {"atr": [1.0] * len(df)}  # 트레일링 테스트용 고정 ATR

    def check_entry(self, k, ctx):
        return self.entry if k == self.entry_idx else None


def test_long_hits_stop_loss():
    df = make_df(20)
    df.loc[df.index[10], "Low"] = 90.0  # 손절가 아래로 찍힘
    strategy = _FixedEntryStrategy(5, {"direction": "LONG", "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0})
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["pct_return"] < 0


def test_long_hits_take_profit():
    df = make_df(20)
    df.loc[df.index[8], "High"] = 115.0
    strategy = _FixedEntryStrategy(5, {"direction": "LONG", "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0})
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "TP"
    assert trades[0]["pct_return"] > 0


def test_short_hits_stop_loss_on_price_rising():
    df = make_df(20)
    df.loc[df.index[8], "High"] = 110.0  # 숏인데 가격이 올라가면 손절
    strategy = _FixedEntryStrategy(5, {"direction": "SHORT", "entry_price": 100.0, "stop_price": 105.0, "target_price": 90.0})
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["pct_return"] < 0


def test_short_hits_take_profit_on_price_falling():
    df = make_df(20)
    df.loc[df.index[8], "Low"] = 85.0
    strategy = _FixedEntryStrategy(5, {"direction": "SHORT", "entry_price": 100.0, "stop_price": 105.0, "target_price": 90.0})
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "TP"
    assert trades[0]["pct_return"] > 0


def test_time_stop_triggers_after_configured_days():
    df = make_df(96)  # 4일치 1시간봉 - 손절/익절 안 닿게 촘촘히 flat
    strategy = _FixedEntryStrategy(
        5, {"direction": "LONG", "entry_price": 100.0, "stop_price": 50.0, "target_price": 200.0, "time_stop_days": 2}
    )
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "TIME"
    entry_time = pd.Timestamp(trades[0]["entry_time"])
    exit_time = pd.Timestamp(trades[0]["exit_time"])
    assert exit_time - entry_time >= pd.Timedelta(days=2)


def test_dynamic_target_uses_moving_series_not_fixed_price():
    df = make_df(20)
    strategy = _FixedEntryStrategy(5, {"direction": "LONG", "entry_price": 100.0, "stop_price": 90.0, "dynamic_target_key": "moving_target"})
    strategy.precompute = lambda d: {"atr": [1.0] * len(d), "moving_target": [103.0] * len(d)}
    df.loc[df.index[8], "High"] = 103.5  # 고정값이 아니라 moving_target(103) 위를 찍어야 익절
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "TP"
    assert trades[0]["exit_price"] == pytest.approx(103.0)


def test_trailing_stop_exits_on_pullback_from_peak():
    df = make_df(20)
    # 5봉부터 계속 신고가를 찍다가 그 다음 훅 빠짐 -> 트레일링(최고가-3*ATR) 이탈
    for i, price in zip(range(6, 12), [102, 104, 106, 108, 110, 112]):
        df.loc[df.index[i], "High"] = price
        df.loc[df.index[i], "Close"] = price
    df.loc[df.index[12], "Low"] = 105.0  # 최고 112 - 3*ATR(1.0) = 109 아래로 이탈
    strategy = _FixedEntryStrategy(5, {"direction": "LONG", "entry_price": 100.0, "trailing": True, "trail_mult": 3.0})
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "TRAIL"


def test_no_overlapping_positions():
    """진입 신호가 나도 이미 포지션이 열려있으면(청산 전) 새로 진입하지 않는다."""
    df = make_df(30)
    df.loc[df.index[15], "Low"] = 90.0  # 5번 봉 진입의 손절

    class TwoEntriesStrategy(LabStrategy):
        min_bars = 1

        def precompute(self, d):
            return {"atr": [1.0] * len(d)}

        def check_entry(self, k, ctx):
            if k in (5, 8):  # 8번 봉은 5번 트레이드가 아직 열려있는 중
                return {"direction": "LONG", "entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0}
            return None

    trades = simulate_lab(df, TwoEntriesStrategy())
    entry_indices = [pd.Timestamp(t["entry_time"]) for t in trades]
    assert len(trades) == 1  # 8번 봉의 진입은 무시됨(5번 트레이드가 15번에 청산되므로 그 이전)


def test_summarize_lab_empty():
    assert summarize_lab([]) == {"trades": 0}


def test_summarize_lab_computes_avg_pct():
    trades = [{"pct_return": 2.0}, {"pct_return": -1.0}, {"pct_return": 1.0}]
    summary = summarize_lab(trades)
    assert summary["trades"] == 3
    assert summary["win_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert summary["avg_pct_per_trade"] == pytest.approx(2 / 3, abs=0.001)
    assert summary["total_pct"] == pytest.approx(2.0)
