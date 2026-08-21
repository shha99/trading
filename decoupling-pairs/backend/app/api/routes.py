from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from .. import batch_update, cache_store, config, correlation, schemas
from ..krx_client import KrxUnavailableError
from ..naver_client import NaverUnavailableError

router = APIRouter(prefix="/api")


def _to_out(p: correlation.PairResult) -> schemas.PairResultOut:
    return schemas.PairResultOut(
        stock_a=schemas.StockInfoOut(**vars(p.stock_a)),
        stock_b=schemas.StockInfoOut(**vars(p.stock_b)),
        correlation=p.correlation,
        badge=p.badge,
        overlap_days=p.overlap_days,
        reliability_warning=p.reliability_warning,
        p_value=p.p_value,
        adjusted_p_value=p.adjusted_p_value,
        fdr_passed=p.fdr_passed,
        spearman_corr=p.spearman_corr,
        spearman_divergence_warning=p.spearman_divergence_warning,
        downturn_corr=p.downturn_corr,
        downturn_days=p.downturn_days,
        downturn_divergence_warning=p.downturn_divergence_warning,
        residual_corr=p.residual_corr,
        residual_illusion_warning=p.residual_illusion_warning,
        stability_badge=p.stability_badge,
    )


def _parse_date(d: str, field: str) -> pd.Timestamp:
    try:
        return pd.to_datetime(d)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"'{field}' 날짜 형식이 올바르지 않습니다: {d}") from exc


def _refresh_gate(state: dict, key_prefix: str, interval: timedelta) -> tuple[bool, str | None]:
    last_at = state.get(f"{key_prefix}_at")
    if not last_at:
        return True, None
    try:
        last_dt = datetime.fromisoformat(last_at)
    except ValueError:
        return True, None
    remaining = (last_dt + interval) - datetime.now(last_dt.tzinfo)
    if remaining.total_seconds() > 0:
        return False, (last_dt + interval).isoformat()
    return True, None


@router.get("/status", response_model=schemas.StatusResponse)
def get_status():
    state = cache_store.load_state()
    meta = cache_store.load_meta()
    can_refresh, next_at = _refresh_gate(state, "last_run", timedelta(hours=config.MANUAL_REFRESH_MIN_INTERVAL_HOURS))
    can_quick, next_quick_at = _refresh_gate(
        state, "last_quick_refresh", timedelta(seconds=config.QUICK_REFRESH_MIN_INTERVAL_SECONDS)
    )
    return schemas.StatusResponse(
        last_update_date=state.get("last_update_date"),
        last_run_at=state.get("last_run_at"),
        last_run_status=state.get("last_run_status"),
        can_manual_refresh=can_refresh,
        next_manual_refresh_at=next_at,
        can_quick_refresh=can_quick,
        next_quick_refresh_at=next_quick_at,
        universe_size=len(meta),
    )


