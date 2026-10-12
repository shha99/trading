// 네트워크 없이 DART 응답을 흉내 내서 정규화와 Worker 라우팅을 검증한다. 실행: npm test
import assert from "node:assert/strict";
import { test } from "node:test";

import { categoriesFor, fetchPage, normalize, tagsFor } from "../src/dart.js";
import { handle, todayKst } from "../src/worker.js";

const raw = (n, o = {}) => ({
  corp_code: "00126380", corp_name: "삼성전자", stock_code: "005930", corp_cls: "Y",
  report_nm: "주요사항보고서", rcept_no: `20261012${String(n).padStart(6, "0")}`, flr_nm: "삼성전자",
  rcept_dt: "20261012", rm: "", ...o,
});

const dartResponse = (list, extra = {}) => async () =>
  new Response(JSON.stringify({ status: "000", message: "정상", total_page: 1, total_count: list.length, list, ...extra }));

test("정규화: 시장·거래소·정정·태그", () => {
  const it = normalize(raw(1, { report_nm: "[기재정정]단일판매ㆍ공급계약체결", rm: "유정" }));
  assert.equal(it.market, "코스피");
  assert.equal(it.is_exchange, true);
  assert.equal(it.is_correction, true);
  assert.ok(it.tags.includes("계약") && it.important);
  assert.ok(it.url.endsWith("rcpNo=20261012000001"));

  const plain = normalize(raw(2, { report_nm: "임원ㆍ주요주주특정증권등소유상황보고서", corp_cls: "K" }));
  assert.equal(plain.market, "코스닥");
  assert.equal(plain.is_exchange, false);
  assert.equal(plain.is_correction, false);
  assert.deepEqual(plain.tags, []);
});

test("제목 공백 정리, 태그는 공백 무시", () => {
  assert.equal(normalize(raw(1, { report_nm: "주주총회소집결의              (임시주주총회)" })).report_nm, "주주총회소집결의 (임시주주총회)");
  assert.ok(tagsFor("최대주주 변경").includes("최대주주"));
  assert.ok(tagsFor("연결재무제표기준영업(잠정)실적(공정공시)").includes("실적"));
});

test("거래·권리 영향 분류 (실제 DART 제목)", () => {
  const cases = {
    "[기재정정]주요사항보고서(유상증자결정)": ["증자·감자"],
    "주요사항보고서(감자결정)": ["증자·감자"],
    "현금ㆍ현물배당결정 (분기배당)": ["배당"],
    "현금ㆍ현물배당을위한주주명부폐쇄(기준일)결정": ["배당", "권리락·기준일"],
    "중간(분기)배당락": ["배당", "권리락·기준일"],
    "권리락 (유상증자)": ["증자·감자", "권리락·기준일"],
    "주주명부폐쇄기간또는기준일설정": ["권리락·기준일"],
    "주식소각결정": ["주식수·가격조정"],
    "전환가액ㆍ신주인수권행사가액ㆍ교환가액의조정(안내공시)": ["주식수·가격조정"],
    "주권매매거래정지 (투자자 보호)": ["거래정지"],
    "주권매매거래정지 (상장적격성 실질심사 대상 (사유발생))": ["거래정지", "시장조치·경고"],
    "기타시장안내(관리종목지정우려종목) (시가총액 200억원 미달)": ["시장조치·경고"],
    "불성실공시법인지정 (공시번복)": ["시장조치·경고"],
    "기타시장안내 (최대주주의 의무보유 이행 관련)": ["의무보유"],
    "임원ㆍ주요주주특정증권등소유상황보고서": [],
    "단일판매ㆍ공급계약체결": [],
  };
  for (const [title, want] of Object.entries(cases)) assert.deepEqual(categoriesFor(title), want, title);

  const it = normalize(raw(1, { report_nm: "현금ㆍ현물배당결정" }));
  assert.deepEqual(it.categories, ["배당"]);
  assert.equal(it.important, true);
});

test("fetchPage: 데이터 없음(013)과 오류", async () => {
  const empty = await fetchPage("k", "20261011", 1, async () => new Response(JSON.stringify({ status: "013", message: "없음" })));
  assert.deepEqual(empty.items, []);
  await assert.rejects(fetchPage("k", "20261012", 1, async () => new Response(JSON.stringify({ status: "020", message: "한도 초과" }))), /020/);
  await assert.rejects(fetchPage("", "20261012", 1, dartResponse([])), /DART_API_KEY/);
});

