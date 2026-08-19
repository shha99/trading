"""데이터 프로바이더 공통 인터페이스."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Ticker:
    symbol: str
    name: str


class DataProvider:
    """시장별 종목 유니버스 조회 + OHLCV 히스토리 조회를 담당한다.

    새로운 시장(예: 암호화폐, 일본 증시 등)을 추가하려면 이 클래스를
    상속해 get_universe / get_ohlcv 두 메서드만 구현하면 된다.
    """

    market: str = ""

    def get_universe(self) -> list[Ticker]:
        raise NotImplementedError

    def get_ohlcv(self, symbol: str, lookback_days: int = 150) -> pd.DataFrame | None:
        """오래된 -> 최신 순으로 정렬된 DataFrame(Open/High/Low/Close/Volume)을
        반환한다. 데이터를 가져오지 못하면 None을 반환한다."""
        raise NotImplementedError
