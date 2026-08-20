import type {
  ChartResponse,
  CommonFilters,
  ScanResponse,
  SearchResponse,
  StatusResponse,
  TickerSuggestion,
} from "./types";

const BASE = "/api";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

// 사용자 입력(억원 단위) -> 원 단위 쿼리 파라미터로 변환
function appendCommonFilters(params: URLSearchParams, f: CommonFilters) {
  params.set("top_n", String(f.topN));
  params.set("exclude_managed", String(f.excludeManaged));
  params.set("exclude_illiquid", String(f.excludeIlliquid));
  params.set("sector_filter", f.sectorFilter);
  if (f.minMarketCap.trim() !== "") {
    params.set("min_market_cap", String(Number(f.minMarketCap) * 1e8));
  }
  if (f.minTradingValue.trim() !== "") {
    params.set("min_trading_value", String(Number(f.minTradingValue) * 1e8));
  }
}

export function fetchStatus(): Promise<StatusResponse> {
  return getJson(`${BASE}/status`);
}

export async function triggerRefresh(): Promise<StatusResponse> {
  const res = await fetch(`${BASE}/refresh`, { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `새로고침 실패 (${res.status})`);
  }
  return res.json();
}

export function searchUniverse(q: string): Promise<TickerSuggestion[]> {
  if (!q.trim()) return Promise.resolve([]);
  const params = new URLSearchParams({ q, limit: "15" });
  return getJson(`${BASE}/universe/search?${params.toString()}`);
}

export function fetchScan(
  start: string,
  end: string,
  filters: CommonFilters
): Promise<ScanResponse> {
  const params = new URLSearchParams({ start, end });
  appendCommonFilters(params, filters);
  return getJson(`${BASE}/scan?${params.toString()}`);
}

export function fetchSearch(
  ticker: string,
  opts: { start?: string; end?: string; fullPeriod: boolean },
  filters: CommonFilters
): Promise<SearchResponse> {
  const params = new URLSearchParams({ ticker, full_period: String(opts.fullPeriod) });
  if (!opts.fullPeriod) {
    if (opts.start) params.set("start", opts.start);
    if (opts.end) params.set("end", opts.end);
  }
  appendCommonFilters(params, filters);
  return getJson(`${BASE}/search?${params.toString()}`);
}

export function fetchChart(a: string, b: string, start: string, end: string): Promise<ChartResponse> {
  const params = new URLSearchParams({ a, b, start, end });
  return getJson(`${BASE}/chart?${params.toString()}`);
}
