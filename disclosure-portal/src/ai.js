// AI분석: 과거 기업 이벤트 뒤 실제로 주가가 얼마나 흔들렸는지(통계)로, 지금 이벤트가 걸린 종목의
// 변동성을 예상한다. 머신러닝 모델이 아니라 "같은 종류 이벤트의 과거 사례 분포"다.
//
// 과거 몇 년치 통계("모델")는 scripts/build-ai-model.mjs로 미리 만들어 public/ai-model.json에 둔다.
// 사이트는 최근 공시와 지금 종목 주가만 받아 그 모델을 적용한다.
//
// 1) history: DART 공시 목록에서 이벤트(증자·감자·합병 등·공개매수·액면분할/병합·거래정지·거래재개·
//    상장폐지 우려·정리매매·신규상장)를 30일 단위 블록으로 모은다. 블록은 날짜에 고정돼 있어
//    (오늘이 바뀌어도 지난 블록은 같은 캐시를 쓴다) 지난 블록은 며칠씩 캐시한다.
// 2) metrics: 종목별 일봉(네이버 차트, 수정주가)을 받아 각 이벤트일 기준
//    - 직전 60거래일 하루 변동성(평소 변동성)
//    - 이벤트일부터 10거래일 동안 이벤트 전날 종가 대비 최대 변동폭(장중 고가·저가 포함)
//    - 10거래일 하루 변동성 ÷ 평소 변동성(변동성 배수), 10거래일 뒤 수익률
//    을 계산하고, 비교용으로 같은 종목의 30거래일 전(이벤트 없는 평소) 같은 값도 낸다.
// 확률 계산과 순위는 화면(app.js)이 한다.
import { PAGE_COUNT, dartRequest } from "./dart.js";

const SCOPE = { Y: "코스피", K: "코스닥" };
export const BLOCK_DAYS = 30;
export const HISTORY_BLOCKS = 13; // 사이트가 직접 보는 범위(신규상장 찾기용) 약 13개월
export const CHART_COUNT = 330; // 13개월 + 평소 변동성 60거래일 + 여유
const CHART_URL = "https://fchart.stock.naver.com/sise.nhn";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

// 이벤트 종류. g: 어느 목록에서 찾나(b: 주요사항·지분·발행공시, i: 거래소 공시 — i1 수시공시, i3 시장조치)
export const AI_TYPES = {
  rights: { label: "유상증자", g: "b", re: /유상증자결정/, not: /유무상/ },
  bonus: { label: "무상증자", g: "b", re: /(?:무상|유무상)증자결정/ },
  reduce: { label: "감자", g: "b", re: /감자결정/ },
  merger: { label: "합병·분할·영업양수도(매수청구권)", g: "b", re: /(?:회사분할합병|회사합병|주식교환ㆍ이전|주식교환·이전|영업양수|영업양도|회사분할|중요한자산양수도)결정/ },
  tender: { label: "공개매수", g: "b", re: /공개매수신고서/ },
  split: { label: "액면분할 결정", g: "i", re: /주식분할결정/ },
  consolidate: { label: "액면병합 결정", g: "i", re: /주식병합결정/ },
  relist: { label: "액면변경 후 변경상장(거래재개)", g: "i", re: /거래정지해제.*변경상장/ },
  delist: { label: "정리매매", g: "i", re: /정리매매(?:개시|재개)/ },
  // 액면변경·무상증자·감자처럼 일정상 당연한 정지는 위 종류들로 따로 본다 — 여기는 공시·심사 사유 정지
  halt: { label: "거래정지(사유 발생)", g: "i", re: /주권매매거래정지/, not: /해제|기간변경|미해소|병합|분할|전자등록|무상증자|자본감소|감자|정리매매/ },
  resume: { label: "거래재개(정지해제)", g: "i", re: /주권매매거래정지해제|매매거래정지및정지해제/, not: /변경상장|정리매매/ },
  delist_warn: { label: "관리종목·상장폐지 우려 안내", g: "i", re: /관리종목지정우려|관리종목지정사유추가우려|상장폐지우려|상장폐지여부/ },
  lockup: { label: "신규상장 의무보유(락업) 해제", g: null },
};

