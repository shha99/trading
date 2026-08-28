// PWA 서비스워커 - 이 앱은 실시간 시세/백테스트/모의투자 데이터가 핵심이라
// "완전 오프라인 동작"은 의미가 없다. 그래서 이 워커의 역할은 딱 두 가지만:
//   1) 설치 가능(installable) 조건을 만족시켜 홈화면/데스크톱에 "앱처럼" 추가되게 함
//   2) 정적 자산(HTML/CSS/JS/아이콘)은 오프라인 폴백용으로만 캐싱함
// /api/*, /ws/* 는 절대 캐싱하지 않고 항상 네트워크로 보낸다 - 캐싱하면 오래된
// 잔고/시세/백테스트 성적이 보일 수 있어 오히려 위험하다.
//
// ⚠️ v1에서는 HTML/JS를 "캐시 우선"(cached || networkFetch)으로 서빙해서, 코드를
// 새로 배포해도 이미 방문했던 브라우저는 예전에 캐싱해둔 낡은 페이지/JS를 계속
// 보여주는 버그가 있었다(차트 자동갱신 같은 새 기능이 안 보이는 원인이었음).
// v2부터는 "네트워크 우선"으로 바꿔서 온라인이면 항상 최신 코드를 받고, 완전히
// 오프라인일 때만 캐시로 폴백한다. CACHE_NAME도 바꿔서 v1 시절 캐시를 강제 폐기한다.
const CACHE_NAME = "binance-futures-bot-shell-v2";
const SHELL_ASSETS = [
  "/", "/strategy", "/vf", "/lab",
  "/static/style.css", "/static/strategy.css", "/static/lab.css",
  "/static/app.js", "/static/strategy_page.js", "/static/lab.js",
  "/static/vendor/lightweight-charts.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return; // 항상 네트워크로

  // 네트워크 우선: 온라인이면 항상 최신 응답을 쓰고(+캐시 갱신), 네트워크
  // 요청 자체가 실패할 때(완전 오프라인)만 캐시로 폴백한다.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return res;
      })
      .catch(() => caches.open(CACHE_NAME).then((cache) => cache.match(event.request)))
  );
});
