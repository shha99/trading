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
