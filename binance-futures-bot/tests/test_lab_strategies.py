"""전략 실험실 후보 7종의 진입 조건 검증 (합성 데이터, 네트워크 없음)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.lab_backtest import simulate_lab
from app.lab_strategies import (
    BigCandleBreakoutStrategy,
    BollingerBreakoutStrategy,
    BollingerReversionStrategy,
    BollingerWickTouchStrategy,
    ResistanceBreakFailStrategy,
    SharpDropBounceStrategy,
    SupportHoldBreakStrategy,
)


def make_df(closes, highs=None, lows=None, opens=None, start="2023-01-01", freq="h"):
    idx = pd.date_range(start, periods=len(closes), freq=freq)
    close = pd.Series([float(c) for c in closes], index=idx)
    high = pd.Series([float(h) for h in highs], index=idx) if highs is not None else close + 0.5
    low = pd.Series([float(l) for l in lows], index=idx) if lows is not None else close - 0.5
    open_ = pd.Series([float(o) for o in opens], index=idx) if opens is not None else close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000.0, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def random_walk_df(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(1, 0.5, n))
    low = close - np.abs(rng.normal(1, 0.5, n))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]  # 실제 시장처럼 이번 봉 시가 = 직전 봉 종가
    idx = pd.date_range("2023-01-01", periods=n, freq="h")
    volume = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


# ---------------------------------------------------------------------------
# 큰 양봉 돌파
# ---------------------------------------------------------------------------

def test_big_candle_breakout_triggers_on_oversized_bullish_candle():
    strategy = BigCandleBreakoutStrategy()
    base = [100.0 + i * 0.05 for i in range(80)]  # 완만한 상승(50EMA 위 유지) + 몸통 작음
    closes = base + [base[-1] + 10.0]  # 평소 몸통(~0.05)의 2배는커녕 훨씬 큰 양봉
    opens = [c - 0.05 for c in base] + [base[-1]]
    df = make_df(closes, opens=opens)
    trades = simulate_lab(df, strategy)
    assert any(t["direction"] == "LONG" and t["exit_reason"] == "TRAIL" for t in trades)


def test_big_candle_breakout_no_signal_on_normal_candles():
    strategy = BigCandleBreakoutStrategy()
    df = make_df([100.0 + i * 0.05 for i in range(100)])
    assert simulate_lab(df, strategy) == []


# ---------------------------------------------------------------------------
# 급락 후 첫 반등
# ---------------------------------------------------------------------------

def test_sharp_drop_bounce_triggers_after_big_red_candle():
    strategy = SharpDropBounceStrategy()
    base = [100.0 + i * 0.2 for i in range(205)]  # 200EMA 위 상승 추세
    drop_open, drop_close = base[-1] + 0.2, base[-1] - 20.0  # 큰 음봉(급락)
    bounce_open, bounce_close = drop_close, drop_close + 5.0  # 첫 양봉
    closes = base + [drop_close, bounce_close]
    opens = [c - 0.2 for c in base] + [drop_open, bounce_open]
    df = make_df(closes, opens=opens)
    trades = simulate_lab(df, strategy)
    assert len(trades) >= 1
    assert trades[0]["direction"] == "LONG"


def test_sharp_drop_bounce_no_signal_without_drop():
    strategy = SharpDropBounceStrategy()
    df = make_df([100.0 + i * 0.2 for i in range(220)])
    assert simulate_lab(df, strategy) == []


# ---------------------------------------------------------------------------
# 볼린저 하단 매수 -> 상단 매도 (역추세)
# ---------------------------------------------------------------------------

def test_bollinger_reversion_triggers_on_pullback_reclaim():
    strategy = BollingerReversionStrategy()
    df = random_walk_df(n=2000, seed=2)
    trades = simulate_lab(df, strategy)
    assert len(trades) > 0
    assert all(t["direction"] == "LONG" for t in trades)
    assert all(t["exit_reason"] in ("TP", "SL", "TIME") for t in trades)


# ---------------------------------------------------------------------------
# 볼린저 돌파 롱/숏
# ---------------------------------------------------------------------------

def test_bollinger_breakout_produces_both_directions_over_long_run():
    strategy = BollingerBreakoutStrategy()
    df = random_walk_df(n=3000, seed=3)
    trades = simulate_lab(df, strategy)
    directions = {t["direction"] for t in trades}
    assert len(trades) > 0
    assert directions <= {"LONG", "SHORT"}
    # 이 전략엔 시간손절이 없다 - "TIME"이 나온다면 그건 실제 시간손절이 아니라
    # 데이터 끝까지 SL/TP를 못 만난 마지막 트레이드의 강제 마감뿐이어야 한다.
    non_terminal = trades[:-1] if trades and trades[-1]["exit_reason"] == "TIME" else trades
    assert all(t["exit_reason"] in ("TP", "SL") for t in non_terminal)


# ---------------------------------------------------------------------------
# 저항선 돌파/실패 · 지지선 지지/이탈
#
# 20봉 롤링 채널이라 손으로 박스권 데이터를 짜면 "평평한 구간에서는 매
# 봉의 고가가 곧 저항선 자신"이 되는 식의 우연한 경계값 문제가 계속
# 생긴다. 실제 시장처럼 매 봉 값이 계속 바뀌는 랜덤워크에서는 저항/지지
# 돌파·이탈·거부가 자연히 여러 번 나오므로, 방향이 실제로 둘 다
# 나오는지와 청산 사유가 정상 범위인지로 검증한다.
# ---------------------------------------------------------------------------

def test_resistance_break_fail_produces_both_directions_over_long_run():
    strategy = ResistanceBreakFailStrategy()
    df = random_walk_df(n=3000, seed=1)
    trades = simulate_lab(df, strategy)
    directions = {t["direction"] for t in trades}
    assert directions == {"LONG", "SHORT"}
    assert all(t["exit_reason"] in ("TP", "SL", "TIME") for t in trades)


def test_support_hold_break_produces_both_directions_over_long_run():
    strategy = SupportHoldBreakStrategy()
    df = random_walk_df(n=3000, seed=1)
    trades = simulate_lab(df, strategy)
    directions = {t["direction"] for t in trades}
    assert directions == {"LONG", "SHORT"}
    assert all(t["exit_reason"] in ("TP", "SL", "TIME") for t in trades)


# ---------------------------------------------------------------------------
# 볼린저 꼬리 터치 롱/숏
# ---------------------------------------------------------------------------

def test_bollinger_wick_touch_produces_trades_over_long_run():
    strategy = BollingerWickTouchStrategy()
    df = random_walk_df(n=3000, seed=4)
    trades = simulate_lab(df, strategy)
    assert len(trades) > 0
    assert all(t["direction"] in ("LONG", "SHORT") for t in trades)