const env = (o = {}) => ({ DART_API_KEY: "test-key", ASSETS: { fetch: async () => new Response("<html>공시 포털</html>") }, ...o });
const ctx = { waitUntil() {} };
const req = (path, headers = {}) => new Request(`https://portal.example${path}`, { headers });

test("/api/page: 키는 서버에서만 붙이고 DART 순서 그대로 돌려준다", async () => {
  let called;
  const fetchImpl = async (url) => {
    called = url;
    return dartResponse([raw(800155, { rm: "유" }), raw(24), raw(1)])();
  };
  const r = await handle(req(`/api/page?date=${todayKst()}&page=1`), env(), ctx, fetchImpl);
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.deepEqual(body.items.map((i) => i.rcept_no.slice(-6)), ["800155", "000024", "000001"]);
  assert.equal(called.searchParams.get("crtfc_key"), "test-key");
  assert.ok(!JSON.stringify(body).includes("test-key"));
});

test("/api/page: 잘못된 날짜·미래·DART 오류", async () => {
  assert.equal((await handle(req("/api/page?date=2026-10-12"), env(), ctx, dartResponse([]))).status, 400);
  assert.equal((await handle(req("/api/page?date=20991231"), env(), ctx, dartResponse([]))).status, 400);
  const noKey = await handle(req("/api/page?date=20261008"), env({ DART_API_KEY: "" }), ctx, dartResponse([]));
  assert.equal(noKey.status, 502);
  assert.match((await noKey.json()).detail, /DART_API_KEY/);
});

test("비밀번호 잠금: 화면·API 모두 막고 /healthz만 열어둔다", async () => {
  const e = env({ PORTAL_PASSWORD: "s3cret" });
  const basic = (pw) => ({ Authorization: `Basic ${btoa(`me:${pw}`)}` });
  assert.equal((await handle(req("/"), e, ctx)).status, 401);
  assert.equal((await handle(req("/api/status"), e, ctx)).status, 401);
  assert.equal((await handle(req("/", basic("wrong")), e, ctx)).status, 401);
  assert.equal((await handle(req("/healthz"), e, ctx)).status, 200);
  const ok = await handle(req("/", basic("s3cret")), e, ctx);
  assert.equal(ok.status, 200);
  assert.match(await ok.text(), /공시 포털/);
  const st = await (await handle(req("/api/status", basic("s3cret")), e, ctx)).json();
  assert.equal(st.api_key_set, true);
  assert.equal(st.today, todayKst());
  assert.ok(st.categories.includes("배당") && st.categories.includes("거래정지"));
});

test("fetchPage: User-Agent를 붙이고, 리다이렉트는 오류로, 오류에 키가 안 새게", async () => {
  let opts;
  await fetchPage("k", "20261012", 1, async (url, o) => { opts = o; return dartResponse([])(); });
  assert.match(opts.headers["User-Agent"], /Mozilla/); // 없으면 DART가 error1.html로 돌려보낸다
  assert.equal(opts.redirect, "manual");

  await assert.rejects(
    fetchPage("secret-key-123", "20261012", 1, async () => new Response(null, { status: 302, headers: { Location: "https://opendart.fss.or.kr/error1.html" } })),
    (e) => /거부/.test(e.message) && !e.message.includes("secret-key-123"),
  );
  await assert.rejects(
    fetchPage("secret-key-123", "20261012", 1, async (url) => { throw new TypeError(`Too many redirects. ${url}, https://x/error1.html`); }),
    (e) => !e.message.includes("secret-key-123") && !e.message.includes("crtfc_key"),
  );
});

test("/api/day: 하루치 한 시장을 여러 페이지 모아서 돌려준다", async () => {
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(url);
    const page = Number(url.searchParams.get("page_no"));
    const list = page === 1 ? [raw(3), raw(2)] : [raw(2), raw(1)]; // 페이지 경계 중복도 걸러야 함
    return dartResponse(list, { total_page: 2, total_count: 3 })();
  };
  const r = await handle(req("/api/day?date=20261008&cls=K"), env(), ctx, fetchImpl);
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.deepEqual(body.items.map((i) => i.rcept_no.slice(-1)), ["3", "2", "1"]);
  assert.equal(calls.length, 2);
  assert.ok(calls.every((u) => u.searchParams.get("corp_cls") === "K" && u.searchParams.get("bgn_de") === "20261008"));

  assert.equal((await handle(req("/api/day?date=20261008&cls=N"), env(), ctx, fetchImpl)).status, 400);
  assert.equal((await handle(req("/api/day?date=20991231&cls=Y"), env(), ctx, fetchImpl)).status, 400);
});

