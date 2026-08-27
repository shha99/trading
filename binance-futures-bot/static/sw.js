// PWA 서비스워커 - 이 앱은 실시간 시세/백테스트/모의투자 데이터가 핵심이라
// "완전 오프라인 동작"은 의미가 없다. 그래서 이 워커의 역할은 딱 두 가지만:
//   1) 설치 가능(installable) 조건을 만족시켜 홈화면/데스크톱에 "앱처럼" 추가되게 함
//   2) 정적 자산(HTML/CSS/JS/아이콘)만 stale-while-revalidate로 캐싱해 재방문 시 조금 더 빠르게 뜸
// /api/*, /ws/* 는 절대 캐싱하지 않고 항상 네트워크로 보낸다 - 캐싱하면 오래된
// 잔고/시세/백테스트 성적이 보일 수 있어 오히려 위험하다.
const CACHE_NAME = "binance-futures-bot-shell-v1";
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

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(event.request);
      const networkFetch = fetch(event.request)
        .then((res) => {
          if (res.ok) cache.put(event.request, res.clone());
          return res;
        })
        .catch(() => cached);
      return cached || networkFetch;
    })
  );
});
