import { useState } from "react";
import type { PairResult } from "../types";
import { formatEok } from "../dateUtils";
import PriceChart from "./PriceChart";

interface Props {
  rank: number;
  pair: PairResult;
  start: string;
  end: string;
}

function StockCell({ ticker, name, market, sector, marketCap, tradingValue }: {
  ticker: string;
  name: string;
  market: string;
  sector: string;
  marketCap: number | null;
  tradingValue: number | null;
}) {
  return (
    <div className="stock-cell">
      <div className="stock-name-row">
        <span className="stock-name">{name}</span>
        <span className="stock-code">{ticker}</span>
      </div>
      <div className="stock-meta-row">
        <span className="stock-market">{market}</span>
        <span className="stock-sector">{sector}</span>
      </div>
      <div className="stock-meta-row muted">
        <span>시총 {formatEok(marketCap)}</span>
        <span>거래대금 {formatEok(tradingValue)}</span>
      </div>
    </div>
  );
}

export default function PairCard({ rank, pair, start, end }: Props) {
  const [expanded, setExpanded] = useState(false);
  const corrPct = (pair.correlation * 100).toFixed(1);
  const intensity = Math.min(1, Math.abs(pair.correlation));

  return (
    <div className="pair-card">
      <div className="pair-card-top">
        <span className="rank-badge">#{rank}</span>
        <div
          className="corr-badge"
          style={{ backgroundColor: `rgba(37, 99, 235, ${0.15 + intensity * 0.5})` }}
        >
          상관계수 {corrPct}%
        </div>
        <span className={`sector-badge ${pair.badge}`}>
          {pair.badge === "cross_sector" ? "크로스섹터 디커플링" : "동일섹터 이례적 디커플링"}
        </span>
        {pair.reliability_warning && (
          <span className="warning-badge" title="표본 기간이 짧아 신뢰도 낮음">
            ⚠ 표본 {pair.overlap_days}일
          </span>
        )}
      </div>

      <div className="pair-card-body">
        <StockCell
          ticker={pair.stock_a.ticker}
          name={pair.stock_a.name}
          market={pair.stock_a.market}
          sector={pair.stock_a.sector}
          marketCap={pair.stock_a.market_cap}
          tradingValue={pair.stock_a.avg_trading_value}
        />
        <div className="vs-divider">↔</div>
        <StockCell
          ticker={pair.stock_b.ticker}
          name={pair.stock_b.name}
          market={pair.stock_b.market}
          sector={pair.stock_b.sector}
          marketCap={pair.stock_b.market_cap}
          tradingValue={pair.stock_b.avg_trading_value}
        />
      </div>

      <button type="button" className="expand-btn" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "차트 접기 ▲" : "가격 차트 펼쳐보기 ▼"}
      </button>
      {expanded && <PriceChart a={pair.stock_a} b={pair.stock_b} start={start} end={end} />}
    </div>
  );
}
