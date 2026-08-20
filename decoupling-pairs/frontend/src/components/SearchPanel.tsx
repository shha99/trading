import { useState } from "react";
import type { CommonFilters, SearchResponse, TickerSuggestion } from "../types";
import { fetchSearch } from "../api";
import { presetRange } from "../dateUtils";
import { DEFAULT_TOP_N } from "../constants";
import SearchBar from "./SearchBar";
import DateRangeControl from "./DateRangeControl";
import FilterBar from "./FilterBar";
import ResultList from "./ResultList";

interface Props {
  asOf: string | null;
}

const DEFAULT_FILTERS: CommonFilters = {
  topN: DEFAULT_TOP_N,
  excludeManaged: true,
  excludeIlliquid: true,
  sectorFilter: "all",
  minMarketCap: "",
  minTradingValue: "",
};

type ScopeMode = "period" | "full";

export default function SearchPanel({ asOf }: Props) {
  const initial = presetRange("3m", asOf);
  const [selected, setSelected] = useState<TickerSuggestion | null>(null);
  const [scope, setScope] = useState<ScopeMode>("period");
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [filters, setFilters] = useState<CommonFilters>(DEFAULT_FILTERS);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  async function runSearch() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setHasSearched(true);
    try {
      const res = await fetchSearch(
        selected.ticker,
        { start, end, fullPeriod: scope === "full" },
        filters
      );
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "조회 중 오류가 발생했습니다");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mode-panel">
      <p className="mode-desc">기준 종목 하나를 골라, 나머지 전종목 중 디커플링이 가장 강한 상대를 찾습니다.</p>
      <SearchBar selected={selected} onSelect={setSelected} />

      <div className="scope-toggle">
        <label>
          <input type="radio" checked={scope === "period"} onChange={() => setScope("period")} />
          기간 지정
        </label>
        <label>
          <input type="radio" checked={scope === "full"} onChange={() => setScope("full")} />
          전체 기간(상장일 ~ 현재)
        </label>
      </div>

      {scope === "period" && (
        <DateRangeControl start={start} end={end} asOf={asOf} onChange={(s, e) => { setStart(s); setEnd(e); }} />
      )}
      {scope === "full" && (
        <p className="full-period-note">
          전체 기간을 사용합니다. 상장 기간이 짧은 종목과 비교할 때는 표본이 그만큼 자동으로 짧아지며, 페어별로
          실제 겹치는 거래일 수가 함께 표시됩니다.
        </p>
      )}

      <FilterBar filters={filters} onChange={setFilters} />
      <button className="search-btn" onClick={runSearch} disabled={loading || !selected}>
        {loading ? "검색 중…" : "종목 검색 실행"}
      </button>

      {result && !loading && (
        <div className="result-summary">
          <strong>{result.base.name}</strong> ({result.base.ticker}) 기준 · 상위 {result.pairs.length}개
          {result.full_period && " · 전체 기간"}
        </div>
      )}

      {hasSearched && (
        <ResultList
          pairs={result?.pairs ?? []}
          start={scope === "full" && result ? approximateEarliestDate(result) : start}
          end={end}
          loading={loading}
          error={error}
          reliabilityWarning={result?.reliability_warning ?? false}
          emptyHint="조건에 맞는 상대 종목이 없습니다. 필터를 완화해보세요."
        />
      )}
    </div>
  );
}

// 전체 기간 모드에서는 페어마다 겹치는 구간이 달라 차트에 쓸 대표 시작일을 넉넉히 과거로 잡는다.
// (차트 API는 실제 겹치는 데이터만 그리고 결측 구간은 비워 표시한다.)
function approximateEarliestDate(result: SearchResponse): string {
  return result.start ?? "2000-01-01";
}
