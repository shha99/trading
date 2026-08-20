export function toIsoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export type Preset = "1m" | "3m" | "6m" | "1y";

export const PRESET_LABELS: Record<Preset, string> = {
  "1m": "최근 1개월",
  "3m": "최근 3개월",
  "6m": "최근 6개월",
  "1y": "최근 1년",
};

/** as-of 날짜(데이터 기준일) 기준으로 프리셋 구간 계산. asOf 없으면 오늘 기준. */
export function presetRange(preset: Preset, asOf: string | null): { start: string; end: string } {
  const end = asOf ? new Date(asOf) : new Date();
  const start = new Date(end);
  switch (preset) {
    case "1m":
      start.setMonth(start.getMonth() - 1);
      break;
    case "3m":
      start.setMonth(start.getMonth() - 3);
      break;
    case "6m":
      start.setMonth(start.getMonth() - 6);
      break;
    case "1y":
      start.setFullYear(start.getFullYear() - 1);
      break;
  }
  return { start: toIsoDate(start), end: toIsoDate(end) };
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return n.toLocaleString("ko-KR");
}

/** 원 단위 큰 금액을 억원 단위로 축약 표시 */
export function formatEok(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return `${Math.round(n / 1e8).toLocaleString("ko-KR")}억`;
}
