"""한국 주식(코스피/코스닥) 데이터 프로바이더.

시세는 FinanceDataReader(pip: finance-datareader)를 사용한다. 유니버스는
universe_kr.json에 정의된 종목 목록을 사용하며, 전체 상장 종목으로
확장하려면 FinanceDataReader의 StockListing("KRX") 결과로 이 파일을
갱신하면 된다 (README 참고).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from .base import DataProvider, Ticker

logger = logging.getLogger(__name__)

UNIVERSE_FILE = Path(__file__).parent / "universe_kr.json"
_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class KRProvider(DataProvider):
    market = "KR"

    def get_universe(self) -> list[Ticker]:
        with UNIVERSE_FILE.open(encoding="utf-8") as f:
            items = json.load(f)
        return [Ticker(symbol=item["symbol"], name=item["name"]) for item in items]

    def get_ohlcv(self, symbol: str, lookback_days: int = 150) -> pd.DataFrame | None:
        try:
            import FinanceDataReader as fdr
        except ImportError:
            logger.error("FinanceDataReader가 설치되어 있지 않습니다 (pip install finance-datareader).")
            return None

        try:
            end = pd.Timestamp.now()
            # 주말/공휴일 여유를 두고 넉넉히 조회
            start = end - pd.Timedelta(days=int(lookback_days * 1.7) + 10)
            df = fdr.DataReader(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if df is None or df.empty:
                return None
            df = df.rename(columns=str.title)
            if not set(_REQUIRED_COLUMNS).issubset(df.columns):
                logger.warning("KR 종목 %s 데이터에 필요한 컬럼이 없습니다: %s", symbol, df.columns.tolist())
                return None
            return df[_REQUIRED_COLUMNS].sort_index()
        except Exception:
            logger.exception("KR OHLCV 조회 실패: %s", symbol)
            return None
