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

import pandas as pd

from . import cache_store, config, krx_client, naver_client

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


def refresh_via_naver(years_back: int = config.DEFAULT_BACKFILL_YEARS, max_workers: int = naver_client.DEFAULT_WORKERS) -> dict:
    """Naver 금융 기반 갱신 - 백필과 증분 갱신을 겸한다.

    1) 유니버스(종목/시장/섹터/시총/거래대금) 갱신
    2) 캐시에 이미 있는 종목은 마지막 캐시일 다음날부터, 없는 종목은
       ``years_back``년 전부터 오늘까지 종목별로 시세를 병렬 수집
    3) 기존 prices/values 패널에 병합(기존 데이터는 덮어쓰지 않고 새 날짜만 추가)
    """
    prices = cache_store.load_prices()
    values = cache_store.load_trading_value()

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
        incr_start = (last_cached + timedelta(days=1)).strftime("%Y%m%d")
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
    meta = meta[["name", "market", "sector", "market_cap", "avg_trading_value", "is_preferred", "is_managed", "is_halted", "listed_shares"]]
    cache_store.save_meta(meta)

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
