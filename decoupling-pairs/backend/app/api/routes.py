from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from .. import batch_update, cache_store, config, correlation, schemas
from ..krx_client import KrxUnavailableError

router = APIRouter(prefix="/api")


def _to_out(p: correlation.PairResult) -> schemas.PairResultOut:
    return schemas.PairResultOut(
        stock_a=schemas.StockInfoOut(**vars(p.stock_a)),
        stock_b=schemas.StockInfoOut(**vars(p.stock_b)),
        correlation=p.correlation,
        badge=p.badge,
        overlap_days=p.overlap_days,
        reliability_warning=p.reliability_warning,
    )


def _parse_date(d: str, field: str) -> pd.Timestamp:
    try:
        return pd.to_datetime(d)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"'{field}' 날짜 형식이 올바르지 않습니다: {d}") from exc


@router.get("/status", response_model=schemas.StatusResponse)
def get_status():
    state = cache_store.load_state()
    meta = cache_store.load_meta()
    last_run_at = state.get("last_run_at")
    can_refresh = True
    next_at = None
    if last_run_at:
        try:
            last_dt = datetime.fromisoformat(last_run_at)
            delta = timedelta(hours=config.MANUAL_REFRESH_MIN_INTERVAL_HOURS)
            if datetime.now(last_dt.tzinfo) - last_dt < delta:
                can_refresh = False
                next_at = (last_dt + delta).isoformat()
        except ValueError:
            pass
    return schemas.StatusResponse(
        last_update_date=state.get("last_update_date"),
        last_run_at=last_run_at,
        last_run_status=state.get("last_run_status"),
        can_manual_refresh=can_refresh,
        next_manual_refresh_at=next_at,
        universe_size=len(meta),
    )


@router.post("/refresh", response_model=schemas.StatusResponse)
def manual_refresh():
    status = get_status()
    if not status.can_manual_refresh:
        raise HTTPException(
            status_code=429,
            detail=f"오늘은 이미 새로고침했습니다. 다음 가능 시각: {status.next_manual_refresh_at}",
        )
    try:
        batch_update.update_latest()
    except KrxUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return get_status()


@router.get("/universe/search", response_model=list[schemas.TickerSuggestion])
def search_universe(q: str = Query(..., min_length=1), limit: int = Query(15, ge=1, le=50)):
    meta = cache_store.load_meta()
    if meta.empty:
        return []
    q_norm = q.strip().lower()
    name_match = meta["name"].astype(str).str.lower().str.contains(q_norm, na=False)
    code_match = meta.index.astype(str).str.contains(q_norm, na=False)
    hits = meta[name_match | code_match].head(limit)
    return [
        schemas.TickerSuggestion(ticker=t, name=row["name"], market=row["market"], sector=row["sector"])
        for t, row in hits.iterrows()
    ]


@router.get("/scan", response_model=schemas.ScanResponse)
def scan(
    start: str = Query(...),
    end: str = Query(...),
    top_n: int = Query(config.DEFAULT_TOP_N, ge=config.MIN_TOP_N, le=config.MAX_TOP_N),
    exclude_managed: bool = Query(True),
    exclude_illiquid: bool = Query(True),
    sector_filter: str = Query("all", pattern="^(all|cross|same)$"),
    min_market_cap: float | None = Query(None, ge=0),
    max_market_cap: float | None = Query(None, ge=0),
    min_trading_value: float | None = Query(None, ge=0),
):
    start_ts, end_ts = _parse_date(start, "start"), _parse_date(end, "end")
    if start_ts > end_ts:
        raise HTTPException(status_code=400, detail="시작일이 종료일보다 늦을 수 없습니다.")

    prices = cache_store.load_prices()
    meta = cache_store.load_meta()
    if prices.empty:
        raise HTTPException(status_code=503, detail="아직 캐시된 데이터가 없습니다. 먼저 배치 갱신을 실행하세요.")

    pairs, warn, trading_days = correlation.scan_pairs(
        prices,
        meta,
        start_ts,
        end_ts,
        top_n=top_n,
        exclude_managed=exclude_managed,
        exclude_illiquid=exclude_illiquid,
        sector_filter=sector_filter,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_trading_value=min_trading_value,
    )
    state = cache_store.load_state()
    return schemas.ScanResponse(
        pairs=[_to_out(p) for p in pairs],
        start=start,
        end=end,
        trading_days=trading_days,
        reliability_warning=warn,
        as_of=state.get("last_update_date"),
        universe_size=len(correlation.eligible_universe(meta, exclude_managed, exclude_illiquid)),
    )


