import type { PairResult } from "../types";
import PairCard from "./PairCard";

interface Props {
  pairs: PairResult[];
  start: string;
  end: string;
  loading: boolean;
  error: string | null;
  reliabilityWarning: boolean;
  emptyHint: string;
}

export default function ResultList({ pairs, start, end, loading, error, reliabilityWarning, emptyHint }: Props) {
  if (loading) return <div className="status-msg">불러오는 중…</div>;
  if (error) return <div className="status-msg error">{error}</div>;

  return (
    <div className="result-list">
      {reliabilityWarning && (
        <div className="reliability-banner">
          ⚠ 표본 기간이 짧아 상관계수 추정의 신뢰도가 낮을 수 있습니다 (최소 20거래일 권장).
        </div>
      )}
      {pairs.length === 0 ? (
        <div className="status-msg">{emptyHint}</div>
      ) : (
        pairs.map((p, i) => (
          <PairCard key={`${p.stock_a.ticker}-${p.stock_b.ticker}`} rank={i + 1} pair={p} start={start} end={end} />
        ))
      )}
    </div>
  );
}
