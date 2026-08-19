"""MACD(이동평균수렴확산) 시그널선 교차 전략."""
from __future__ import annotations

import pandas as pd

from .base import Signal, SignalType, Strategy


class MACDStrategy(Strategy):
    key = "macd"
    label = "MACD"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.min_bars = slow + signal + 5

    def evaluate(self, symbol: str, name: str, market: str, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_bars:
            return None

        close = df["Close"]
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.signal, adjust=False).mean()
        histogram = macd_line - signal_line

        if histogram.iloc[-2:].isna().any():
            return None

        prev_hist = float(histogram.iloc[-2])
        curr_hist = float(histogram.iloc[-1])
        ts = df.index[-1]
        price = close.iloc[-1]

        details = {
            "macd": round(float(macd_line.iloc[-1]), 3),
            "signal_line": round(float(signal_line.iloc[-1]), 3),
            "histogram": round(curr_hist, 3),
        }

        if prev_hist <= 0 < curr_hist:
            return self._make_signal(
                symbol, name, market, SignalType.BUY, price, ts,
                pattern="macd_golden_cross", **details,
            )
        if prev_hist >= 0 > curr_hist:
            return self._make_signal(
                symbol, name, market, SignalType.SELL, price, ts,
                pattern="macd_dead_cross", **details,
            )
        return None
