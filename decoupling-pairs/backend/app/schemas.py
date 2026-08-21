from __future__ import annotations

from pydantic import BaseModel


class StockInfoOut(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str
    market_cap: float | None
    avg_trading_value: float | None
    index_corr: float | None = None
    index_reversal: bool = False
    volatility: float | None = None


class PairResultOut(BaseModel):
    stock_a: StockInfoOut
    stock_b: StockInfoOut
    correlation: float
    badge: str
    overlap_days: int
    reliability_warning: bool
    p_value: float | None = None
    adjusted_p_value: float | None = None
    fdr_passed: bool | None = None
    spearman_corr: float | None = None
    spearman_divergence_warning: bool = False
    downturn_corr: float | None = None
    downturn_days: int | None = None
    downturn_divergence_warning: bool = False
    residual_corr: float | None = None
    residual_illusion_warning: bool = False
    stability_badge: bool | None = None


class ScanResponse(BaseModel):
    pairs: list[PairResultOut]
    low_significance_pairs: list[PairResultOut]
    same_sector_highlights: list[PairResultOut]
    start: str
    end: str
    trading_days: int
    reliability_warning: bool
    as_of: str | None
    universe_size: int
    pool_size: int = 0  # 필터 통과 후 음의 상관관계 후보 전체 개수 (1티어+2티어)


class SearchResponse(BaseModel):
    pairs: list[PairResultOut]
    low_significance_pairs: list[PairResultOut]
    same_sector_highlights: list[PairResultOut]
    base: StockInfoOut
    start: str | None
    end: str | None
    full_period: bool
    reliability_warning: bool
    as_of: str | None
    pool_size: int = 0  # 필터 통과 후 음의 상관관계 후보 전체 개수 (1티어+2티어)


class TickerSuggestion(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str


class StatusResponse(BaseModel):
    last_update_date: str | None
    last_run_at: str | None
    last_run_status: str | None
    can_manual_refresh: bool
    next_manual_refresh_at: str | None
    can_quick_refresh: bool
    next_quick_refresh_at: str | None
    universe_size: int


class ChartPoint(BaseModel):
    date: str
    a: float | None
    b: float | None


class ChartResponse(BaseModel):
    a: StockInfoOut
    b: StockInfoOut
    points: list[ChartPoint]


class SimulatePoint(BaseModel):
    date: str
    value_a: float
    value_b: float
    value_combined: float
    pnl_a_pct: float
    pnl_b_pct: float
    pnl_combined_pct: float


class SimulateResponse(BaseModel):
    """'IF' 탭 - 페어 매수 시뮬레이션 결과."""

    a: StockInfoOut
    b: StockInfoOut
    start: str
    end: str
    amount_a: float
    amount_b: float
    shares_a: float
    shares_b: float
    entry_price_a: float
    entry_price_b: float
    points: list[SimulatePoint]
    final_value_a: float
    final_value_b: float
    final_value_combined: float
    final_pnl_a_pct: float
    final_pnl_b_pct: float
    final_pnl_combined_pct: float
    volatility_a: float
    volatility_b: float
    volatility_combined: float
    volatility_reduction_pct: float
