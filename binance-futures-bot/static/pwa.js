// 4개 페이지(index/strategy/vf/lab) 전부 공유하는 서비스워커 등록 한 줄.
// 지원 안 하는 브라우저에서도 조용히 넘어가도록 존재 여부만 체크한다.
//
// /sw.js(루트 경로)로 등록해야 스코프가 사이트 전체("/")가 된다 - 서비스워커
// 스코프는 기본적으로 스크립트가 위치한 디렉터리로 제한되므로, /static/sw.js로
// 등록하면 /static/ 아래만 제어하게 돼 "/", "/vf" 같은 페이지 자체는 제어 밖이다.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}
