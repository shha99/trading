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
import { fetchExchangeCandidates } from "../src/events.js";
import { cleanName, fetchCandidates, fetchCapitalCandidates, fetchDeal, parseAppraisalText, parseCapitalText, parseKoDate, parseTenderText, unzipFirst } from "../src/events.js";

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
  // [첨부정정]은 본문이 없어 건너뛰고 그 전 보고서를 읽는다
  assert.deepEqual(c.deals.map((d) => [d.corp_name, d.api, d.rcept_no]), [["우성", "cmpMgDecsn", "20260904000355"]]);
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

test("DART 5xx·연결 실패는 두 번까지 재시도, 그래도 안 되면 알기 쉬운 메시지", async () => {
  let n = 0;
  const flaky = async () => (++n < 3 ? new Response("", { status: 522 }) : dartResponse([raw(1)])());
  const ok = await fetchPage("k", "20261012", 1, flaky);
  assert.equal(ok.items.length, 1);
  assert.equal(n, 3);

  let m = 0;
  await assert.rejects(fetchPage("secret", "20261012", 1, async () => { m++; return new Response("", { status: 522 }); }),
    (e) => /제때 응답하지 않았습니다/.test(e.message) && !e.message.includes("secret"));
  assert.equal(m, 3);

  let k = 0;
  await assert.rejects(fetchPage("k", "20261012", 1, async () => { k++; return new Response("", { status: 404 }); }), /HTTP 404/);
  assert.equal(k, 1); // 4xx는 재시도하지 않는다
});

test("증자 본문: 정정 전·후가 있으면 뒤(정정 후) 일정, 일정변경 표는 무시", () => {
  const t = "16. 신주의 상장예정일 일정 변경 2026년 12월 23일 2027년 01월 14일 … (3) 신주인수권증서 상장예정기간 : 2026년 11월 06일 ~ 2026년 11월 12일 … 신주배정기준일( 2026년 10월 20일 예정) … " +
    "16. 신주의 상장예정일 2027년 01월 14일 … (3) 신주인수권증서 상장예정기간 : 2026년 12월 03일 ~ 2026년 12월 09일 … 신주배정기준일( 2026년 11월 16일 예정)";
  assert.deepEqual(parseCapitalText(t), { cert_start: "20261203", cert_end: "20261209", listing: "20270114", record_date: "20261116" });
  assert.deepEqual(parseCapitalText("무상증자 … 신주의 상장예정일 2026년 11월 10일"), { cert_start: null, cert_end: null, listing: "20261110", record_date: null });
});

test("증자 후보: 유상증자는 증권신고서 낸 회사만, 무상은 전부", async () => {
  const mk = (rcept_no, corp_code, corp_name, report_nm) => ({ rcept_no, corp_code, corp_name, stock_code: "000000", corp_cls: "K", report_nm, rcept_dt: rcept_no.slice(0, 8) });
  const B = [
    mk("20261015000277", "01344202", "뷰노", "[기재정정]주요사항보고서(유상증자결정)"),
    mk("20261001000001", "00000009", "제3자배정회사", "주요사항보고서(유상증자결정)"),
    mk("20261001000135", "00000010", "MH에탄올", "주요사항보고서(무상증자결정)"),
  ];
  const C = [mk("20261015000309", "01344202", "뷰노", "[기재정정]증권신고서(지분증권)")];
  const fetchImpl = async (url) => {
    const ty = url.searchParams.get("pblntf_ty") || url.searchParams.get("pblntf_detail_ty");
    if (ty === "B") return listResp(url.searchParams.get("corp_cls") === "K" ? B : [])();
    return listResp(C)();
  };
  const c = await fetchCapitalCandidates("k", "20261015", fetchImpl);
  assert.deepEqual(c.map((x) => [x.corp_name, x.kind]).sort(), [["MH에탄올", "무상증자"], ["뷰노", "유상증자"]]);
});

