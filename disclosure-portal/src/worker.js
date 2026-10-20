// 공시 포털 Cloudflare Worker.
//
// 서버는 DART API 키를 숨긴 채 DART 목록 한 페이지를 대신 받아주는 일만 한다(OpenDART는
// 브라우저에서 직접 호출하는 걸 CORS로 막는다). 새 공시 감지·대사·관심종목은 전부
// 브라우저(public/app.js)가 한다 — 그래서 24시간 켜둘 서버도, DB도 필요 없다.
//
// 비밀값(Cloudflare 대시보드 → Worker → Settings → Variables and Secrets):
//   DART_API_KEY     OpenDART 인증키 (필수)
//   PORTAL_PASSWORD  설정하면 접속할 때 비밀번호를 묻는다(아이디는 아무거나) — "나만 보기"
import { DartError, TRADE_CATEGORIES, fetchDay, fetchPage } from "./dart.js";
import { MARKET_KINDS, MarketError, fetchMarketList, fetchQuotes } from "./market.js";
import { KindError, fetchKindToday } from "./kind.js";
import { checkLogin, clearCookie, loginPage, makeSession, readSession, safeNext, sessionCookie, timingSafeEqual } from "./auth.js";
import { HISTORY_BLOCKS, blockOf, fetchAiHistory, fetchAiMetrics, parseMetricsQuery } from "./ai.js";
import { DEAL_APIS, fetchCandidates, fetchCapital, fetchCapitalCandidates, fetchDeal, fetchExchangeCandidates, fetchSplit, fetchSplitCandidates, fetchTender } from "./events.js";

const TODAY_TTL = 15; // 오늘 목록 캐시(초) — 탭 여러 개를 열어도 DART 호출이 늘지 않게
const PAST_TTL = 600; // 지난 날짜는 거의 안 바뀐다
const MARKET_TTL = 300; // 시장조치 종목 목록은 5분 캐시
// 캘린더 후보 목록(새 공시 발견)은 짧게 — 새 공개매수·합병 공시가 늦어도 15분 안에 잡힌다.
// 회사별 일정·신고서는 보고서 접수번호로 캐시하므로 정정 공시가 나오면 바로 새로 읽는다.
const EVENTS_TTL = 15 * 60;
const DEAL_TTL = 12 * 3600;
const QUOTE_TTL = 60;
const DAY_PAST_TTL = 12 * 3600; // 기간 조회(/api/day)용 지난 날짜 — 한번 받으면 반나절 재사용

export function todayKst(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(now).replaceAll("-", "");
}

const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });


function authorized(request, password) {
  if (!password) return true;
  const [scheme, encoded] = (request.headers.get("Authorization") || "").split(" ");
  if (!scheme || scheme.toLowerCase() !== "basic" || !encoded) return false;
  try {
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0)));
    return timingSafeEqual(decoded.slice(decoded.indexOf(":") + 1), password);
  } catch {
    return false;
  }
}

function checkDate(date, today) {
  if (!/^\d{8}$/.test(date)) return "date는 YYYYMMDD 형식이어야 합니다.";
  if (date > today) return "미래 날짜는 조회할 수 없습니다.";
  return null;
}

// Cloudflare 캐시에 있으면 그걸, 없으면 load()로 받아 ttl초 동안 캐시한다. fresh면 캐시를 건너뛴다.
async function cached(key, ttl, fresh, ctx, load) {
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const cacheKey = new Request(`https://cache.internal/${key}`);
  if (cache && !fresh) {
    const hit = await cache.match(cacheKey);
    if (hit) return new Response(hit.body, { headers: { ...Object.fromEntries(hit.headers), "Cache-Control": "no-store" } });
  }
  let body;
  try {
    body = await load();
  } catch (e) {
    if (e instanceof DartError || e instanceof MarketError || e instanceof KindError) return json({ detail: e.message }, 502);
    throw e;
  }
  if (cache) ctx.waitUntil(cache.put(cacheKey, json(body, 200, { "Cache-Control": `max-age=${ttl}` })));
  return json(body);
}

