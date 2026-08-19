import type { StrategyInfo } from "../types";

interface Props {
  strategies: StrategyInfo[];
  active: string | null;
  onSelect: (key: string | null) => void;
  counts: Record<string, number>;
}

export default function StrategyTabs({ strategies, active, onSelect, counts }: Props) {
  const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <nav className="tabs" aria-label="트레이딩 기법 필터">
      <button
        className={`tab ${active === null ? "tab--active" : ""}`}
        onClick={() => onSelect(null)}
      >
        전체
        <span className="tab__count">{totalCount}</span>
      </button>
      {strategies.map((s) => (
        <button
          key={s.key}
          className={`tab ${active === s.key ? "tab--active" : ""}`}
          onClick={() => onSelect(s.key)}
        >
          {s.label}
          <span className="tab__count">{counts[s.key] ?? 0}</span>
        </button>
      ))}
    </nav>
  );
}