test("거래소 공시로 먼저 뜬 영업양도결정도 후보로, 자회사 결정은 제외", async () => {
  const mk = (rcept_no, corp_name, report_nm, corp_code = "00106623") => ({ rcept_no, corp_code, corp_name, stock_code: "011210", corp_cls: "Y", report_nm, rcept_dt: rcept_no.slice(0, 8) });
  const rows = [
    mk("20261019800137", "현대위아", "영업양도결정"),
    mk("20261019800160", "현대위아", "주주총회소집결의"),
    mk("20261018800001", "어떤지주", "회사합병결정(자회사의 주요경영사항)", "00000001"),
  ];
  const c = await fetchExchangeCandidates("k", "20261019", async (url) => listResp(url.searchParams.get("corp_cls") === "Y" ? rows : [])());
  assert.deepEqual(c.map((x) => [x.corp_name, x.kind, x.api, x.rcept_no]), [["현대위아", "영업양도", "doc", "20261019800137"]]);
});

test("매수예정가격 뒤에 '원'이 없어도 읽는다", () => {
  const t = parseAppraisalText("매수예정가격 51,811 행사절차, 방법 … (3) 행사기간: 2026년 11월 27일부터 2026년 12월 16일까지 … 금 일천사백억원(140,000,000,000원)을 초과");
  assert.deepEqual([t.start, t.end, t.price], ["20261127", "20261216", 51811]);
});

// ---- KIND ----
import { parseKindList, toItem } from "../src/kind.js";

const KIND_HTML = `<table><thead><tr><th>시간</th></tr></thead><tbody>
<tr id="parkman" class="first"> <td class="first txc">17:01</td> <td><img src='/x.gif' class='vmiddle legend' alt='유가증권'> <a id="companysum" href="#companysum" onclick="companysummary_open('06435'); return false;" title='현대로템'> 현대로템</a> </td> <td><a href="#viewer" onclick="openDisclsViewer('20261019000576','')" title='자기주식 취득 결정'>자기주식 취득 결정</a></td> <td>현대로템</td> <td class="txc"></td> </tr>
<tr id="parkman" > <td class="first txc">16:40</td> <td><img src='/x.gif' class='vmiddle legend' alt='유가증권'> <a id="companysum" href="#companysum" onclick="companysummary_open('01121'); return false;" title='현대위아'> 현대위아</a> </td> <td><a href="#viewer" onclick="openDisclsViewer('20261019800137','')" title='영업양도결정'>영업양도결정</a></td> <td>현대위아</td> <td></td> </tr>
</tbody></table><div class="info type-00"> 전체 <em>2</em>건 : <strong>1</strong>/1 </div>`;

test("KIND 오늘의 공시 HTML 읽기", () => {
  const p = parseKindList(KIND_HTML);
  assert.equal(p.total, 2);
  assert.deepEqual(p.items.map((i) => [i.rcept_no, i.time, i.corp_name, i.report_nm, i.corp_cls, i.kind_code]), [
    ["20261019000576", "17:01", "현대로템", "자기주식 취득 결정", "Y", "06435"],
    ["20261019800137", "16:40", "현대위아", "영업양도결정", "Y", "01121"],
  ]);
  const ex = toItem(p.items[1]);
  assert.equal(ex.market, "코스피");
  assert.equal(ex.stock_code, "011210");
  assert.equal(ex.is_exchange, true); // 거래소 공시(80번대)
  assert.equal(ex.source, "KIND");
  assert.match(ex.url, /kind\.krx\.co\.kr.*20261019800137/);
  assert.ok(toItem(p.items[0]).tags.includes("자사주"));
});

test("/api/kind/today: KIND 페이지를 받아 DART 항목 모양으로", async () => {
  let posted;
  const r = await handle(req("/api/kind/today?fresh=1"), env(), ctx, async (url, o) => { posted = o; return new Response(KIND_HTML); });
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(body.total, 2);
  assert.equal(body.items.length, 2);
  assert.equal(posted.method, "POST");
  const bad = await handle(req("/api/kind/today?fresh=1"), env(), ctx, async () => new Response("<html>점검중</html>"));
  assert.equal(bad.status, 502);
});

