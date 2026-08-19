"""RSI(상대강도지수) 과매수/과매도 전략.

RSI가 과매도 임계값(기본 30) 아래로 막 진입하면 BUY(반등 관찰) 시그널을,
과매수 임계값(기본 70) 위로 막 진입하면 SELL(조정 관찰) 시그널을 낸다.
"""
from __future__ import annotations

import pandas as pd

from .base import Signal, SignalType, Strategy


class RSIStrategy(Strategy):
    key = "rsi"
    label = "RSI 과매수/과매도"

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.min_bars = period + 5

    def _rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / self.period, min_periods=self.period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self.period, min_periods=self.period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        # avg_loss가 0인데(하락이 전혀 없었음) 워밍업이 끝나 avg_gain은 유효한 구간은
        # RSI=100이 맞다. 반면 아직 워밍업 중이라 avg_gain 자체가 NaN인 구간은
        # NaN으로 남겨두어 evaluate()가 "데이터 부족"으로 올바르게 걸러내게 한다.
        rsi = rsi.mask((avg_loss == 0) & avg_gain.notna(), 100.0)
        return rsi

    def evaluate(self, symbol: str, name: str, market: str, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_bars:
            return None

        close = df["Close"]
        rsi = self._rsi(close)
        if rsi.iloc[-2:].isna().any():
            return None

        prev, curr = float(rsi.iloc[-2]), float(rsi.iloc[-1])
        ts = df.index[-1]
        price = close.iloc[-1]

        if prev > self.oversold >= curr:
            return self._make_signal(
                symbol, name, market, SignalType.BUY, price, ts,
                pattern="oversold", rsi=round(curr, 2), threshold=self.oversold,
            )
        if prev < self.overbought <= curr:
            return self._make_signal(
                symbol, name, market, SignalType.SELL, price, ts,
                pattern="overbought", rsi=round(curr, 2), threshold=self.overbought,
            )
        return None
