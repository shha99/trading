// 캘린더: 공개매수 기간과 주식매수청구권 행사기간 (코스피·코스닥 대상회사).
//
// 1) candidates: 최근 6개월 공시 목록에서
//    - 주요사항보고서 중 합병·분할합병·주식교환이전·영업양수·영업양도 결정(→ 매수청구 후보)
//    - 지분공시 중 공개매수 관련 서류(신고서·정정·결과보고서)
//    를 찾아 회사별로 묶는다. 목록 API는 회사 지정 없이 3개월씩만 되므로 3개월 창 두 개.
// 2) deal: 후보 회사 하나의 주요사항보고서 구조화 API를 불러 매수청구 기간·매수예정가격·주총일을 꺼낸다.
// 3) tender: 공개매수신고서 원문(document.xml, zip)을 받아 공개매수기간·가격을 읽는다(구조화 API 없음).
// 화면은 1)을 받은 뒤 2)·3)을 회사마다 따로 부른다 — Workers 무료 플랜의 요청당 외부 호출 한도(50)
// 안에 들어오게 하려는 것이고, 결과는 Worker가 몇 시간씩 캐시한다.
import { DartError, PAGE_COUNT, dartRequest } from "./dart.js";

// 주요사항보고서 제목 → 구조화 API. 분할합병을 합병보다 먼저 확인한다.
// "doc"은 구조화 API가 없어 보고서 본문만 읽는 종류다.
export const DEAL_APIS = [
  ["회사분할합병결정", "cmpDvmgDecsn", "분할합병"],
  ["회사합병결정", "cmpMgDecsn", "합병"],
  ["주식교환ㆍ이전결정", "stkExtrDecsn", "주식교환·이전"],
  ["주식교환·이전결정", "stkExtrDecsn", "주식교환·이전"],
  ["영업양수결정", "bsnInhDecsn", "영업양수"],
  ["영업양도결정", "bsnTrfDecsn", "영업양도"],
  ["회사분할결정", "cmpDvDecsn", "물적분할"], // 인적분할은 매수청구 없음 — 물적분할만 남긴다
  ["중요한자산양수도결정", "doc", "중요자산양수도"],
];
const DEAL_LABEL = Object.fromEntries(DEAL_APIS.map(([, api, label]) => [api, label]));

const SCOPE = new Set(["Y", "K"]); // 코스피·코스닥
const MARKET = { Y: "코스피", K: "코스닥" };

export function ymd(d) {
  return d.toISOString().slice(0, 10).replaceAll("-", "");
}
export function addDays(yyyymmdd, n) {
  const d = new Date(Date.UTC(+yyyymmdd.slice(0, 4), +yyyymmdd.slice(4, 6) - 1, +yyyymmdd.slice(6, 8) + n));
  return ymd(d);
}

// "2026년 10월 29일" / "2026.10.29" / "2026-10-29" → "20261029", 그 밖("-", "미정")은 null
export function parseKoDate(s) {
  const m = String(s || "").match(/(\d{4})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})/);
  return m ? `${m[1]}${m[2].padStart(2, "0")}${m[3].padStart(2, "0")}` : null;
}

const price = (s) => {
  const n = Number(String(s || "").replace(/[^\d.]/g, ""));
  return Number.isFinite(n) && n > 0 ? n : null;
};

async function listAll(apiKey, params, fetchImpl) {
  const first = await dartRequest(apiKey, "list.json", { ...params, page_no: "1", page_count: String(PAGE_COUNT) }, fetchImpl);
  if (!first) return [];
  const pages = Math.min(Number(first.total_page) || 1, 15);
  const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, i) =>
    dartRequest(apiKey, "list.json", { ...params, page_no: String(i + 2), page_count: String(PAGE_COUNT) }, fetchImpl)));
  return [first, ...rest].flatMap((d) => (d && d.list) || []);
}