test("/api/market: 네이버 목록을 코스피·코스닥 주식만 남기고 사유 코드를 풀어준다", async () => {
  const rows = [
    { itemcode: "054410", itemname: "케이피티유", sosok: "1", type: "ST", nowPrice: "2450", prevChangeRate: "1.66", managementDate: "20261008", managementReasonCode: "6045", tradeStopYn: "N", marketAlertType: "00" },
    { itemcode: "047400", itemname: "유니온머티리얼", sosok: "0", type: "ST", nowPrice: "633", prevChangeRate: "-25.62", managementDate: "20261008", managementReasonCode: "9999", tradeStopYn: "N", marketAlertType: "02" },
    { itemcode: "001380", itemname: "SG글로벌", sosok: "0", type: "ST", managementDate: "20261007", managementReasonCode: "2008", tradeStopYn: "N" },
    { itemcode: "123456", itemname: "어떤ETF", sosok: "0", type: "ETF", managementDate: "20261001", tradeStopYn: "N" },
    { itemcode: "999999", itemname: "코넥스주", sosok: "2", type: "ST", managementDate: "20261001", tradeStopYn: "N" },
  ];
  let called;
  const fetchImpl = async (url) => { called = url; return new Response(JSON.stringify(rows)); };
  const r = await handle(req("/api/market?kind=management"), env(), ctx, fetchImpl);
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(called.searchParams.get("orderType"), "statusTag");
  assert.deepEqual(body.items.map((i) => i.name), ["케이피티유", "유니온머티리얼", "SG글로벌"]);
  assert.equal(body.items[2].reason, "매출액/주가/시가총액 (코드 2008)");
  assert.equal(body.items[0].reason, "시가총액 40억원 미달 30일 연속");
  assert.equal(body.items[0].market, "코스닥");
  assert.equal(body.items[1].reason, "사유코드 9999");
  assert.equal(body.items[1].alert, "투자경고");

  assert.equal((await handle(req("/api/market?kind=nope"), env(), ctx, fetchImpl)).status, 400);
  const broken = await handle(req("/api/market?kind=warning"), env(), ctx, async () => new Response("<html>", { status: 200 }));
  assert.equal(broken.status, 502);
});

// ---- 캘린더 ----
import { deflateRawSync } from "node:zlib";
import { cleanName, fetchCandidates, fetchDeal, parseAppraisalText, parseKoDate, parseTenderText, unzipFirst } from "../src/events.js";

test("상대회사 이름 정리", () => {
  assert.equal(cleanName("(주)진에어 (JIN AIR CO., LTD.) (에어서울(주)에 대한 사항은 '18. 기타 투자판단과 관련한 중요사항'을 참고하시기 바랍니다.)"), "(주)진에어");
  assert.equal(cleanName("주식회사 우성유통(Woosung Distribution Co., Ltd.)"), "주식회사 우성유통");
  assert.equal(cleanName("에어부산(주)"), "에어부산(주)");
});

test("날짜 파싱", () => {
  assert.equal(parseKoDate("2026년 10월 29일"), "20261029");
  assert.equal(parseKoDate("2026.1.5"), "20260105");
  assert.equal(parseKoDate("-"), null);
});

test("공개매수신고서 본문에서 기간·가격", () => {
  const t = parseTenderText("<P>공개매수기간</P><TD>2026.10.12. - 2026.11.02.(결제일: 2026.11.04)</TD> ... 매수가격 매수예정수량 지급방법 기명식 보통주식 주당 8,000원 최대 16,060,000주");
  assert.deepEqual(t, { start: "20261012", end: "20261102", price: 8000 });
});