@router.post("/refresh", response_model=schemas.StatusResponse)
def manual_refresh():
    """전종목 배치 재수집 (느림, 수 분 - 하루 1회로 제한)."""
    status = get_status()
    if not status.can_manual_refresh:
        raise HTTPException(
            status_code=429,
            detail=f"오늘은 이미 새로고침했습니다. 다음 가능 시각: {status.next_manual_refresh_at}",
        )
    try:
        batch_update.refresh_via_naver()
    except (KrxUnavailableError, NaverUnavailableError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return get_status()


@router.post("/quick-refresh", response_model=schemas.StatusResponse)
def quick_refresh(tickers: str = Query(..., description="쉼표로 구분된 종목코드 목록 (현재 화면에 보이는 종목만)")):
    """화면에 지금 보이는 종목만 즉시 재조회하는 가벼운 새로고침 (1분에 1회로 제한).

    전종목 배치(수 분 소요)와 달리 몇 초 안에 끝나며, 이후 프론트엔드가 바로
    /api/scan 또는 /api/search를 다시 호출해 "즉시 재스크리닝"을 완성한다.
    """
    status = get_status()
    if not status.can_quick_refresh:
        raise HTTPException(
            status_code=429,
            detail=f"잠시 후 다시 시도해주세요. 다음 가능 시각: {status.next_quick_refresh_at}",
        )
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="tickers가 비어 있습니다.")
    try:
        batch_update.quick_refresh_tickers(ticker_list)
    except NaverUnavailableError as exc:
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
    exclude_index_reversal: bool = Query(True),
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
    index_prices = cache_store.load_index()
    if prices.empty:
        raise HTTPException(status_code=503, detail="아직 캐시된 데이터가 없습니다. 먼저 배치 갱신을 실행하세요.")

    result = correlation.scan_pairs(
        prices,
        meta,
        start_ts,
        end_ts,
        top_n=top_n,
        exclude_managed=exclude_managed,
        exclude_illiquid=exclude_illiquid,
        exclude_index_reversal=exclude_index_reversal,
        sector_filter=sector_filter,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_trading_value=min_trading_value,
        index_prices=index_prices,
    )
    state = cache_store.load_state()
    return schemas.ScanResponse(
        pairs=[_to_out(p) for p in result["pairs"]],
        low_significance_pairs=[_to_out(p) for p in result["low_significance_pairs"]],
        same_sector_highlights=[_to_out(p) for p in result["same_sector_highlights"]],
        start=start,
        end=end,
        trading_days=result["trading_days"],
        reliability_warning=result["reliability_warning"],
        as_of=state.get("last_update_date"),
        universe_size=result["universe_size"],
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
    exclude_index_reversal: bool = Query(True),
    sector_filter: str = Query("all", pattern="^(all|cross|same)$"),
    min_market_cap: float | None = Query(None, ge=0),
    max_market_cap: float | None = Query(None, ge=0),
    min_trading_value: float | None = Query(None, ge=0),
):
    prices = cache_store.load_prices()
    meta = cache_store.load_meta()
    index_prices = cache_store.load_index()
    if prices.empty:
        raise HTTPException(status_code=503, detail="아직 캐시된 데이터가 없습니다. 먼저 배치 갱신을 실행하세요.")
    ticker = ticker.strip()
    if ticker not in prices.columns:
        raise HTTPException(status_code=404, detail=f"종목 '{ticker}'을(를) 캐시에서 찾을 수 없습니다.")

    if not full_period and (start is None or end is None):
        raise HTTPException(status_code=400, detail="전체 기간이 아니면 start/end를 모두 지정해야 합니다.")

    start_ts = _parse_date(start, "start") if start else None
    end_ts = _parse_date(end, "end") if end else None

    result = correlation.search_correlations(
        ticker,
        prices,
        meta,
        start_ts,
        end_ts,
        full_period=full_period,
        top_n=top_n,
        exclude_managed=exclude_managed,
        exclude_illiquid=exclude_illiquid,
        exclude_index_reversal=exclude_index_reversal,
        sector_filter=sector_filter,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_trading_value=min_trading_value,
        index_prices=index_prices,
    )
    state = cache_store.load_state()
    base_info = correlation.stock_info(ticker, meta)
    return schemas.SearchResponse(
        pairs=[_to_out(p) for p in result["pairs"]],
        low_significance_pairs=[_to_out(p) for p in result["low_significance_pairs"]],
        same_sector_highlights=[_to_out(p) for p in result["same_sector_highlights"]],
        base=schemas.StockInfoOut(**vars(base_info)),
        start=start,
        end=end,
        full_period=full_period,
        reliability_warning=result["any_short_sample"],
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


@router.get("/simulate", response_model=schemas.SimulateResponse)
def simulate(
    a: str = Query(...),
    b: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    amount_a: float = Query(10_000_000, gt=0),
    amount_b: float = Query(10_000_000, gt=0),
):
    """'IF' 탭 - 시작일에 A/B를 각각 지정 금액만큼 매수했다면의 손익 시뮬레이션.

    매수 수량 = 투자금액 ÷ 시작일 종가, 정수 주 단위로 내림(실제로 살 수 있는 수량).
    """
    prices = cache_store.load_prices()
    meta = cache_store.load_meta()
    start_ts, end_ts = _parse_date(start, "start"), _parse_date(end, "end")
    if start_ts > end_ts:
        raise HTTPException(status_code=400, detail="시작일이 종료일보다 늦을 수 없습니다.")
    for t in (a, b):
        if t not in prices.columns:
            raise HTTPException(status_code=404, detail=f"종목 '{t}'을(를) 캐시에서 찾을 수 없습니다.")

    window = prices.loc[(prices.index >= start_ts) & (prices.index <= end_ts), [a, b]].dropna(how="all")
    window = window.ffill().dropna()
    if len(window) < 2:
        raise HTTPException(status_code=400, detail="해당 구간에 두 종목 모두의 가격 데이터가 부족합니다.")

    entry_price_a, entry_price_b = float(window[a].iloc[0]), float(window[b].iloc[0])
    shares_a = np.floor(amount_a / entry_price_a)
    shares_b = np.floor(amount_b / entry_price_b)
    if shares_a < 1 or shares_b < 1:
        raise HTTPException(status_code=400, detail="투자금액이 너무 작아 1주도 매수할 수 없습니다.")

    value_a = window[a] * shares_a
    value_b = window[b] * shares_b
    value_combined = value_a + value_b
    invested_a, invested_b = shares_a * entry_price_a, shares_b * entry_price_b
    invested_combined = invested_a + invested_b

    pnl_a = (value_a / invested_a - 1) * 100
    pnl_b = (value_b / invested_b - 1) * 100
    pnl_combined = (value_combined / invested_combined - 1) * 100

    points = [
        schemas.SimulatePoint(
            date=idx.strftime("%Y-%m-%d"),
            value_a=float(value_a[idx]),
            value_b=float(value_b[idx]),
            value_combined=float(value_combined[idx]),
            pnl_a_pct=float(pnl_a[idx]),
            pnl_b_pct=float(pnl_b[idx]),
            pnl_combined_pct=float(pnl_combined[idx]),
        )
        for idx in window.index
    ]

    ret_a = np.log(window[a]).diff().dropna()
    ret_b = np.log(window[b]).diff().dropna()
    ret_combined = np.log(value_combined).diff().dropna()
    vol_a, vol_b, vol_combined = float(ret_a.std()), float(ret_b.std()), float(ret_combined.std())
    avg_individual_vol = (vol_a + vol_b) / 2
    vol_reduction = (1 - vol_combined / avg_individual_vol) * 100 if avg_individual_vol else 0.0

    return schemas.SimulateResponse(
        a=schemas.StockInfoOut(**vars(correlation.stock_info(a, meta))),
        b=schemas.StockInfoOut(**vars(correlation.stock_info(b, meta))),
        start=start,
        end=end,
        amount_a=amount_a,
        amount_b=amount_b,
        shares_a=shares_a,
        shares_b=shares_b,
        entry_price_a=entry_price_a,
        entry_price_b=entry_price_b,
        points=points,
        final_value_a=float(value_a.iloc[-1]),
        final_value_b=float(value_b.iloc[-1]),
        final_value_combined=float(value_combined.iloc[-1]),
        final_pnl_a_pct=float(pnl_a.iloc[-1]),
        final_pnl_b_pct=float(pnl_b.iloc[-1]),
        final_pnl_combined_pct=float(pnl_combined.iloc[-1]),
        volatility_a=vol_a,
        volatility_b=vol_b,
        volatility_combined=vol_combined,
        volatility_reduction_pct=vol_reduction,
    )


def _none_if_nan(v) -> float | None:
    return None if pd.isna(v) else float(v)
