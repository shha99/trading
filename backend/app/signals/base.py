"""트레이딩 시그널 전략들이 공통으로 사용하는 타입 정의.

각 Strategy는 종목 하나의 OHLCV 히스토리(오래된 -> 최신 순, 컬럼:
Open/High/Low/Close/Volume)를 받아 "가장 최근 봉"에서 해당 전략의
조건이 방금 발생했는지를 판단하고, 발생했다면 Signal을, 아니면 None을
반환한다. 즉 매 실행마다 "새로 트리거된" 시그널만 리턴하는 것이 원칙이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Signal:
    symbol: str
    name: str
    market: str
    strategy: str
    strategy_label: str
    signal_type: SignalType
    price: float
    timestamp: datetime
    details: dict[str, Any] = field(default_factory=dict)


class Strategy:
    """모든 시그널 전략의 베이스 클래스."""

    key: str = ""
    label: str = ""
    # 시그널 계산에 필요한 최소 봉 수 (지표 워밍업 기간 포함)
    min_bars: int = 30

    def evaluate(self, symbol: str, name: str, market: str, df: pd.DataFrame) -> Signal | None:
        raise NotImplementedError

    def _make_signal(
        self,
        symbol: str,
        name: str,
        market: str,
        signal_type: SignalType,
        price: float,
        timestamp,
        **details: Any,
    ) -> Signal:
        ts = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
        return Signal(
            symbol=symbol,
            name=name,
            market=market,
            strategy=self.key,
            strategy_label=self.label,
            signal_type=signal_type,
            price=float(price),
            timestamp=ts,
            details=details,
        )
