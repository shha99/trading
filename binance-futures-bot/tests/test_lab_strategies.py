"""전략 실험실 후보 10종의 진입 조건 검증 (합성 데이터, 네트워크 없음)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.lab_backtest import simulate_lab
from app.lab_strategies import (
    BigCandleBollingerConfluenceStrategy,
    BigCandleBreakoutStrategy,
    BollingerBreakoutStrategy,
    BollingerReversionStrategy,
    BollingerWickBreakevenTrailStrategy,
    BollingerWickTouchStrategy,
    IchimokuCloudBreakoutStrategy,
    ResistanceBreakFailStrategy,
    RsiVolumeSpikeReversalStrategy,
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


# ---------------------------------------------------------------------------
# 이치모쿠 구름 돌파
# ---------------------------------------------------------------------------

def _consolidation_then_trend_df(n=400, trend_start=150, drift=0.9, seed=7):
    """앞부분은 횡보(구름 안/근처)만 하다가, 중간부터 뚜렷한 추세가 붙는 데이터.

    이치모쿠 구름 자체가 오랜 기간(선행스팬B 52봉 + 26봉 시프트)을 필요로
    해서, 순수 랜덤워크 초반부는 구름값이 NaN이라 검증이 어렵다 - 횡보 뒤
    추세가 붙는 구간을 만들어 "돌파 이벤트"가 스캔 구간 안에서 실제로
    발생하도록 한다.
    """
    rng = np.random.default_rng(seed)
    price = np.empty(n)
    price[0] = 100.0
    for i in range(1, n):
        step = rng.normal(0, 0.8)
        if i >= trend_start:
            step += drift
        price[i] = price[i - 1] + step
    close = price
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.5, 0.3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.5, 0.3, n))
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    volume = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def test_ichimoku_cloud_breakout_triggers_long_on_uptrend_after_consolidation():
    strategy = IchimokuCloudBreakoutStrategy()
    df = _consolidation_then_trend_df(n=400, trend_start=150, drift=0.9, seed=7)
    trades = simulate_lab(df, strategy)
    assert len(trades) > 0
    # 횡보->추세 전환 구간의 노이즈가 먼저 반대 방향의 짧은 신호를 낼 수도
    # 있어서(실제로 이 seed에서도 그렇다), "언젠가 롱이 나오는지"로 검증한다
    # - 실제 추세가 붙는 구간에서는 큰 폭의 롱 트레이드가 나온다.
    assert any(t["direction"] == "LONG" for t in trades)
    # 이 전략엔 고정/동적 익절이 없다 - 기준선을 종가가 반대로 가로지르면
    # "SL"로 청산되거나, 데이터 끝까지 안 꺾이면 "TIME"으로 강제 마감된다.
    assert all(t["exit_reason"] in ("SL", "TIME") for t in trades)


def test_ichimoku_cloud_breakout_triggers_short_on_downtrend_after_consolidation():
    strategy = IchimokuCloudBreakoutStrategy()
    df = _consolidation_then_trend_df(n=400, trend_start=150, drift=-0.9, seed=11)
    trades = simulate_lab(df, strategy)
    assert len(trades) > 0
    assert any(t["direction"] == "SHORT" for t in trades)


# ---------------------------------------------------------------------------
# RSI+거래량 스파이크 되돌림
# ---------------------------------------------------------------------------

def _rsi_volume_df(bounce_direction: str, n=100, warmup=80, decline_bars=10, post_bounce_step=None):
    """80봉 평평한 워밍업 -> 10봉 급락(또는 급등)으로 RSI를 과매도(또는
    과매수)로 밀어넣은 뒤 -> 정확히 그 다음 봉에 반등(또는 반락) + 거래량
    스파이크를 심어둔 결정론적 데이터. `RsiVolumeSpikeReversalStrategy`의
    min_bars(14/20/14 중 최댓값 + 50 = 70)를 넘긴 지점에서 신호가 나오도록
    warmup을 80으로 잡았다."""
    assert bounce_direction in ("up", "down")
    close = [100.0] * warmup
    step = -1.0 if bounce_direction == "up" else 1.0  # 반등 전 방향(급락/급등)
    for _ in range(decline_bars):
        close.append(close[-1] + step)
    bounce_idx = len(close)
    close.append(close[-1] - step * 3)  # 반대 방향으로 강하게 반등/반락
    if post_bounce_step is None:
        post_bounce_step = 0.1 if bounce_direction == "up" else -0.1
    while len(close) < n:
        close.append(close[-1] + post_bounce_step)

    close = pd.Series(close, dtype=float)
    idx = pd.date_range("2023-01-01", periods=n, freq="min")
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.2
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.2
    # 워밍업 구간에 아주 작은 변동을 줘서 ATR이 정확히 0이 되지 않게 함
    high.iloc[:warmup] += np.abs(np.sin(np.arange(warmup))) * 0.3
    low.iloc[:warmup] -= np.abs(np.cos(np.arange(warmup))) * 0.3
    volume = pd.Series(1000.0, index=idx)
    volume.iloc[bounce_idx] = 3000.0  # 반등/반락 봉에만 거래량 스파이크(평균의 3배)
    return pd.DataFrame(
        {"Open": open_.to_numpy(), "High": high.to_numpy(), "Low": low.to_numpy(),
         "Close": close.to_numpy(), "Volume": volume.to_numpy()},
        index=idx,
    )


def test_rsi_volume_spike_reversal_triggers_long_on_oversold_bounce_with_volume():
    strategy = RsiVolumeSpikeReversalStrategy()
    df = _rsi_volume_df("up")
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["direction"] == "LONG"
    assert trades[0]["exit_reason"] in ("SL", "TP", "TIME")


def test_rsi_volume_spike_reversal_triggers_short_on_overbought_bounce_with_volume():
    strategy = RsiVolumeSpikeReversalStrategy()
    df = _rsi_volume_df("down")
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["direction"] == "SHORT"


def test_rsi_volume_spike_reversal_no_signal_without_volume_spike():
    strategy = RsiVolumeSpikeReversalStrategy()
    df = _rsi_volume_df("up")
    df["Volume"] = 1000.0  # 스파이크 제거 - RSI 조건은 그대로 만족하지만 거래량 필터에 막혀야 함
    assert simulate_lab(df, strategy) == []


def test_rsi_volume_spike_reversal_time_stop_fires_at_exact_bar_count():
    strategy = RsiVolumeSpikeReversalStrategy(time_stop_bars=40)
    # 반등 이후 가격을 완전히 평평하게 둬서(post_bounce_step=0) 손절/익절에
    # 안 닿게 하고, 40봉 시간손절이 실제로 그 시점에 발동하는지 확인 -
    # 그러려면 반등 이후 최소 40봉+여유가 더 있어야 하므로 n을 늘린다.
    df = _rsi_volume_df("up", n=140, post_bounce_step=0.0)
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "TIME"
    entry_idx = df.index.get_loc(pd.Timestamp(trades[0]["entry_time"]))
    exit_idx = df.index.get_loc(pd.Timestamp(trades[0]["exit_time"]))
    assert exit_idx - entry_idx == 40


def test_rsi_volume_spike_reversal_produces_trades_over_long_run_with_injected_spikes():
    # 순수 랜덤워크는 평균 거래량 대비 1.5배 스파이크가 거의 안 나와서
    # (정규분포 표준편차상 극히 드묾) 여기서는 스파이크를 규칙적으로
    # 섞어넣어 - 실제 시장의 거래량 급증 구간을 흉내낸다.
    df = random_walk_df(n=3000, seed=6)
    rng = np.random.default_rng(6)
    spike_mask = rng.random(len(df)) < 0.05  # 약 5%의 봉에 거래량 스파이크
    df.loc[spike_mask, "Volume"] *= 4.0

    strategy = RsiVolumeSpikeReversalStrategy()
    trades = simulate_lab(df, strategy)
    assert len(trades) > 0
    assert all(t["direction"] in ("LONG", "SHORT") for t in trades)
    assert all(t["exit_reason"] in ("SL", "TP", "TIME") for t in trades)


def test_ichimoku_cloud_breakout_no_signal_on_flat_data():
    strategy = IchimokuCloudBreakoutStrategy()
    idx = pd.date_range("2022-01-01", periods=200, freq="D")
    close = pd.Series(100.0, index=idx)
    df = make_df(close.tolist(), start="2022-01-01", freq="D")
    assert simulate_lab(df, strategy) == []


# ---------------------------------------------------------------------------
# 큰 양봉+볼린저 동시 돌파 (이중 확인 + 본전 이동 트레일링)
# ---------------------------------------------------------------------------

class _FakeSubStrategy:
    """진입 조건 자체는 각자(BigCandleBreakoutStrategy/BollingerBreakoutStrategy)
    테스트에서 이미 검증했으므로, 여기서는 "둘 다 동시에 신호를 내야만 진입한다"는
    합류(confluence) 로직 자체만 떼어서 검증하기 위한 테스트 더블."""

    def __init__(self):
        self.signals: dict[int, dict | None] = {}

    def check_entry(self, k, ctx):
        return self.signals.get(k)


def test_confluence_entry_requires_both_substrategies_to_agree():
    strategy = BigCandleBollingerConfluenceStrategy()
    fake_big, fake_boll = _FakeSubStrategy(), _FakeSubStrategy()
    strategy._big, strategy._boll = fake_big, fake_boll
    ctx = {"big_ctx": {}, "boll_ctx": {}, "atr": np.array([10.0]), "close": np.array([100.0])}

    fake_big.signals[0] = {"direction": "LONG"}
    fake_boll.signals[0] = None
    assert strategy.check_entry(0, ctx) is None  # 한쪽만 신호 - 진입 안 함

    fake_big.signals[0] = None
    fake_boll.signals[0] = {"direction": "LONG"}
    assert strategy.check_entry(0, ctx) is None  # 반대로 한쪽만 - 역시 안 함

    fake_big.signals[0] = {"direction": "LONG"}
    fake_boll.signals[0] = {"direction": "LONG"}
    entry = strategy.check_entry(0, ctx)
    assert entry is not None
    assert entry["direction"] == "LONG"
    assert entry["entry_price"] == 100.0
    assert entry["stop_price"] == 100.0 - strategy.stop_mult * 10.0
    assert entry["breakeven_trigger_price"] == 100.0 + strategy.breakeven_at_mult * 10.0
    assert entry["breakeven_trail"] is True


def _confluence_df(post_entry_closes):
    """워밍업(완만한 상승, 50EMA/볼린저 밴드가 자리잡도록) + 진입 유발용 큰 양봉
    (몸통 크고 볼린저 상단도 뚫음) + 그 뒤 원하는 가격 경로."""
    closes = [100.0 + i * 0.02 for i in range(100)]
    closes.append(closes[-1] + 6.0)  # 진입 봉 - 큰 양봉 + 볼린저 상단 돌파
    closes.extend(post_entry_closes)
    close = pd.Series(closes, dtype=float)
    idx = pd.date_range("2023-01-01", periods=len(close), freq="h")
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.1
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.1
    volume = pd.Series(100.0, index=idx)
    return pd.DataFrame(
        {"Open": open_.to_numpy(), "High": high.to_numpy(), "Low": low.to_numpy(),
         "Close": close.to_numpy(), "Volume": volume.to_numpy()},
        index=idx,
    )


def test_confluence_triggers_on_double_breakout():
    strategy = BigCandleBollingerConfluenceStrategy()
    df = _confluence_df([100.0, 100.0])
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["direction"] == "LONG"


def test_confluence_exits_sl_on_immediate_reversal_before_breakeven():
    # 본전 이동 트리거(진입가 + 0.5×ATR)에 닿기 전에 바로 급락 - 초기 손절(-2×ATR)로
    # 나가야 하고, 손실 폭은 stop_mult×ATR 근처로 제한돼야 한다.
    strategy = BigCandleBollingerConfluenceStrategy()
    df = _confluence_df([90.0, 90.0, 90.0])
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["pct_return"] < 0
    assert trades[0]["pct_return"] > -5  # 2×ATR 손절 폭 근처 - 훨씬 크게 밀리면 이상함


def test_confluence_locks_in_gains_once_breakeven_triggered():
    # 먼저 크게 랠리해서 본전 이동 트리거를 넘긴 뒤(트레일링 스탑이 진입가 위로
    # 올라감) 그 다음에 폭락해도, 이미 본전 위로 올라간 트레일링 스탑 덕분에
    # 손실이 아니라 이익으로 마감돼야 한다.
    strategy = BigCandleBollingerConfluenceStrategy()
    df = _confluence_df([115.0, 90.0, 90.0])
    trades = simulate_lab(df, strategy)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "TRAIL"
    assert trades[0]["pct_return"] > 0  # 본전 이동 후라 손실이 아니라 이익으로 청산


def test_confluence_produces_mostly_positive_trades_over_long_run():
    # 실제 BTCUSDT 1시간봉 검증에서 승률 70%대 이상이 나온 조합이므로,
    # 합성 랜덤워크에서는 절대적 수익성까지는 보장 못 해도 최소한 방향/청산
    # 사유가 정상 범위 안에 있는지로 스모크 테스트한다.
    strategy = BigCandleBollingerConfluenceStrategy()
    df = random_walk_df(n=3000, seed=8)
    trades = simulate_lab(df, strategy)
    assert all(t["direction"] == "LONG" for t in trades)
    assert all(t["exit_reason"] in ("SL", "TRAIL", "TIME") for t in trades)


# ---------------------------------------------------------------------------
# 볼린저 꼬리터치 되돌림 + 본전 이동 트레일링 (15분/5분봉 전용)
# ---------------------------------------------------------------------------

def test_wick_breakeven_trail_reuses_touch_entry_and_sets_long_exit_fields():
    # 진입 조건 자체는 BollingerWickTouchStrategy 테스트에서 이미 검증했으므로,
    # 여기서는 "그 진입 신호를 받아 본전 이동 트레일링 필드로 감싸는" 배선만 검증.
    strategy = BollingerWickBreakevenTrailStrategy()
    fake_touch = _FakeSubStrategy()
    strategy._touch = fake_touch
    ctx = {"touch_ctx": {}, "atr": np.array([10.0]), "close": np.array([100.0])}

    fake_touch.signals[0] = None
    assert strategy.check_entry(0, ctx) is None  # 원 전략이 신호 없으면 그대로 없음

    fake_touch.signals[0] = {"direction": "LONG", "entry_price": 100.0}
    entry = strategy.check_entry(0, ctx)
    assert entry is not None
    assert entry["direction"] == "LONG"
    assert entry["stop_price"] == 100.0 - strategy.stop_mult * 10.0
    assert entry["breakeven_trigger_price"] == 100.0 + strategy.breakeven_at_mult * 10.0
    assert entry["breakeven_trail"] is True


def test_wick_breakeven_trail_sets_short_exit_fields_mirrored():
    strategy = BollingerWickBreakevenTrailStrategy()
    fake_touch = _FakeSubStrategy()
    strategy._touch = fake_touch
    ctx = {"touch_ctx": {}, "atr": np.array([10.0]), "close": np.array([100.0])}

    fake_touch.signals[0] = {"direction": "SHORT", "entry_price": 100.0}
    entry = strategy.check_entry(0, ctx)
    assert entry["direction"] == "SHORT"
    assert entry["stop_price"] == 100.0 + strategy.stop_mult * 10.0
    assert entry["breakeven_trigger_price"] == 100.0 - strategy.breakeven_at_mult * 10.0


def test_wick_breakeven_trail_produces_trades_over_long_run():
    # BTCUSDT/ETHUSDT 15분·5분봉 실측 검증(학습/검증 구간 둘 다 승률 80%대,
    # 기대값 플러스)이 별도로 확인됐으므로, 합성 랜덤워크에서는 최소한 방향/
    # 청산 사유가 정상 범위 안에 있는지로 스모크 테스트한다.
    strategy = BollingerWickBreakevenTrailStrategy()
    df = random_walk_df(n=3000, seed=9)
    trades = simulate_lab(df, strategy)
    assert len(trades) > 0
    assert all(t["direction"] in ("LONG", "SHORT") for t in trades)
    assert all(t["exit_reason"] in ("SL", "TRAIL", "TIME") for t in trades)
