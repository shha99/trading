import { useEffect, useState } from "react";
import type { CommonFilters, ScanResponse } from "../types";
import { fetchScan } from "../api";
import { presetRange } from "../dateUtils";
import { DEFAULT_TOP_N } from "../constants";
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

export default function ScanPanel({ asOf }: Props) {
  const initial = presetRange("3m", asOf);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [filters, setFilters] = useState<CommonFilters>(DEFAULT_FILTERS);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  // asOf가 로드되면(최초 1회) 기본 구간을 데이터 기준일에 맞춰 다시 계산
  useEffect(() => {
    if (asOf) {
      const r = presetRange("3m", asOf);
      setStart(r.start);
      setEnd(r.end);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asOf]);

  async function runScan() {
    setLoading(true);
    setError(null);
    setHasSearched(true);
    try {
      const res = await fetchScan(start, end, filters);
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
      <p className="mode-desc">지정한 기간 동안 코스피·코스닥 전종목 조합 중 음의 상관관계가 가장 큰 페어를 찾습니다.</p>
      <DateRangeControl start={start} end={end} asOf={asOf} onChange={(s, e) => { setStart(s); setEnd(e); }} />
      <FilterBar filters={filters} onChange={setFilters} />
      <button className="search-btn" onClick={runScan} disabled={loading}>
        {loading ? "스캔 중…" : "전체 스캔 실행"}
      </button>

      {result && !loading && (
        <div className="result-summary">
          {result.trading_days}거래일 · 필터 통과 종목 {result.universe_size}개 중 상위 {result.pairs.length}개 페어
        </div>
      )}

      {hasSearched && (
        <ResultList
          pairs={result?.pairs ?? []}
          start={start}
          end={end}
          loading={loading}
          error={error}
          reliabilityWarning={result?.reliability_warning ?? false}
          emptyHint="조건에 맞는 페어가 없습니다. 필터를 완화해보세요."
        />
      )}
    </div>
  );
}