// 고정 블록: 1970-01-01부터 30일씩. 블록 k는 [k*30, k*30+29]일.
const DAY0 = Date.UTC(1970, 0, 1);
const toYmd = (ms) => new Date(ms).toISOString().slice(0, 10).replaceAll("-", "");
const toMs = (d) => Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8));
export const blockOf = (ymdStr) => Math.floor((toMs(ymdStr) - DAY0) / 86400000 / BLOCK_DAYS);
export function blockRange(k, today) {
  const bgn = toYmd(DAY0 + k * BLOCK_DAYS * 86400000);
  const end = toYmd(DAY0 + (k * BLOCK_DAYS + BLOCK_DAYS - 1) * 86400000);
  return [bgn, end > today ? today : end];
}

async function listAll(apiKey, params, fetchImpl) {
  const first = await dartRequest(apiKey, "list.json", { ...params, page_no: "1", page_count: String(PAGE_COUNT) }, fetchImpl);
  if (!first) return [];
  const pages = Math.min(Number(first.total_page) || 1, 20);
  const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, i) =>
    dartRequest(apiKey, "list.json", { ...params, page_no: String(i + 2), page_count: String(PAGE_COUNT) }, fetchImpl)));
  return [first, ...rest].flatMap((d) => (d && d.list) || []);
}

// 공시 제목 → 이벤트 종류
export function classify(reportNm, group) {
  const title = reportNm.replace(/\s/g, "");
  if (/자회사|종속회사|철회/.test(title)) return null;
  if (title.startsWith("[")) return null; // [기재정정]·[첨부정정]은 처음 공시가 아니다
  for (const [key, t] of Object.entries(AI_TYPES)) {
    if (t.g !== group || !t.re.test(title) || (t.not && t.not.test(title))) continue;
    return key;
  }
  return null;
}

// 블록 하나의 이벤트 목록. 종목×종류마다 블록 안 첫 공시만 남긴다.
// g="b"·"c"면 신규상장 후보(지분증권 증권신고서를 낸 상장사)도 돌려준다("c"는 그것만).
// delisted: 모델 만들 때만 — 지금은 상장폐지돼 "기타(E)"로 바뀐 회사의 과거 공시도 넣는다(생존편향 줄이기).
export async function fetchAiHistory(apiKey, k, g, today, fetchImpl = fetch, { delisted = false } = {}) {
  const group = g[0]; // 제목 분류는 b / i 두 갈래
  const [bgn, end] = blockRange(k, today);
  if (bgn > today) return { block: k, bgn, end, events: [], ipo: [] };
  const jobs = [];
  const classes = delisted ? [...Object.keys(SCOPE), "E"] : Object.keys(SCOPE);
  if (g === "c") {
    jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, pblntf_detail_ty: "C001" }, fetchImpl));
  } else if (g === "b") {
    for (const cls of classes) jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, corp_cls: cls, pblntf_ty: "B" }, fetchImpl));
    jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, pblntf_detail_ty: "D004" }, fetchImpl)); // 공개매수
    jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, pblntf_detail_ty: "C001" }, fetchImpl)); // 지분증권 증권신고서(공모·상장)
  } else if (g === "i1") { // 거래소 수시공시(액면분할·병합 결정) — 양이 많아 따로
    for (const cls of classes) jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, corp_cls: cls, pblntf_detail_ty: "I001" }, fetchImpl));
  } else if (g === "i3") { // 시장조치·안내(거래정지·재개·변경상장·상장폐지)
    for (const cls of Object.keys(SCOPE)) jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, corp_cls: cls, pblntf_detail_ty: "I003" }, fetchImpl));
    // 정리매매 중인 회사는 이미 "기타(E)"로 분류돼 있다
    jobs.push(listAll(apiKey, { bgn_de: bgn, end_de: end, corp_cls: "E", pblntf_detail_ty: "I003" }, fetchImpl));
  } else {
    throw new Error("g는 b, c, i1, i3 중 하나");
  }
  const rows = (await Promise.all(jobs)).flat().filter((r) => /^[0-9A-Z]{6}$/.test((r.stock_code || "").trim()) &&
    (r.corp_cls in SCOPE || (r.corp_cls === "E" && (delisted || classify(r.report_nm, group) === "delist"))));
  rows.sort((a, b) => a.rcept_no.localeCompare(b.rcept_no));
  const events = new Map();
  const ipo = new Map();
  for (const r of rows) {
    const code = r.stock_code.trim();
    const name = r.corp_name.trim();
    if (g === "c" || r.report_nm.includes("증권신고서(지분증권)")) {
      if (!r.report_nm.includes("증권신고서(지분증권)") || !(r.corp_cls in SCOPE || delisted)) continue;
      if (!/스팩|기업인수목적/.test(name)) ipo.set(code, { c: code, n: name, m: SCOPE[r.corp_cls] || "상장폐지" });
      continue;
    }
    const t = classify(r.report_nm, group);
    if (!t || events.has(`${t}:${code}`)) continue;
    events.set(`${t}:${code}`, { t, c: code, n: name, m: SCOPE[r.corp_cls] || "상장폐지", d: r.rcept_dt, r: r.rcept_no, title: r.report_nm.trim().slice(0, 60) });
  }
  return { block: k, bgn, end, events: [...events.values()], ipo: [...ipo.values()] };
}