export async function fetchCandidates(apiKey, today, fetchImpl = fetch) {
  const windows = [[addDays(today, -89), today], [addDays(today, -179), addDays(today, -90)]];
  const jobs = [];
  for (const [bgn, end] of windows) {
    for (const cls of SCOPE) jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, corp_cls: cls, pblntf_ty: "B" }, fetchImpl));
    jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, pblntf_detail_ty: "D004" }, fetchImpl)); // 공개매수
    jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, pblntf_detail_ty: "C004" }, fetchImpl)); // 합병등 증권신고서(철회 확인용)
  }
  const rows = (await Promise.all(jobs)).flat().filter((r) => SCOPE.has(r.corp_cls));
  rows.sort((a, b) => a.rcept_no.localeCompare(b.rcept_no)); // 오래된 것부터

  const base = (r) => ({ corp_code: r.corp_code, corp_name: r.corp_name.trim(), stock_code: (r.stock_code || "").trim(), market: MARKET[r.corp_cls] });

  // 철회: 합병등 증권신고서 철회신고서, 또는 제목에 "철회"가 붙은 주요사항보고서 → 회사별 마지막 철회 접수번호
  const withdrawn = new Map();
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    if (title.includes("철회신고서") || (title.includes("주요사항보고서") && title.includes("철회"))) withdrawn.set(r.corp_code, r.rcept_no);
  }

  // 매수청구 후보: 회사×API마다 가장 최근 보고서 하나
  const deals = new Map();
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    if (!title.includes("주요사항보고서") || title.includes("철회")) continue;
    const hit = DEAL_APIS.find(([kw]) => title.includes(kw));
    if (!hit) continue;
    const key = `${r.corp_code}:${hit[1]}`;
    const prev = deals.get(key);
    // rcept_no: 최신 보고서(정정 포함, 일정을 읽을 것), first_rcept_no: 처음 공시된 보고서(언제 "떴는지")
    deals.set(key, { ...base(r), api: hit[1], kind: hit[2], rcept_no: r.rcept_no, rcept_dt: r.rcept_dt, first_rcept_no: prev ? prev.first_rcept_no : r.rcept_no });
  }

  // 공개매수: 정정이 아닌 신고서가 새 공개매수의 시작, 그 뒤 정정은 같은 건, 결과보고서가 나오면 종료
  const tenders = [];
  const open = new Map(); // corp_code → 진행 중인 건
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    if (title.includes("공개매수신고서")) {
      const corrected = title.startsWith("[");
      let t = open.get(r.corp_code);
      if (!t || !corrected) {
        t = { ...base(r), offeror: (r.flr_nm || "").trim(), rcept_no: r.rcept_no, first_rcept_no: r.rcept_no, done: false };
        tenders.push(t);
        open.set(r.corp_code, t);
      } else if (!title.includes("첨부정정")) {
        t.rcept_no = r.rcept_no; // 정정된 최신 신고서를 읽는다(첨부서류만 바꾼 정정은 본문이 없어 건너뜀)
      }
    } else if (title.includes("공개매수결과보고서")) {
      const t = open.get(r.corp_code);
      if (t) { t.done = true; t.result_rcept_no = r.rcept_no; open.delete(r.corp_code); }
    }
  }
  for (const d of deals.values()) {
    const w = withdrawn.get(d.corp_code);
    if (w && w > d.rcept_no) d.withdrawn_rcept_no = w; // 결정 뒤에 철회가 나왔다
  }
  return { deals: [...deals.values()], tenders };
}


