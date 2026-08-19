"""이동평균 골든크로스 / 데드크로스 전략."""
from __future__ import annotations

import pandas as pd

from .base import Signal, SignalType, Strategy


class MovingAverageCrossStrategy(Strategy):
    key = "ma_cross"
    label = "이동평균 골든/데드크로스"

    def __init__(self, short_window: int = 5, long_window: int = 20):
        self.short_window = short_window
        self.long_window = long_window
        self.min_bars = long_window + 5

    def evaluate(self, symbol: str, name: str, market: str, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_bars:
            return None

        close = df["Close"]
        short_ma = close.rolling(self.short_window).mean()
        long_ma = close.rolling(self.long_window).mean()

        if short_ma.iloc[-2:].isna().any() or long_ma.iloc[-2:].isna().any():
            return None

        prev_diff = short_ma.iloc[-2] - long_ma.iloc[-2]
        curr_diff = short_ma.iloc[-1] - long_ma.iloc[-1]
        ts = df.index[-1]
        price = close.iloc[-1]

        details = {
            "short_window": self.short_window,
            "long_window": self.long_window,
            "short_ma": round(float(short_ma.iloc[-1]), 2),
            "long_ma": round(float(long_ma.iloc[-1]), 2),
        }

        if prev_diff <= 0 < curr_diff:
            return self._make_signal(
                symbol, name, market, SignalType.BUY, price, ts,
                pattern="golden_cross", **details,
            )
        if prev_diff >= 0 > curr_diff:
            return self._make_signal(
                symbol, name, market, SignalType.SELL, price, ts,
                pattern="dead_cross", **details,
            )
        return None
