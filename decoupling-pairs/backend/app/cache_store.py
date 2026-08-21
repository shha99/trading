"""로컬 parquet/JSON 캐시 입출력.

조회(스캔/검색) 요청은 절대 KRX를 직접 호출하지 않고, 이 모듈이 읽어들이는
캐시된 패널 데이터에서만 상관계수를 계산한다. 캐시 갱신은 batch_update.py가
전담한다.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime

import pandas as pd

from . import config

_LOCK = threading.Lock()


def load_prices() -> pd.DataFrame:
    """date x ticker 종가 패널. 캐시가 없으면 빈 DataFrame."""
    if not config.PRICES_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(config.PRICES_PATH)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def load_trading_value() -> pd.DataFrame:
    if not config.VALUE_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(config.VALUE_PATH)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def load_index() -> pd.DataFrame:
    """date x {KOSPI, KOSDAQ} 지수 종가 패널. 캐시가 없으면 빈 DataFrame."""
    if not config.INDEX_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(config.INDEX_PATH)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def save_index(df: pd.DataFrame) -> None:
    with _LOCK:
        df.sort_index().to_parquet(config.INDEX_PATH)


def load_meta() -> pd.DataFrame:
    if not config.META_PATH.exists():
        return pd.DataFrame(
            columns=[
                "name",
                "market",
                "sector",
                "market_cap",
                "avg_trading_value",
                "is_preferred",
                "is_managed",
                "is_halted",
                "listed_shares",
                "index_corr",
                "index_reversal",
            ]
        )
    return pd.read_parquet(config.META_PATH)


def save_panel(df: pd.DataFrame, which: str) -> None:
    path = config.PRICES_PATH if which == "prices" else config.VALUE_PATH
    with _LOCK:
        df.sort_index().to_parquet(path)


def save_meta(df: pd.DataFrame) -> None:
    with _LOCK:
        df.to_parquet(config.META_PATH)


def load_state() -> dict:
    if not config.STATE_PATH.exists():
        return {}
    try:
        return json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(**updates) -> dict:
    with _LOCK:
        state = load_state()
        state.update(updates)
        config.STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        return state


def merge_new_day(existing: pd.DataFrame, new_row: pd.Series, trading_date: datetime) -> pd.DataFrame:
    """기존 패널에 새 거래일 데이터를 append(누적, 기존 데이터는 덮어쓰지 않음).

    같은 날짜가 이미 있으면 그 행만 최신값으로 갱신(정정 반영), 없으면 새 행 추가.
    컬럼(종목) 합집합으로 확장하며, 신규 상장 종목은 그 이전 구간이 NaN으로 채워진다.
    """
    ts = pd.Timestamp(trading_date)
    if existing is None or existing.empty:
        return pd.DataFrame([new_row], index=[ts])
    all_cols = existing.columns.union(new_row.index)
    existing = existing.reindex(columns=all_cols)
    existing.loc[ts] = new_row.reindex(all_cols)
    return existing.sort_index()
