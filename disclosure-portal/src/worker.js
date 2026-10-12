// 공시 포털 Cloudflare Worker.
//
// 서버는 DART API 키를 숨긴 채 DART 목록 한 페이지를 대신 받아주는 일만 한다(OpenDART는
// 브라우저에서 직접 호출하는 걸 CORS로 막는다). 새 공시 감지·대사·관심종목은 전부
// 브라우저(public/app.js)가 한다 — 그래서 24시간 켜둘 서버도, DB도 필요 없다.
//
// 비밀값(Cloudflare 대시보드 → Worker → Settings → Variables and Secrets):
//   DART_API_KEY     OpenDART 인증키 (필수)
//   PORTAL_PASSWORD  설정하면 접속할 때 비밀번호를 묻는다(아이디는 아무거나) — "나만 보기"
import { DartError, TRADE_CATEGORIES, fetchPage } from "./dart.js";

const TODAY_TTL = 15; // 오늘 목록 캐시(초) — 탭 여러 개를 열어도 DART 호출이 늘지 않게
const PAST_TTL = 600; // 지난 날짜는 거의 안 바뀐다

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

async function handlePage(url, env, ctx, fetchImpl) {
  const today = todayKst();
  const date = url.searchParams.get("date") || today;
  const page = Number(url.searchParams.get("page") || "1");
  if (!/^\d{8}$/.test(date)) return json({ detail: "date는 YYYYMMDD 형식이어야 합니다." }, 400);
  if (date > today) return json({ detail: "미래 날짜는 조회할 수 없습니다." }, 400);
  if (!Number.isInteger(page) || page < 1 || page > 100) return json({ detail: "page가 잘못됐습니다." }, 400);

  // fresh=1(새로고침 대사)이면 캐시를 건너뛰고 DART에서 바로 받는다
  const fresh = url.searchParams.get("fresh") === "1";
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const cacheKey = new Request(`https://cache.internal/page?date=${date}&page=${page}`);
  if (cache && !fresh) {
    const hit = await cache.match(cacheKey);
    if (hit) return new Response(hit.body, { headers: { ...Object.fromEntries(hit.headers), "Cache-Control": "no-store" } });
  }

  let body;
  try {
    body = { date, page, ...(await fetchPage(env.DART_API_KEY, date, page, fetchImpl)) };
  } catch (e) {
    if (e instanceof DartError) return json({ detail: e.message }, 502);
    throw e;
  }
  if (cache) {
    const ttl = date === today ? TODAY_TTL : PAST_TTL;
    ctx.waitUntil(cache.put(cacheKey, json(body, 200, { "Cache-Control": `max-age=${ttl}` })));
  }
  return json(body);
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
  if (url.pathname === "/api/status") {
    return json({ today: todayKst(), api_key_set: Boolean(env.DART_API_KEY), categories: TRADE_CATEGORIES.map(([name]) => name) });
  }
  if (url.pathname.startsWith("/api/")) return json({ detail: "not found" }, 404);
  return env.ASSETS.fetch(request);
}

export default {
  fetch: (request, env, ctx) => handle(request, env, ctx),
};
