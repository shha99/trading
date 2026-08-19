"""사용 가능한 전략들의 레지스트리."""
from __future__ import annotations

from .base import Strategy
from .bollinger import BollingerBandStrategy
from .macd import MACDStrategy
from .moving_average import MovingAverageCrossStrategy
from .rsi import RSIStrategy
from .volume_breakout import VolumeBreakoutStrategy


def default_strategies() -> list[Strategy]:
    """기본 설정으로 초기화된 전체 전략 목록.

    새 전략을 추가하려면 이 리스트에 인스턴스를 추가하기만 하면
    데이터 엔진, API, 프론트엔드 탭에 자동으로 반영된다.
    """
    return [
        MovingAverageCrossStrategy(),
        RSIStrategy(),
        MACDStrategy(),
        BollingerBandStrategy(),
        VolumeBreakoutStrategy(),
    ]