// 보고서 본문에서 매수청구 정보 읽기. 구조화 API에 없는 칸(영업양수도의 행사기간, 물적분할 등)을
// 채우는 데 쓴다. 표가 글자로 풀리면 "주식매수청구권 행사기간 시작일 2026년 10월 20일 종료일 …"이나
// "행사기간 2026.11.06 ~ 2026.11.25" 꼴이 된다.
const DATE = "(\\d{4})\\s*[년.\\-]\\s*(\\d{1,2})\\s*[월.\\-]\\s*(\\d{1,2})\\s*일?\\.?";
export function parseAppraisalText(text) {
  const t = text.replace(/<[^>]+>/g, " ").replace(/&nbsp;|&#160;/g, " ").replace(/\s+/g, " ");
  const section = t.match(/주식\s*매수\s*청구권?에\s*관한\s*사항\s*[-–:]?\s*(.{0,30})/);
  if (section && /해당\s*사항\s*(?:이\s*)?없/.test(section[1])) return { none: true };
  const pad = (x) => x.padStart(2, "0");
  const pm = t.match(new RegExp(`행사\\s*기간[^0-9]{0,30}?${DATE}[^0-9]{0,15}?${DATE}`));
  const gm = t.match(new RegExp(`주주\\s*총회\\s*(?:예정)?\\s*(?:일자|일)[^0-9]{0,10}?${DATE}`));
  let planned = null;
  // "1주당"의 1은 건너뛰고 숫자를 찾는다
  for (const m of t.matchAll(/(?:매수\s*예정\s*가격|주식\s*매수\s*(?:청구\s*)?가격|매수\s*가격)(?:1\s*주\s*당|[^0-9]){0,80}?([\d,]{3,})\s*(?:원|(?=[\s(]|$))/g)) {
    const v = price(m[1]);
    if (v && v >= 100 && v <= 5_000_000) { planned = v; break; } // "100억원 초과 시 해제" 같은 조건 금액은 건너뜀
  }
  return {
    start: pm ? `${pm[1]}${pad(pm[2])}${pad(pm[3])}` : null,
    end: pm ? `${pm[4]}${pad(pm[5])}${pad(pm[6])}` : null,
    gm_date: gm ? `${gm[1]}${pad(gm[2])}${pad(gm[3])}` : null,
    price: planned,
  };
}

async function documentText(apiKey, rceptNo, fetchImpl) {
  const buf = await dartRequest(apiKey, "document.xml", { rcept_no: rceptNo }, fetchImpl);
  const head = new TextDecoder().decode(new Uint8Array(buf).subarray(0, 200));
  if (!head.startsWith("PK")) {
    const m = head.match(/<message>([^<]*)/);
    throw new DartError(`공시 원문을 받지 못했습니다${m ? `: ${m[1]}` : ""}`);
  }
  return new TextDecoder("utf-8").decode(await unzipFirst(buf));
}

// "(주)진에어 (JIN AIR CO., LTD.) (에어서울(주)에 대한 사항은 …)" → "(주)진에어": 띄어 쓴 괄호 설명과 영문명을 뗀다
export function cleanName(s) {
  let out = String(s).replace(/\s+/g, " ").trim();
  for (let prev = ""; prev !== out;) { prev = out; out = out.replace(/\s+\((?:[^()]|\([^()]*\))*\)\s*$/, "").trim(); }
  return out.replace(/\((?=[A-Za-z])[^()]*\)/g, "").trim();
}

// 매수청구 일정 하나. 순서:
// 1) 구조화 API(합병·분할합병·주식교환이전·영업양수도·분할)의 행사기간·매수예정가격·주총일 칸
// 2) 빈 칸이 있거나 추정이 필요한 종류면 같은 보고서 본문을 읽어 채운다
// 3) 그래도 기간이 없고 주총일만 있으면 "주총일부터 20일"로 추정(추정 표시)
// rceptNo는 후보 목록에서 찾은 최신 보고서(정정 포함) — 본문만 읽는 종류("doc")에 쓴다.
export async function fetchDeal(apiKey, api, corpCode, today, fetchImpl = fetch, rceptNo = "") {
  if (!DEAL_LABEL[api]) throw new DartError("알 수 없는 보고서 종류입니다.");
  let x = { rcept_no: rceptNo };
  if (api !== "doc") {
    // 이 API의 기간은 이사회 결의일 기준이라, 결의가 오래전이고 정정만 최근인 건도 잡히게 넉넉히 본다
    const data = await dartRequest(apiKey, `${api}.json`, { corp_code: corpCode, bgn_de: addDays(today, -500), end_de: today }, fetchImpl);
    const list = (data && data.list) || [];
    if (!list.length) return null;
    x = list.reduce((a, b) => (a.rcept_no > b.rcept_no ? a : b)); // 가장 최근(정정 포함)
    if (api === "cmpDvDecsn" && !/물적\s*분할/.test(x.dv_mth || "")) return { none: true, rcept_no: x.rcept_no };
  }
  const field = (suffix) => {
    const k = Object.keys(x).find((key) => key.endsWith(suffix));
    return k ? x[k] : null;
  };
  let start = parseKoDate(field("aprskh_expd_bgd"));
  let end = parseKoDate(field("aprskh_expd_edd"));
  let gm = parseKoDate(field("gmtsck_prd"));
  let planned = price(x.aprskh_plnprc);
  let source = "구조화";

  if (!start || !end || !planned) {
    try {
      const t = parseAppraisalText(await documentText(apiKey, x.rcept_no, fetchImpl));
      if (t.none && api === "doc") return { none: true, rcept_no: x.rcept_no };
      if (!start && !end && t.start && t.end) { start = t.start; end = t.end; source = "본문"; }
      if (!planned && t.price) planned = t.price;
      if (!gm && t.gm_date) gm = t.gm_date;
    } catch {
      // 본문을 못 읽으면 구조화 칸만으로 간다
    }
  }
  let estimated = false;
  if (!start && !end && gm && ["bsnInhDecsn", "bsnTrfDecsn", "cmpDvDecsn", "doc"].includes(api)) {
    // 상법상 주총 결의일부터 20일(첫날 불산입)
    start = gm;
    end = addDays(gm, 20);
    estimated = true;
  }
  if (!start && !end && !planned) return { none: true, rcept_no: x.rcept_no }; // 소규모·간이합병 등 매수청구 없음
  return {
    type: "buyback",
    kind: DEAL_LABEL[api],
    start,
    end,
    estimated,
    source: estimated ? "추정" : source,
    gm_date: gm,
    price: planned,
    partner: cleanName(x.mgptncmp_cmpnm || x.extrcmp_cmpnm || x.dlptn_cmpnm || x.dvfcmp_cmpnm || "") || null,
    rcept_no: x.rcept_no,
  };
}

// ---- 공개매수신고서 원문 ----

// DART document.xml은 zip이다. 첫 파일(본문 XML)만 꺼낸다(중앙 디렉터리 기준).
export async function unzipFirst(buf) {
  const u8 = new Uint8Array(buf);
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new DartError("공시 원문 형식이 올바르지 않습니다.");
  const cd = dv.getUint32(eocd + 16, true);
  if (dv.getUint32(cd, true) !== 0x02014b50) throw new DartError("공시 원문 형식이 올바르지 않습니다.");
  const method = dv.getUint16(cd + 10, true);
  const size = dv.getUint32(cd + 20, true);
  const local = dv.getUint32(cd + 42, true);
  const start = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true);
  const data = u8.subarray(start, start + size);
  if (method === 0) return data;
  if (method !== 8) throw new DartError("지원하지 않는 압축 형식입니다.");
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export function parseTenderText(text) {
  const t = text.replace(/<[^>]+>/g, " ").replace(/&nbsp;|&#160;/g, " ").replace(/\s+/g, " ");
  const d = "(\\d{4})\\s*[.\\-년]\\s*(\\d{1,2})\\s*[.\\-월]\\s*(\\d{1,2})\\s*일?\\.?";
  const pm = t.match(new RegExp(`공개매수\\s*기간\\s*:?\\s*${d}\\s*[-~]\\s*${d}`));
  const pp = t.match(/매수가격[\s\S]{0,200}?주당\s*(?:금\s*)?([\d,]+)\s*원/) || t.match(/공개매수가격[^0-9]{0,60}?([\d,]{3,})\s*원/);
  const pad = (x) => x.padStart(2, "0");
  return {
    start: pm ? `${pm[1]}${pad(pm[2])}${pad(pm[3])}` : null,
    end: pm ? `${pm[4]}${pad(pm[5])}${pad(pm[6])}` : null,
    price: pp ? price(pp[1]) : null,
  };
}

export async function fetchTender(apiKey, rceptNo, fetchImpl = fetch) {
  return { type: "tender", rcept_no: rceptNo, ...parseTenderText(await documentText(apiKey, rceptNo, fetchImpl)) };
}

// ---- 증자: 신주인수권증서 상장기간·신주 상장예정일 ----
// 유상증자 중 주주배정·일반공모(증권신고서를 내는 공모)만 신주인수권증서가 상장된다. 제3자배정까지 다
// 읽으면 너무 많아서, 같은 기간에 증권신고서(지분증권)를 낸 회사의 유상증자결정만 고른다. 무상·유무상증자는
// 전부. 일정은 구조화 API에 없어서 결정 보고서 본문에서 읽는다.
export const CAPITAL_KINDS = [
  ["유무상증자결정", "유무상증자"],
  ["무상증자결정", "무상증자"],
  ["유상증자결정", "유상증자"],
];

export async function fetchCapitalCandidates(apiKey, today, fetchImpl = fetch) {
  const bgn = addDays(today, -89);
  const lists = await Promise.all([
    ...[...SCOPE].map((cls) => listAll(apiKey, { bgn_de: bgn, end_de: today, corp_cls: cls, pblntf_ty: "B" }, fetchImpl)),
    listAll(apiKey, { bgn_de: bgn, end_de: today, pblntf_detail_ty: "C001" }, fetchImpl), // 증권신고서(지분증권)
  ]);
  const rows = lists.flat().filter((r) => SCOPE.has(r.corp_cls));
  rows.sort((a, b) => a.rcept_no.localeCompare(b.rcept_no));
  const offering = new Set(rows.filter((r) => r.report_nm.replace(/\s/g, "").includes("증권신고서(지분증권)")).map((r) => r.corp_code));
  const out = new Map();
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    if (!title.includes("주요사항보고서") || title.includes("철회") || title.includes("첨부정정")) continue;
    const hit = CAPITAL_KINDS.find(([kw]) => title.includes(kw));
    if (!hit) continue;
    if (hit[1] === "유상증자" && !offering.has(r.corp_code)) continue; // 제3자배정 등 비공모는 건너뜀
    const key = `${r.corp_code}:${hit[1]}`;
    const prev = out.get(key);
    out.set(key, {
      corp_code: r.corp_code, corp_name: r.corp_name.trim(), stock_code: (r.stock_code || "").trim(), market: MARKET[r.corp_cls],
      kind: hit[1], rcept_no: r.rcept_no, first_rcept_no: prev ? prev.first_rcept_no : r.rcept_no,
    });
  }
  return [...out.values()];
}

// 정정 보고서 본문엔 정정 전·후가 다 들어 있고 정정 후(현재 일정)가 뒤에 나온다 — 마지막 값을 쓴다.
function lastMatch(t, re) {
  let m = null;
  for (const x of t.matchAll(re)) m = x;
  return m;
}
const ymdOf = (m, i) => `${m[i]}${m[i + 1].padStart(2, "0")}${m[i + 2].padStart(2, "0")}`;

export function parseCapitalText(text) {
  const t = text.replace(/<[^>]+>/g, " ").replace(/&nbsp;|&#160;/g, " ").replace(/\s+/g, " ");
  const cert = lastMatch(t, new RegExp(`신주인수권\\s*증서\\s*(?:의\\s*)?상장\\s*(?:예정)?\\s*기간\\s*:?\\s*${DATE}\\s*[~\\-]\\s*${DATE}`, "g"));
  const listing = lastMatch(t, new RegExp(`신주의\\s*상장\\s*예정일\\s*:?\\s*${DATE}`, "g"));
  const record = lastMatch(t, new RegExp(`신주\\s*배정\\s*기준일\\s*:?\\s*\\(?\\s*${DATE}`, "g"));
  return {
    cert_start: cert ? ymdOf(cert, 1) : null,
    cert_end: cert ? ymdOf(cert, 4) : null,
    listing: listing ? ymdOf(listing, 1) : null,
    record_date: record ? ymdOf(record, 1) : null,
  };
}

export async function fetchCapital(apiKey, rceptNo, fetchImpl = fetch) {
  return { rcept_no: rceptNo, ...parseCapitalText(await documentText(apiKey, rceptNo, fetchImpl)) };
}

// ---- 거래소(KIND) 공시로 먼저 뜬 결정 ----
// 합병·영업양수도 같은 결정은 거래소 공시(접수번호 80/90번대)가 먼저 나오고 DART 주요사항보고서는
// 며칠 뒤에 나오는 경우가 있다. 그 사이에도 캘린더에 보이도록 최근 14일 거래소 공시에서 같은 결정을
// 찾아 본문("doc")으로 읽는다. 같은 회사·같은 종류의 주요사항보고서가 생기면 화면에서 그쪽을 쓴다.
export const EXCHANGE_DEAL_TITLES = [
  ["회사분할합병결정", "분할합병"],
  ["회사합병결정", "합병"],
  ["주식교환ㆍ이전결정", "주식교환·이전"],
  ["주식교환·이전결정", "주식교환·이전"],
  ["영업양수결정", "영업양수"],
  ["영업양도결정", "영업양도"],
  ["회사분할결정", "물적분할"],
  ["중요한자산양수도결정", "중요자산양수도"],
];

export async function fetchExchangeCandidates(apiKey, today, fetchImpl = fetch) {
  const bgn = addDays(today, -13);
  const rows = (await Promise.all([...SCOPE].map((cls) =>
    listAll(apiKey, { bgn_de: bgn, end_de: today, corp_cls: cls, pblntf_ty: "I" }, fetchImpl)))).flat();
  rows.sort((a, b) => a.rcept_no.localeCompare(b.rcept_no));
  const out = new Map();
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    if (title.includes("자회사") || title.includes("종속회사") || title.includes("철회")) continue; // 자회사 결정은 우리 주주 매수청구와 무관
    const hit = EXCHANGE_DEAL_TITLES.find(([kw]) => title.includes(kw));
    if (!hit) continue;
    const key = `${r.corp_code}:${hit[1]}`;
    const prev = out.get(key);
    out.set(key, {
      corp_code: r.corp_code, corp_name: r.corp_name.trim(), stock_code: (r.stock_code || "").trim(), market: MARKET[r.corp_cls],
      api: "doc", kind: hit[1], rcept_no: r.rcept_no, rcept_dt: r.rcept_dt, first_rcept_no: prev ? prev.first_rcept_no : r.rcept_no, source: "거래소",
    });
  }
  return [...out.values()];
}

// ---- 액면변경(주식분할·병합) ----
// 거래소 공시(수시공시 I001) "주식분할결정"/"주식병합결정"에 일정이 있다. 거래소 공시는 양이 많아 30일씩
// 창을 나눠(w=0: 최근 30일, 1: 31~60일 전, 2: 61~90일 전) 따로 부른다 — 요청당 외부 호출 한도 때문.
// 철회 공시가 나오면 그 회사 건은 철회로 본다.
export async function fetchSplitCandidates(apiKey, today, w = 0, fetchImpl = fetch) {
  const end = addDays(today, -30 * w);
  const bgn = addDays(end, -29);
  const rows = (await Promise.all([...SCOPE].map((cls) =>
    listAll(apiKey, { bgn_de: bgn, end_de: end, corp_cls: cls, pblntf_detail_ty: "I001" }, fetchImpl)))).flat();
  rows.sort((a, b) => a.rcept_no.localeCompare(b.rcept_no));
  const out = new Map();
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    const m = title.match(/주식(분할|병합)결정|액면(분할|병합)/);
    if (!m || title.includes("자회사") || title.includes("종속회사")) continue;
    const kind = (m[1] || m[2]) === "분할" ? "액면분할" : "액면병합";
    const prev = out.get(r.corp_code);
    out.set(r.corp_code, {
      corp_code: r.corp_code, corp_name: r.corp_name.trim(), stock_code: (r.stock_code || "").trim(), market: MARKET[r.corp_cls],
      kind, rcept_no: title.includes("철회") && prev ? prev.rcept_no : r.rcept_no,
      first_rcept_no: prev ? prev.first_rcept_no : r.rcept_no,
      withdrawn: title.includes("철회") || Boolean(prev && prev.withdrawn && !title.includes("결정")),
    });
  }
  return [...out.values()];
}

export function parseSplitText(text) {
  const t = text.replace(/<[^>]+>/g, " ").replace(/&nbsp;|&#160;/g, " ").replace(/\s+/g, " ");
  const one = (label) => {
    const m = lastMatch(t, new RegExp(`${label}\\s*:?\\s*${DATE}`, "g"));
    return m ? ymdOf(m, 1) : null;
  };
  const halt = lastMatch(t, new RegExp(`매매\\s*거래\\s*정지\\s*(?:예정)?\\s*기간\\s*시작일\\s*${DATE}\\s*종료일\\s*${DATE}`, "g"));
  const par = lastMatch(t, /1주당\s*가액\s*\(원\)\s*([\d,.]+)\s+([\d,.]+)/g);
  return {
    gm_date: one("주주\\s*총회\\s*예정일"),
    effective: one("신주의\\s*효력\\s*발생일"),
    halt_start: halt ? ymdOf(halt, 1) : null,
    halt_end: halt ? ymdOf(halt, 4) : null,
    listing: one("신주권\\s*상장\\s*예정일"),
    par_before: par ? price(par[1]) : null,
    par_after: par ? price(par[2]) : null,
  };
}

export async function fetchSplit(apiKey, rceptNo, fetchImpl = fetch) {
  return { rcept_no: rceptNo, ...parseSplitText(await documentText(apiKey, rceptNo, fetchImpl)) };
}
