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
export const DEAL_APIS = [
  ["회사분할합병결정", "cmpDvmgDecsn", "분할합병"],
  ["회사합병결정", "cmpMgDecsn", "합병"],
  ["주식교환ㆍ이전결정", "stkExtrDecsn", "주식교환·이전"],
  ["주식교환·이전결정", "stkExtrDecsn", "주식교환·이전"],
  ["영업양수결정", "bsnInhDecsn", "영업양수"],
  ["영업양도결정", "bsnTrfDecsn", "영업양도"],
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
  }
  const rows = (await Promise.all(jobs)).flat().filter((r) => SCOPE.has(r.corp_cls));
  rows.sort((a, b) => a.rcept_no.localeCompare(b.rcept_no)); // 오래된 것부터

  const base = (r) => ({ corp_code: r.corp_code, corp_name: r.corp_name.trim(), stock_code: (r.stock_code || "").trim(), market: MARKET[r.corp_cls] });

  // 매수청구 후보: 회사×API마다 가장 최근 보고서 하나
  const deals = new Map();
  for (const r of rows) {
    const title = r.report_nm.replace(/\s/g, "");
    if (!title.includes("주요사항보고서")) continue;
    const hit = DEAL_APIS.find(([kw]) => title.includes(kw));
    if (!hit) continue;
    deals.set(`${r.corp_code}:${hit[1]}`, { ...base(r), api: hit[1], kind: hit[2], rcept_no: r.rcept_no, rcept_dt: r.rcept_dt });
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
  return { deals: [...deals.values()], tenders };
}

// "(주)진에어 (JIN AIR CO., LTD.) (에어서울(주)에 대한 사항은 …)" → "(주)진에어": 띄어 쓴 괄호 설명과 영문명을 뗀다
export function cleanName(s) {
  let out = String(s).replace(/\s+/g, " ").trim();
  for (let prev = ""; prev !== out;) { prev = out; out = out.replace(/\s+\((?:[^()]|\([^()]*\))*\)\s*$/, "").trim(); }
  return out.replace(/\((?=[A-Za-z])[^()]*\)/g, "").trim();
}

// 매수청구: 구조화 API에서 기간·가격·주총일을 꺼낸다. 필드 이름이 API마다 접두어만 달라서
// (mgsc_aprskh_expd_bgd, extrsc_aprskh_expd_bgd …) 끝부분으로 찾는다.
export async function fetchDeal(apiKey, api, corpCode, today, fetchImpl = fetch) {
  if (!DEAL_LABEL[api]) throw new DartError("알 수 없는 보고서 종류입니다.");
  // 이 API의 기간은 이사회 결의일 기준이라, 결의가 오래전이고 정정만 최근인 건도 잡히게 넉넉히 본다
  const data = await dartRequest(apiKey, `${api}.json`, { corp_code: corpCode, bgn_de: addDays(today, -500), end_de: today }, fetchImpl);
  const list = (data && data.list) || [];
  if (!list.length) return null;
  const x = list.reduce((a, b) => (a.rcept_no > b.rcept_no ? a : b)); // 가장 최근(정정 포함)
  const field = (suffix) => {
    const k = Object.keys(x).find((key) => key.endsWith(suffix));
    return k ? x[k] : null;
  };
  let start = parseKoDate(field("aprskh_expd_bgd"));
  let end = parseKoDate(field("aprskh_expd_edd"));
  const gm = parseKoDate(field("gmtsck_prd"));
  let estimated = false;
  if (!start && !end && gm && (api === "bsnInhDecsn" || api === "bsnTrfDecsn")) {
    // 영업양수·양도 보고서엔 행사기간 칸이 없다 — 상법상 주총 결의일부터 20일(첫날 불산입)
    start = gm;
    end = addDays(gm, 20);
    estimated = true;
  }
  const planned = price(x.aprskh_plnprc);
  if (!start && !end && !planned) return { none: true, rcept_no: x.rcept_no }; // 소규모·간이합병 등 매수청구 없음
  return {
    type: "buyback",
    kind: DEAL_LABEL[api],
    start,
    end,
    estimated,
    gm_date: gm,
    price: planned,
    partner: cleanName(x.mgptncmp_cmpnm || x.extrcmp_cmpnm || x.dlptn_cmpnm || "") || null,
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
  const buf = await dartRequest(apiKey, "document.xml", { rcept_no: rceptNo }, fetchImpl);
  const head = new TextDecoder().decode(new Uint8Array(buf).subarray(0, 200));
  if (!head.startsWith("PK")) {
    const m = head.match(/<message>([^<]*)/);
    throw new DartError(`공시 원문을 받지 못했습니다${m ? `: ${m[1]}` : ""}`);
  }
  const xml = new TextDecoder("utf-8").decode(await unzipFirst(buf));
  return { type: "tender", rcept_no: rceptNo, ...parseTenderText(xml) };
}
