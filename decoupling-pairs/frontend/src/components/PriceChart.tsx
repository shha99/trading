import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { StockInfo } from "../types";
import { fetchChart } from "../api";

interface Props {
  a: StockInfo;
  b: StockInfo;
  start: string;
  end: string;
}

export default function PriceChart({ a, b, start, end }: Props) {
  const [data, setData] = useState<Array<{ date: string; a: number | null; b: number | null }> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    fetchChart(a.ticker, b.ticker, start, end)
      .then((res) => {
        if (!cancelled) setData(res.points);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "차트를 불러오지 못했습니다");
      });
    return () => {
      cancelled = true;
    };
  }, [a.ticker, b.ticker, start, end]);

  if (error) return <div className="chart-error">{error}</div>;
  if (!data) return <div className="chart-loading">차트 불러오는 중…</div>;

  return (
    <div className="price-chart">
      <p className="chart-caption">시작일 = 100 기준 정규화 가격 (겹쳐서 반대 방향을 확인해보세요)</p>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={30} />
          <YAxis tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="a" name={a.name} stroke="#2563eb" dot={false} strokeWidth={2} connectNulls />
          <Line type="monotone" dataKey="b" name={b.name} stroke="#dc2626" dot={false} strokeWidth={2} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