// 하루 화면(실시간)용: 전 시장 한 페이지
async function handlePage(url, env, ctx, fetchImpl) {
  const today = todayKst();
  const date = url.searchParams.get("date") || today;
  const page = Number(url.searchParams.get("page") || "1");
  const bad = checkDate(date, today);
  if (bad) return json({ detail: bad }, 400);
  if (!Number.isInteger(page) || page < 1 || page > 100) return json({ detail: "page가 잘못됐습니다." }, 400);
  // fresh=1(새로고침 대사)이면 캐시를 건너뛰고 DART에서 바로 받는다
  return cached(`page?date=${date}&page=${page}`, date === today ? TODAY_TTL : PAST_TTL, url.searchParams.get("fresh") === "1", ctx,
    async () => ({ date, page, ...(await fetchPage(env.DART_API_KEY, date, page, fetchImpl)) }));
}

// 기간 조회용: 하루치 한 시장(Y=코스피, K=코스닥) 전체. 지난 날짜는 길게 캐시한다.
async function handleDay(url, env, ctx, fetchImpl) {
  const today = todayKst();
  const date = url.searchParams.get("date") || "";
  const cls = url.searchParams.get("cls") || "";
  const bad = checkDate(date, today);
  if (bad) return json({ detail: bad }, 400);
  if (cls !== "Y" && cls !== "K") return json({ detail: "cls는 Y(코스피) 또는 K(코스닥)여야 합니다." }, 400);
  return cached(`day?date=${date}&cls=${cls}`, date === today ? TODAY_TTL : DAY_PAST_TTL, url.searchParams.get("fresh") === "1", ctx,
    async () => ({ date, cls, ...(await fetchDay(env.DART_API_KEY, date, cls, fetchImpl)) }));
}

// 시장조치 종목 목록(투자경고·관리종목 등)
async function handleMarket(url, env, ctx, fetchImpl) {
  const kind = url.searchParams.get("kind") || "";
  if (!(kind in MARKET_KINDS)) return json({ detail: `kind는 ${Object.keys(MARKET_KINDS).join(", ")} 중 하나여야 합니다.` }, 400);
  return cached(`market?kind=${kind}`, MARKET_TTL, url.searchParams.get("fresh") === "1", ctx,
    async () => ({ ...(await fetchMarketList(kind, fetchImpl)), fetched_at: new Date().toISOString() }));
}

