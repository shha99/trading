"""전략에 필요한 최소 지표만 pandas로 직접 계산한다 (TA-Lib 불필요).

- ema: 추세 필터(200EMA)와 켈트너 채널의 중심선에 사용.
- atr: 변동성 측정, 켈트너 채널 폭 + 손절/익절 폭 계산에 사용.
- keltner_lower: 켈트너 채널 하단 (중심선 - ATR*배수).
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    # Wilder 방식 스무딩 (RSI와 동일한 관례 - 기존 저장소 RSIStrategy 참고)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def keltner_lower(
    df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0
) -> pd.Series:
    mid = ema(df["Close"], ema_period)
    width = atr(df, atr_period) * mult
    return mid - width
