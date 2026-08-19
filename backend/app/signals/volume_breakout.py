"""거래량 급증 + 가격 돌파/붕괴 전략.

최근 거래량이 평균 대비 volume_multiplier배 이상 급증하면서, 동시에
직전 price_lookback봉 고점을 돌파하면 BUY, 직전 저점을 하향 이탈하면
SELL 시그널을 낸다.
"""
from __future__ import annotations

import pandas as pd

from .base import Signal, SignalType, Strategy


class VolumeBreakoutStrategy(Strategy):
    key = "volume_breakout"
    label = "거래량 급증 + 가격 돌파"

    def __init__(
        self,
        volume_window: int = 20,
        volume_multiplier: float = 2.0,
        price_lookback: int = 20,
    ):
        self.volume_window = volume_window
        self.volume_multiplier = volume_multiplier
        self.price_lookback = price_lookback
        self.min_bars = max(volume_window, price_lookback) + 5

    def evaluate(self, symbol: str, name: str, market: str, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_bars:
            return None

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        avg_volume = volume.rolling(self.volume_window).mean().shift(1)
        curr_avg_volume = avg_volume.iloc[-1]
        curr_volume = volume.iloc[-1]
        if pd.isna(curr_avg_volume) or curr_avg_volume <= 0:
            return None
        volume_ratio = float(curr_volume / curr_avg_volume)
        if volume_ratio < self.volume_multiplier:
            return None

        prior_high = float(high.iloc[-(self.price_lookback + 1):-1].max())
        prior_low = float(low.iloc[-(self.price_lookback + 1):-1].min())
        curr_close = float(close.iloc[-1])
        ts = df.index[-1]

        details = {
            "volume_ratio": round(volume_ratio, 2),
            "prior_high": round(prior_high, 2),
            "prior_low": round(prior_low, 2),
        }

        if curr_close > prior_high:
            return self._make_signal(
                symbol, name, market, SignalType.BUY, curr_close, ts,
                pattern="volume_breakout_up", **details,
            )
        if curr_close < prior_low:
            return self._make_signal(
                symbol, name, market, SignalType.SELL, curr_close, ts,
                pattern="volume_breakdown", **details,
            )
        return None
