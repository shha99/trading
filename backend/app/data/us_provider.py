"""미국 주식 데이터 프로바이더.

시세는 yfinance를 사용한다. 유니버스는 universe_us.json에 정의된
종목 목록을 사용한다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from .base import DataProvider, Ticker

logger = logging.getLogger(__name__)

UNIVERSE_FILE = Path(__file__).parent / "universe_us.json"
_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class USProvider(DataProvider):
    market = "US"

    def get_universe(self) -> list[Ticker]:
        with UNIVERSE_FILE.open(encoding="utf-8") as f:
            items = json.load(f)
        return [Ticker(symbol=item["symbol"], name=item["name"]) for item in items]

    def get_ohlcv(self, symbol: str, lookback_days: int = 150) -> pd.DataFrame | None:
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance가 설치되어 있지 않습니다 (pip install yfinance).")
            return None

        try:
            period_days = int(lookback_days * 1.7) + 10
            df = yf.Ticker(symbol).history(period=f"{period_days}d", auto_adjust=False)
            if df is None or df.empty:
                return None
            df = df.rename(columns=str.title)
            if not set(_REQUIRED_COLUMNS).issubset(df.columns):
                logger.warning("US 종목 %s 데이터에 필요한 컬럼이 없습니다: %s", symbol, df.columns.tolist())
                return None
            idx = pd.to_datetime(df.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
            df.index = idx
            return df[_REQUIRED_COLUMNS].sort_index()
        except Exception:
            logger.exception("US OHLCV 조회 실패: %s", symbol)
            return None
