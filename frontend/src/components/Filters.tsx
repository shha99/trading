export type MarketFilter = "ALL" | "KR" | "US";
export type DirectionFilter = "ALL" | "BUY" | "SELL";

interface Props {
  market: MarketFilter;
  onMarketChange: (v: MarketFilter) => void;
  direction: DirectionFilter;
  onDirectionChange: (v: DirectionFilter) => void;
  onRefresh: () => void;
  refreshing: boolean;
  lastUpdated: Date | null;
}

export default function Filters({
  market,
  onMarketChange,
  direction,
  onDirectionChange,
  onRefresh,
  refreshing,
  lastUpdated,
}: Props) {
  return (
    <div className="filters">
      <div className="filters__group">
        <label>
          시장
          <select value={market} onChange={(e) => onMarketChange(e.target.value as MarketFilter)}>
            <option value="ALL">전체</option>
            <option value="KR">한국</option>
            <option value="US">미국</option>
          </select>
        </label>
        <label>
          방향
          <select
            value={direction}
            onChange={(e) => onDirectionChange(e.target.value as DirectionFilter)}
          >
            <option value="ALL">전체</option>
            <option value="BUY">매수(BUY)</option>
            <option value="SELL">매도(SELL)</option>
          </select>
        </label>
      </div>
      <div className="filters__status">
        {lastUpdated && (
          <span className="filters__updated">
            최근 갱신: {lastUpdated.toLocaleTimeString("ko-KR")}
          </span>
        )}
        <button className="btn" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "갱신 중…" : "지금 새로고침"}
        </button>
      </div>
    </div>
  );
}
