import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchSignals, fetchStrategies, triggerRefresh } from "./api";
import Filters from "./components/Filters";
import type { DirectionFilter, MarketFilter } from "./components/Filters";
import SignalTable from "./components/SignalTable";
import StrategyTabs from "./components/StrategyTabs";
import type { SignalItem, StrategyInfo } from "./types";

const AUTO_REFRESH_MS = 60_000;

export default function App() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [activeStrategy, setActiveStrategy] = useState<string | null>(null);
  const [market, setMarket] = useState<MarketFilter>("ALL");
  const [direction, setDirection] = useState<DirectionFilter>("ALL");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    fetchStrategies()
      .then(setStrategies)
      .catch((e) => setError(String(e)));
  }, []);

  // 탭별 개수를 항상 정확히 보여주기 위해 전략 필터는 서버에 보내지 않고
  // 시장/방향 필터만 서버에 보낸 뒤, 전략(탭) 필터는 클라이언트에서 적용한다.
  const loadSignals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSignals({
        market: market === "ALL" ? undefined : market,
        signalType: direction === "ALL" ? undefined : direction,
      });
      setSignals(data);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [market, direction]);

  useEffect(() => {
    loadSignals();
  }, [loadSignals]);

  useEffect(() => {
    const timer = setInterval(loadSignals, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [loadSignals]);

  const handleManualRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await triggerRefresh();
      await loadSignals();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }, [loadSignals]);

  const strategyCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const sig of signals) {
      counts[sig.strategy] = (counts[sig.strategy] ?? 0) + 1;
    }
    return counts;
  }, [signals]);

  const visibleSignals = useMemo(
    () => (activeStrategy ? signals.filter((s) => s.strategy === activeStrategy) : signals),
    [signals, activeStrategy],
  );

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1>트레이딩 시그널 대시보드</h1>
          <p className="app__subtitle">
            기법별로 매수/매도 시그널이 감지된 종목을 한눈에 확인하세요.
          </p>
        </div>
      </header>

      <StrategyTabs
        strategies={strategies}
        active={activeStrategy}
        onSelect={setActiveStrategy}
        counts={strategyCounts}
      />

      <Filters
        market={market}
        onMarketChange={setMarket}
        direction={direction}
        onDirectionChange={setDirection}
        onRefresh={handleManualRefresh}
        refreshing={refreshing}
        lastUpdated={lastUpdated}
      />

      <SignalTable signals={visibleSignals} loading={loading} error={error} />

      <footer className="app__footer">
        {AUTO_REFRESH_MS / 1000}초마다 자동으로 새로고침됩니다.
      </footer>
    </div>
  );
}