import { parseSplitText } from "../src/events.js";
test("액면변경(병합·분할) 본문 일정 — 유가(매매거래정지기간)·코스닥(매매거래정지예정기간) 둘 다", () => {
  const y = parseSplitText("1주당 가액(원) 339 1,017 발행주식총수 … 주주총회예정일 2026-11-10 구주권제출기간 시작일 - 종료일 - 신주의 효력발생일 2026-11-26 매매거래정지기간 시작일 2026-11-24 종료일 2027-01-12 명의개서정지기간 시작일 - 종료일 - 신주권상장예정일 2027-01-13");
  assert.deepEqual(y, { gm_date: "20261110", effective: "20261126", halt_start: "20261124", halt_end: "20270112", listing: "20270113", par_before: 339, par_after: 1017 });
  const k = parseSplitText("1주당 가액(원) 500 2,500 … 신주의 효력발생일 2026-12-29 매매거래정지예정기간 시작일 2026-12-24 종료일 2027-01-18 명의개서정지기간 - 신주권교부예정일 - 신주권상장예정일 2027-01-19");
  assert.deepEqual([k.halt_start, k.halt_end, k.listing, k.par_after], ["20261224", "20270118", "20270119", 2500]);
});

// ---- 사람별 로그인 ----
import { parseUsers } from "../src/auth.js";

test("PORTAL_USERS 파싱", () => {
  const u = parseUsers("hyun:Abc:123!, kim:qwer\nlee:pw3, broken, :nopw");
  assert.deepEqual([...u.entries()], [["hyun", "Abc:123!"], ["kim", "qwer"], ["lee", "pw3"]]);
});

test("로그인: 화면·API 막고, 맞는 계정이면 쿠키로 통과, 목록에서 빼면 바로 거절", async () => {
  const users = "hyun:Abc123!,kim:qwer5678";
  const e = env({ PORTAL_USERS: users });
  const get = (path, cookie) => handle(new Request(`https://portal.example${path}`, { headers: cookie ? { Cookie: cookie } : {} }), e, ctx);

  let r = await get("/");
  assert.equal(r.status, 302);
  assert.match(r.headers.get("Location"), /\/login\?next=%2F$/);
  r = await get("/api/status");
  assert.equal(r.status, 401);
  assert.equal((await r.json()).login, true);
  assert.equal((await get("/healthz")).status, 200);
  assert.match(await (await get("/login")).text(), /로그인/);

  const post = (id, pw, next = "/") => handle(new Request("https://portal.example/login", {
    method: "POST", body: new URLSearchParams({ id, pw, next }), headers: { "Content-Type": "application/x-www-form-urlencoded" },
  }), e, ctx);
  r = await post("hyun", "wrong");
  assert.equal(r.status, 401);
  assert.match(await r.text(), /맞지 않습니다/);
  r = await post("nobody", "Abc123!");
  assert.equal(r.status, 401);

  r = await post("hyun", "Abc123!", "//evil.example/x"); // 외부로 튕기는 next는 무시
  assert.equal(r.status, 302);
  assert.equal(r.headers.get("Location"), "/");
  const setCookie = r.headers.get("Set-Cookie");
  assert.match(setCookie, /dp_session=.+HttpOnly; Secure/);
  const cookie = setCookie.split(";")[0];

  r = await get("/api/status", cookie);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).user, "hyun");
  assert.equal((await get("/", cookie)).status, 200);

  // 위조 쿠키 거절
  assert.equal((await get("/api/status", cookie.replace(/.$/, (c) => (c === "A" ? "B" : "A")))).status, 401);
  // 목록을 바꾸면(사람 삭제) 기존 쿠키는 무효
  const e2 = env({ PORTAL_USERS: "kim:qwer5678" });
  assert.equal((await handle(new Request("https://portal.example/api/status", { headers: { Cookie: cookie } }), e2, ctx)).status, 401);
  // 로그아웃은 쿠키를 지운다
  r = await get("/logout", cookie);
  assert.match(r.headers.get("Set-Cookie"), /Max-Age=0/);
});

// ---- AI분석 ----
import { blockOf, blockRange, classify, parseChart, parseMetricsQuery, stockMetrics } from "../src/ai.js";

