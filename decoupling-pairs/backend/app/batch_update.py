"""일별 데이터 갱신 배치.

- update_latest(): 캐시에 없는 최신 거래일들을 순차적으로 KRX에서 받아와 누적 append.
- backfill(): 최초 캐시 구축 시 과거 구간을 통째로 채워 넣는 용도.

두 경로 모두 결국 _ingest_one_day()를 재사용한다(스펙의 "일별 자동 갱신"
로직을 백필에도 그대로 재사용).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

import pandas as pd

from . import cache_store, config, krx_client

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


def main():
    parser = argparse.ArgumentParser(description="코스피/코스닥 종가 캐시 배치 갱신")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("update", help="최신 거래일까지 증분 갱신")

    bf = sub.add_parser("backfill", help="과거 구간 백필")
    bf.add_argument("--start", required=True, help="YYYYMMDD")
    bf.add_argument("--end", default=None, help="YYYYMMDD (기본: 최근 영업일)")

    args = parser.parse_args()
    if args.cmd == "update":
        result = update_latest()
    else:
        result = backfill(args.start, args.end)
    logger.info("결과: %s", result)


if __name__ == "__main__":
    sys.exit(main())
