"""pykrx 래퍼.

KRX 원본 호출부를 이 모듈 하나로 모아서, 배치/백필 로직이 pykrx의
세부 함수 시그니처에 직접 의존하지 않도록 한다. 네트워크 호출은
전부 여기서만 일어난다.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import pandas as pd
from pykrx import stock

logger = logging.getLogger(__name__)

MARKETS = ("KOSPI", "KOSDAQ")


class KrxUnavailableError(RuntimeError):
    """KRX 응답이 비정상(빈 응답/차단 등)일 때 배치 로직이 잡아서 재시도할 수 있도록 하는 예외."""

# KRX 서버에 과도한 요청을 보내지 않기 위한 최소 호출 간격(초)
_REQUEST_SLEEP_SEC = 0.15


def _sleep():
    time.sleep(_REQUEST_SLEEP_SEC)


def _to_yyyymmdd(d: str | date | datetime) -> str:
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def nearest_business_day(target: str | date | datetime | None = None, prev: bool = True) -> str:
    """target 기준 가장 가까운 영업일(YYYYMMDD). target이 None이면 오늘 기준."""
    d = _to_yyyymmdd(target) if target else today_str()
    try:
        return stock.get_nearest_business_day_in_a_week(date=d, prev=prev)
    except (IndexError, ValueError) as exc:
        raise KrxUnavailableError(
            "KRX 응답을 받지 못했습니다(네트워크 차단 또는 일시 장애 가능성). 잠시 후 다시 시도하세요."
        ) from exc


def business_days_between(start: str | date, end: str | date) -> list[str]:
    """[start, end] 구간의 영업일 목록(YYYYMMDD, 오름차순).

    KOSPI 지수 OHLCV를 이용해 실제 개장일만 뽑아낸다(공휴일 자동 제외).
    """
    s, e = _to_yyyymmdd(start), _to_yyyymmdd(end)
    try:
        df = stock.get_index_ohlcv_by_date(s, e, "1001")  # 코스피 종합지수
    except (ValueError, KeyError) as exc:
        raise KrxUnavailableError(
            "KRX 영업일 조회에 실패했습니다(네트워크 차단 또는 일시 장애 가능성)."
        ) from exc
    _sleep()
    if df is None or df.empty:
        return []
    return [ts.strftime("%Y%m%d") for ts in df.index]


def fetch_market_snapshot(trading_date: str) -> pd.DataFrame:
    """한 거래일의 코스피+코스닥 전종목 스냅샷.

    Returns:
        DataFrame indexed by ticker with columns:
        close, volume, trading_value, change_rate, market_cap, listed_shares
    """
    d = _to_yyyymmdd(trading_date)
    frames = []
    for market in MARKETS:
        ohlcv = stock.get_market_ohlcv_by_ticker(d, market=market)
        _sleep()
        cap = stock.get_market_cap_by_ticker(d, market=market)
        _sleep()
        if ohlcv is None or ohlcv.empty:
            continue
        ohlcv = ohlcv.rename(
            columns={
                "종가": "close",
                "거래량": "volume",
                "거래대금": "trading_value",
                "등락률": "change_rate",
            }
        )
        cap = cap.rename(columns={"시가총액": "market_cap", "상장주식수": "listed_shares"})
        merged = ohlcv[["close", "volume", "trading_value", "change_rate"]].join(
            cap[["market_cap", "listed_shares"]], how="left"
        )
        merged["market"] = market
        frames.append(merged)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    out.index.name = "ticker"
    return out


def fetch_sector_map(trading_date: str) -> pd.DataFrame:
    """한 거래일의 업종 분류표(KRX 업종분류 기준, KOSPI+KOSDAQ).

    Returns: DataFrame indexed by ticker with columns: name, sector
    일부 종목(우선주 등)은 업종분류표에서 빠질 수 있다 - 상위 레이어에서 보완.
    """
    d = _to_yyyymmdd(trading_date)
    frames = []
    for market in MARKETS:
        try:
            df = stock.get_market_sector_classifications(d, market)
        except Exception:  # noqa: BLE001 - KRX 응답 이슈는 스킵하고 계속 진행
            logger.exception("sector classification fetch failed for %s", market)
            df = pd.DataFrame()
        _sleep()
        if df is None or df.empty:
            continue
        df = df.rename(columns={"종목명": "name", "업종명": "sector"})
        frames.append(df[["name", "sector"]])
    if not frames:
        return pd.DataFrame(columns=["name", "sector"])
    out = pd.concat(frames)
    out.index.name = "ticker"
    return out[~out.index.duplicated(keep="first")]


def fetch_ticker_name(ticker: str) -> str:
    try:
        name = stock.get_market_ticker_name(ticker)
        _sleep()
        return name or ticker
    except Exception:  # noqa: BLE001
        return ticker


def is_preferred_stock(ticker: str, name: str) -> bool:
    """우선주 여부 추정.

    KRX 종목코드는 보통주가 마지막 자리 '0'으로 끝나고, 우선주는 그 외
    (5/7/9 등)로 끝난다. 종목명에 흔히 붙는 접미사('우', '우B' 등)도 함께 확인.
    """
    code_hint = len(ticker) == 6 and ticker[-1] != "0"
    name_hint = bool(name) and (
        name.endswith("우") or "우B" in name or "우C" in name or name.rstrip("0123456789").endswith("우")
    )
    return code_hint or name_hint
