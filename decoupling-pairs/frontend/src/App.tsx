import { useEffect, useState } from "react";
import type { StatusResponse } from "./types";
import { fetchStatus } from "./api";
import Header from "./components/Header";
import ScanPanel from "./components/ScanPanel";
import SearchPanel from "./components/SearchPanel";

type Mode = "scan" | "search";

export default function App() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [mode, setMode] = useState<Mode>("scan");

  useEffect(() => {
    fetchStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  return (
    <div className="app-shell">
      <Header status={status} onRefreshed={setStatus} />

      <nav className="mode-tabs">
        <button className={mode === "scan" ? "active" : ""} onClick={() => setMode("scan")}>
          전체 스캔
        </button>
        <button className={mode === "search" ? "active" : ""} onClick={() => setMode("search")}>
          종목 검색
        </button>
      </nav>

      <main className="app-main">
        {status?.universe_size === 0 && (
          <div className="no-data-banner">
            아직 캐시된 데이터가 없습니다. 서버에서 배치 갱신(<code>python -m app.batch_update backfill --start=...</code>)을
            먼저 실행하거나, 위의 "수동 새로고침" 버튼을 눌러주세요.
          </div>
        )}
        {mode === "scan" ? (
          <ScanPanel asOf={status?.last_update_date ?? null} />
        ) : (
          <SearchPanel asOf={status?.last_update_date ?? null} />
        )}
      </main>

      <footer className="app-footer">
        상관관계는 지정된 과거 기간 내 통계적 관계이며, 미래에도 동일하게 유지된다는 보장은 없습니다. 표본 기간이
        짧을수록 결과 신뢰도가 낮아질 수 있습니다.
      </footer>
    </div>
  );
}
