// OpenDART 공시검색(list.json) 응답 정규화.
//
// KIND(kind.krx.co.kr)는 공개 API가 없고 해외 서버 IP를 막아서(403) 직접 긁지 않는다.
// 대신 거래소 시장본부가 낸 공시(조회공시, 시장조치, 투자주의 등 KIND 공시)도 DART
// 목록에 함께 올라오고, 비고(rm) 필드의 "유"/"코"/"넥" 표시로 구분된다 — 이걸
// `is_exchange`로 노출해 KIND 공시를 따로 볼 수 있게 한다.

export const DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json";
export const PAGE_COUNT = 100; // OpenDART 최대값

const MARKET_BY_CLS = { Y: "코스피", K: "코스닥", N: "코넥스", E: "기타" };

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

// 거래·권리 영향 분류: 상장주식의 매매에 제한이나 조건(권리락, 거래정지, 주식수·가격 변동,
// 매도 제한 등)이 생기는 공시. 제목(공백 제거)에 키워드가 들어 있으면 그 분류로 본다.
// 실제 DART 코스피·코스닥 공시 제목(2026년 10월 며칠치)을 보고 정했다.
export const TRADE_CATEGORIES = [
  ["증자·감자", ["유상증자", "무상증자", "감자결정", "신주발행가액", "발행가액결정", "발행가액확정"]],
  ["배당", ["배당"]], // 현금ㆍ현물배당결정, 주식배당, 중간배당락 등
  ["권리락·기준일", ["권리락", "배당락", "주주명부폐쇄", "기준일설정"]],
  ["주식수·가격조정", [
    "주식소각", "주식병합", "주식분할", "액면분할", "액면병합", "추가상장", "변경상장",
    "전환청구권", "신주인수권행사", "교환청구권", "전환가액", "행사가액", "교환가액", "주식교환", "주식이전",
  ]],
  ["거래정지", ["매매거래정지", "거래정지", "정지해제"]],
  ["시장조치·경고", [
    "투자주의", "투자경고", "투자위험", "투자유의", "단기과열", "공매도과열", "시장경보", "관리종목",
    "상장폐지", "상장적격성", "정리매매", "불성실공시", "소속부변경", "조회공시요구", "회생절차", "해산사유",
  ]],
  ["의무보유", ["의무보유", "보호예수", "매각제한"]],
];

// 그 밖의 참고 태그
const KEYWORD_TAGS = [
  ["계약", ["단일판매", "공급계약"]],
  ["자사주", ["자기주식"]],
  ["메자닌", ["전환사채", "신주인수권부사채", "교환사채"]],
  ["최대주주", ["최대주주변경"]],
  ["합병·분할", ["합병", "회사분할", "분할결정"]],
  ["실적", ["영업(잠정)실적", "영업실적", "매출액또는손익구조"]],
  ["횡령·배임", ["횡령", "배임"]],
];
// 위 태그 중 "중요 공시"로 강조할 것 (거래·권리 영향 분류는 전부 중요로 본다)
const IMPORTANT_TAGS = new Set(["계약", "메자닌", "최대주주", "합병·분할", "횡령·배임"]);

const compact = (title) => title.replace(/\s/g, "");
const match = (table, title) => {
  const t = compact(title);
  return table.filter(([, words]) => words.some((w) => t.includes(w))).map(([name]) => name);
};

export const categoriesFor = (title) => match(TRADE_CATEGORIES, title);

export const tagsFor = (title) => match(KEYWORD_TAGS, title);

export function normalize(raw) {
  const rm = (raw.rm || "").trim();
  const flags = [...rm].filter((c) => c in RM_FLAGS);
  const reportNm = (raw.report_nm || "").split(/\s+/).filter(Boolean).join(" "); // 제목에 공백이 길게 끼어 있다
  const tags = tagsFor(reportNm);
  const categories = categoriesFor(reportNm);
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
    categories,
    important: categories.length > 0 || tags.some((t) => IMPORTANT_TAGS.has(t)),
    url: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${raw.rcept_no}`,
  };
}

export class DartError extends Error {}

const USER_AGENT = "Mozilla/5.0 (compatible; disclosure-portal/1.0)";

// 메시지에서 API 키와 URL을 지운다 — 오류가 화면에 그대로 나가기 때문
export function redact(message, apiKey) {
  let m = String(message || "");
  if (apiKey) m = m.split(apiKey).join("***");
  return m.replace(/https?:\/\/\S+/g, "(URL)").slice(0, 300);
}

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
    // User-Agent가 없으면 DART가 error1.html로 계속 리다이렉트한다(Workers fetch는 기본으로 안 붙임).
    // 리다이렉트는 따라가지 않고 오류로 처리한다.
    const resp = await fetchImpl(url, { headers: { Accept: "application/json", "User-Agent": USER_AGENT }, redirect: "manual" });
    if (resp.status >= 300 && resp.status < 400) throw new Error(`DART가 요청을 거부했습니다(리다이렉트 ${resp.status})`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (e) {
    // 오류 메시지에 요청 URL(=API 키)이 섞여 들어올 수 있어 키를 지운다
    throw new DartError(`DART 요청 실패: ${redact(e.message, apiKey)}`);
  }
  if (data.status === "013") return { items: [], total_page: 0, total_count: 0 }; // 조회된 데이터 없음
  if (data.status !== "000") throw new DartError(`DART 오류 ${data.status}: ${redact(data.message, apiKey)}`);
  return {
    items: (data.list || []).map(normalize),
    total_page: Number(data.total_page) || 1,
    total_count: Number(data.total_count) || 0,
  };
}