// ---- 주가 ----
// 거래정지일은 시가·고가·저가가 0인 줄로 온다 — 거래된 날만 남긴다. first/count는 정지일 포함 원래 줄 기준.
export function parseChart(xml) {
  const rows = [];
  let first = null;
  let count = 0;
  for (const m of xml.matchAll(/data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"/g)) {
    count++;
    if (!first) first = m[1];
    const [h, l, c] = [+m[3], +m[4], +m[5]];
    if (c > 0 && h > 0 && l > 0) rows.push({ d: m[1], h, l, c });
  }
  rows.first = first;
  rows.count = count;
  return rows;
}

export async function fetchChart(code, fetchImpl = fetch, count = CHART_COUNT) {
  const resp = await fetchImpl(`${CHART_URL}?symbol=${code}&timeframe=day&count=${count}&requestType=0`, { headers: { "User-Agent": UA } });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return parseChart(await resp.text());
}

const round = (x, n = 4) => (Number.isFinite(x) ? Math.round(x * 10 ** n) / 10 ** n : null);

function dailyVol(rows, from, to) { // rows[from..to] 종가의 하루 로그수익률 표준편차
  const r = [];
  for (let i = Math.max(from, 1); i <= to; i++) r.push(Math.log(rows[i].c / rows[i - 1].c));
  if (r.length < 5) return null;
  const mean = r.reduce((a, b) => a + b, 0) / r.length;
  return Math.sqrt(r.reduce((a, b) => a + (b - mean) ** 2, 0) / (r.length - 1));
}

export const WINDOW = 10; // 이벤트 뒤 10거래일
const BASE_DAYS = 60;

// i0(이벤트 뒤 첫 거래일 인덱스) 기준 측정값. ended: 상장폐지 등으로 거래가 끝난 종목(남은 날만 본다)
function measure(rows, i0, ended = false) {
  if (i0 < 16) return null; // 평소 변동성은 15거래일 이상으로
  const n = Math.min(WINDOW, rows.length - i0);
  if (n < (ended ? 3 : WINDOW)) return null; // 아직 10거래일이 안 지났으면 측정하지 않는다
  const base = dailyVol(rows, Math.max(1, i0 - BASE_DAYS), i0 - 1);
  if (!base) return null;
  const ref = rows[i0 - 1].c;
  let move = 0;
  for (let i = i0; i < i0 + n; i++) move = Math.max(move, rows[i].h / ref - 1, 1 - rows[i].l / ref);
  const post = dailyVol(rows, i0, i0 + n - 1);
  return { base: round(base), move: round(move), ratio: post ? round(post / base, 2) : null, ret: round(rows[i0 + n - 1].c / ref - 1) };
}

const shiftYmd = (d, n) => toYmd(toMs(d) + n * 86400000);
const addMonths = (d, n) => {
  const x = new Date(Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1 + n, +d.slice(6, 8)));
  return toYmd(x.getTime());
};

