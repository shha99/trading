import type { SignalItem, StrategyInfo } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`요청 실패 (${res.status}): ${url}`);
  }
  return (await res.json()) as T;
}

export function fetchStrategies(): Promise<StrategyInfo[]> {
  return getJSON<StrategyInfo[]>(`${API_BASE}/strategies`);
}

export interface SignalFilters {
  strategy?: string;
  market?: string;
  signalType?: string;
}

export function fetchSignals(filters: SignalFilters = {}): Promise<SignalItem[]> {
  const qs = new URLSearchParams();
  if (filters.strategy) qs.set("strategy", filters.strategy);
  if (filters.market) qs.set("market", filters.market);
  if (filters.signalType) qs.set("signal_type", filters.signalType);
  qs.set("latest_only", "true");
  qs.set("limit", "300");
  return getJSON<SignalItem[]>(`${API_BASE}/signals?${qs.toString()}`);
}

export async function triggerRefresh(): Promise<{ detected: number }> {
  const res = await fetch(`${API_BASE}/refresh`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`재계산 요청 실패 (${res.status})`);
  }
  return res.json();
}
