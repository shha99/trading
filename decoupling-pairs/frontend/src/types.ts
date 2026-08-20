export interface StockInfo {
  ticker: string;
  name: string;
  market: string;
  sector: string;
  market_cap: number | null;
  avg_trading_value: number | null;
}

export type Badge = "cross_sector" | "same_sector_anomaly";

export interface PairResult {
  stock_a: StockInfo;
  stock_b: StockInfo;
  correlation: number;
  badge: Badge;
  overlap_days: number;
  reliability_warning: boolean;
}

export interface ScanResponse {
  pairs: PairResult[];
  start: string;
  end: string;
  trading_days: number;
  reliability_warning: boolean;
  as_of: string | null;
  universe_size: number;
}

export interface SearchResponse {
  pairs: PairResult[];
  base: StockInfo;
  start: string | null;
  end: string | null;
  full_period: boolean;
  reliability_warning: boolean;
  as_of: string | null;
}

export interface TickerSuggestion {
  ticker: string;
  name: string;
  market: string;
  sector: string;
}

export interface StatusResponse {
  last_update_date: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  can_manual_refresh: boolean;
  next_manual_refresh_at: string | null;
  universe_size: number;
}

export interface ChartPoint {
  date: string;
  a: number | null;
  b: number | null;
}

export interface ChartResponse {
  a: StockInfo;
  b: StockInfo;
  points: ChartPoint[];
}

export type SectorFilter = "all" | "cross" | "same";

export interface CommonFilters {
  topN: number;
  excludeManaged: boolean;
  excludeIlliquid: boolean;
  sectorFilter: SectorFilter;
  minMarketCap: string; // 억원 단위 사용자 입력, 빈 문자열이면 미적용
  minTradingValue: string; // 억원 단위
}
