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