// 캘린더(공개매수·매수청구)
async function handleEvents(url, env, ctx, fetchImpl) {
  const fresh = url.searchParams.get("fresh") === "1";
  const part = url.pathname.slice("/api/events/".length);
  if (part === "candidates") {
    const today = todayKst();
    return cached(`events/candidates?d=${today}`, EVENTS_TTL, fresh, ctx, async () => ({ today, ...(await fetchCandidates(env.DART_API_KEY, today, fetchImpl)) }));
  }
  if (part === "exchange-candidates") {
    const today = todayKst();
    return cached(`events/exchange-candidates?d=${today}`, EVENTS_TTL, fresh, ctx, async () => ({ today, items: await fetchExchangeCandidates(env.DART_API_KEY, today, fetchImpl) }));
  }
  if (part === "split-candidates") {
    const today = todayKst();
    const w = Number(url.searchParams.get("w") || "0");
    if (![0, 1, 2].includes(w)) return json({ detail: "w는 0, 1, 2 중 하나여야 합니다." }, 400);
    return cached(`events/split-candidates?d=${today}&w=${w}`, w === 0 ? EVENTS_TTL : DEAL_TTL, fresh, ctx,
      async () => ({ today, items: await fetchSplitCandidates(env.DART_API_KEY, today, w, fetchImpl) }));
  }
  if (part === "split") {
    const rcept = url.searchParams.get("rcept_no") || "";
    if (!/^\d{14}$/.test(rcept)) return json({ detail: "rcept_no가 잘못됐습니다." }, 400);
    return cached(`events/split?r=${rcept}`, DEAL_TTL, fresh, ctx, async () => ({ event: await fetchSplit(env.DART_API_KEY, rcept, fetchImpl) }));
  }
  if (part === "capital-candidates") {
    const today = todayKst();
    return cached(`events/capital-candidates?d=${today}`, EVENTS_TTL, fresh, ctx, async () => ({ today, items: await fetchCapitalCandidates(env.DART_API_KEY, today, fetchImpl) }));
  }
  if (part === "capital") {
    const rcept = url.searchParams.get("rcept_no") || "";
    if (!/^\d{14}$/.test(rcept)) return json({ detail: "rcept_no가 잘못됐습니다." }, 400);
    return cached(`events/capital?r=${rcept}`, DEAL_TTL, fresh, ctx, async () => ({ event: await fetchCapital(env.DART_API_KEY, rcept, fetchImpl) }));
  }
  if (part === "deal") {
    const api = url.searchParams.get("api") || "";
    const corp = url.searchParams.get("corp_code") || "";
    const rcept = url.searchParams.get("rcept_no") || ""; // 후보 목록의 최신 보고서 — 캐시 키, 본문 읽기에 쓴다
    if (!DEAL_APIS.some(([, a]) => a === api) || !/^\d{8}$/.test(corp) || (rcept && !/^\d{14}$/.test(rcept))) return json({ detail: "api, corp_code 또는 rcept_no가 잘못됐습니다." }, 400);
    if (api === "doc" && !rcept) return json({ detail: "rcept_no가 필요합니다." }, 400);
    const today = todayKst();
    return cached(`events/deal?api=${api}&corp=${corp}&r=${rcept}&d=${today}`, DEAL_TTL, fresh, ctx, async () => ({ event: await fetchDeal(env.DART_API_KEY, api, corp, today, fetchImpl, rcept) }));
  }
  if (part === "tender") {
    const rcept = url.searchParams.get("rcept_no") || "";
    if (!/^\d{14}$/.test(rcept)) return json({ detail: "rcept_no가 잘못됐습니다." }, 400);
    return cached(`events/tender?r=${rcept}`, DEAL_TTL * 2, fresh, ctx, async () => ({ event: await fetchTender(env.DART_API_KEY, rcept, fetchImpl) }));
  }
  return json({ detail: "not found" }, 404);
}

// AI분석: 과거 이벤트 목록(30일 고정 블록)과 종목별 이벤트 전후 주가 측정값
async function handleAi(url, env, ctx, fetchImpl) {
  const fresh = url.searchParams.get("fresh") === "1";
  const today = todayKst();
  if (url.pathname === "/api/ai/history") {
    const k = Number(url.searchParams.get("k"));
    const g = url.searchParams.get("g");
    const cur = blockOf(today);
    if (!Number.isInteger(k) || k > cur || k < cur - HISTORY_BLOCKS - 1 || !["b", "c", "i1", "i3"].includes(g)) return json({ detail: "k 또는 g가 잘못됐습니다." }, 400);
    // 지난 블록은 거의 안 바뀌어 사흘, 이번 블록은 1시간
    return cached(`ai/history?k=${k}&g=${g}${k === cur ? `&d=${today}` : ""}`, k === cur ? 3600 : 3 * 86400, fresh && k === cur, ctx,
      async () => fetchAiHistory(env.DART_API_KEY, k, g, today, fetchImpl));
  }
  if (url.pathname === "/api/ai/metrics") {
    const by = parseMetricsQuery(url.searchParams.get("q"));
    if (!by.size || by.size > 40) return json({ detail: "q는 종목 1~40개여야 합니다." }, 400);
    const q = [...by].sort(([a], [b]) => a.localeCompare(b)).map(([c, w]) => `${c}:${[...w.dates].sort().join("/")}${w.listing ? "L" : ""}`).join(",");
    return cached(`ai/metrics?d=${today}&q=${q}`, 12 * 3600, false, ctx, async () => ({ today, stocks: await fetchAiMetrics(by, today, fetchImpl) }));
  }
  return json({ detail: "not found" }, 404);
}

