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


# ---------------------------------------------------------------------------
# 본전 이동 트레일링(breakeven_trail) 스트레스 테스트 - 가상의 극단적 시나리오로
# 손절/트레일링이 항상 안정적으로(예외 없이, 스탑이 불리한 방향으로 되돌아가지
# 않게) 동작하는지 검증한다. 가상 계좌 실전 테스트 전에 이 청산 로직 자체의
# 신뢰성을 확인해달라는 요청에 따라 추가함.
# ---------------------------------------------------------------------------

def _breakeven_entry(direction="LONG", entry_price=100.0, stop_mult=2.0, breakeven_at_mult=0.5, trail_mult=0.5, atr=1.0):
    if direction == "LONG":
        stop = entry_price - stop_mult * atr
        trigger = entry_price + breakeven_at_mult * atr
    else:
        stop = entry_price + stop_mult * atr
        trigger = entry_price - breakeven_at_mult * atr
    return {
        "direction": direction, "entry_price": entry_price, "stop_price": stop,
        "breakeven_trigger_price": trigger, "trail_mult": trail_mult, "breakeven_trail": True,
    }


def test_breakeven_trail_long_gap_crash_through_stop_before_breakeven():
    """본전 이동 전에 갑자기 스탑 아래로 갭이 뚫려도(플래시크래시), 손절가(-2xATR)
    선에서 정확히 청산 처리된다 - 단, 이 백테스트 모델은 슬리피지를 반영하지
    않으므로(스탑가 자체에 체결됐다고 가정) 실거래에서는 갭이 클수록 실제
    체결가가 이보다 더 나쁠 수 있다는 걸 알고 있어야 한다."""
    df = make_df(20)
    df.loc[df.index[6], "Low"] = 1.0  # 순간적으로 스탑(98) 훨씬 아래까지 급락
    strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG"))
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["exit_price"] == pytest.approx(98.0)  # entry 100 - 2*ATR(1.0)
    assert trades[0]["pct_return"] == pytest.approx(-2.0, abs=0.01)  # 실제 체결가는 이보다 나쁠 수 있음


def test_breakeven_trail_short_gap_spike_through_stop_before_breakeven():
    df = make_df(20)
    df.loc[df.index[6], "High"] = 500.0  # 숏 포지션인데 급등
    strategy = _FixedEntryStrategy(5, _breakeven_entry("SHORT"))
    trades = simulate_lab(df, strategy)
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["exit_price"] == pytest.approx(102.0)  # entry 100 + 2*ATR(1.0)
    assert trades[0]["pct_return"] == pytest.approx(-2.0, abs=0.01)


def test_breakeven_trail_locks_in_gain_then_never_regresses_below_it():
    """본전 이동 후 스탑은 어떤 경우에도 '뒤로(불리하게)' 물러나지 않아야 한다 -
    오르내림이 반복돼도 트레일링 스탑은 단조증가(LONG)해야 함."""
    df = make_df(30)
    # 5번 진입 이후: 크게 올랐다가(본전 이동 트리거), 다시 살짝 밀렸다가, 또 신고가
    for i, high in zip(range(6, 14), [101, 103, 105, 102.5, 104, 108, 106, 106]):
        df.loc[df.index[i], "High"] = high
    strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG", breakeven_at_mult=0.5, trail_mult=0.5))
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    # 최종 트레이드가 손실이 아니라(본전 이동이 실제로 걸렸다면) 이익 근처여야 함
    assert trades[0]["pct_return"] >= -0.01


def test_breakeven_trail_survives_nan_atr_mid_trade_without_crashing():
    """포지션이 열린 중간에 ATR이 NaN이 되는 봉이 끼어도(데이터 결측 등) 예외
    없이 넘어가야 하고, 트레일링 스탑이 NaN으로 오염되거나 불리하게 풀리면
    안 된다 - NaN인 봉에서는 스탑을 '그대로 유지'하는 게 안전한 동작이다."""
    import math
    df = make_df(20)
    df.loc[df.index[6], "High"] = 103.0  # 본전 이동 트리거(100+0.5) 통과, 트레일 스탑 상향
    strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG", breakeven_at_mult=0.5, trail_mult=0.5))
    atr_values = [1.0] * len(df)
    atr_values[7] = math.nan  # 7번 봉만 ATR 결측
    strategy.precompute = lambda d, _atr=atr_values: {"atr": _atr}
    df.loc[df.index[8], "Low"] = 99.0  # NaN 봉 다음, 원래 스탑(103-0.5=102.5)보다 낮지만 매우 낮진 않음
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    # 예외 없이 끝까지 처리됐고, 최종 손익이 NaN이 아니어야 함
    assert not math.isnan(trades[0]["pct_return"])


