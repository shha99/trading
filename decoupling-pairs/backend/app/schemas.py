from __future__ import annotations

from pydantic import BaseModel


class StockInfoOut(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str
    market_cap: float | None
    avg_trading_value: float | None


class PairResultOut(BaseModel):
    stock_a: StockInfoOut
    stock_b: StockInfoOut
    correlation: float
    badge: str
    overlap_days: int
    reliability_warning: bool


class ScanResponse(BaseModel):
    pairs: list[PairResultOut]
    start: str
    end: str
    trading_days: int
    reliability_warning: bool
    as_of: str | None
    universe_size: int


class SearchResponse(BaseModel):
    pairs: list[PairResultOut]
    base: StockInfoOut
    start: str | None
    end: str | None
    full_period: bool
    reliability_warning: bool
    as_of: str | None


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
    universe_size: int


class ChartPoint(BaseModel):
    date: str
    a: float | None
    b: float | None


class ChartResponse(BaseModel):
    a: StockInfoOut
    b: StockInfoOut
    points: list[ChartPoint]
