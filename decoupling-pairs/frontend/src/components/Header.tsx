import { useState } from "react";
import type { StatusResponse } from "../types";
import { triggerRefresh } from "../api";

interface Props {
  status: StatusResponse | null;
  onRefreshed: (s: StatusResponse) => void;
}

export default function Header({ status, onRefreshed }: Props) {
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      const s = await triggerRefresh();
      onRefreshed(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "새로고침 실패");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <header className="app-header">
      <div className="app-header-title">
        <h1>코스피 · 코스닥 디커플링 페어 검색</h1>
        <p className="subtitle">음의 상관관계가 높은(디커플링) 종목쌍을 찾아보세요</p>
      </div>
      <div className="app-header-status">
        <div className="as-of-badge">
          데이터 기준일:{" "}
          <strong>{status?.last_update_date ? `${status.last_update_date} 장마감` : "갱신 필요"}</strong>
        </div>
        <button
          className="refresh-btn"
          onClick={handleRefresh}
          disabled={refreshing || (status ? !status.can_manual_refresh : false)}
          title={
            status && !status.can_manual_refresh
              ? `1일 1회로 제한됩니다. 다음 가능 시각: ${status.next_manual_refresh_at ?? "-"}`
              : "최신 데이터로 수동 새로고침"
          }
        >
          {refreshing ? "갱신 중…" : "수동 새로고침"}
        </button>
        {error && <span className="error-text">{error}</span>}
      </div>
    </header>
  );
}
