import type { CommonFilters, SectorFilter } from "../types";
import { MAX_TOP_N, MIN_TOP_N } from "../constants";

interface Props {
  filters: CommonFilters;
  onChange: (next: CommonFilters) => void;
}

export default function FilterBar({ filters, onChange }: Props) {
  function set<K extends keyof CommonFilters>(key: K, value: CommonFilters[K]) {
    onChange({ ...filters, [key]: value });
  }

  return (
    <div className="filter-bar">
      <div className="filter-row">
        <label className="slider-label">
          결과 개수: <strong>{filters.topN}</strong>개
          <input
            type="range"
            min={MIN_TOP_N}
            max={MAX_TOP_N}
            value={filters.topN}
            onChange={(e) => set("topN", Number(e.target.value))}
          />
        </label>
      </div>

      <div className="filter-row toggles">
        <label className="toggle">
          <input
            type="checkbox"
            checked={filters.excludeManaged}
            onChange={(e) => set("excludeManaged", e.target.checked)}
          />
          관리종목/거래정지/우선주 제외
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={filters.excludeIlliquid}
            onChange={(e) => set("excludeIlliquid", e.target.checked)}
          />
          거래대금 하위 20% 제외
        </label>
      </div>

      <div className="filter-row">
        <label>
          섹터 필터
          <select
            value={filters.sectorFilter}
            onChange={(e) => set("sectorFilter", e.target.value as SectorFilter)}
          >
            <option value="all">전체</option>
            <option value="cross">크로스섹터만</option>
            <option value="same">동일섹터만</option>
          </select>
        </label>
        <label>
          최소 시가총액(억원)
          <input
            type="number"
            min={0}
            placeholder="제한 없음"
            value={filters.minMarketCap}
            onChange={(e) => set("minMarketCap", e.target.value)}
          />
        </label>
        <label>
          최소 거래대금(억원)
          <input
            type="number"
            min={0}
            placeholder="제한 없음"
            value={filters.minTradingValue}
            onChange={(e) => set("minTradingValue", e.target.value)}
          />
        </label>
      </div>
    </div>
  );
}
