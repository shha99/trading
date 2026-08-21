"""검증된 단일 전략: 200EMA 위 켈트너 하단 눌림목 복귀.

친구분이 BTC 1시간봉 5년치(45,000봉)로 후보 114개 조합을 백테스트해
학습(2021-07~2025-02)/검증(2025-02~2026-08) 두 구간 모두 플러스를 낸 유일한
전략을 그대로 구현한다 (조합 탐색 자체를 다시 하는 게 아니라, 이미 나온
규칙을 신호 엔진에 옮기는 것).

진입 (완결된 봉 기준, 전부 충족 → 그 다음 순간 시장가 매수):
  - 종가 > 200EMA (큰 흐름 상승)
  - 직전 봉 종가 <= 켈트너 하단 (눌림목)
  - 이번 봉 종가 > 켈트너 하단 (복귀)

청산: 손절 = 진입가 - 2*ATR, 익절 = 진입가 + 4*ATR, 3일 경과 시 시간손절.
(청산 가격 계산은 여기서 하고, 실제 주문 부착은 broker.py가 담당한다.)

한계 (반드시 인지하고 사용할 것 — 친구분 자료 그대로):
  - 규칙은 BTC 1시간봉에서 찾은 것. ETH 1시간봉은 검증 구간에서 손실이었음.
  - 2022년 하락장에서 -9.8% 손실 — 상승장 편향이 있는 전략.
  - 표본이 적어 우연/과최적화(114개 조합 다중검정) 가능성이 남아 있음.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import pandas as pd

from .config import settings
from .indicators import atr, ema, keltner_lower


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Signal:
    symbol: str
    timeframe: str
    signal_type: SignalType
    entry_price: float
    stop_price: float
    target_price: float
    time_stop_at: datetime
    timestamp: datetime
    details: dict[str, Any] = field(default_factory=dict)


class KeltnerReclaimStrategy:
    key = "keltner_reclaim_200ema"
    label = "200EMA + 켈트너 하단 눌림목 복귀"

    def __init__(
        self,
        trend_ema_period: int | None = None,
        keltner_ema_period: int | None = None,
        keltner_atr_period: int | None = None,
        keltner_atr_mult: float | None = None,
        stop_atr_mult: float | None = None,
        target_atr_mult: float | None = None,
        time_stop_days: float | None = None,
    ):
        self.trend_ema_period = trend_ema_period or settings.trend_ema_period
        self.keltner_ema_period = keltner_ema_period or settings.keltner_ema_period
        self.keltner_atr_period = keltner_atr_period or settings.keltner_atr_period
        self.keltner_atr_mult = keltner_atr_mult or settings.keltner_atr_mult
        self.stop_atr_mult = stop_atr_mult or settings.stop_atr_mult
        self.target_atr_mult = target_atr_mult or settings.target_atr_mult
        self.time_stop_days = time_stop_days or settings.time_stop_days
        # 워밍업 기간까지 고려한 최소 봉 수
        self.min_bars = max(self.trend_ema_period, self.keltner_ema_period, self.keltner_atr_period) + 5

    def evaluate(self, symbol: str, timeframe: str, df: pd.DataFrame) -> Signal | None:
        """df는 오래된 -> 최신 순, 마지막 행이 "완결된" 가장 최근 봉이어야 한다.

        (진행 중인 봉을 넣으면 미완결 데이터로 잘못 판단하므로, 호출 전에
        history.is_candle_closed로 걸러야 한다.)
        """
        if len(df) < self.min_bars:
            return None

        close = df["Close"]
        ema_trend = ema(close, self.trend_ema_period)
        kelt_lower = keltner_lower(df, self.keltner_ema_period, self.keltner_atr_period, self.keltner_atr_mult)
        atr_series = atr(df, self.keltner_atr_period)

        if pd.isna(ema_trend.iloc[-1]) or pd.isna(kelt_lower.iloc[-2:]).any() or pd.isna(atr_series.iloc[-1]):
            return None

        prev_close, curr_close = float(close.iloc[-2]), float(close.iloc[-1])
        prev_lower, curr_lower = float(kelt_lower.iloc[-2]), float(kelt_lower.iloc[-1])
        trend_ok = curr_close > float(ema_trend.iloc[-1])
        pullback = prev_close <= prev_lower
        reclaim = curr_close > curr_lower

        if not (trend_ok and pullback and reclaim):
            return None

        entry = curr_close
        atr_now = float(atr_series.iloc[-1])
        stop = entry - self.stop_atr_mult * atr_now
        target = entry + self.target_atr_mult * atr_now
        ts = df.index[-1]
        ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        time_stop_at = ts + timedelta(days=self.time_stop_days)

        return Signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type=SignalType.BUY,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            time_stop_at=time_stop_at,
            timestamp=ts,
            details={
                "pattern": "keltner_lower_reclaim",
                "ema_trend": round(float(ema_trend.iloc[-1]), 4),
                "keltner_lower": round(curr_lower, 4),
                "atr": round(atr_now, 4),
            },
        )

    def condition_status(self, df: pd.DataFrame) -> dict:
        """진입조건 3개의 현재 충족 여부를 각각 반환한다 (전략 페이지 상단 패널용).

        evaluate()는 "전부 충족"일 때만 Signal을 주지만, 이 메서드는 조건이
        하나라도 미충족일 때 어디가 걸리는지 보여주려고 각각을 따로 계산한다.
        """
        if len(df) < self.min_bars:
            return {"ready": False, "reason": "not_enough_bars", "min_bars": self.min_bars, "bars": len(df)}

        close = df["Close"]
        ema_trend = ema(close, self.trend_ema_period)
        kelt_lower = keltner_lower(df, self.keltner_ema_period, self.keltner_atr_period, self.keltner_atr_mult)

        if pd.isna(ema_trend.iloc[-1]) or pd.isna(kelt_lower.iloc[-2:]).any():
            return {"ready": False, "reason": "warming_up"}

        prev_close, curr_close = float(close.iloc[-2]), float(close.iloc[-1])
        prev_lower, curr_lower = float(kelt_lower.iloc[-2]), float(kelt_lower.iloc[-1])
        trend_ok = curr_close > float(ema_trend.iloc[-1])
        pullback = prev_close <= prev_lower
        reclaim = curr_close > curr_lower

        return {
            "ready": True,
            "conditions": {
                "trend_above_200ema": trend_ok,
                "prev_bar_pulled_back_below_keltner_lower": pullback,
                "curr_bar_reclaimed_keltner_lower": reclaim,
            },
            "all_met": trend_ok and pullback and reclaim,
            "values": {
                "close": curr_close,
                "ema_trend": round(float(ema_trend.iloc[-1]), 4),
                "keltner_lower": round(curr_lower, 4),
                "prev_close": prev_close,
                "prev_keltner_lower": round(prev_lower, 4),
            },
        }
