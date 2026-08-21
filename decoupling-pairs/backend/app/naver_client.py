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


INVERSE_LEVERAGE_RE = re.compile(r"인버스|레버리지|곱버스|INVERSE|LEVERAGE", re.IGNORECASE)


def build_universe(max_workers: int = DEFAULT_WORKERS) -> pd.DataFrame:
    """stockEndType == "stock"인 종목 중 업종 분류가 있는 것만 최종 유니버스로 채택.

    stockEndType 필터로 ETF/ETN 대부분은 걸러지지만, 혹시 새 상품 유형이
    추가로 껴 있을 경우를 대비해 종목명에 "인버스/레버리지/곱버스"가 들어간
    것도 이중으로 제외한다(지수와 구조적으로 반대/증폭 방향으로 움직이는
    상품은 "진짜 디커플링"이 아니라 상품 설계 그 자체이므로).
    """
    sector_map = fetch_sector_map(max_workers=max_workers)
    listings: dict[str, dict] = {}
    for m in MARKETS:
        listings.update(fetch_market_listing(m))

    rows = []
    excluded_by_name = 0
    for code, info in listings.items():
        sector = sector_map.get(code)
        if sector is None:
            continue
        if INVERSE_LEVERAGE_RE.search(info.get("name", "")):
            excluded_by_name += 1
            continue
        rows.append({"ticker": code, "sector": sector, **info})
    if not rows:
        raise NaverUnavailableError("유니버스를 하나도 구성하지 못했습니다.")
    df = pd.DataFrame(rows).set_index("ticker")
    logger.info(
        "유니버스 구성 완료: %d종목 (KOSPI+KOSDAQ, 인버스/레버리지 명칭 %d개 추가 제외)",
        len(df),
        excluded_by_name,
    )
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
    df = pd.DataFrame(rows).set_index("date").sort_index()
    df["close"] = adjust_for_corporate_actions(df["close"])
    return df


# KRX 상하한가는 ±30%다(2015.6~). 정규 거래일에는 이 폭을 하루 만에 벗어날 수 없으므로,
# raw 시세에서 이 범위를 넘는 일간 변동은 100% 무상증자/액면분할/감자 등 기업행동으로 인한
# 가격 재산정이지 실제 시장 변동이 아니다. Naver 차트 API가 수정주가가 아닌 원주가를
# 반환하는 것으로 확인되어(에이프로젠 007460이 2026-05-08 하루 만에 293원→4,530원(15.5배)
# 뛴 것처럼 보이는 등, 실제로는 감자), 이 시그널로 자동 역보정한다.
_DAILY_LIMIT_PCT = 0.30
_ADJUST_BUFFER_PCT = 0.03  # 상하한가 근처 반올림 오차를 오탐지하지 않기 위한 여유(퍼센트 포인트)
# 상/하한은 대칭적인 배율(reciprocal)이 아니라 KRX 고시처럼 "±퍼센트"로 정의해야 한다.
# 예: -30%는 ratio 0.70이지, 1/1.30(=0.769)이 아니다 - 반대로 계산하면 정상적인
# -28%대 하락(ratio 0.72)까지 오탐지하게 된다.
_UPPER_RATIO = 1 + _DAILY_LIMIT_PCT + _ADJUST_BUFFER_PCT
_LOWER_RATIO = 1 - _DAILY_LIMIT_PCT - _ADJUST_BUFFER_PCT


def adjust_for_corporate_actions(close: pd.Series) -> pd.Series:
    """무상증자/액면분할/감자 등으로 인한 raw 종가 불연속을 역산해 보정한다.

    가장 최근 이벤트부터 과거 방향으로 처리하면서, 이벤트 이전 구간 전체에
    (이벤트 당일 종가 / 전일 종가) 배율을 곱해 현재 주식 수 기준으로
    재환산한다(표준적인 "수정주가" 구성 방식과 동일한 원리).
    """
    if close is None or len(close) < 2:
        return close
    adjusted = close.copy().astype(float)
    # pandas Copy-on-Write 모드에서는 to_numpy()가 읽기 전용 뷰를 돌려줄 수 있으므로 명시적으로 복사.
    values = adjusted.to_numpy().copy()
    n = len(values)
    # 뒤(최근)에서 앞(과거)으로 훑으며, 이벤트 지점 이전 구간을 통째로 배율 조정.
    for i in range(n - 1, 0, -1):
        prev, cur = values[i - 1], values[i]
        if prev <= 0 or cur <= 0:
            continue
        ratio = cur / prev
        if ratio > _UPPER_RATIO or ratio < _LOWER_RATIO:
            values[:i] *= ratio
    adjusted.iloc[:] = values
    return adjusted


# ---------------------------------------------------------------------------
# 4. 시장 지수 (KOSPI/KOSDAQ)
# ---------------------------------------------------------------------------
def fetch_index_history(index: str, start: str, end: str) -> pd.Series:
    """index('KOSPI'|'KOSDAQ')의 [start, end] 일별 종가."""
    url = f"https://api.stock.naver.com/chart/domestic/index/{index}/day?startDateTime={start}&endDateTime={end}"
    data = _get_json(url, retries=2)
    rows = [
        (pd.to_datetime(r["localDate"], format="%Y%m%d"), r["closePrice"])
        for r in data
        if r.get("localDate") and r.get("closePrice")
    ]
    if not rows:
        return pd.Series(dtype=float)
    idx, vals = zip(*rows)
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name=index).sort_index()


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


def market_status() -> tuple[bool, str]:
    """대표 종목 하나로 현재 장 상태를 확인한다.

    장이 아직 안 끝났으면 오늘 날짜로 받은 "종가"는 확정 종가가 아니라 그 순간의
    체결가일 수 있으므로, 배치 로직이 이 값을 보고 당일 데이터를 캐시에 확정
    반영할지 결정한다.

    Returns: (still_open, today_kst) - today_kst는 YYYY-MM-DD.
    """
    d = _get_json("https://m.stock.naver.com/api/stock/005930/basic")
    traded_at = d.get("localTradedAt", "")  # e.g. "2026-08-21T10:00:22+09:00"
    today_kst = traded_at[:10] if traded_at else datetime.now().strftime("%Y-%m-%d")
    still_open = d.get("marketStatus") == "OPEN"
    return still_open, today_kst


def is_preferred_stock(ticker: str, name: str) -> bool:
    code_hint = len(ticker) == 6 and ticker[-1] != "0"
    name_hint = bool(name) and (name.endswith("우") or "우B" in name or "우C" in name)
    return code_hint or name_hint


def today_str() -> str:
    return datetime.now().strftime("%Y%m%d")