test("AI분석: 공시 제목 → 이벤트 종류", () => {
  assert.equal(classify("주요사항보고서(유상증자결정)", "b"), "rights");
  assert.equal(classify("주요사항보고서(전환사채권발행결정)", "b"), "cb");
  assert.equal(classify("주요사항보고서(신주인수권부사채권발행결정)", "b"), "bw");
  assert.equal(classify("주요사항보고서(교환사채권발행결정)", "b"), "eb");
  assert.equal(classify("주요사항보고서(유무상증자결정)", "b"), "bonus");
  assert.equal(classify("[기재정정]주요사항보고서(유상증자결정)", "b"), null);
  assert.equal(classify("주요사항보고서(회사합병결정)", "b"), "merger");
  assert.equal(classify("주요사항보고서(자회사의 주요경영사항)", "b"), null);
  assert.equal(classify("주식병합결정", "i"), "consolidate");
  assert.equal(classify("주권매매거래정지해제(액면병합 주권 변경상장)", "i"), "relist");
  assert.equal(classify("주권매매거래정지(주식의 병합, 분할 등 전자등록 변경, 말소)", "i"), null);
  assert.equal(classify("주권매매거래정지(풍문 또는 보도 관련)", "i"), "halt");
  assert.equal(classify("주권매매거래정지해제", "i"), "resume");
  assert.equal(classify("주권매매거래정지해제(상장폐지에 따른 정리매매 개시)", "i"), "delist");
  assert.equal(classify("기타시장안내(관리종목지정 우려 종목)(주가 1,000원 미달)", "i"), "delist_warn");
  assert.equal(classify("전환청구권행사 (제3회차)", "i"), "conv_exercise");
  assert.equal(classify("전환가액ㆍ신주인수권행사가액ㆍ교환가액의조정", "i"), "refix_adj");
  assert.equal(classify("자기전환사채매도결정", "i"), "cb_resale");
  assert.equal(classify("주식매수선택권행사", "i"), null);
  assert.equal(classify("권리락              (무상증자)", "i"), "exright_b");
});

test("AI분석: 30일 고정 블록", () => {
  const k = blockOf("20261020");
  const [bgn, end] = blockRange(k, "20261020");
  assert.ok(bgn <= "20261020" && end === "20261020");
  assert.equal(blockRange(k - 1, "20261020")[1] < bgn, true);
});

test("AI분석: 차트 파싱(정지일 제외)과 이벤트 전후 측정", () => {
  const lines = [];
  let d = Date.UTC(2026, 0, 1);
  const days = [];
  for (let i = 0; i < 120; i++) { days.push(new Date(d).toISOString().slice(0, 10).replaceAll("-", "")); d += 86400000; }
  days.forEach((day, i) => {
    const c = i < 100 ? 1000 + (i % 2 ? 10 : -10) : 1300; // 100번째 날부터 +30% 급등
    lines.push(i === 50 ? `<item data="${day}|0|0|0|1000|0" />` : `<item data="${day}|${c}|${c + 5}|${c - 5}|${c}|100" />`);
  });
  const rows = parseChart(lines.join("\n"));
  assert.equal(rows.length, 119);
  assert.equal(rows.count, 120);
  const m = stockMetrics(rows, [days[100], days[115]], true, days[119]);
  const ev = m.ev[days[100]];
  assert.ok(ev.m.move > 0.29 && ev.m.move < 0.32);
  assert.ok(ev.m.ret > 0.28);
  assert.ok(ev.pre > 0 && ev.k);
  assert.equal(m.ev[days[115]].m, null); // 10거래일이 안 지났다
  assert.equal(m.ev[days[115]].sofar.days, 5); // 115~119일
  assert.equal(m.listed, days[0]); // 차트가 짧으면 신규상장으로 본다
  assert.deepEqual(m.lockups.map((l) => l.months), [1, 3, 6, 12]);
  const q = parseMetricsQuery("005930:20260105,005930:L,bad,0015N0:20260101");
  assert.deepEqual([...q.keys()], ["005930", "0015N0"]);
  assert.equal(q.get("005930").listing, true);
});

test("AI분석 API: 입력 검사", async () => {
  const e = env();
  const get = (p) => handle(new Request(`https://portal.example${p}`), e, ctx);
  assert.equal((await get("/api/ai/history?k=1&g=b")).status, 400);
  assert.equal((await get(`/api/ai/history?k=${blockOf("20261020")}&g=x`)).status, 400);
  assert.equal((await get("/api/ai/metrics?q=")).status, 400);
});

