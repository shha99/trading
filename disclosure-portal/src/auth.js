// 로그인: 사람마다 아이디·비밀번호를 따로 준다.
//
// 계정은 Cloudflare 비밀값 PORTAL_USERS에 "아이디:비밀번호"를 쉼표(또는 줄바꿈)로 이어 적는다.
//   예) PORTAL_USERS = "hyun:Abc123!,kim:qwer5678"
// 비밀번호에는 쉼표를 쓰지 말 것(구분자). 콜론은 첫 번째 것만 아이디/비밀번호 구분으로 쓴다.
//
// 로그인하면 서명한 쿠키(30일)를 준다. 서명 키는 PORTAL_USERS 내용에서 만들기 때문에, 목록을
// 바꾸면(사람 추가·삭제·비밀번호 변경) 기존 로그인은 모두 풀리고 다시 로그인해야 한다. 별도 DB는 없다.

export const COOKIE = "dp_session";
const MAX_AGE = 30 * 24 * 3600;
const enc = new TextEncoder();

export function parseUsers(raw) {
  const users = new Map();
  for (const part of String(raw || "").split(/[,\n]/)) {
    const s = part.trim();
    const i = s.indexOf(":");
    if (i > 0 && i < s.length - 1) users.set(s.slice(0, i).trim(), s.slice(i + 1));
  }
  return users;
}

export function timingSafeEqual(a, b) {
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0;
}

const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function key(secretSource) {
  const raw = await crypto.subtle.digest("SHA-256", enc.encode(`dp-session-v1:${secretSource}`));
  return crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

async function sign(secretSource, payload) {
  return b64url(await crypto.subtle.sign("HMAC", await key(secretSource), enc.encode(payload)));
}

// 쿠키 값: base64url(아이디) . 만료(초) . 서명
export async function makeSession(usersRaw, user, now = Date.now()) {
  const exp = Math.floor(now / 1000) + MAX_AGE;
  const u = b64url(enc.encode(user));
  return `${u}.${exp}.${await sign(usersRaw, `${u}.${exp}`)}`;
}

export async function readSession(request, usersRaw, now = Date.now()) {
  const cookie = request.headers.get("Cookie") || "";
  const m = cookie.match(new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`));
  if (!m) return null;
  const [u, exp, sig] = m[1].split(".");
  if (!u || !exp || !sig || Number(exp) * 1000 < now) return null;
  if (!timingSafeEqual(sig, await sign(usersRaw, `${u}.${exp}`))) return null;
  let user;
  try {
    user = new TextDecoder().decode(Uint8Array.from(atob(u.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0)));
  } catch {
    return null;
  }
  return parseUsers(usersRaw).has(user) ? user : null; // 목록에서 빠진 사람은 바로 거절
}

export function checkLogin(usersRaw, id, pw) {
  const users = parseUsers(usersRaw);
  const want = users.get(String(id || "").trim());
  // 없는 아이디여도 같은 비교를 한 번 해서 응답 시간으로 아이디 존재 여부가 드러나지 않게
  const ok = timingSafeEqual(String(pw || ""), want ?? "\u0000invalid\u0000");
  return ok && want !== undefined;
}

export const sessionCookie = (value) => `${COOKIE}=${value}; Path=/; Max-Age=${MAX_AGE}; HttpOnly; Secure; SameSite=Lax`;
export const clearCookie = () => `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// 안전한 이동 경로만(외부 주소로 튕기기 방지)
export const safeNext = (n) => (typeof n === "string" && /^\/(?!\/)[^\s]*$/.test(n) && !n.startsWith("/login") ? n : "/");

export function loginPage({ error = "", next = "/", id = "" } = {}) {
  return `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>로그인 · 공시 포털</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f5f6f8; color: #191f28;
    font-family: "Pretendard", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }
  form { width: min(360px, calc(100vw - 32px)); background: #fff; border: 1px solid #e7e9ee; border-radius: 16px; padding: 28px 24px; }
  .logo { width: 40px; height: 40px; border-radius: 10px; background: #3182f6; color: #fff; display: grid; place-items: center; font-weight: 700; font-size: 13px; }
  h1 { font-size: 20px; margin: 14px 0 4px; } p { margin: 0 0 18px; color: #6b7684; font-size: 13px; }
  label { display: block; font-size: 13px; font-weight: 600; margin: 12px 0 6px; }
  input { width: 100%; height: 42px; border: 1px solid #e7e9ee; border-radius: 10px; padding: 0 12px; font: inherit; }
  input:focus { outline: 2px solid #e8f3ff; border-color: #3182f6; }
  button { width: 100%; height: 44px; margin-top: 18px; border: 0; border-radius: 10px; background: #3182f6; color: #fff; font: inherit; font-weight: 700; cursor: pointer; }
  .err { background: #fdecec; color: #b42318; border-radius: 10px; padding: 10px 12px; font-size: 13px; margin-bottom: 4px; }
</style></head>
<body><form method="post" action="/login">
  <div class="logo">공시</div>
  <h1>공시 포털</h1>
  <p>받은 아이디와 비밀번호로 로그인하세요.</p>
  ${error ? `<div class="err">${esc(error)}</div>` : ""}
  <input type="hidden" name="next" value="${esc(next)}">
  <label for="id">아이디</label><input id="id" name="id" autocomplete="username" value="${esc(id)}" required autofocus>
  <label for="pw">비밀번호</label><input id="pw" name="pw" type="password" autocomplete="current-password" required>
  <button type="submit">로그인</button>
</form></body></html>`;
}
