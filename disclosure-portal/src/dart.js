// OpenDART 공시검색(list.json) 응답 정규화.
//
// KIND(kind.krx.co.kr)는 공개 API가 없고 해외 서버 IP를 막아서(403) 직접 긁지 않는다.
// 대신 거래소 시장본부가 낸 공시(조회공시, 시장조치, 투자주의 등 KIND 공시)도 DART
// 목록에 함께 올라오고, 비고(rm) 필드의 "유"/"코"/"넥" 표시로 구분된다 — 이걸
// `is_exchange`로 노출해 KIND 공시를 따로 볼 수 있게 한다.

export const DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json";
export const PAGE_COUNT = 100; // OpenDART 최대값

const MARKET_BY_CLS = { Y: "유가", K: "코스닥", N: "코넥스", E: "기타" };

// 비고(rm) 필드의 한 글자 표시 — OpenDART 개발가이드 기준
const RM_FLAGS = {
  유: "유가증권시장본부",
  코: "코스닥시장본부",
  넥: "코넥스시장본부",
  채: "채권상장법인",
  공: "공정위",
  연: "연결부분 포함",
  정: "정정 제출 예정",
  철: "철회",
};
const EXCHANGE_FLAGS = new Set(["유", "코", "넥"]);

// 제목 키워드 → 태그. 한 공시에 여러 태그가 붙을 수 있다.
const KEYWORD_TAGS = [
  ["계약", ["단일판매", "공급계약"]],
  ["증자", ["유상증자", "무상증자"]],
  ["감자", ["감자결정"]],
  ["자사주", ["자기주식"]],
  ["메자닌", ["전환사채", "신주인수권부사채", "교환사채"]],
  ["최대주주", ["최대주주변경"]],
  ["합병·분할", ["합병", "분할"]],
  ["실적", ["영업(잠정)실적", "매출액또는손익구조"]],
  ["시장조치", ["거래정지", "관리종목", "상장폐지", "불성실공시", "투자주의", "투자경고", "투자위험"]],
  ["조회공시", ["조회공시"]],
  ["횡령·배임", ["횡령", "배임"]],
];
// 위 태그 중 "중요 공시"로 강조할 것
const IMPORTANT_TAGS = new Set(["계약", "증자", "감자", "자사주", "메자닌", "최대주주", "합병·분할", "시장조치", "횡령·배임"]);

export function tagsFor(title) {
  const t = title.replace(/\s/g, "");
  return KEYWORD_TAGS.filter(([, words]) => words.some((w) => t.includes(w))).map(([tag]) => tag);
}

export function normalize(raw) {
  const rm = (raw.rm || "").trim();
  const flags = [...rm].filter((c) => c in RM_FLAGS);
  const reportNm = (raw.report_nm || "").split(/\s+/).filter(Boolean).join(" "); // 제목에 공백이 길게 끼어 있다
  const tags = tagsFor(reportNm);
  const bracket = reportNm.startsWith("[") ? reportNm.slice(0, reportNm.indexOf("]") + 1) : "";
  return {
    rcept_no: raw.rcept_no,
    rcept_dt: raw.rcept_dt || raw.rcept_no.slice(0, 8),
    corp_code: raw.corp_code || "",
    corp_name: (raw.corp_name || "").trim(),
    stock_code: (raw.stock_code || "").trim(),
    market: MARKET_BY_CLS[raw.corp_cls] || "기타",
    report_nm: reportNm,
    flr_nm: (raw.flr_nm || "").trim(),
    rm,
    flags: flags.map((f) => RM_FLAGS[f]),
    is_exchange: flags.some((f) => EXCHANGE_FLAGS.has(f)),
    is_correction: bracket.includes("정정"),
    tags,
    important: tags.some((t) => IMPORTANT_TAGS.has(t)),
    url: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${raw.rcept_no}`,
  };
}

export class DartError extends Error {}

// date(YYYYMMDD)의 공시 목록 한 페이지(최신순)와 전체 페이지 수.
export async function fetchPage(apiKey, date, page, fetchImpl = fetch) {
  if (!apiKey) throw new DartError("DART_API_KEY가 설정되지 않았습니다.");
  const url = new URL(DART_LIST_URL);
  url.search = new URLSearchParams({
    crtfc_key: apiKey,
    bgn_de: date,
    end_de: date,
    page_no: String(page),
    page_count: String(PAGE_COUNT),
    sort: "date",
    sort_mth: "desc",
  });
  let data;
  try {
    const resp = await fetchImpl(url, { headers: { Accept: "application/json" } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (e) {
    throw new DartError(`DART 요청 실패: ${e.message}`);
  }
  if (data.status === "013") return { items: [], total_page: 0, total_count: 0 }; // 조회된 데이터 없음
  if (data.status !== "000") throw new DartError(`DART 오류 ${data.status}: ${data.message || ""}`);
  return {
    items: (data.list || []).map(normalize),
    total_page: Number(data.total_page) || 1,
    total_count: Number(data.total_count) || 0,
  };
}