test("AI분석: 메자닌·유상증자 희석률", async () => {
  const { parseDilution, bucketOf } = await import("../src/ai.js");
  assert.deepEqual(parseDilution("cb", { cvisstk_tisstk_vs: "19.52", act_mktprcfl_cvprc_lwtrsprc: "798", cvrqpd_bgd: "2025년 07월 18일", cvrqpd_edd: "2027년 06월 18일" }),
    { pct: 19.52, refix: true, bgd: "20250718", edd: "20270618" });
  assert.deepEqual(parseDilution("bw", { nstk_isstk_tisstk_vs: "2.72" }), { pct: 2.72, bgd: null, edd: null });
  assert.deepEqual(parseDilution("eb", { extg_tisstk_vs: "-" }), { pct: null, bgd: null, edd: null });
  const r = parseDilution("rights", { nstk_ostk_cnt: "-", nstk_estk_cnt: "1,000", bfic_tisstk_ostk: "9,000", bfic_tisstk_estk: "1,000", ic_mthn: "제3자배정증자" });
  assert.deepEqual(r, { pct: 10, rcps: true, method: "제3자배정증자" });
  assert.equal(bucketOf(3), 0);
  assert.equal(bucketOf(10), 2);
  assert.equal(bucketOf(55), 3);
  assert.equal(bucketOf(null), -1);
});

test("AI분석: 유통주식 비율", async () => {
  const { parseFloat_, floatBucketOf } = await import("../src/ai.js");
  const f = parseFloat_(
    [{ nm: "LS전선㈜", stock_knd: "보통주", trmend_posesn_stock_co: "13,501,695" }, { nm: "계", stock_knd: "보통주", trmend_posesn_stock_co: "13,518,091" }],
    [{ se: "보통주", istc_totqy: "16,543,115", tesstk_co: "-" }],
  );
  assert.equal(f.float, 0.1829);
  assert.equal(floatBucketOf(0.1829), 0);
  assert.equal(floatBucketOf(0.6), 3);
  assert.equal(parseFloat_([], []), null);
});

test("액면변경 후보: [첨부정정]은 본문이 없어 그 전 보고서를 읽는다", async () => {
  const { fetchSplitCandidates } = await import("../src/events.js");
  const rows = [
    { rcept_no: "20260728900305", corp_code: "00550082", corp_name: "빅텐츠", stock_code: "210120", corp_cls: "K", report_nm: "[기재정정]주식병합결정" },
    { rcept_no: "20260811900499", corp_code: "00550082", corp_name: "빅텐츠", stock_code: "210120", corp_cls: "K", report_nm: "[기재정정]주식병합결정" },
    { rcept_no: "20260811900624", corp_code: "00550082", corp_name: "빅텐츠", stock_code: "210120", corp_cls: "K", report_nm: "[첨부정정]주식병합결정" },
  ];
  const fakeFetch = async (url) => {
    const cls = new URL(url).searchParams.get("corp_cls");
    return new Response(JSON.stringify({ status: "000", total_page: 1, list: cls === "K" ? rows : [] }), { headers: { "Content-Type": "application/json" } });
  };
  const items = await fetchSplitCandidates("key", "20260820", 0, fakeFetch);
  assert.equal(items.length, 1);
  assert.equal(items[0].rcept_no, "20260811900499");
  assert.equal(items[0].first_rcept_no, "20260728900305");
});

test("코스피200·코스닥150 구성종목", async () => {
  const { parseEtfHoldings, fetchIndexMembers } = await import("../src/market.js");
  const html = 'x var CU_data = {"grid_data":[{"STK_NM_KOR":"알테오젠"},{"STK_NM_KOR":"원화현금"},{"STK_NM_KOR":"HLB"}],"chart_data":[]}; y';
  assert.deepEqual(parseEtfHoldings(html), ["알테오젠", "HLB"]);
  const fake = async (url) => {
    const u = String(url);
    if (u.includes("KPI200")) return Response.json(u.includes("page=1&") ? [{ itemCode: "005930" }, { itemCode: "000660" }] : []);
    if (u.includes("wisereport")) return new Response(html);
    if (u.includes("marketValue")) return Response.json(u.includes("page=1&") ? { stocks: [{ stockName: "알테오젠", itemCode: "196170" }, { stockName: "HLB", itemCode: "028300" }] } : {});
    return new Response("", { status: 404 });
  };
  const r = await fetchIndexMembers(fake);
  assert.deepEqual([r.ks200, r.kq150, r.kq_missing], [["005930", "000660"], ["196170", "028300"], []]);
});

