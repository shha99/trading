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
import { DEAL_APIS, fetchCandidates, fetchDeal, fetchTender } from "./events.js";

const TODAY_TTL = 15; // 오늘 목록 캐시(초) — 탭 여러 개를 열어도 DART 호출이 늘지 않게
const PAST_TTL = 600; // 지난 날짜는 거의 안 바뀐다
const MARKET_TTL = 300; // 시장조치 종목 목록은 5분 캐시
const EVENTS_TTL = 3 * 3600; // 캘린더 후보 목록
const DEAL_TTL = 6 * 3600; // 회사별 매수청구 일정·공개매수 신고서
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

function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0;
}

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
    if (e instanceof DartError || e instanceof MarketError) return json({ detail: e.message }, 502);
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
  if (part === "deal") {
    const api = url.searchParams.get("api") || "";
    const corp = url.searchParams.get("corp_code") || "";
    if (!DEAL_APIS.some(([, a]) => a === api) || !/^\d{8}$/.test(corp)) return json({ detail: "api 또는 corp_code가 잘못됐습니다." }, 400);
    const today = todayKst();
    return cached(`events/deal?api=${api}&corp=${corp}&d=${today}`, DEAL_TTL, fresh, ctx, async () => ({ event: await fetchDeal(env.DART_API_KEY, api, corp, today, fetchImpl) }));
  }
  if (part === "tender") {
    const rcept = url.searchParams.get("rcept_no") || "";
    if (!/^\d{14}$/.test(rcept)) return json({ detail: "rcept_no가 잘못됐습니다." }, 400);
    return cached(`events/tender?r=${rcept}`, DEAL_TTL * 4, fresh, ctx, async () => ({ event: await fetchTender(env.DART_API_KEY, rcept, fetchImpl) }));
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
  if (!authorized(request, env.PORTAL_PASSWORD)) {
    return new Response("비밀번호가 필요합니다.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="disclosure-portal", charset="UTF-8"', "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  if (url.pathname === "/api/page" && request.method === "GET") return handlePage(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/day" && request.method === "GET") return handleDay(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/market" && request.method === "GET") return handleMarket(url, env, ctx, fetchImpl);
  if (url.pathname.startsWith("/api/events/") && request.method === "GET") return handleEvents(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/quotes" && request.method === "GET") return handleQuotes(url, env, ctx, fetchImpl);
  if (url.pathname === "/api/status") {
    return json({ today: todayKst(), api_key_set: Boolean(env.DART_API_KEY), categories: TRADE_CATEGORIES.map(([name]) => name) });
  }
  if (url.pathname.startsWith("/api/")) return json({ detail: "not found" }, 404);
  return env.ASSETS.fetch(request);
}

export default {
  fetch: (request, env, ctx) => handle(request, env, ctx),
};
