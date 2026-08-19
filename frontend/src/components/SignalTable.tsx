import type { SignalItem } from "../types";

interface Props {
  signals: SignalItem[];
  loading: boolean;
  error: string | null;
}

function formatPrice(price: number, market: string): string {
  if (market === "KR") {
    return `${Math.round(price).toLocaleString("ko-KR")}원`;
  }
  return `$${price.toFixed(2)}`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDetails(sig: SignalItem): string {
  const d = sig.details;
  switch (sig.strategy) {
    case "ma_cross":
      return `단기MA ${d.short_ma} / 장기MA ${d.long_ma}`;
    case "rsi":
      return `RSI ${d.rsi} (기준 ${d.threshold})`;
    case "macd":
      return `MACD ${d.macd} / 시그널 ${d.signal_line} / 히스토그램 ${d.histogram}`;
    case "bollinger":
      return `중심 ${d.band_mid} · 상단 ${d.band_upper} · 하단 ${d.band_lower}`;
    case "volume_breakout":
      return `거래량 평균대비 ${d.volume_ratio}배`;
    default:
      return Object.entries(d)
        .map(([k, v]) => `${k}: ${v}`)
        .join(", ");
  }
}

export default function SignalTable({ signals, loading, error }: Props) {
  if (loading && signals.length === 0) {
    return <div className="state state--info">시그널을 불러오는 중입니다…</div>;
  }
  if (error) {
    return <div className="state state--error">{error}</div>;
  }
  if (signals.length === 0) {
    return <div className="state state--info">조건에 맞는 시그널이 아직 없습니다.</div>;
  }

  return (
    <div className="table-wrap">
      <table className="signal-table">
        <thead>
          <tr>
            <th>시장</th>
            <th>종목</th>
            <th>기법</th>
            <th>방향</th>
            <th>가격</th>
            <th>세부 지표</th>
            <th>발생 시각</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((sig) => (
            <tr key={`${sig.strategy}-${sig.symbol}-${sig.id}`}>
              <td>
                <span className={`market-badge market-badge--${sig.market}`}>{sig.market}</span>
              </td>
              <td>
                <div className="symbol-cell">
                  <span className="symbol-cell__name">{sig.name}</span>
                  <span className="symbol-cell__code">{sig.symbol}</span>
                </div>
              </td>
              <td>{sig.strategy_label}</td>
              <td>
                <span className={`direction-badge direction-badge--${sig.signal_type}`}>
                  {sig.signal_type === "BUY" ? "매수" : "매도"}
                </span>
              </td>
              <td className="num">{formatPrice(sig.price, sig.market)}</td>
              <td className="details-cell">{formatDetails(sig)}</td>
              <td>{formatTime(sig.timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
