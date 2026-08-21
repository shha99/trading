"""일별 데이터 갱신 배치.

두 가지 데이터 소스 경로를 제공한다:

- ``update_latest()`` / ``backfill()`` - KRX(data.krx.co.kr) 직접 호출 경로.
  한국 리전 네트워크에서만 동작한다(README 참고).
- ``refresh_via_naver()`` - Naver 금융 공개 엔드포인트 기반 경로. KRX 직접
  접속이 막힌 환경(해외 서버 등)에서도 동작하며, **기본 갱신 경로**로
  권장된다. 종목 단위로 기간 전체 시세를 한 번에 받아오는 방식이라
  백필/증분 갱신이 동일한 함수 하나로 처리된다.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import cache_store, config, krx_client, naver_client, sector_instruments

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _ingest_one_day(trading_date: str, prices: pd.DataFrame, values: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """하루치 스냅샷을 받아와 prices/values 패널에 append하고, 그날의 meta 조각을 반환."""
    snapshot = krx_client.fetch_market_snapshot(trading_date)
    if snapshot.empty:
        logger.warning("%s: 빈 스냅샷(휴장일이거나 조회 실패) - 건너뜀", trading_date)
        return prices, values, pd.DataFrame()

    ts = pd.to_datetime(trading_date, format="%Y%m%d")
    prices = cache_store.merge_new_day(prices, snapshot["close"], ts)
    values = cache_store.merge_new_day(values, snapshot["trading_value"], ts)

    day_meta = snapshot[["market", "market_cap", "listed_shares", "trading_value", "volume"]].copy()
    return prices, values, day_meta


def _rebuild_meta(prices: pd.DataFrame, values: pd.DataFrame, latest_day_meta: pd.DataFrame, trading_date: str) -> pd.DataFrame:
    """최신 스냅샷 + 최근 20거래일 평균거래대금 + 업종/명칭 정보를 합쳐 메타 테이블 생성."""
    sector_df = krx_client.fetch_sector_map(trading_date)

    tickers = latest_day_meta.index
    lookback = values.tail(config.LIQUIDITY_LOOKBACK_DAYS)
    avg_value = lookback.reindex(columns=tickers).mean(axis=0, skipna=True)

    meta = latest_day_meta.copy()
    meta["avg_trading_value"] = avg_value.reindex(meta.index)

    meta = meta.join(sector_df, how="left")

    missing_name = meta["name"].isna() if "name" in meta.columns else pd.Series(True, index=meta.index)
    for ticker in meta.index[missing_name.fillna(True)]:
        meta.loc[ticker, "name"] = krx_client.fetch_ticker_name(ticker)
    meta["sector"] = meta["sector"].fillna("기타")

    meta["is_preferred"] = [
        krx_client.is_preferred_stock(t, n) for t, n in zip(meta.index, meta["name"])
    ]
    # 최근 거래일 거래량 0 -> 거래정지 추정(pykrx는 관리종목 지정 여부를 직접 제공하지 않아
    # 거래정지는 거래량으로 근사하고, 관리종목 여부는 별도 공시 데이터 연동 전까지 False로 둔다)
    meta["is_halted"] = meta["volume"].fillna(0) <= 0
    meta["is_managed"] = False

    meta = meta.rename(columns={"market_cap": "market_cap", "listed_shares": "listed_shares"})
    keep_cols = [
        "name",
        "market",
        "sector",
        "market_cap",
        "avg_trading_value",
        "is_preferred",
        "is_managed",
        "is_halted",
        "listed_shares",
    ]
    return meta[keep_cols]


def update_latest(max_days: int = 10) -> dict:
    """캐시에 없는 최신 영업일까지 순차 갱신. 하루도 밀리지 않았으면 no-op.

    max_days: 한 번 호출로 최대 며칠까지 따라잡을지(장기간 미실행 시 과도한 호출 방지).
    """
    prices = cache_store.load_prices()
    values = cache_store.load_trading_value()

    last_cached = prices.index.max() if not prices.empty else None
    try:
        latest_business_day = krx_client.nearest_business_day(prev=True)
    except krx_client.KrxUnavailableError as exc:
        logger.error("영업일 조회 실패: %s", exc)
        return cache_store.save_state(
            last_run_at=datetime.now(config.KST).isoformat(),
            last_run_status="error",
            last_error=str(exc),
        )
    latest_ts = pd.to_datetime(latest_business_day, format="%Y%m%d")

    if last_cached is not None and last_cached >= latest_ts:
        logger.info("이미 최신 상태(%s). 갱신 없음.", last_cached.date())
        state = cache_store.save_state(
            last_update_date=str(last_cached.date()),
            last_run_at=datetime.now(config.KST).isoformat(),
            last_run_status="up_to_date",
        )
        return state

    start = (last_cached + timedelta(days=1)) if last_cached is not None else (
        latest_ts - timedelta(days=config.DEFAULT_BACKFILL_YEARS * 365)
    )
    try:
        business_days = krx_client.business_days_between(start, latest_ts)
    except krx_client.KrxUnavailableError as exc:
        logger.error("영업일 목록 조회 실패: %s", exc)
        return cache_store.save_state(
            last_run_at=datetime.now(config.KST).isoformat(),
            last_run_status="error",
            last_error=str(exc),
        )
    business_days = business_days[-max_days:] if len(business_days) > max_days else business_days

    if not business_days:
        logger.info("추가로 반영할 영업일 없음.")
        return cache_store.save_state(
            last_run_at=datetime.now(config.KST).isoformat(), last_run_status="up_to_date"
        )

    latest_meta_chunk = pd.DataFrame()
    ingested = []
    for d in business_days:
        try:
            prices, values, day_meta = _ingest_one_day(d, prices, values)
            if not day_meta.empty:
                latest_meta_chunk = day_meta
                ingested.append(d)
        except Exception:  # noqa: BLE001
            logger.exception("%s 갱신 실패 - 다음 스케줄에서 재시도", d)
            cache_store.save_state(
                last_run_at=datetime.now(config.KST).isoformat(),
                last_run_status="error",
                last_error=f"{d} ingest failed",
            )
            break

    cache_store.save_panel(prices, "prices")
    cache_store.save_panel(values, "value")

    if not latest_meta_chunk.empty:
        meta = _rebuild_meta(prices, values, latest_meta_chunk, ingested[-1])
        cache_store.save_meta(meta)

    last_cached_after = prices.index.max() if not prices.empty else None
    return cache_store.save_state(
        last_update_date=str(last_cached_after.date()) if last_cached_after is not None else None,
        last_run_at=datetime.now(config.KST).isoformat(),
        last_run_status="ok" if ingested else "no_new_data",
        days_ingested=ingested,
    )


def backfill(start: str, end: str | None = None) -> dict:
    """과거 구간 전체를 처음부터 채워 넣는다(최초 1회 or 장기 재구축용)."""
    end = end or krx_client.nearest_business_day(prev=True)
    business_days = krx_client.business_days_between(start, end)
    logger.info("백필 대상 영업일 수: %d (%s ~ %s)", len(business_days), start, end)

    prices = cache_store.load_prices()
    values = cache_store.load_trading_value()
    latest_meta_chunk = pd.DataFrame()
    ingested = []
    for i, d in enumerate(business_days, start=1):
        prices, values, day_meta = _ingest_one_day(d, prices, values)
        if not day_meta.empty:
            latest_meta_chunk = day_meta
            ingested.append(d)
        if i % 20 == 0:
            logger.info("백필 진행: %d/%d (%s)", i, len(business_days), d)
            cache_store.save_panel(prices, "prices")
            cache_store.save_panel(values, "value")

    cache_store.save_panel(prices, "prices")
    cache_store.save_panel(values, "value")
    if not latest_meta_chunk.empty:
        meta = _rebuild_meta(prices, values, latest_meta_chunk, ingested[-1])
        cache_store.save_meta(meta)

    return cache_store.save_state(
        last_update_date=ingested[-1] if ingested else None,
        last_run_at=datetime.now(config.KST).isoformat(),
        last_run_status="backfill_ok",
        days_ingested_count=len(ingested),
    )


def _compute_index_correlations(prices: pd.DataFrame, meta: pd.DataFrame, index_prices: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """종목별로 소속 시장(KOSPI/KOSDAQ) 지수와의 상관계수를 계산해 "지수 역행 종목" 판정.

    최근 INDEX_CORR_LOOKBACK_DAYS 구간의 로그수익률로 계산한다. 특정 종목이 지수 자체와도
    비정상적으로 강한 음의 상관관계를 보이면 데이터 문제(수정 안 된 급등락 등)이거나
    설계상 지수와 반대로 움직이는 상품일 가능성이 높다는 진단 신호로 쓴다.
    """
    if index_prices.empty or prices.empty:
        empty = pd.Series(index=meta.index, dtype=float)
        return empty, empty.fillna(False).astype(bool)

    tail_prices = prices.tail(config.INDEX_CORR_LOOKBACK_DAYS)
    tail_index = index_prices.reindex(tail_prices.index)
    stock_returns = np.log(tail_prices).diff()
    index_returns = np.log(tail_index).diff()

    corr = pd.Series(index=meta.index, dtype=float)
    for market, idx_col in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
        if idx_col not in index_returns.columns:
            continue
        tickers = meta.index[meta["market"] == market].intersection(stock_returns.columns)
        if len(tickers) == 0:
            continue
        corr.loc[tickers] = stock_returns[tickers].corrwith(index_returns[idx_col])

    reversal = corr <= config.INDEX_REVERSAL_CORR_THRESHOLD
    n_reversal = int(reversal.sum())
    if n_reversal:
        logger.warning("지수 역행 종목 %d개 발견 (기본 유니버스에서 제외, 토글로 강제 포함 가능)", n_reversal)
        for ticker in corr.index[reversal][:10]:
            name = meta.loc[ticker, "name"] if ticker in meta.index else ticker
            series = prices[ticker].dropna().tail(15) if ticker in prices.columns else pd.Series(dtype=float)
            logger.debug("[지수역행 디버그] %s(%s) corr=%.3f 최근가:\n%s", ticker, name, corr.get(ticker, float("nan")), series)
    return corr, reversal.fillna(False)


def _compute_volatility(prices: pd.DataFrame) -> pd.Series:
    """종목별 연환산 변동성(일별 로그수익률 표준편차 × √252) - 보유한 전체 히스토리 기준.

    저변동성(경기방어주) 오탐 필터에 쓰인다: BGF리테일/KT&G류 저변동성 종목은 평소
    거의 안 움직이다 배당 등 개별 이슈로 며칠만 튀는 패턴이 흔한데, 그 소수의 튀는
    날들이 상관계수 계산을 지배해 통계적으로 불안정한 강한 음의 상관관계를 만들어낸다.
    """
    if prices.empty:
        return pd.Series(index=prices.columns, dtype=float)
    returns = np.log(prices).diff()
    counts = returns.notna().sum()
    vol = returns.std(ddof=1) * np.sqrt(config.TRADING_DAYS_PER_YEAR)
    vol[counts < 30] = np.nan
    return vol


def _compute_long_halt_flags(values: pd.DataFrame) -> pd.Series:
    """4: "장기 거래정지 이력" 판정 - 최근 구간 내 거래량 0/결측이
    LONG_HALT_MIN_CONSECUTIVE_DAYS 이상 연속으로 이어진 적이 있으면 True.

    공식 거래정지 사유 코드는 Naver 공개 엔드포인트로 얻을 수 없어, 거래량 연속
    결측 구간을 프록시로 쓴다(장기 정지 중에는 체결 자체가 없으므로 거래량이 0).
    """
    if values.empty:
        return pd.Series(index=values.columns, dtype=bool)
    tail = values.tail(config.LONG_HALT_LOOKBACK_DAYS)
    is_zero = (tail.fillna(0) <= 0)

    def _max_run(col: pd.Series) -> int:
        run = (col != col.shift()).cumsum()
        return int(col.groupby(run).transform("size")[col].max()) if col.any() else 0

    max_runs = is_zero.apply(_max_run, axis=0)
    return max_runs >= config.LONG_HALT_MIN_CONSECUTIVE_DAYS


def _compute_concentration(prices: pd.DataFrame) -> pd.Series:
    """4: 변동성 집중도 - 절대값 기준 상위 K거래일이 전체 수익률 "에너지"(제곱합)에서
    차지하는 비중. 이 비중이 높을수록 소수의 이벤트일(감자/급등락 등)이 상관계수
    계산 전체를 지배한다는 뜻이라 신뢰도가 낮다(흥아해운류 오탐 방지).
    """
    if prices.empty:
        return pd.Series(index=prices.columns, dtype=float)
    returns = np.log(prices).diff()
    sq = returns.pow(2)
    total = sq.sum(skipna=True)
    top_k = sq.apply(lambda col: col.dropna().nlargest(config.CONCENTRATION_TOP_K_DAYS).sum(), axis=0)
    ratio = (top_k / total.replace(0, np.nan)).clip(upper=1.0)
    ratio[returns.notna().sum() < 30] = np.nan
    return ratio


def _refresh_sector_etfs(start: str, end: str) -> None:
    """신규 탭 "섹터/ETF 비교"용 큐레이션된 섹터 ETF 종가/메타 갱신."""
    meta = sector_instruments.build_etf_universe()
    if meta.empty:
        logger.warning("섹터 ETF 유니버스를 하나도 구성하지 못했습니다.")
        return
    histories = sector_instruments.fetch_etf_prices(start, end)
    if not histories:
        logger.warning("섹터 ETF 시세를 하나도 받아오지 못했습니다.")
        return
    prices = pd.DataFrame({t: df["close"] for t, df in histories.items()})
    cache_store.save_sector_etf_prices(prices)
    cache_store.save_sector_etf_meta(meta.reindex(prices.columns))
    logger.info("섹터 ETF 갱신 완료: %d종목", len(prices.columns))


def quick_refresh_tickers(tickers: list[str], max_workers: int = naver_client.DEFAULT_WORKERS) -> dict:
    """화면에 지금 보이는 종목 몇 개만 즉시 재조회하는 가벼운 새로고침.

    전종목 배치(``refresh_via_naver``, 수 분 소요)와 달리 종목당 요청 1번씩만
    하므로 몇 초 안에 끝난다. 유니버스/메타/지수는 건드리지 않고 prices/values
    캐시만 갱신한다.
    """
    prices = cache_store.load_prices()
    values = cache_store.load_trading_value()
    today = naver_client.today_str()
    start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    histories = naver_client.fetch_histories(tickers, start, today, max_workers=max_workers)
    if not histories:
        raise naver_client.NaverUnavailableError("빠른 새로고침: 시세를 하나도 받아오지 못했습니다.")

    try:
        still_open, today_kst = naver_client.market_status()
    except naver_client.NaverUnavailableError:
        still_open, today_kst = False, today
    if still_open:
        for df in histories.values():
            if today_kst in df.index.astype(str).str[:10].values:
                df.drop(df.index[df.index.astype(str).str[:10] == today_kst], inplace=True)

    new_prices = pd.DataFrame({t: df["close"] for t, df in histories.items()})
    new_values = pd.DataFrame({t: df["close"] * df["volume"] for t, df in histories.items()})

    prices = new_prices if prices.empty else prices.combine_first(new_prices)
    prices.update(new_prices)
    values = new_values if values.empty else values.combine_first(new_values)
    values.update(new_values)
    cache_store.save_panel(prices, "prices")
    cache_store.save_panel(values, "value")

    return cache_store.save_state(
        last_quick_refresh_at=datetime.now(config.KST).isoformat(),
        last_quick_refresh_tickers=list(histories.keys()),
    )


def refresh_via_naver(years_back: int = config.DEFAULT_BACKFILL_YEARS, max_workers: int = naver_client.DEFAULT_WORKERS) -> dict:
    """Naver 금융 기반 갱신 - 백필과 증분 갱신을 겸한다.

    1) 유니버스(종목/시장/섹터/시총/거래대금) 갱신
    2) 캐시에 이미 있는 종목은 마지막 캐시일 다음날부터, 없는 종목은
       ``years_back``년 전부터 오늘까지 종목별로 시세를 병렬 수집
    3) 기존 prices/values 패널에 병합(기존 데이터는 덮어쓰지 않고 새 날짜만 추가)
    """
    prices = cache_store.load_prices()
    values = cache_store.load_trading_value()
    old_meta = cache_store.load_meta()  # 4: structural_break는 증분 갱신에도 한번 감지되면 유지되는 이력이라 이전 값을 이어받는다

    try:
        universe = naver_client.build_universe(max_workers=max_workers)
    except naver_client.NaverUnavailableError as exc:
        logger.error("유니버스 조회 실패: %s", exc)
        return cache_store.save_state(
            last_run_at=datetime.now(config.KST).isoformat(), last_run_status="error", last_error=str(exc)
        )

    today = naver_client.today_str()
    default_start = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y%m%d")

    if prices.empty:
        fetch_plan = {t: (default_start, today) for t in universe.index}
    else:
        last_cached = prices.index.max()
        # last_cached + 1일이 아니라 며칠 더 앞으로 잡아 다시 받는다: 직전 실행이 장중에
        # 돌았다면 그날 캐시된 종가는 확정 종가가 아니라 그 순간의 체결가였을 수 있고,
        # Naver 차트 API는 (시작,종료) 구간 길이와 무관하게 종목당 요청 1번이라 비용도
        # 늘지 않으므로 재조회로 자연스럽게 정정한다.
        incr_start = (last_cached - timedelta(days=5)).strftime("%Y%m%d")
        new_tickers = universe.index.difference(prices.columns)
        fetch_plan = {t: (incr_start, today) for t in universe.index.intersection(prices.columns)}
        fetch_plan.update({t: (default_start, today) for t in new_tickers})

    # Naver 차트 API는 종목당 (시작,종료) 구간이 달라도 한 번에 하나씩만 받을 수 있어
    # 구간별로 그룹핑해 fetch_histories를 나눠 호출한다(대부분 같은 구간이라 보통 1~2그룹).
    groups: dict[tuple[str, str], list[str]] = {}
    for t, rng in fetch_plan.items():
        groups.setdefault(rng, []).append(t)

    histories: dict[str, pd.DataFrame] = {}
    for (start, end), tickers in groups.items():
        logger.info("시세 수집: %d종목 (%s ~ %s)", len(tickers), start, end)
        histories.update(naver_client.fetch_histories(tickers, start, end, max_workers=max_workers))

    if not histories:
        logger.warning("수집된 시세가 없습니다.")
        return cache_store.save_state(
            last_run_at=datetime.now(config.KST).isoformat(), last_run_status="error", last_error="no histories fetched"
        )

    # 장이 아직 안 끝났으면 오늘 날짜로 받은 값은 확정 종가가 아니라 장중 체결가이므로,
    # 캐시에 확정 데이터로 반영하지 않고 다음 실행(장 마감 후)까지 보류한다.
    try:
        still_open, today_kst = naver_client.market_status()
    except naver_client.NaverUnavailableError:
        still_open, today_kst = False, today  # 상태 확인 실패 시 보수적으로 그냥 반영(다음 실행에서 재조회로 정정됨)
    if still_open:
        logger.info("장중(%s) - 당일 데이터는 확정 종가가 아니므로 이번 실행에서 제외합니다.", today_kst)
        for df in histories.values():
            if today_kst in df.index.astype(str).str[:10].values:
                df.drop(df.index[df.index.astype(str).str[:10] == today_kst], inplace=True)

    close_cols = {t: df["close"] for t, df in histories.items()}
    volume_cols = {t: df["close"] * df["volume"] for t, df in histories.items()}  # 거래대금 근사(종가*거래량)
    new_prices = pd.DataFrame(close_cols)
    new_values = pd.DataFrame(volume_cols)

    prices = new_prices if prices.empty else prices.combine_first(new_prices)
    prices.update(new_prices)
    values = new_values if values.empty else values.combine_first(new_values)
    values.update(new_values)

    cache_store.save_panel(prices, "prices")
    cache_store.save_panel(values, "value")

    # KOSPI/KOSDAQ 지수도 같은 구간으로 함께 갱신 (하락일 조건부 상관계수, 시장베타 잔차,
    # 지수 역행 종목 판정에 공통으로 쓰인다).
    index_prices = cache_store.load_index()
    idx_start = default_start if index_prices.empty else (index_prices.index.max() - timedelta(days=5)).strftime("%Y%m%d")
    idx_new = {}
    for idx_name in ("KOSPI", "KOSDAQ"):
        try:
            s = naver_client.fetch_index_history(idx_name, idx_start, today)
        except naver_client.NaverUnavailableError:
            logger.warning("%s 지수 조회 실패 - 이번 실행에서는 건너뜀", idx_name)
            continue
        if still_open and today_kst in s.index.astype(str).str[:10].values:
            s = s[s.index.astype(str).str[:10] != today_kst]
        idx_new[idx_name] = s
    if idx_new:
        new_index_df = pd.DataFrame(idx_new)
        index_prices = new_index_df if index_prices.empty else index_prices.combine_first(new_index_df)
        index_prices.update(new_index_df)
        cache_store.save_index(index_prices)

    fetched_tickers = list(histories.keys())
    lookback = values.tail(config.LIQUIDITY_LOOKBACK_DAYS)
    avg_value = lookback.reindex(columns=universe.index).mean(axis=0, skipna=True)

    meta = universe.copy()
    meta["avg_trading_value"] = avg_value.reindex(meta.index)
    meta["is_preferred"] = [naver_client.is_preferred_stock(t, n) for t, n in zip(meta.index, meta["name"])]
    last_row = prices.reindex(columns=meta.index).iloc[-1] if not prices.empty else pd.Series(dtype=float)
    meta["is_halted"] = last_row.isna().reindex(meta.index).fillna(True)
    meta["is_managed"] = False
    meta["listed_shares"] = None

    index_corr, index_reversal = _compute_index_correlations(prices, meta, index_prices)
    meta["index_corr"] = index_corr
    meta["index_reversal"] = index_reversal
    meta["volatility"] = _compute_volatility(prices).reindex(meta.index)

    # 4: 이상치/구조적 단절 종목 필터
    # structural_break는 이번 실행에서 새로 받은 구간(증분이면 최근 며칠뿐)에서 감지된 것만
    # 반영하면 과거에 있었던 감자/액면병합 이력을 놓치므로, 이전 메타의 값과 OR로 합쳐
    # "한번이라도 감지되면 계속 유지"되는 이력으로 취급한다.
    new_structural = pd.Series(
        {t: bool(df.attrs.get("structural_break", False)) for t, df in histories.items()}
    )
    old_structural = old_meta["structural_break"] if "structural_break" in old_meta.columns else pd.Series(dtype=bool)
    meta["structural_break"] = (
        old_structural.reindex(meta.index).fillna(False).astype(bool)
        | new_structural.reindex(meta.index).fillna(False).astype(bool)
    )
    meta["long_halt_history"] = _compute_long_halt_flags(values).reindex(meta.index)
    meta["concentration_ratio"] = _compute_concentration(prices).reindex(meta.index)

    meta = meta[
        [
            "name", "market", "sector", "market_cap", "avg_trading_value",
            "is_preferred", "is_managed", "is_halted", "listed_shares",
            "index_corr", "index_reversal", "volatility",
            "structural_break", "long_halt_history", "concentration_ratio",
        ]
    ]
    cache_store.save_meta(meta)

    # 신규 탭 "섹터/ETF 비교" - 큐레이션된 섹터 ETF 종가도 같은 실행에서 함께 갱신한다
    # (22종목뿐이라 비용이 작음). 실패해도 메인 갱신 자체는 이미 끝났으니 로그만 남기고 계속.
    try:
        _refresh_sector_etfs(default_start, today)
    except Exception:
        logger.exception("섹터 ETF 갱신 실패 - 다음 배치에서 재시도")

    last_cached_after = prices.index.max() if not prices.empty else None
    return cache_store.save_state(
        last_update_date=str(last_cached_after.date()) if last_cached_after is not None else None,
        last_run_at=datetime.now(config.KST).isoformat(),
        last_run_status="ok",
        source="naver",
        tickers_updated=len(fetched_tickers),
        universe_size=len(meta),
    )


def main():
    parser = argparse.ArgumentParser(description="코스피/코스닥 종가 캐시 배치 갱신")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("update", help="[KRX 직접] 최신 거래일까지 증분 갱신 - 한국 리전에서만 동작")

    bf = sub.add_parser("backfill", help="[KRX 직접] 과거 구간 백필 - 한국 리전에서만 동작")
    bf.add_argument("--start", required=True, help="YYYYMMDD")
    bf.add_argument("--end", default=None, help="YYYYMMDD (기본: 최근 영업일)")

    nv = sub.add_parser("naver-refresh", help="[권장] Naver 금융 기반 백필+증분 갱신 - 어디서나 동작")
    nv.add_argument("--years-back", type=int, default=config.DEFAULT_BACKFILL_YEARS)

    args = parser.parse_args()
    if args.cmd == "update":
        result = update_latest()
    elif args.cmd == "backfill":
        result = backfill(args.start, args.end)
    else:
        result = refresh_via_naver(years_back=args.years_back)
    logger.info("결과: %s", result)


if __name__ == "__main__":
    sys.exit(main())
