// KIND(kind.krx.co.kr) "오늘의 공시" 목록.
//
// 우리 사이트(로컬 테스트 환경)에서는 KIND가 해외 IP라고 막지만(403), Cloudflare Worker에서는 접속된다.
// 공개 API가 아니라 화면용 HTML을 읽으므로 KIND 화면이 바뀌면 깨질 수 있다 — 그러면 새로고침 결과에
// "KIND 확인 실패"만 뜨고 DART 쪽은 그대로 동작한다.
// KIND 접수번호는 DART 접수번호와 같아서 DART 목록과 정확히 맞춰볼 수 있다.
import { normalize } from "./dart.js";

const KIND_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";
export const KIND_VIEWER_URL = "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=";

const MARKET_CLS = { 유가증권: "Y", 코스닥: "K", 코넥스: "N" };

export class KindError extends Error {}

const decode = (s) => String(s)
  .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
  .replace(/&#39;|&apos;/g, "'").replace(/&nbsp;/g, " ").replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n));
const text = (html) => decode(html.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();

export function parseKindList(html) {
  const total = Number((html.match(/전체\s*<em>\s*([\d,]+)\s*<\/em>\s*건/) || [])[1]?.replace(/,/g, "") || 0);
  const items = [];
  for (const row of html.match(/<tr[^>]*>[\s\S]*?<\/tr>/g) || []) {
    const rcept = (row.match(/openDisclsViewer\('(\d{14})'/) || [])[1];
    if (!rcept) continue; // 머리글 행
    const tds = row.match(/<td[^>]*>[\s\S]*?<\/td>/g) || [];
    const market = (tds[1] && (tds[1].match(/alt='([^']+)'/) || tds[1].match(/alt="([^"]+)"/) || [])[1]) || "";
    const corpCode = (row.match(/companysummary_open\('([0-9A-Z]+)'\)/) || [])[1] || "";
    const titleAttr = (row.match(/openDisclsViewer\('\d{14}'[^)]*\)"\s*title='([^']*)'/) || [])[1];
    items.push({
      rcept_no: rcept,
      time: tds[0] ? text(tds[0]) : "",
      market_label: market,
      corp_cls: MARKET_CLS[market] || "E",
      kind_code: corpCode,
      corp_name: decode((row.match(/id="companysum"[^>]*title='([^']*)'/) || [])[1] || (tds[1] ? text(tds[1]) : "")).trim(),
      report_nm: titleAttr !== undefined ? decode(titleAttr) : tds[2] ? text(tds[2]) : "",
      flr_nm: tds[3] ? text(tds[3]) : "",
    });
  }
  return { total, items };
}

// KIND 회사코드는 보통 종목코드 앞 5자리(064350 → 06435). 끝에 0을 붙여 종목코드로 쓴다.
const stockCodeOf = (kindCode) => (/^[0-9A-Z]{5}$/.test(kindCode) ? `${kindCode}0` : "");

// DART 목록 항목과 같은 모양으로 바꾼다(태그·분류 그대로 쓰려고). 거래소 공시(접수번호 80/90번대)는
// DART 비고 "유/코"처럼 표시한다.
export function toItem(k) {
  const exch = /^\d{8}[89]/.test(k.rcept_no);
  const it = normalize({
    rcept_no: k.rcept_no,
    rcept_dt: k.rcept_no.slice(0, 8),
    corp_code: "",
    corp_name: k.corp_name,
    stock_code: stockCodeOf(k.kind_code),
    corp_cls: k.corp_cls,
    report_nm: k.report_nm,
    flr_nm: k.flr_nm,
    rm: exch ? (k.corp_cls === "K" ? "코" : k.corp_cls === "N" ? "넥" : "유") : "",
  });
  return { ...it, source: "KIND", kind_time: k.time, url: KIND_VIEWER_URL + k.rcept_no };
}

// date: YYYYMMDD(없으면 오늘). KIND "오늘의 공시" 화면은 날짜를 골라 지난 날 목록도 보여준다.
export const kindDateParams = (date, today) => (!date || date === today ? { todayFlag: "Y", selDate: "" } : { todayFlag: "N", selDate: `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}` });

async function fetchKindPage(page, fetchImpl, dateParams = { todayFlag: "Y", selDate: "" }) {
  const body = new URLSearchParams({
    method: "searchTodayDisclosureSub", currentPageSize: "100", pageIndex: String(page), orderMode: "0", orderStat: "D",
    forward: "todaydisclosure_sub", marketType: "", searchMode: "", searchCodeType: "", chose: "S",
    repIsuSrtCd: "", kosdaqSegment: "", ...dateParams,
  });
  let html;
  try {
    const resp = await fetchImpl(KIND_URL, {
      method: "POST",
      headers: { "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", Referer: `${KIND_URL}?method=searchTodayDisclosureMain` },
      body,
      redirect: "manual",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    html = await resp.text();
  } catch (e) {
    throw new KindError(`KIND 요청 실패: ${String(e.message).slice(0, 200)}`);
  }
  const parsed = parseKindList(html);
  if (!parsed.items.length && !/전체\s*<em>/.test(html)) throw new KindError("KIND 화면 형식이 바뀌었거나 응답이 비었습니다.");
  return parsed;
}

// 하루치 KIND 공시 전부(최신순). 지난 날짜도 된다.
export async function fetchKindDay(date, today, fetchImpl = fetch, maxPages = 20) {
  const dp = kindDateParams(date, today);
  const first = await fetchKindPage(1, fetchImpl, dp);
  const pages = Math.min(Math.ceil(first.total / 100), maxPages);
  const rest = await Promise.all(Array.from({ length: Math.max(0, pages - 1) }, (_, i) => fetchKindPage(i + 2, fetchImpl, dp)));
  const seen = new Set();
  const items = [];
  for (const k of [first, ...rest].flatMap((p) => p.items)) {
    // 다른 날짜 공시가 섞여 오면(화면 방식이 바뀐 경우) 버린다 — 접수번호 앞 8자리가 날짜
    if (date && date !== today && !k.rcept_no.startsWith(date)) continue;
    if (!seen.has(k.rcept_no)) { seen.add(k.rcept_no); items.push(toItem(k)); }
  }
  return { date, total: first.total, items };
}

// 오늘 KIND 공시 전부(최신순)
export const fetchKindToday = (fetchImpl = fetch, maxPages = 15, today = "") => fetchKindDay(today, today, fetchImpl, maxPages);

// KIND 공시 원문(DART에 아직 없는 공시용). 뷰어 → 본문 문서번호 → 본문 경로 → 본문 HTML 순서로 읽는다.
// 화면용 페이지를 읽는 것이라 KIND 화면이 바뀌면 깨질 수 있다(그러면 KindError).
export async function fetchKindDocHtml(acptno, fetchImpl = fetch) {
  const headers = { "User-Agent": UA, Referer: "https://kind.krx.co.kr/disclosure/todaydisclosure.do" };
  const get = async (url) => {
    const r = await fetchImpl(url, { headers, redirect: "manual" });
    if (!r.ok) throw new KindError(`KIND 원문 요청 실패: HTTP ${r.status}`);
    return r.text();
  };
  try {
    const viewer = await get(`https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=${acptno}&docno=&viewerhost=&viewerport=`);
    // <option value="20261023001893|Y">감자결정 (2026.10.23)</option> — 첫 본문 문서
    const docNo = ([...viewer.matchAll(/<option[^>]*value=['"](\d{10,})(?:\|[^'"]*)?['"]/g)][0] || [])[1];
    if (!docNo) throw new KindError("KIND 원문 문서번호를 찾지 못했습니다.");
    const contents = await get(`https://kind.krx.co.kr/common/disclsviewer.do?method=searchContents&docNo=${docNo}`);
    const path = (contents.match(/['"](\/?external\/[^'"]+\.htm)['"]/) || contents.match(/setPath\([^)]*['"]([^'"]+\.htm)['"]/) || [])[1];
    if (!path) throw new KindError("KIND 원문 경로를 찾지 못했습니다.");
    return await get(new URL(path, "https://kind.krx.co.kr/").href);
  } catch (e) {
    if (e instanceof KindError) throw e;
    throw new KindError(`KIND 원문 요청 실패: ${String(e.message).slice(0, 200)}`);
  }
}