test("/api/kind/day: 지난 날짜는 KIND에 날짜를 골라 요청한다", async () => {
  let posted;
  const r = await handle(req("/api/kind/day?date=20261021&fresh=1"), env(), ctx, async (url, o) => { posted = o; return new Response(KIND_HTML); });
  assert.equal(r.status, 200);
  const sent = new URLSearchParams(posted.body);
  assert.equal(sent.get("selDate"), "2026-10-21");
  assert.equal(sent.get("todayFlag"), "N");
  assert.equal((await handle(req("/api/kind/day?date=29991231"), env(), ctx)).status, 400);
});

test("증자 일정: 결정 뒤 투자설명서·정정 서류까지 읽어 가장 최근 값을 쓴다", async () => {
  const { fetchCapital } = await import("../src/events.js");
  const docs = {
    "20261001000449": "<P>신주의 상장 예정일 2026년 11월 30일 신주배정기준일 2026년 10월 06일 신주인수권증서의 상장여부 예</P>",
    "20261001000485": "<P>2026년 10월 26일 신주인수권증서 상장일 5거래일 이상 동안 거래(26.10.26~26.10.30) 2026년 11월 02일 신주인수권증서 상장폐지</P>",
    "20260914000001": "<P>2026년 10월 20일 신주인수권증서 상장일 (26.10.20~26.10.24)</P>", // 더 옛 투자설명서 — 무시돼야
  };
  const fake = async (url) => new Response(makeZip("1.xml", docs[new URL(url).searchParams.get("rcept_no")] || "<P></P>"));
  const e = await fetchCapital("k", "20261001000449", fake, "", ["20261001000485", "20260914000001"]);
  assert.deepEqual([e.cert_start, e.cert_end, e.listing, e.record_date], ["20261026", "20261030", "20261130", "20261006"]);
  assert.deepEqual(e.from, ["20261001000485"]);
});

test("증자 일정: 결정 보고서 본문이 아직 없어도 공모 서류에서 읽는다", async () => {
  const { fetchCapital } = await import("../src/events.js");
  const fake = async (url) => {
    const r = new URL(url).searchParams.get("rcept_no");
    if (r === "20261001000449") return new Response("<result><status>014</status><message>파일이 존재하지 않습니다.</message></result>");
    return new Response(makeZip("1.xml", "<P>2026년 10월 26일 신주인수권증서 상장일 (26.10.26~26.10.30) 2026년 11월 30일 신주 상장</P>"));
  };
  const e = await fetchCapital("k", "20261001000449", fake, "", ["20261001000485"]);
  assert.deepEqual([e.cert_start, e.cert_end, e.listing], ["20261026", "20261030", "20261130"]);
  await assert.rejects(fetchCapital("k", "20261001000449", fake, "", []), /파일이 존재하지/);
});

test("거래정지 공시 본문: 정지·해제·기간변경, 감자 일정", async () => {
  const { parseHaltNotice, parseCapitalText } = await import("../src/events.js");
  assert.deepEqual(parseHaltNotice("주권매매거래정지 1.대상종목 (주)퀀텀레일 보통주 2.정지사유 자본감소 3.정지기간 가.정지일시 2026-10-23 18:12:00 나.만료일시 장종료시까지 4.근거규정"),
    { kind: "halt", reason: "자본감소", start: "20261023 18:12", end_text: "장종료시까지" });
  assert.deepEqual(parseHaltNotice("매매거래정지 및 정지해제(중요내용공시) 1. 종목명 현대위아(주) 주권 3. 매매거래정지 일시 2026-10-19 10:19 4. 매매거래정지 해제일시 2026-10-19 10:49 5. 매매거래정지 사유 영업양도 6. 근거"),
    { kind: "both", reason: "영업양도", start: "20261019 10:19", release: "20261019 10:49" });
  assert.equal(parseHaltNotice("주권매매거래정지해제 1.대상종목 옴니시스템(주) 보통주 2.해제사유 액면병합 주권 변경상장 3.해제일시 2026-10-20 - 4.근거규정").release, "20261020");
  const c = parseCapitalText("감자일정 주주총회 예정일 2026년 11월 20일 매매거래정지예정기간 시작일 2026년 12월 03일 종료일 2026년 12월 22일 신주권교부예정일 - 신주상장예정일 2026년 12월 23일 감자기준일 2026년 12월 05일");
  assert.deepEqual([c.halt_start, c.halt_end, c.listing, c.record_date, c.gm_date], ["20261203", "20261222", "20261223", "20261205", "20261120"]);
});
