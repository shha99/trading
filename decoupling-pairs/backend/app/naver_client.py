"""Naver 금융 공개 엔드포인트 래퍼 (KRX 직접 접속이 막힌 환경을 위한 대체 데이터 소스).

`data.krx.co.kr`는 국내(한국) IP 대역이 아니면 403으로 차단하는 것이 확인되어
(README 참고), 이 모듈이 `krx_client.py`를 대체하는 기본 데이터 소스로 쓰인다.
Naver가 자사 앱/웹에서 쓰는 비공식 공개 JSON/HTML 엔드포인트를 사용하므로,
과도한 동시 요청은 피하고(기본 동시성 12, 요청 간 소폭 지연) 개인적/저빈도
용도로만 사용해야 한다.

엔드포인트 3종:
  1. 업종(섹터) 분류    - finance.naver.com/sise/sise_group*.naver (WICS 유사 분류)
  2. 시가총액/거래대금  - m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ}
  3. 일별 시세(OHLC)   - api.stock.naver.com/chart/domestic/item/{code}/day
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) decoupling-pairs-research/1.0"}
_SESSION = requests.Session()
_SESSION.headers.update(_UA)

MARKETS = ("KOSPI", "KOSDAQ")
DEFAULT_WORKERS = 12
_REQUEST_TIMEOUT = 15


class NaverUnavailableError(RuntimeError):
    """Naver 응답이 비정상일 때 배치 로직이 잡아서 재시도할 수 있도록 하는 예외."""


def _get_json(url: str, retries: int = 3):
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            r = _SESSION.get(url, timeout=_REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(0.4)
    raise NaverUnavailableError(f"Naver JSON 요청 실패: {url}") from last_exc


def _get_text(url: str, encoding: str = "euc-kr", retries: int = 3) -> str:
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            r = _SESSION.get(url, timeout=_REQUEST_TIMEOUT)
            r.raise_for_status()
            r.encoding = encoding
            return r.text
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(0.4)
    raise NaverUnavailableError(f"Naver 페이지 요청 실패: {url}") from last_exc


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).replace(",", "")
    if s in ("", "N/A", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. 업종 분류
# ---------------------------------------------------------------------------
def fetch_sector_map(max_workers: int = DEFAULT_WORKERS) -> dict[str, str]:
    """ticker -> 업종명 (WICS와 유사한 Naver 자체 업종 분류, KOSPI+KOSDAQ 공통)."""
    html = _get_text("https://finance.naver.com/sise/sise_group.naver?type=upjong")
    group_ids = sorted(set(re.findall(r"sise_group_detail\.naver\?type=upjong&no=(\d+)", html)), key=int)

    def one(no: str) -> tuple[str, list[str]]:
        page = _get_text(f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}")
        name_m = re.search(r"<title>(.*?)\s*:\s*Npay", page)
        sector_name = name_m.group(1).strip() if name_m else f"업종{no}"
        codes = re.findall(r"/item/main\.naver\?code=(\d{6})", page)
        return sector_name, codes

    ticker_sector: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(one, no): no for no in group_ids}
        for fut in as_completed(futs):
            try:
                sector_name, codes = fut.result()
            except NaverUnavailableError:
                logger.exception("업종 그룹 %s 조회 실패 - 건너뜀", futs[fut])
                continue
            for c in codes:
                ticker_sector.setdefault(c, sector_name)
    logger.info("업종 매핑 %d개 종목 (%d개 그룹)", len(ticker_sector), len(group_ids))
    return ticker_sector


# ---------------------------------------------------------------------------
# 2. 시가총액/거래대금 - 시장별 전체 리스팅
# ---------------------------------------------------------------------------
def fetch_market_listing(market: str) -> dict[str, dict]:
    """market('KOSPI'|'KOSDAQ') 전체 종목의 현재가/시총/거래대금 스냅샷.

    Naver 앱의 시가총액 랭킹 API를 페이지네이션한다. ``stockEndType`` 필드로
    ETF/ETN/ELW 등 펀드 상품을 걸러내고(Naver가 "stock"과 "etf" 등을 이
    필드로 구분해줌), 코드가 6자리 숫자가 아닌 것(합성 코드)도 제외한다.
    우선주는 실제 상장주식이므로 남겨두고, 관리종목/우선주 필터는 메타
    테이블의 ``is_preferred``/``is_managed`` 토글에서 처리한다.
    """
    out: dict[str, dict] = {}
    page = 1
    while True:
        d = _get_json(f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100")
        stocks = d.get("stocks", [])
        if not stocks:
            break
        for s in stocks:
            code = s.get("itemCode", "")
            if not re.fullmatch(r"\d{6}", code):
                continue
            if s.get("stockEndType") != "stock":
                continue  # ETF/ETN/ELW 등 펀드 상품 제외 (Naver가 종목/펀드를 이 필드로 구분해줌)
            market_cap = _num(s.get("marketValue"))
            trading_value = _num(s.get("accumulatedTradingValue"))
            out[code] = {
                "name": s.get("stockName", code),
                "market": market,
                "close": _num(s.get("closePrice")),
                "market_cap": market_cap * 1e8 if market_cap is not None else None,
                "trading_value": trading_value * 1e6 if trading_value is not None else None,
            }
        if len(stocks) < 100:
            break
        page += 1
        if page > 60:  # 안전장치
            break
    return out


def build_universe(max_workers: int = DEFAULT_WORKERS) -> pd.DataFrame:
    """stockEndType == "stock"인 종목 중 업종 분류가 있는 것만 최종 유니버스로 채택."""
    sector_map = fetch_sector_map(max_workers=max_workers)
    listings: dict[str, dict] = {}
    for m in MARKETS:
        listings.update(fetch_market_listing(m))

    rows = []
    for code, info in listings.items():
        sector = sector_map.get(code)
        if sector is None:
            continue
        rows.append({"ticker": code, "sector": sector, **info})
    if not rows:
        raise NaverUnavailableError("유니버스를 하나도 구성하지 못했습니다.")
    df = pd.DataFrame(rows).set_index("ticker")
    logger.info("유니버스 구성 완료: %d종목 (KOSPI+KOSDAQ)", len(df))
    return df


# ---------------------------------------------------------------------------
# 3. 일별 시세
# ---------------------------------------------------------------------------
def fetch_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """ticker의 [start, end] 일별 종가/거래량. start/end는 YYYYMMDD."""
    url = f"https://api.stock.naver.com/chart/domestic/item/{ticker}/day?startDateTime={start}&endDateTime={end}"
    data = _get_json(url, retries=2)
    rows = []
    for row in data:
        d = row.get("localDate")
        c = row.get("closePrice")
        if not d or not c:
            continue
        rows.append(
            {
                "date": pd.to_datetime(d, format="%Y%m%d"),
                "close": c,
                "volume": row.get("accumulatedTradingVolume"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["close", "volume"])
    return pd.DataFrame(rows).set_index("date").sort_index()


def fetch_histories(tickers: list[str], start: str, end: str, max_workers: int = DEFAULT_WORKERS) -> dict[str, pd.DataFrame]:
    """여러 종목의 일별 시세를 병렬로 수집. 실패한 종목은 결과에서 제외되고 로그만 남는다."""
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_history, t, start, end): t for t in tickers}
        for i, fut in enumerate(as_completed(futs), start=1):
            t = futs[fut]
            try:
                df = fut.result()
                if not df.empty:
                    out[t] = df
            except NaverUnavailableError:
                logger.warning("시세 수집 실패: %s (건너뜀)", t)
            if i % 300 == 0:
                logger.info("시세 수집 진행 %d/%d", i, len(tickers))
    return out


def is_preferred_stock(ticker: str, name: str) -> bool:
    code_hint = len(ticker) == 6 and ticker[-1] != "0"
    name_hint = bool(name) and (name.endswith("우") or "우B" in name or "우C" in name)
    return code_hint or name_hint


def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")
