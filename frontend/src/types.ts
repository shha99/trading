export interface StrategyInfo {
  key: string;
  label: string;
}

export type SignalDirection = "BUY" | "SELL";

export interface SignalItem {
  id: number;
  symbol: string;
  name: string;
  market: string;
  strategy: string;
  strategy_label: string;
  signal_type: SignalDirection;
  price: number;
  timestamp: string;
  created_at: string | null;
  details: Record<string, unknown>;
}