@router.get("/search", response_model=schemas.SearchResponse)
def search(
    ticker: str = Query(...),
    start: str | None = Query(None),
    end: str | None = Query(None),
    full_period: bool = Query(False),
    top_n: int = Query(config.DEFAULT_TOP_N, ge=config.MIN_TOP_N, le=config.MAX_TOP_N),
    exclude_managed: bool = Query(True),
    exclude_illiquid: bool = Query(True),
    sector_filter: str = Query("all", pattern="^(all|cross|same)$"),
    min_market_cap: float | None = Query(None, ge=0),
    max_market_cap: float | None = Query(None, ge=0),
    min_trading_value: float | None = Query(None, ge=0),
):
    prices = cache_store.load_prices()
    meta = cache_store.load_meta()
    if prices.empty:
        raise HTTPException(status_code=503, detail="아직 캐시된 데이터가 없습니다. 먼저 배치 갱신을 실행하세요.")
    ticker = ticker.strip()
    if ticker not in prices.columns:
        raise HTTPException(status_code=404, detail=f"종목 '{ticker}'을(를) 캐시에서 찾을 수 없습니다.")

    if not full_period and (start is None or end is None):
        raise HTTPException(status_code=400, detail="전체 기간이 아니면 start/end를 모두 지정해야 합니다.")

    start_ts = _parse_date(start, "start") if start else None
    end_ts = _parse_date(end, "end") if end else None

    pairs, warn = correlation.search_correlations(
        ticker,
        prices,
        meta,
        start_ts,
        end_ts,
        full_period=full_period,
        top_n=top_n,
        exclude_managed=exclude_managed,
        exclude_illiquid=exclude_illiquid,
        sector_filter=sector_filter,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_trading_value=min_trading_value,
    )
    state = cache_store.load_state()
    base_info = correlation.stock_info(ticker, meta)
    return schemas.SearchResponse(
        pairs=[_to_out(p) for p in pairs],
        base=schemas.StockInfoOut(**vars(base_info)),
        start=start,
        end=end,
        full_period=full_period,
        reliability_warning=warn,
        as_of=state.get("last_update_date"),
    )


@router.get("/chart", response_model=schemas.ChartResponse)
def chart(
    a: str = Query(...),
    b: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
):
    prices = cache_store.load_prices()
    meta = cache_store.load_meta()
    start_ts, end_ts = _parse_date(start, "start"), _parse_date(end, "end")
    for t in (a, b):
        if t not in prices.columns:
            raise HTTPException(status_code=404, detail=f"종목 '{t}'을(를) 캐시에서 찾을 수 없습니다.")

    window = prices.loc[(prices.index >= start_ts) & (prices.index <= end_ts), [a, b]]
    window = window.dropna(how="all")
    normed = window / window.bfill().iloc[0] * 100.0

    points = [
        schemas.ChartPoint(
            date=idx.strftime("%Y-%m-%d"),
            a=_none_if_nan(row[a]),
            b=_none_if_nan(row[b]),
        )
        for idx, row in normed.iterrows()
    ]
    return schemas.ChartResponse(
        a=schemas.StockInfoOut(**vars(correlation.stock_info(a, meta))),
        b=schemas.StockInfoOut(**vars(correlation.stock_info(b, meta))),
        points=points,
    )


def _none_if_nan(v) -> float | None:
    return None if pd.isna(v) else float(v)