function makeZip(name, content) {
  const data = deflateRawSync(Buffer.from(content, "utf8"));
  const nameBuf = Buffer.from(name);
  const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(8, 8);
  local.writeUInt32LE(data.length, 18); local.writeUInt16LE(nameBuf.length, 26);
  const cd = Buffer.alloc(46); cd.writeUInt32LE(0x02014b50, 0); cd.writeUInt16LE(8, 10);
  cd.writeUInt32LE(data.length, 20); cd.writeUInt16LE(nameBuf.length, 28); cd.writeUInt32LE(0, 42);
  const cdOffset = 30 + nameBuf.length + data.length;
  const eocd = Buffer.alloc(22); eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt32LE(cdOffset, 16);
  const all = Buffer.concat([local, nameBuf, data, cd, nameBuf, eocd]);
  return all.buffer.slice(all.byteOffset, all.byteOffset + all.length);
}

test("DART 원문 zip 풀기", async () => {
  const out = await unzipFirst(makeZip("1.xml", "<X>공개매수기간 2026.10.08. - 2026.10.27.</X>"));
  assert.match(new TextDecoder().decode(out), /2026\.10\.27/);
});

const listResp = (list) => async () => new Response(JSON.stringify({ status: "000", total_page: 1, total_count: list.length, list }));

test("캘린더 후보: 공개매수 정정·결과보고서 묶기, 매수청구 후보, 코넥스 제외", async () => {
  const row = (rcept_no, corp_name, report_nm, extra = {}) => ({ rcept_no, corp_code: corp_name === "가비아" ? "00000001" : corp_name === "한화" ? "00000002" : "00000003", corp_name, stock_code: "079940", corp_cls: "K", report_nm, flr_nm: "누군가", rcept_dt: rcept_no.slice(0, 8), ...extra });
  const rows = [
    row("20260720000002", "가비아", "공개매수신고서"),
    row("20260824000263", "가비아", "[기재정정]공개매수신고서"),
    row("20260903000009", "가비아", "[첨부정정]공개매수신고서"),
    row("20260918000001", "가비아", "공개매수결과보고서"),
    row("20261012000001", "한화", "공개매수신고서", { corp_cls: "Y" }),
    row("20260904000355", "우성", "주요사항보고서(회사합병결정)"),
    row("20260909000530", "우성", "[첨부정정]주요사항보고서(회사합병결정)"),
    row("20260828000004", "SK시그넷", "공개매수신고서", { corp_cls: "N" }),
  ];
  const fetchImpl = async (url) => {
    const ty = url.searchParams.get("pblntf_ty") || url.searchParams.get("pblntf_detail_ty");
    const end = url.searchParams.get("end_de");
    if (end !== "20261012" || ty === "C004") return listResp([])(); // 옛날 창·합병 증권신고서는 비어 있게
    const want = rows.filter((r) => (ty === "B" ? r.report_nm.includes("주요사항") : r.report_nm.includes("공개매수")) && (!url.searchParams.get("corp_cls") || r.corp_cls === url.searchParams.get("corp_cls")));
    return listResp(want)();
  };
  const c = await fetchCandidates("k", "20261012", fetchImpl);
  assert.equal(c.tenders.length, 2);
  const gabia = c.tenders.find((t) => t.corp_name === "가비아");
  assert.equal(gabia.rcept_no, "20260824000263"); // 정정 반영
  assert.equal(gabia.done, true);
  assert.equal(c.tenders.find((t) => t.corp_name === "한화").done, false);
  assert.deepEqual(c.deals.map((d) => [d.corp_name, d.api, d.rcept_no]), [["우성", "cmpMgDecsn", "20260909000530"]]);
});

test("매수청구 일정: 합병은 행사기간 칸, 영업양수는 주총일+20일 추정", async () => {
  const merger = { rcept_no: "20260904000355", aprskh_plnprc: "18,313", mgsc_gmtsck_prd: "2026년 10월 29일", mgsc_aprskh_expd_bgd: "2026년 10월 29일", mgsc_aprskh_expd_edd: "2026년 11월 18일", mgptncmp_cmpnm: "우성유통" };
  const e = await fetchDeal("k", "cmpMgDecsn", "00142661", "20261012", listResp([merger]));
  assert.deepEqual([e.start, e.end, e.price, e.estimated, e.partner], ["20261029", "20261118", 18313, false, "우성유통"]);

  const inh = { rcept_no: "20260929000583", aprskh_plnprc: "7,332", gmtsck_prd: "2026년 11월 06일" };
  const b = await fetchDeal("k", "bsnInhDecsn", "00176835", "20261012", listResp([inh]));
  assert.deepEqual([b.start, b.end, b.estimated, b.kind], ["20261106", "20261126", true, "영업양수"]);

  const small = { rcept_no: "20260101000001", aprskh_plnprc: "-", mgsc_aprskh_expd_bgd: "-", mgsc_aprskh_expd_edd: "-" };
  assert.equal((await fetchDeal("k", "cmpMgDecsn", "00000001", "20261012", listResp([small]))).none, true);
});