// 한 종목의 측정값. dates: 이벤트일(YYYYMMDD) 목록, wantListing: 신규상장 락업 해제일 계산 여부
export function stockMetrics(rows, dates, wantListing, today, count = CHART_COUNT) {
  const ended = Boolean(today && rows.length && rows[rows.length - 1].d < shiftYmd(today, -10)); // 열흘 넘게 거래 없음(폐지 등)
  const out = { now_base: rows.length > 20 ? round(dailyVol(rows, rows.length - BASE_DAYS, rows.length - 1)) : null, last: rows.length ? rows[rows.length - 1].d : null, ev: {} };
  const idx = (d) => { // d 이후 첫 거래일
    let lo = 0;
    let hi = rows.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (rows[mid].d < d) lo = mid + 1; else hi = mid; }
    return lo;
  };
  const one = (d) => {
    const i0 = idx(d);
    const m = i0 < rows.length ? measure(rows, i0, ended) : null;
    const ctrl = i0 - 30 >= 61 ? measure(rows, i0 - 30) : null; // 비교군: 같은 종목 30거래일 전
    // 이벤트 직전 평소 변동성(진행 중인 이벤트의 예측에 쓴다 — 이벤트 뒤 움직임이 섞이지 않게)
    const pre = i0 >= 16 ? round(dailyVol(rows, Math.max(1, i0 - BASE_DAYS), i0 - 1)) : null;
    let sofar = null; // 아직 10거래일이 안 지난 이벤트: 지금까지 최대 변동폭
    if (!m && i0 >= 1 && i0 < rows.length) {
      const ref = rows[i0 - 1].c;
      sofar = 0;
      for (let i = i0; i < rows.length; i++) sofar = Math.max(sofar, rows[i].h / ref - 1, 1 - rows[i].l / ref);
      sofar = { move: round(sofar), days: rows.length - i0 };
    }
    return { m, k: ctrl, pre, sofar };
  };
  for (const d of dates) out.ev[d] = one(d);
  // 신규상장: 차트가 CHART_COUNT보다 짧으면 첫 거래일이 상장일. 1·3·6·12개월 뒤가 흔한 의무보유 해제 시점.
  if (wantListing && rows.first && rows.count < count) {
    out.listed = rows.first;
    out.lockups = [1, 3, 6, 12].map((n) => {
      const d = addMonths(rows.first, n);
      return { months: n, d, ...one(d) };
    });
  }
  return out;
}

// q: "005930:20260105,005930:L,..." (L = 신규상장 락업)
export function parseMetricsQuery(q) {
  const by = new Map();
  for (const part of String(q || "").split(",")) {
    const m = part.match(/^([0-9A-Z]{6}):(\d{8}|L)$/);
    if (!m) continue;
    if (!by.has(m[1])) by.set(m[1], { dates: [], listing: false });
    if (m[2] === "L") by.get(m[1]).listing = true;
    else by.get(m[1]).dates.push(m[2]);
  }
  return by;
}

export async function fetchAiMetrics(by, today, fetchImpl = fetch, count = CHART_COUNT) {
  const out = {};
  await Promise.all([...by].map(async ([code, want]) => {
    try {
      out[code] = stockMetrics(await fetchChart(code, fetchImpl, count), want.dates, want.listing, today, count);
    } catch (e) {
      out[code] = { error: String(e.message).slice(0, 80) };
    }
  }));
  return out;
}

