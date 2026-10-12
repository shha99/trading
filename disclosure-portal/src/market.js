// 시장조치 종목 목록: 투자주의·경고·위험, 관리종목, 거래정지 (코스피·코스닥 주식만).
//
// KRX 공식 목록(KIND, data.krx.co.kr)은 해외 서버 차단·로그인 필요로 Worker에서 못 쓴다. 그래서
// 네이버 증권의 "관리·거래정지·경보" 화면이 쓰는 JSON을 읽는다. 공개 API가 아니라 예고 없이
// 바뀌거나 막힐 수 있다 — 그러면 이 탭만 오류를 보여주고 공시 화면은 그대로 동작한다.
import { HALT_REASONS, MANAGEMENT_REASONS } from "./krx-codes.js";

const NAVER_URL = "https://stock.naver.com/api/domestic/market/stock/default";
const USER_AGENT = "Mozilla/5.0 (compatible; disclosure-portal/1.0)";

export const MARKET_KINDS = {
  warning: { label: "투자경고", params: { orderType: "marketAlertType", alertType: "02" } },
  risk: { label: "투자위험", params: { orderType: "marketAlertType", alertType: "03" } },
  caution: { label: "투자주의", params: { orderType: "marketAlertType", alertType: "01" } },
  management: { label: "관리종목", params: { orderType: "statusTag" } },
  halt: { label: "거래정지", params: { orderType: "tradeStopYn" } },
};

const MARKET_BY_SOSOK = { 0: "코스피", 1: "코스닥" };

export class MarketError extends Error {}

const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));

// 표에 없는 세부 코드는 같은 100단위 묶음(예: 2008 → 2000 "매출액/주가/시가총액")으로 보여준다
function reason(map, code) {
  if (!code) return "";
  const n = Number(code);
  if (map[n]) return map[n];
  const group = map[Math.floor(n / 100) * 100];
  return group ? `${group} (코드 ${code})` : `사유코드 ${code}`;
}

export function normalizeStock(raw, kind) {
  const managed = Boolean(raw.managementDate);
  const halted = raw.tradeStopYn === "Y";
  const date = kind === "management" ? raw.managementDate : kind === "halt" ? raw.tradingHaltDate : null;
  return {
    code: raw.itemcode,
    name: raw.itemname,
    market: MARKET_BY_SOSOK[raw.sosok] || "기타",
    price: num(raw.nowPrice),
    change_rate: num(raw.prevChangeRate),
    date: date || null,
    reason:
      kind === "management" ? reason(MANAGEMENT_REASONS, raw.managementReasonCode)
      : kind === "halt" ? reason(HALT_REASONS, raw.tradingHaltReasonCode)
      : "",
    managed,
    halted,
    alert: { "01": "투자주의", "02": "투자경고", "03": "투자위험" }[raw.marketAlertType] || "",
  };
}

export async function fetchMarketList(kind, fetchImpl = fetch) {
  const spec = MARKET_KINDS[kind];
  if (!spec) throw new MarketError("알 수 없는 목록입니다.");
  const url = new URL(NAVER_URL);
  // startIdx로 넘기는 페이지는 동작하지 않아서 한 번에 넉넉히 받는다(관리종목도 200개 안팎)
  url.search = new URLSearchParams({ tradeType: "KRX", marketType: "ALL", startIdx: "0", pageSize: "500", ...spec.params });
  let data;
  try {
    const resp = await fetchImpl(url, { headers: { Accept: "application/json", "User-Agent": USER_AGENT } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (e) {
    throw new MarketError(`네이버 증권 목록을 불러오지 못했습니다: ${String(e.message).slice(0, 200)}`);
  }
  if (!Array.isArray(data)) throw new MarketError("네이버 증권 응답 형식이 바뀌었습니다.");
  const items = data
    .filter((r) => r.type === "ST" && r.sosok in MARKET_BY_SOSOK) // 코스피·코스닥 주식만 (ETF·ETN·리츠 등 제외)
    .map((r) => normalizeStock(r, kind))
    .filter((it) => kind !== "management" || it.managed);
  return { kind, label: spec.label, items };
}

// 현재가: 네이버 실시간 시세(공개 API 아님). codes는 6자리 종목코드 배열.
export async function fetchQuotes(codes, fetchImpl = fetch) {
  const url = `https://polling.finance.naver.com/api/realtime/domestic/stock/${codes.map(encodeURIComponent).join(",")}`;
  let data;
  try {
    const resp = await fetchImpl(url, { headers: { Accept: "application/json", "User-Agent": USER_AGENT } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (e) {
    throw new MarketError(`현재가를 불러오지 못했습니다: ${String(e.message).slice(0, 200)}`);
  }
  const out = {};
  for (const d of (data && data.datas) || []) {
    out[d.itemCode] = { price: num(String(d.closePrice || "").replace(/,/g, "")), change_rate: num(d.fluctuationsRatio) };
  }
  return out;
}
