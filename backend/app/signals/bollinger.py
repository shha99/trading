"""볼린저 밴드 상/하단 터치(돌파) 전략.

종가가 하단 밴드를 하향 이탈하면 과매도 반등 관찰 BUY,
상단 밴드를 상향 돌파하면 과열 SELL 시그널을 낸다.
"""
from __future__ import annotations

import pandas as pd

from .base import Signal, SignalType, Strategy


class BollingerBandStrategy(Strategy):
    key = "bollinger"
    label = "볼린저 밴드"

    def __init__(self, window: int = 20, num_std: float = 2.0):
        self.window = window
        self.num_std = num_std
        self.min_bars = window + 5

    def evaluate(self, symbol: str, name: str, market: str, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_bars:
            return None

        close = df["Close"]
        mid = close.rolling(self.window).mean()
        std = close.rolling(self.window).std()
        upper = mid + self.num_std * std
        lower = mid - self.num_std * std

        if upper.iloc[-2:].isna().any() or lower.iloc[-2:].isna().any():
            return None

        prev_close, curr_close = float(close.iloc[-2]), float(close.iloc[-1])
        prev_upper, curr_upper = float(upper.iloc[-2]), float(upper.iloc[-1])
        prev_lower, curr_lower = float(lower.iloc[-2]), float(lower.iloc[-1])
        ts = df.index[-1]

        details = {
            "band_mid": round(float(mid.iloc[-1]), 2),
            "band_upper": round(curr_upper, 2),
            "band_lower": round(curr_lower, 2),
        }

        if prev_close >= prev_lower and curr_close < curr_lower:
            return self._make_signal(
                symbol, name, market, SignalType.BUY, curr_close, ts,
                pattern="lower_band_break", **details,
            )
        if prev_close <= prev_upper and curr_close > curr_upper:
            return self._make_signal(
                symbol, name, market, SignalType.SELL, curr_close, ts,
                pattern="upper_band_break", **details,
            )
        return None