async function handleQuotes(url, env, ctx, fetchImpl) {
  const codes = [...new Set((url.searchParams.get("codes") || "").split(",").filter((c) => /^[0-9A-Z]{6}$/.test(c)))].sort();
  if (!codes.length || codes.length > 100) return json({ detail: "codes는 종목코드 1~100개(쉼표 구분)여야 합니다." }, 400);
  return cached(`quotes?c=${codes.join(",")}`, QUOTE_TTL, false, ctx, async () => ({ quotes: await fetchQuotes(codes, fetchImpl) }));
}

export async function handle(request, env, ctx, fetchImpl = fetch) {
  const url = new URL(request.url);
  if (url.pathname === "/healthz") return json({ ok: true });

  // 사람별 로그인(PORTAL_USERS가 있으면). 없으면 예전 방식(PORTAL_PASSWORD 하나, 브라우저 팝업)으로.
  let user = null;
  if (env.PORTAL_USERS) {
    const html = (body, status = 200, headers = {}) => new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", ...headers } });
    if (url.pathname === "/login" && request.method === "GET") {
      if (await readSession(request, env.PORTAL_USERS)) return Response.redirect(new URL(safeNext(url.searchParams.get("next")), url), 302);
      return html(loginPage({ next: safeNext(url.searchParams.get("next")) }));
    }
    if (url.pathname === "/login" && request.method === "POST") {
      const form = await request.formData().catch(() => new FormData());
      const id = String(form.get("id") || "").trim();
      const next = safeNext(String(form.get("next") || "/"));
      if (!checkLogin(env.PORTAL_USERS, id, form.get("pw"))) {
        await new Promise((r) => setTimeout(r, 600)); // 무작위 대입을 느리게
        return html(loginPage({ error: "아이디 또는 비밀번호가 맞지 않습니다.", next, id }), 401);
      }
      return new Response(null, { status: 302, headers: { Location: next, "Set-Cookie": sessionCookie(await makeSession(env.PORTAL_USERS, id)), "Cache-Control": "no-store" } });
    }
    if (url.pathname === "/logout") {
      return new Response(null, { status: 302, headers: { Location: "/login", "Set-Cookie": clearCookie(), "Cache-Control": "no-store" } });
    }
    user = await readSession(request, env.PORTAL_USERS);
    if (!user) {
      if (url.pathname.startsWith("/api/")) return json({ detail: "로그인이 필요합니다.", login: true }, 401);
      return Response.redirect(new URL(`/login?next=${encodeURIComponent(url.pathname + url.search)}`, url), 302);
    }
  } else if (!authorized(request, env.PORTAL_PASSWORD)) {
    return new Response("비밀번호가 필요합니다.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="disclosure-portal", charset="UTF-8"', "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  if (url.pathname === "/api/page" && request.method === "GET") return handlePage(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/day" && request.method === "GET") return handleDay(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/market" && request.method === "GET") return handleMarket(url, env, ctx, fetchImpl);
  if (url.pathname.startsWith("/api/events/") && request.method === "GET") return handleEvents(url, env, ctx, fetchImpl);
  if (url.pathname.startsWith("/api/ai/") && request.method === "GET") return handleAi(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/quotes" && request.method === "GET") return handleQuotes(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/kind/today" && request.method === "GET") {
    // 오늘 KIND 공시 전체(새로고침 때 DART와 맞춰보기용). 1분 캐시, fresh=1이면 바로 받는다.
    return cached(`kind/today?d=${todayKst()}`, 60, url.searchParams.get("fresh") === "1", ctx,
      async () => ({ date: todayKst(), ...(await fetchKindToday(fetchImpl)), fetched_at: new Date().toISOString() }));
  }
  if (url.pathname === "/api/status") {
    return json({ today: todayKst(), user, api_key_set: Boolean(env.DART_API_KEY), categories: TRADE_CATEGORIES.map(([name]) => name) });
  }
  if (url.pathname.startsWith("/api/")) return json({ detail: "not found" }, 404);
  return env.ASSETS.fetch(request);
}

export default {
  fetch: (request, env, ctx) => handle(request, env, ctx),
};