def test_breakeven_trail_handles_extreme_single_bar_volatility_spike():
    """평소 대비 수십~수백 배 큰 단일 봉 변동(예: 거래소 오류/이상 데이터)이
    끼어도 예외 없이 처리되고, 청산가/손익이 유한한 값으로 나와야 한다."""
    import math
    df = make_df(20)
    df.loc[df.index[6], "High"] = 999999.0  # 극단적 스파이크
    df.loc[df.index[6], "Low"] = 0.5
    strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG"))
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert math.isfinite(trades[0]["pct_return"])
    assert math.isfinite(trades[0]["exit_price"])


def test_breakeven_trail_same_bar_spike_then_crash_exits_at_trailed_level():
    """같은 봉 안에서 고가가 트리거를 넘고 저가도 새 트레일 스탑 아래로 찍히는
    경우(초 단위 변동은 봉 하나로는 알 수 없음) - 이 백테스트 모델은 '고가가
    저가보다 먼저 발생했다'고 가정해 본전 이동 이후의 트레일 스탑가로
    청산 처리한다(SL이 아니라 TRAIL, 그리고 손실이 아니라 최소 본전 근처)."""
    df = make_df(20)
    df.loc[df.index[6], "High"] = 110.0  # 트리거(100.5) 훨씬 위로 - 트레일 스탑 = 110-0.5=109.5
    df.loc[df.index[6], "Low"] = 50.0     # 같은 봉에 급락도 같이 찍힘
    strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG", breakeven_at_mult=0.5, trail_mult=0.5))
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "TRAIL"
    assert trades[0]["exit_price"] == pytest.approx(109.5)
    assert trades[0]["pct_return"] > 0  # 손실이 아니라 이익으로 마감(본전 이동이 실제 반영됨)


def test_breakeven_trail_never_triggered_falls_back_to_time_exit_at_data_end():
    """손절에도, 본전 트리거에도 안 닿고 데이터가 끝나면 마지막 종가로
    안전하게 청산돼야 한다(무한 보유/예외 없이)."""
    df = make_df(20)  # 가격이 계속 flat이라 손절도 트리거도 안 닿음
    strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG"))
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "TIME"


def test_breakeven_trail_stable_across_extreme_price_magnitudes():
    """가격 자릿수가 극단적으로 크거나(대형 코인 아님/스테이블 오류) 작아도
    (저가 알트코인처럼 0.0001 단위) 손절/트레일링 계산이 동일하게 안정적으로
    동작해야 한다(퍼센트 기반이라 절대 가격 크기에 영향받으면 안 됨)."""
    for scale in [1e-4, 1.0, 1e6]:
        df = make_df(20, start_price=100.0 * scale)
        # make_df 기본 꼬리폭(±0.1)은 scale과 무관한 고정값이라, scale이 작을 땐
        # 그 자체가 본전 트리거를 훨씬 웃돌아버린다 - 꼬리도 scale에 맞게 좁혀야 함.
        df["High"] = df["Close"] + 0.01 * scale
        df["Low"] = df["Close"] - 0.01 * scale
        df.loc[df.index[6], "Low"] = (100.0 * scale) - (5.0 * scale)  # 스탑(2xATR=2*scale) 아래로 손절
        strategy = _FixedEntryStrategy(5, _breakeven_entry("LONG", entry_price=100.0 * scale, atr=1.0 * scale))
        trades = simulate_lab(df, strategy)
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "SL"
        assert trades[0]["pct_return"] == pytest.approx(-2.0, abs=0.05)  # scale과 무관하게 항상 -2%대


def test_summarize_lab_empty():
    assert summarize_lab([]) == {"trades": 0}


def test_summarize_lab_computes_avg_pct():
    trades = [{"pct_return": 2.0}, {"pct_return": -1.0}, {"pct_return": 1.0}]
    summary = summarize_lab(trades)
    assert summary["trades"] == 3
    assert summary["win_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert summary["avg_pct_per_trade"] == pytest.approx(2 / 3, abs=0.001)
    assert summary["total_pct"] == pytest.approx(2.0)