test("보고서 본문에서 매수청구 기간·가격 읽기", () => {
  // 물적분할 표(금양) — "행사기간 시작일 … 종료일 …"
  const a = parseAppraisalText("주주총회예정일자 2026년 10월 19일 주식매수청구권 행사기간 시작일 2026년 10월 20일 종료일 2026년 11월 08일 채권자 이의제출기간 … 주식매수예정가격 1주당 12,345원");
  assert.deepEqual(a, { start: "20261020", end: "20261108", gm_date: "20261019", price: 12345 });
  // 조건 금액(100억원)은 가격으로 보지 않는다
  const b = parseAppraisalText("매수대금 합이 금 10,000,000,000원을 초과하는 경우 … 행사 기간 2026.11.06 ~ 2026.11.25 … 매수예정가격 7,332원");
  assert.deepEqual([b.start, b.end, b.price], ["20261106", "20261125", 7332]);
  assert.equal(parseAppraisalText("5. 주식매수청구권에 관한 사항 - 해당사항 없습니다. 6. 양수도되는 자산").none, true);
});

test("영업양수: 본문에 기간이 있으면 추정 대신 본문 값", async () => {
  const inh = { rcept_no: "20260929000583", aprskh_plnprc: "7,332", gmtsck_prd: "2026년 11월 06일" };
  const fetchImpl = async (url) => {
    if (url.pathname.endsWith("document.xml")) return new Response(makeZip("1.xml", "<P>주식매수청구권 행사기간 2026년 11월 06일 ~ 2026년 11월 25일</P>"));
    return listResp([inh])();
  };
  const e = await fetchDeal("k", "bsnInhDecsn", "00176835", "20261012", fetchImpl);
  assert.deepEqual([e.start, e.end, e.estimated, e.source], ["20261106", "20261125", false, "본문"]);
});

test("회사분할: 인적분할은 제외, 물적분할은 본문에서 일정", async () => {
  const base = { rcept_no: "20261002000513", gmtsck_prd: "2026년 10월 19일" };
  const human = await fetchDeal("k", "cmpDvDecsn", "1", "20261012", listResp([{ ...base, dv_mth: "인적분할 방식" }]));
  assert.equal(human.none, true);
  const fetchImpl = async (url) => {
    if (url.pathname.endsWith("document.xml")) return new Response(makeZip("1.xml", "주식매수청구권 행사기간 시작일 2026년 10월 20일 종료일 2026년 11월 08일"));
    return listResp([{ ...base, dv_mth: "단순ㆍ물적분할의 방식으로 분할한다" }])();
  };
  const e = await fetchDeal("k", "cmpDvDecsn", "00106119", "20261012", fetchImpl);
  assert.deepEqual([e.kind, e.start, e.end, e.gm_date], ["물적분할", "20261020", "20261108", "20261019"]);
});

test("캘린더 후보: 결정 뒤 철회신고서가 나오면 철회 표시", async () => {
  const rows = [
    { rcept_no: "20260716000668", corp_code: "00605522", corp_name: "아리바이오홀딩스", stock_code: "000000", corp_cls: "K", report_nm: "주요사항보고서(회사합병결정)", rcept_dt: "20260716" },
    { rcept_no: "20260929000637", corp_code: "00605522", corp_name: "아리바이오홀딩스", stock_code: "000000", corp_cls: "K", report_nm: "철회신고서", rcept_dt: "20260929" },
  ];
  const fetchImpl = async (url) => {
    const ty = url.searchParams.get("pblntf_ty") || url.searchParams.get("pblntf_detail_ty");
    if (url.searchParams.get("end_de") !== "20261012") return listResp([])();
    if (ty === "B") return listResp(rows.slice(0, 1).filter((r) => r.corp_cls === url.searchParams.get("corp_cls")))();
    if (ty === "C004") return listResp(rows.slice(1))();
    return listResp([])();
  };
  const c = await fetchCandidates("k", "20261012", fetchImpl);
  assert.equal(c.deals[0].withdrawn_rcept_no, "20260929000637");
});
