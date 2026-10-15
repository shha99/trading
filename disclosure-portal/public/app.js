// 공시 포털 프론트엔드 — 빌드 없이 쓰는 바닐라 JS.
// 관심종목은 localStorage에 저장한다(재배포와 무관하게 이 브라우저에 유지).
(() => {
  "use strict";

  const WATCH_KEY = "disclosure-portal.watchlist.v1";
  const VIEW_KEY = "disclosure-portal.view.v1"; // 마지막으로 본 모드·기간
  const MAX_RANGE_DAYS = 92; // DART 기간 검색 한도(3개월)에 맞춤
  const RANGE_CONCURRENCY = 6;
  const REFRESH_MS = 30_000;
  const PAGE = 300;

  const $ = (id) => document.getElementById(id);
  const state = {
    today: null,      // 오늘(KST) YYYYMMDD
    view: "disc",     // "disc"(공시) | "market"(투자경고·관리종목)
    mk: {
      kind: "all", market: "", lists: {}, errors: {}, flags: new Map(), loadedAt: null,
      seen: null, added: {}, released: {}, notified: new Set(),
    },
    cal: {
      month: null, events: [], quotes: {}, loadedAt: null, loading: false, selected: null, error: null, newIds: new Set(),
      manual: [],
    },
    mode: "day",      // "day"(하루, 실시간) | "range"(기간 조회)
    date: null,       // 하루 모드에서 보고 있는 날짜
    range: null,      // 기간 모드의 { from, to } (YYYYMMDD)
    items: new Map(), // rcept_no -> item
    seq: 0,
    lastChecked: null, // 마지막으로 DART를 확인한 시각(KST HH:MM:SS)
    error: null,
    apiKeySet: null,
    tab: "all",
    market: "",
    cats: new Set(),  // 고른 거래·권리 영향 분류 (비면 필터 없음, "*"는 분류 전체)
    catNames: [],
    selected: null,   // 사이드바에서 고른 관심종목 key
    limit: PAGE,
    freshIds: new Set(),
    watch: loadWatch(),
    timer: null,
  };

  // ---------- 관심종목 저장 ----------
  function loadWatch() {
    try {
      const v = JSON.parse(localStorage.getItem(WATCH_KEY) || "[]");
      return Array.isArray(v) ? v.filter((w) => w && (w.code || w.name)) : [];
    } catch { return []; }
  }
  function saveWatch() {
    try { localStorage.setItem(WATCH_KEY, JSON.stringify(state.watch)); } catch { /* 저장 불가(사파리 개인모드 등) */ }
  }
  const watchKey = (w) => w.code || `name:${w.name}`;
  function watchOf(it) {
    return state.watch.find((w) => (w.code ? w.code === it.stock_code : w.name === it.corp_name)) || null;
  }
  function addWatch(entry) {
    if (state.watch.some((w) => watchKey(w) === watchKey(entry))) return false;
    state.watch.push(entry);
    saveWatch();
    return true;
  }
  function removeWatch(key) {
    state.watch = state.watch.filter((w) => watchKey(w) !== key);
    if (state.selected === key) state.selected = null;
    saveWatch();
  }
  function toggleWatchFor(it) {
    const w = watchOf(it);
    if (w) removeWatch(watchKey(w));
    else addWatch(it.stock_code ? { code: it.stock_code, name: it.corp_name } : { name: it.corp_name });
    render();
  }

  // ---------- 날짜 ----------
  const toInput = (d) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
  const fromInput = (v) => v.replaceAll("-", "");
  function shiftDate(d, days) {
    const t = new Date(Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8) + days));
    return t.toISOString().slice(0, 10).replaceAll("-", "");
  }

  // ---------- 데이터 ----------
  // 서버(Worker)는 DART 목록 한 페이지를 대신 받아줄 뿐이고, 새 공시 감지·순서·대사는 전부
  // 여기서 한다. seq는 DART가 준 순서(최신순)를 뒤집어 매긴 번호로, 클수록 최신이다.
  // 접수번호는 거래소 공시가 800/900번대라 시간순이 아니어서 정렬에 쓰면 안 된다.
  const MAX_PAGES = 45;
  const todayKst = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date()).replaceAll("-", "");
  const nowKst = () => new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour12: false });
  const pageURL = (date, page, fresh) => `/api/page?date=${date}&page=${page}${fresh ? "&fresh=1" : ""}`;

  // 코스피·코스닥 종목만 본다. 코넥스·기타(비상장·펀드 등)도 받아두긴 해야 새 공시 감지
  // (이미 아는 공시가 나오면 멈춤)가 맞게 돌아서, 저장은 다 하고 보여줄 때만 거른다.
  const inScope = (it) => it.market === "코스피" || it.market === "코스닥";
  function scoped() {
    const out = [];
    for (const it of state.items.values()) if (inScope(it)) out.push(it);
    return out;
  }

  let busy = false;  // 불러오기/대사 중엔 폴링을 쉰다
  let loadToken = 0; // 날짜를 바꾸거나 대사를 시작하면 늘려서, 늦게 끝난 이전 요청 결과를 버린다

  async function fetchJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    return body;
  }

  // 그 날짜 전체 공시를 DART 순서(최신순) 그대로, 중복 없이
  async function fetchAll(date, fresh) {
    const items = [];
    const seen = new Set();
    let page = 1;
    let total = 1;
    while (page <= Math.min(total, MAX_PAGES)) {
      const d = await fetchJSON(pageURL(date, page, fresh));
      total = d.total_page;
      for (const it of d.items) {
        if (!seen.has(it.rcept_no)) { seen.add(it.rcept_no); items.push(it); }
      }
      page++;
    }
    return items;
  }

  // 최신순 목록으로 state.items를 통째로 다시 만든다
  function setAll(list, firstSeenOf) {
    state.items = new Map();
    state.seq = 0;
    for (let i = list.length - 1; i >= 0; i--) {
      const it = list[i];
      it.seq = ++state.seq;
      it.first_seen = firstSeenOf(it);
      state.items.set(it.rcept_no, it);
    }
  }

  async function loadDate(date) {
    const token = ++loadToken;
    setMode("day");
    state.date = date;
    state.items = new Map();
    state.seq = 0;
    state.limit = PAGE;
    state.freshIds.clear();
    state.error = null;
    state.lastChecked = null; // 불러오는 동안 이전 시각이 남지 않게
    $("dateInput").value = toInput(date);
    $("nextDay").disabled = date >= state.today;
    $("rows").innerHTML = "";
    showEmpty("불러오는 중…");
    updateLive();
    busy = true;
    try {
      const list = await fetchAll(date, false);
      if (token !== loadToken) return;
      setAll(list, () => null); // 처음 불러온 것은 감지 시각을 비워둔다
      state.lastChecked = nowKst();
    } catch (e) {
      if (token !== loadToken) return;
      state.error = e.message;
      showEmpty(`불러오지 못했습니다: ${e.message}`);
      updateLive();
      return;
    } finally {
      if (token === loadToken) busy = false;
    }
    updateLive();
    render();
  }

  // ---------- 기간 조회 ----------
  // 날짜×시장(코스피 Y, 코스닥 K)마다 /api/day로 하루치를 받는다. 지난 날짜는 Worker가 반나절
  // 캐시하므로 두 번째부터는 빠르다. 결과는 최신 날짜부터, 같은 날 안에서는 두 시장 목록을
  // 각자 순서를 지키며 섞는다(두 시장 사이의 정확한 게시 순서는 DART가 따로 주지 않는다).
  function daysBetween(from, to) {
    const out = [];
    for (let d = to; d >= from; d = shiftDate(d, -1)) out.push(d);
    return out;
  }

  function interleave(a, b) {
    const out = [];
    let i = 0;
    let j = 0;
    while (i < a.length || j < b.length) {
      if (j >= b.length || (i < a.length && i / a.length <= j / b.length)) out.push(a[i++]);
      else out.push(b[j++]);
    }
    return out;
  }

  async function fetchRange(from, to, fresh, onProgress) {
    const days = daysBetween(from, to);
    const jobs = days.flatMap((d) => ["Y", "K"].map((c) => ({ d, c })));
    const got = new Map();
    let next = 0;
    let done = 0;
    let failed = null;
    const run = async () => {
      while (next < jobs.length && !failed) {
        const { d, c } = jobs[next++];
        try {
          const r = await fetchJSON(`/api/day?date=${d}&cls=${c}${fresh ? "&fresh=1" : ""}`);
          got.set(d + c, r.items);
          onProgress(++done, jobs.length);
        } catch (e) {
          failed = e;
        }
      }
    };
    await Promise.all(Array.from({ length: RANGE_CONCURRENCY }, run));
    if (failed) throw failed;
    return days.flatMap((d) => interleave(got.get(d + "Y") || [], got.get(d + "K") || []));
  }

  function validRange(from, to) {
    if (!/^\d{8}$/.test(from) || !/^\d{8}$/.test(to)) return "시작일과 종료일을 모두 고르세요.";
    if (from > to) return "시작일이 종료일보다 늦습니다.";
    if (to > state.today) return "종료일은 오늘까지만 고를 수 있습니다.";
    if (daysBetween(from, to).length > MAX_RANGE_DAYS) return "기간은 최대 3개월까지입니다.";
    return null;
  }

  function progressText(done, total) {
    return `불러오는 중 ${Math.round((done / total) * 100)}% (${done}/${total})`;
  }

  async function loadRange(from, to) {
    const bad = validRange(from, to);
    if (bad) { toast(bad); return; }
    const token = ++loadToken;
    setMode("range");
    state.range = { from, to };
    saveView();
    state.date = null;
    state.items = new Map();
    state.seq = 0;
    state.limit = PAGE;
    state.freshIds.clear();
    state.error = null;
    state.lastChecked = null; // 불러오는 동안 이전 시각이 남지 않게
    $("rangeFrom").value = toInput(from);
    $("rangeTo").value = toInput(to);
    $("rows").innerHTML = "";
    $("rangeLoad").disabled = true;
    showEmpty("불러오는 중…");
    updateLive();
    busy = true;
    try {
      const list = await fetchRange(from, to, false, (d, t) => {
        if (token !== loadToken) return;
        $("rangeProgress").textContent = progressText(d, t);
        showEmpty(progressText(d, t));
      });
      if (token !== loadToken) return;
      setAll(list, () => null);
      state.lastChecked = nowKst();
      $("rangeProgress").textContent = `${list.length.toLocaleString()}건`;
    } catch (e) {
      if (token !== loadToken) return;
      state.error = e.message;
      $("rangeProgress").textContent = "";
      showEmpty(`불러오지 못했습니다: ${e.message}`);
      updateLive();
      return;
    } finally {
      if (token === loadToken) {
        busy = false;
        $("rangeLoad").disabled = false;
      }
    }
    updateLive();
    render();
  }

  function setMode(mode) {
    state.mode = mode;
    for (const b of document.querySelectorAll("#modeSeg button")) b.classList.toggle("on", b.dataset.mode === mode);
    $("dayNav").hidden = mode !== "day";
    $("rangeBar").hidden = mode !== "range";
    $("timeHead").textContent = mode === "range" ? "일자" : "감지";
    $("recon").hidden = true;
    if (mode === "day") $("rangeProgress").textContent = "";
  }

  function saveView() {
    try { localStorage.setItem(VIEW_KEY, JSON.stringify({ mode: state.mode, range: state.range })); } catch { /* 무시 */ }
  }
  function loadView() {
    try { return JSON.parse(localStorage.getItem(VIEW_KEY) || "null") || {}; } catch { return {}; }
  }

  // 기본 기간: 지난달 1일 ~ 오늘
  function presetRange(p) {
    const to = state.today;
    if (p === "month") {
      const y = +to.slice(0, 4);
      const m = +to.slice(4, 6);
      const from = m === 1 ? `${y - 1}1201` : `${y}${String(m - 1).padStart(2, "0")}01`;
      return { from, to };
    }
    return { from: shiftDate(to, -(Number(p) - 1)), to };
  }

  // 오늘 화면이면 1페이지부터 받아, 이미 아는 공시가 섞인 페이지에서 멈춘다(평소 요청 1번)
  async function poll() {
    const today = todayKst();
    if (today !== state.today) {
      const wasToday = state.mode === "day" && state.date === state.today;
      state.today = today;
      $("dateInput").max = toInput(today);
      if (wasToday) return loadDate(today); // 자정이 지나면 새 날짜로
    }
    if (busy || state.mode !== "day" || state.date !== state.today) return;
    busy = true;
    const token = loadToken;
    try {
      const known = new Set(state.items.keys());
      const fresh = [];
      let page = 1;
      let total = 1;
      while (page <= Math.min(total, MAX_PAGES)) {
        const d = await fetchJSON(pageURL(state.date, page));
        total = d.total_page;
        const nw = d.items.filter((it) => !known.has(it.rcept_no));
        nw.forEach((it) => known.add(it.rcept_no));
        fresh.push(...nw);
        if (nw.length < d.items.length) break;
        page++;
      }
      if (token !== loadToken) return;
      const t = nowKst();
            for (let i = fresh.length - 1; i >= 0; i--) {
        const it = fresh[i];
        it.seq = ++state.seq;
        it.first_seen = t;
        state.items.set(it.rcept_no, it);
        if (inScope(it)) state.freshIds.add(it.rcept_no);
      }
      state.lastChecked = t;
      state.error = null;
      if (fresh.length) {
        if (state.freshIds.size) document.title = `(${state.freshIds.size}) 공시 포털`;
        const watched = fresh.filter((it) => inScope(it) && watchOf(it));
        if (watched.length) {
          const it = watched[0];
          toast(`★ ${it.corp_name}: ${it.report_nm}${watched.length > 1 ? ` 외 ${watched.length - 1}건` : ""}`);
        }
        render();
      }
    } catch (e) {
      if (token === loadToken) state.error = e.message;
    } finally {
      if (token === loadToken) busy = false;
      updateLive();
    }
  }

  function updateLive() {
    const banner = $("banner");
    if (state.apiKeySet === false) {
      banner.hidden = false;
      banner.textContent = "DART_API_KEY가 설정되지 않았습니다. Cloudflare 대시보드 → 이 Worker → Settings → Variables and Secrets에 인증키를 넣어주세요.";
    } else if (state.error) {
      banner.hidden = false;
      // 이미 받아둔 목록이 있으면 그대로 보여주고 있다는 걸 알린다(일시적 지연은 30초 뒤 자동 재시도)
      banner.textContent = state.items.size
        ? `DART 연결이 잠시 원활하지 않습니다. 받아둔 목록을 그대로 보여주는 중이고 30초마다 다시 시도합니다. (${state.error})`
        : `DART 조회 오류: ${state.error}`;
    } else {
      banner.hidden = true;
    }
    if (state.mode === "range") {
      const r = state.range;
      const fmt = (d) => `${+d.slice(4, 6)}/${+d.slice(6, 8)}`;
      setLive(state.error ? "err" : "", state.error ? "기간 조회 실패" : `기간 조회 · ${fmt(r.from)}~${fmt(r.to)} · 실시간 갱신 없음`);
    } else if (state.date !== state.today) setLive("", "지난 날짜 보는 중");
    else if (state.error) setLive("err", "갱신 실패 · 30초 뒤 다시 시도");
    else if (state.lastChecked) setLive("ok", `실시간 · 30초마다 확인 · 마지막 ${state.lastChecked}`);
    else setLive("", "불러오는 중…");
    renderDiscMeta();
  }

  // 필터 줄의 "N건 · 마지막 업데이트" (투자경고·관리종목 탭과 같은 자리)
  function renderDiscMeta(count = scoped().length) {
    const t = state.lastChecked;
    $("discMeta").textContent = !t ? ""
      : state.mode === "range" ? `${count.toLocaleString()}건 · ${t} 불러옴`
      : state.date === state.today ? `${count.toLocaleString()}건 · ${t} 업데이트 (30초마다 확인)`
      : `${count.toLocaleString()}건 · ${t} 불러옴`;
  }

  function setLive(cls, text) {
    $("live").className = `live ${cls}`;
    $("liveText").textContent = text;
  }

  // ---------- 필터/렌더 ----------
  // 검색: 대소문자·공백·가운뎃점·괄호를 무시하고, 입력한 글자가 일부만 들어 있어도 찾는다.
  // 띄어 쓴 여러 단어는 각각 들어 있으면 찾는다(순서 무관). 회사명·제목·종목코드·제출인·태그 대상.
  const norm = (s) => String(s).toLowerCase().replace(/[\s·ㆍ.,:;()[\]{}<>\-_/'"]/g, "");
  function haystack(it) {
    if (!it._hay) it._hay = norm([it.corp_name, it.report_nm, it.stock_code, it.flr_nm, it.market, ...it.tags, ...it.categories].join(" "));
    return it._hay;
  }
  function searchTerms() {
    const q = $("search").value.trim();
    return { whole: norm(q), terms: q.split(/\s+/).map(norm).filter(Boolean), raw: q.split(/\s+/).filter(Boolean) };
  }
  function matches(it, st) {
    if (!st.whole) return true;
    const h = haystack(it);
    return h.includes(st.whole) || (st.terms.length > 1 && st.terms.every((t) => h.includes(t)));
  }
  function catMatch(it) {
    if (!state.cats.size) return true;
    if (state.cats.has("*")) return it.categories.length > 0;
    return it.categories.some((c) => state.cats.has(c));
  }

  // 시장·체크박스·검색 조건 (분류 조건은 빼고) — 분류 버튼 옆 건수를 세는 데도 쓴다
  function baseFiltered() {
    const ex = $("fExchange").checked;
    const imp = $("fImportant").checked;
    const noFix = $("fNoCorrection").checked;
    const st = searchTerms();
    const sel = state.selected && state.watch.find((w) => watchKey(w) === state.selected);
    const out = [];
    for (const it of state.items.values()) {
      if (!inScope(it)) continue;
      if (state.tab === "watch") {
        const w = watchOf(it);
        if (!w || (sel && w !== sel)) continue;
      }
      if (state.market && it.market !== state.market) continue;
      if (ex && !it.is_exchange) continue;
      if (imp && !it.important) continue;
      if (noFix && it.is_correction) continue;
      if (!matches(it, st)) continue;
      out.push(it);
    }
    return out;
  }

  function filtered(base = baseFiltered()) {
    return base.filter(catMatch).sort((a, b) => b.seq - a.seq); // seq = DART 게시 순서(접수번호는 시간순이 아님)
  }

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // 검색어와 글자 그대로 같은 부분에 형광펜 (공백·기호를 무시하고 맞은 경우엔 표시 안 될 수 있음)
  function hl(text, st) {
    const words = [...new Set(st.raw)];
    if (!words.length) return esc(text);
    const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
    // split에 캡처 그룹을 쓰면 홀수 번째 조각이 일치한 부분이다
    return String(text).split(re).map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part))).join("");
  }

  function rowHTML(it, st) {
    const watched = !!watchOf(it);
    const cls = [watched ? "watched" : "", state.freshIds.has(it.rcept_no) ? "fresh" : ""].join(" ");
    const time = state.mode === "range"
      ? `${it.rcept_dt.slice(4, 6)}.${it.rcept_dt.slice(6, 8)}`
      : it.first_seen ? it.first_seen.slice(0, 5) : "–";
    const tags = [
      it.is_exchange ? `<span class="tag krx" title="${esc(it.flags.join(", "))}">KRX</span>` : "",
      ...it.categories.map((c) => `<span class="tag cat">${esc(c)}</span>`),
      ...it.tags.map((t) => `<span class="tag ${it.important ? "imp" : ""}">${esc(t)}</span>`),
      it.is_correction ? `<span class="tag fix">정정</span>` : "",
    ].join("");
    return `<tr class="${cls}" data-id="${it.rcept_no}">
      <td class="time">${time}</td>
      <td class="mkt-cell"><span class="mkt ${esc(it.market)}">${esc(it.market)}</span></td>
      <td><div class="corp">
        <button class="star ${watched ? "on" : ""}" data-star="${it.rcept_no}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
        <span class="nm" title="${esc(it.corp_name)}">${hl(it.corp_name, st)}</span>
        ${it.stock_code ? `<span class="cd">${hl(it.stock_code, st)}</span>` : ""}${flagBadges(it.stock_code)}
      </div></td>
      <td class="title"><a href="${esc(it.url)}" target="_blank" rel="noopener">${hl(it.report_nm, st)}</a>${tags}</td>
      <td class="flr" title="${esc(it.flr_nm)}">${hl(it.flr_nm, st)}</td>
    </tr>`;
  }

  function render() {
    const base = baseFiltered();
    const list = filtered(base);
    const st = searchTerms();
    $("rows").innerHTML = list.slice(0, state.limit).map((it) => rowHTML(it, st)).join("");
    $("moreBtn").hidden = list.length <= state.limit;
    const all = scoped();
    if (!list.length) {
      const msg = state.tab === "watch" && !state.watch.length
        ? "관심종목이 없습니다. 목록의 ☆를 눌러 추가하세요."
        : all.length ? "조건에 맞는 공시가 없습니다." : "코스피·코스닥 공시가 없습니다.";
      showEmpty(msg);
    } else {
      $("empty").hidden = true;
    }
    const filtering = st.whole || state.cats.size || state.market || $("fExchange").checked || $("fImportant").checked || $("fNoCorrection").checked;
    $("resultLine").textContent = filtering ? `조건에 맞는 공시 ${list.length.toLocaleString()}건` : "";
    renderDiscMeta(all.length);
    $("allCount").textContent = all.length.toLocaleString();
    $("watchFeedCount").textContent = all.filter((it) => watchOf(it)).length.toLocaleString();
    renderCats(base);
    renderWatch();
  }

  function renderCats(base) {
    const counts = new Map([["*", 0]]);
    for (const it of base) {
      if (it.categories.length) counts.set("*", counts.get("*") + 1);
      for (const c of it.categories) counts.set(c, (counts.get(c) || 0) + 1);
    }
    for (const b of $("catBar").querySelectorAll("button[data-c]")) {
      const n = counts.get(b.dataset.c) || 0;
      b.querySelector(".n").textContent = n;
      b.classList.toggle("on", state.cats.has(b.dataset.c));
      b.classList.toggle("zero", n === 0);
    }
  }

  function buildCatBar(names) {
    state.catNames = names;
    $("catBar").insertAdjacentHTML("beforeend", names
      .map((c) => `<button data-c="${esc(c)}">${esc(c)} <span class="n"></span></button>`).join(""));
  }

  function showEmpty(msg) {
    $("empty").hidden = false;
    $("empty").textContent = msg;
  }

  function renderWatch() {
    const counts = new Map();
    const all = scoped();
    for (const it of all) {
      const w = watchOf(it);
      if (w) counts.set(watchKey(w), (counts.get(watchKey(w)) || 0) + 1);
    }
    $("watchCount").textContent = state.watch.length;
    $("watchEmpty").hidden = state.watch.length > 0;
    $("watchList").innerHTML = state.watch.map((w) => {
      const k = watchKey(w);
      const n = counts.get(k) || 0;
      return `<li data-key="${esc(k)}" class="${state.selected === k ? "sel" : ""}">
        <span class="nm">${esc(w.name || w.code)}</span>
        ${w.code ? `<span class="cd">${esc(w.code)}</span>` : ""}
        <span class="n ${n ? "has" : ""}">${n}건</span>
        <button class="x" data-remove="${esc(k)}" title="삭제">×</button>
      </li>`;
    }).join("");

    // 자동완성: 오늘 목록에 나온 회사들
    const seen = new Map();
    for (const it of all) if (!seen.has(it.corp_name)) seen.set(it.corp_name, it.stock_code);
    $("corpList").innerHTML = [...seen].slice(0, 3000)
      .map(([n, c]) => `<option value="${esc(n)}">${c ? esc(c) : ""}</option>`).join("");
  }

  let toastTimer = null;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
  }

  // ---------- 투자경고·관리종목 ----------
  const MK_KINDS = [["warning", "투자경고"], ["risk", "투자위험"], ["caution", "투자주의"], ["management", "관리종목"], ["halt", "거래정지"]];
  const MK_LABEL = Object.fromEntries(MK_KINDS);
  const MK_SHORT = { warning: "경고", risk: "위험", caution: "주의", management: "관리", halt: "정지" };
  const MK_REFRESH_MS = 5 * 60_000; // 서버도 5분 캐시
  // 신규 지정 알림: "마지막으로 확인한 목록"을 브라우저에 저장해 두고, 지금 목록과 비교한다.
  // 확인 완료를 누르기 전까지 새 종목은 NEW로 남는다.
  const SEEN_KEY = "disclosure-portal.market-seen.v1";

  function loadSeen() {
    try {
      const v = JSON.parse(localStorage.getItem(SEEN_KEY) || "null");
      return v && v.kinds ? v : { kinds: {}, at: null };
    } catch { return { kinds: {}, at: null }; }
  }
  function saveSeen() {
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(state.mk.seen)); } catch { /* 저장 불가 */ }
  }

  async function loadMarketLists(fresh) {
    const res = await Promise.all(MK_KINDS.map(([k]) =>
      fetchJSON(`/api/market?kind=${k}${fresh ? "&fresh=1" : ""}`).then((d) => [k, d, null], (e) => [k, null, e.message])));
    const flags = new Map();
    for (const [k, d, err] of res) {
      state.mk.errors[k] = err;
      if (d) state.mk.lists[k] = d.items;
      for (const it of state.mk.lists[k] || []) {
        if (!flags.has(it.code)) flags.set(it.code, new Set());
        flags.get(it.code).add(k);
      }
    }
    state.mk.flags = flags;
    state.mk.loadedAt = nowKst();
    diffWithSeen();
    renderMarket();
    if (state.view === "disc") render();
  }

  // 지난 확인 이후 새로 지정된/해제된 종목. 처음 보는 목록은 조용히 기준으로 저장한다.
  function diffWithSeen() {
    const seen = state.mk.seen;
    let changed = false;
    state.mk.added = {};
    state.mk.released = {};
    for (const [k] of MK_KINDS) {
      const list = state.mk.lists[k];
      if (!list) continue;
      if (!seen.kinds[k]) {
        seen.kinds[k] = list.map((it) => ({ code: it.code, name: it.name }));
        changed = true;
        continue;
      }
      const before = new Set(seen.kinds[k].map((x) => x.code));
      const now = new Set(list.map((it) => it.code));
      state.mk.added[k] = new Set(list.filter((it) => !before.has(it.code)).map((it) => it.code));
      state.mk.released[k] = seen.kinds[k].filter((x) => !now.has(x.code));
    }
    if (changed) {
      seen.at = seen.at || nowKst();
      saveSeen();
    }
    notifyNew();
  }

  const isNew = (code, kind) => (kind === "all"
    ? MK_KINDS.some(([k]) => state.mk.added[k] && state.mk.added[k].has(code))
    : Boolean(state.mk.added[kind] && state.mk.added[kind].has(code)));
  const addedCount = (k) => (state.mk.added[k] ? state.mk.added[k].size : 0);
  const totalAdded = () => MK_KINDS.reduce((n, [k]) => n + addedCount(k), 0);

  // 이번 접속에서 처음 발견한 신규 종목만 알린다(5분마다 다시 확인해도 같은 걸 반복해서 알리지 않음)
  function notifyNew() {
    const fresh = [];
    for (const [k, label] of MK_KINDS) {
      for (const code of state.mk.added[k] || []) {
        const key = `${k}:${code}`;
        if (state.mk.notified.has(key)) continue;
        state.mk.notified.add(key);
        const it = state.mk.lists[k].find((x) => x.code === code);
        fresh.push(`${label} ${it ? it.name : code}`);
      }
    }
    updateViewBadge();
    if (!fresh.length) return;
    const msg = `새로 지정: ${fresh.slice(0, 3).join(", ")}${fresh.length > 3 ? ` 외 ${fresh.length - 3}건` : ""}`;
    toast(`⚠ ${msg}`);
    if ("Notification" in window && Notification.permission === "granted") {
      try { new Notification("투자경고·관리종목 신규 지정", { body: msg, tag: "market-new" }); } catch { /* 일부 모바일 브라우저 */ }
    }
  }

  function ackSeen() {
    for (const [k] of MK_KINDS) {
      if (state.mk.lists[k]) state.mk.seen.kinds[k] = state.mk.lists[k].map((it) => ({ code: it.code, name: it.name }));
    }
    state.mk.seen.at = nowKst();
    saveSeen();
    diffWithSeen();
    renderMarket();
  }

  function updateViewBadge() {
    const n = totalAdded();
    const b = document.querySelector('#viewNav button[data-view="market"]');
    b.innerHTML = `투자경고·관리종목${n ? ` <span class="nbadge">${n}</span>` : ""}`;
  }

  // 전체: 다섯 목록을 종목 하나로 합친다(조치 여러 개면 배지 여러 개)
  function mergedList() {
    const by = new Map();
    for (const [k] of MK_KINDS) {
      for (const it of state.mk.lists[k] || []) {
        let m = by.get(it.code);
        if (!m) { m = { ...it, kinds: [], reasons: [] }; by.set(it.code, m); }
        m.kinds.push(k);
        if (it.reason && !m.reasons.includes(it.reason)) m.reasons.push(it.reason);
        if (it.date && (!m.date || it.date > m.date)) m.date = it.date;
      }
    }
    const order = (m) => MK_KINDS.findIndex(([k]) => m.kinds.includes(k));
    return [...by.values()].sort((a, b) => (isNew(b.code, "all") - isNew(a.code, "all")) || order(a) - order(b) || (b.date || "").localeCompare(a.date || ""));
  }

  // 공시 목록의 회사 옆에 붙이는 표시 (경고·관리·정지 등)
  function flagBadges(code, except) {
    const f = code && state.mk.flags.get(code);
    if (!f) return "";
    return MK_KINDS.filter(([k]) => f.has(k) && k !== except)
      .map(([k, label]) => `<span class="badge ${k}" title="${label}">${MK_SHORT[k]}</span>`).join("");
  }

  function mkRowHTML(it, q, kind) {
    const watched = state.watch.some((w) => w.code === it.code);
    const fresh = isNew(it.code, kind);
    const rate = it.change_rate;
    const rateCls = rate > 0 ? "up" : rate < 0 ? "down" : "";
    const date = it.date ? `${it.date.slice(0, 4)}.${it.date.slice(4, 6)}.${it.date.slice(6, 8)}` : "–";
    const st = { raw: q ? q.split(/\s+/).filter(Boolean) : [] };
    const reasons = kind === "all" ? it.reasons.join(" · ") : it.reason || "";
    const badges = kind === "all"
      ? it.kinds.map((k) => `<span class="badge ${k}">${MK_SHORT[k]}</span>`).join("")
      : flagBadges(it.code, kind);
    return `<tr class="${watched ? "watched" : ""} ${fresh ? "mk-new" : ""}">
      <td><div class="corp">
        <button class="star ${watched ? "on" : ""}" data-mkstar="${esc(it.code)}" data-name="${esc(it.name)}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
        <span class="nm" title="${esc(it.name)}">${hl(it.name, st)}</span><span class="cd">${hl(it.code, st)}</span>
        ${fresh ? '<span class="badge new">NEW</span>' : ""}
      </div></td>
      <td class="mkt-cell"><span class="mkt ${esc(it.market)}">${esc(it.market)}</span></td>
      <td class="num">${it.price === null ? "–" : it.price.toLocaleString()}</td>
      <td class="num ${rateCls}">${rate === null ? "–" : `${rate > 0 ? "+" : ""}${rate.toFixed(2)}%`}</td>
      <td class="date">${date}</td>
      <td class="reason">${kind === "all" ? badges + " " : ""}${hl(reasons, st)}${kind === "all" ? "" : badges}</td>
      <td><button class="linkbtn" data-disc="${esc(it.name)}">공시 보기</button></td>
    </tr>`;
  }

  function renderNews() {
    const box = $("mkNews");
    const n = totalAdded();
    const rel = MK_KINDS.flatMap(([k, label]) => (state.mk.released[k] || []).map((x) => `${label} ${x.name}`));
    const perm = "Notification" in window ? Notification.permission : "unsupported";
    const notifBtn = perm === "default" ? '<button id="mkNotify" class="ghost small">브라우저 알림 켜기</button>' : "";
    const since = state.mk.seen.at ? `${state.mk.seen.at} 확인 이후` : "";
    if (!n && !rel.length) {
      box.className = "mk-news";
      box.innerHTML = `<span>${since ? `${since} 새로 지정된 종목이 없습니다.` : "지금 목록을 기준으로 저장했습니다. 다음부터 새로 지정되면 알려드립니다."} 열어두면 5분마다 확인합니다.</span>${notifBtn}`;
      return;
    }
    const parts = MK_KINDS.filter(([k]) => addedCount(k)).map(([k, label]) => `${label} ${addedCount(k)}`);
    box.className = "mk-news alert";
    box.innerHTML = `<span><b>${since} 새로 지정 ${n}종목</b>${parts.length ? ` (${parts.join(", ")})` : ""}` +
      `${rel.length ? ` · 해제 ${rel.length}종목: ${esc(rel.slice(0, 5).join(", "))}${rel.length > 5 ? " …" : ""}` : ""}</span>` +
      `<button id="mkAck" class="primary small">확인 완료</button>${notifBtn}`;
  }

  function renderMarket() {
    const k = state.mk.kind;
    const merged = mergedList();
    const tabs = [["all", "전체"], ...MK_KINDS];
    $("mkTabs").innerHTML = tabs.map(([kind, label]) => {
      const list = kind === "all" ? merged : state.mk.lists[kind];
      const w = (list || []).filter((it) => state.watch.some((x) => x.code === it.code)).length;
      const err = kind === "all" ? MK_KINDS.some(([x]) => state.mk.errors[x] && !state.mk.lists[x]) : state.mk.errors[kind] && !list;
      const count = err && !(list && list.length) ? "!" : list ? list.length : "…";
      const nNew = kind === "all" ? totalAdded() : addedCount(kind);
      return `<button data-k="${kind}" class="${kind === k ? "on" : ""}">
        <span class="t">${label}</span><span class="c">${count}</span>
        <span class="w">${nNew ? `<span class="new">신규 ${nNew}</span>` : ""}${w ? `관심종목 ${w}` : ""}</span></button>`;
    }).join("");
    $("mkDateHead").textContent = ["management", "halt", "all"].includes(k) ? "지정일" : "";
    renderNews();
    const q = $("mkSearch").value.trim();
    const nq = norm(q);
    const watchOnly = $("mkWatchOnly").checked;
    const source = k === "all" ? merged : state.mk.lists[k];
    const rows = (source || []).filter((it) =>
      (!state.mk.market || it.market === state.mk.market) &&
      (!watchOnly || state.watch.some((w) => w.code === it.code)) &&
      (!nq || norm(`${it.name} ${it.code} ${k === "all" ? it.reasons.join(" ") + " " + it.kinds.map((x) => MK_LABEL[x]).join(" ") : it.reason}`).includes(nq)));
    if (k !== "all") rows.sort((a, b) => isNew(b.code, k) - isNew(a.code, k)); // 신규를 맨 위로
    $("mkRows").innerHTML = rows.map((it) => mkRowHTML(it, q, k)).join("");
    const empty = $("mkEmpty");
    const err = k === "all" ? null : state.mk.errors[k];
    if (err && !source) { empty.hidden = false; empty.textContent = `불러오지 못했습니다: ${err}`; }
    else if (!source || (k === "all" && !state.mk.loadedAt)) { empty.hidden = false; empty.textContent = "불러오는 중…"; }
    else if (!rows.length) { empty.hidden = false; empty.textContent = source.length ? "조건에 맞는 종목이 없습니다." : "지금 지정된 종목이 없습니다."; }
    else empty.hidden = true;
    $("mkMeta").textContent = state.mk.loadedAt ? `${rows.length}종목 · ${state.mk.loadedAt} 확인` : "";
  }

  function setView(view) {
    state.view = view;
    for (const b of document.querySelectorAll("#viewNav button")) b.classList.toggle("on", b.dataset.view === view);
    const disc = view === "disc";
    $("discView").hidden = !disc;
    $("marketView").hidden = view !== "market";
    $("calView").hidden = view !== "cal";
    for (const id of ["live", "refreshBtn", "modeSeg"]) $(id).hidden = !disc;
    if (disc) {
      $("dayNav").hidden = state.mode !== "day";
      $("rangeBar").hidden = state.mode !== "range";
      render();
    } else {
      $("dayNav").hidden = true;
      $("rangeBar").hidden = true;
      $("recon").hidden = true;
      if (view === "market") {
        renderMarket();
        if (!state.mk.loadedAt) loadMarketLists(false);
      } else {
        renderCal();
        if (!state.cal.loadedAt && !state.cal.loading) loadCalendar(false);
      }
    }
  }

  // ---------- 캘린더 (공개매수·매수청구) ----------
  const MANUAL_KEY = "disclosure-portal.calendar-manual.v1";
  const fmtMD = (d) => `${d.slice(4, 6)}.${d.slice(6, 8)}`;
  const daysDiff = (a, b) => Math.round((Date.UTC(+b.slice(0, 4), +b.slice(4, 6) - 1, +b.slice(6, 8)) - Date.UTC(+a.slice(0, 4), +a.slice(4, 6) - 1, +a.slice(6, 8))) / 86400000);

  function loadManual() {
    try { const v = JSON.parse(localStorage.getItem(MANUAL_KEY) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; }
  }
  function saveManual() {
    try { localStorage.setItem(MANUAL_KEY, JSON.stringify(state.cal.manual)); } catch { /* 저장 불가 */ }
  }

  // 서버에서 후보를 받고, 회사마다 일정(매수청구)·신고서(공개매수)를 동시에 몇 개씩 불러온다
  async function loadCalendar(fresh) {
    const cal = state.cal;
    cal.loading = true;
    cal.error = null;
    $("calMeta").textContent = "불러오는 중…";
    renderCal();
    const q = fresh ? "&fresh=1" : "";
    try {
      const [c, cap] = await Promise.all([
        fetchJSON(`/api/events/candidates?x=1${q}`),
        fetchJSON(`/api/events/capital-candidates?x=1${q}`).catch(() => ({ items: [] })), // 증자는 실패해도 나머지는 보여준다
      ]);
      const jobs = [
        // 증자: 신주인수권증서 상장기간, 신주 상장예정일 → 일정 두 개로 나눈다
        ...cap.items.map((x) => async () => {
          const r = await fetchJSON(`/api/events/capital?rcept_no=${x.rcept_no}${q}`);
          const e = r.event;
          const base = { type: "capital", name: x.corp_name, code: x.stock_code, market: x.market, rcept_no: x.rcept_no, announced: x.first_rcept_no.slice(0, 8), record_date: e.record_date };
          const out = [];
          if (e.cert_start) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:cert`, sub: "cert", kind: `증서상장·${x.kind}`, start: e.cert_start, end: e.cert_end || e.cert_start });
          if (e.listing) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:list`, sub: "listing", kind: `신주상장·${x.kind}`, start: e.listing, end: e.listing });
          if (e.record_date) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:rec`, sub: "record", kind: `배정기준일·${x.kind}`, start: e.record_date, end: e.record_date });
          return out;
        }),
        ...c.deals.map((d) => async () => {
          const r = await fetchJSON(`/api/events/deal?api=${d.api}&corp_code=${d.corp_code}&rcept_no=${d.rcept_no}${q}`);
          const e = r.event;
          if (!e || e.none) return null;
          return { ...e, id: `deal:${d.corp_code}:${d.api}`, name: d.corp_name, code: d.stock_code, market: d.market, withdrawn: Boolean(d.withdrawn_rcept_no), announced: (d.first_rcept_no || d.rcept_no).slice(0, 8) };
        }),
        ...c.tenders.map((t) => async () => {
          const r = await fetchJSON(`/api/events/tender?rcept_no=${t.rcept_no}${q}`);
          return { ...r.event, id: `tender:${t.first_rcept_no}`, announced: t.first_rcept_no.slice(0, 8), kind: "공개매수", name: t.corp_name, code: t.stock_code, market: t.market, offeror: t.offeror, done: t.done };
        }),
      ];
      const out = [];
      let failed = 0;
      let next = 0;
      let done = 0;
      const run = async () => {
        while (next < jobs.length) {
          const job = jobs[next++];
          try { const e = await job(); if (Array.isArray(e)) out.push(...e); else if (e) out.push(e); } catch { failed++; }
          $("calMeta").textContent = `불러오는 중 ${++done}/${jobs.length}`;
        }
      };
      await Promise.all(Array.from({ length: 6 }, run));
      // 이전에 불러온 것과 비교해 새로 생긴 일정 표시(처음 불러올 땐 표시 안 함)
      if (cal.loadedAt) {
        const before = new Set(cal.events.map((e) => e.id));
        const added = out.filter((e) => !before.has(e.id));
        for (const e of added) cal.newIds.add(e.id);
        if (added.length) toast(`📅 캘린더에 새 일정 ${added.length}건: ${added.slice(0, 3).map((e) => `${e.name}(${e.type === "tender" ? "공개매수" : e.type === "capital" ? e.kind : "매수청구"})`).join(", ")}`);
      }
      cal.events = out;
      cal.failed = failed;
      cal.loadedAt = nowKst();
      await loadQuotes();
    } catch (e) {
      cal.error = e.message;
    } finally {
      cal.loading = false;
      renderCal();
    }
  }

  async function loadQuotes() {
    const codes = [...new Set(allEvents().map((e) => e.code).filter((c) => /^[0-9A-Z]{6}$/.test(c || "")))];
    for (let i = 0; i < codes.length; i += 100) {
      try {
        const r = await fetchJSON(`/api/quotes?codes=${codes.slice(i, i + 100).join(",")}`);
        Object.assign(state.cal.quotes, r.quotes);
      } catch { /* 현재가는 없어도 된다 */ }
    }
  }

  function allEvents() {
    return [...state.cal.events, ...state.cal.manual.map((m) => ({ ...m, manual: true }))];
  }

  function status(e) {
    const t = state.today;
    if (e.withdrawn) return { key: "done", label: "철회", order: 5 };
    if (!e.start && !e.end) return { key: "tbd", label: "미정", order: 3 };
    if (e.end && t > e.end) return { key: "done", label: "종료", order: 4 };
    if (e.start && t < e.start) return { key: "soon", label: `D-${daysDiff(t, e.start)}`, order: 2, sort: e.start };
    return { key: "on", label: e.end ? (t === e.end ? "오늘 마감" : `진행 D-${daysDiff(t, e.end)}`) : "진행중", order: 1, sort: e.end || "99999999" };
  }

  function visibleEvents() {
    const tender = $("calTender").checked;
    const buyback = $("calBuyback").checked;
    const hidePast = $("calHidePast").checked;
    const watchOnly = $("calWatchOnly").checked;
    return allEvents().filter((e) =>
      (e.type === "tender" ? tender : e.type === "capital" ? $("calCapital").checked : buyback) &&
      (!hidePast || status(e).key !== "done") &&
      (!watchOnly || state.watch.some((w) => w.code === e.code)));
  }

  const isWatchedCode = (code) => Boolean(code) && state.watch.some((w) => w.code === code);
  const activeOn = (e, d) => e.start && e.end && e.start <= d && d <= e.end;

  function renderCalGrid(events) {
    const m = state.cal.month; // YYYYMM
    const y = +m.slice(0, 4);
    const mo = +m.slice(4, 6);
    $("calTitle").textContent = `${y}년 ${mo}월`;
    const first = new Date(Date.UTC(y, mo - 1, 1));
    const startDate = ymdUTC(new Date(Date.UTC(y, mo - 1, 1 - first.getUTCDay())));
    const cells = ["일", "월", "화", "수", "목", "금", "토"].map((d, i) => `<div class="dow ${i === 0 ? "sun" : i === 6 ? "sat" : ""}">${d}</div>`);
    for (let i = 0; i < 42; i++) {
      const d = shiftDate(startDate, i);
      if (i === 35 && d.slice(0, 6) !== m) break; // 6주째가 통째로 다음 달이면 생략
      const dow = i % 7;
      const chips = [];
      let active = 0;
      for (const e of events) {
        if (activeOn(e, d)) active++;
        const w = isWatchedCode(e.code) ? "watched" : "";
        if (e.sub === "listing" || e.sub === "record") { // 신주 상장일·신주배정기준일은 하루짜리
          const label = e.sub === "listing" ? "◆ " + esc(e.name) + " 신주상장" : "● " + esc(e.name) + " 배정기준일";
          if (e.start === d) chips.push(`<span class="ev capital ${e.sub === "listing" ? "end" : ""} ${w}" title="${esc(e.name)} ${esc(e.kind)}">${label}</span>`);
          continue;
        }
        const tag = e.sub === "cert" ? " 증서" : "";
        if (e.start === d) chips.push(`<span class="ev ${e.type} ${w}" title="${esc(e.name)} ${esc(e.kind)} 시작">▶ ${esc(e.name)}${tag}</span>`);
        if (e.end === d) chips.push(`<span class="ev ${e.type} end ${w}" title="${esc(e.name)} ${esc(e.kind)} 마감">■ ${esc(e.name)}${tag} 마감</span>`);
      }
      const cls = ["day", d.slice(0, 6) !== m ? "other" : "", d === state.today ? "today" : "", d === state.cal.selected ? "sel" : "", dow === 0 ? "sun" : dow === 6 ? "sat" : ""].join(" ");
      cells.push(`<div class="${cls}" data-day="${d}">
        <div class="top"><span class="n">${+d.slice(6, 8)}</span>${active ? `<span class="act">진행 ${active}</span>` : ""}</div>
        ${chips.slice(0, 4).join("")}${chips.length > 4 ? `<span class="more">+${chips.length - 4}</span>` : ""}
      </div>`);
    }
    $("calGrid").innerHTML = cells.join("");
  }

  function ymdUTC(dt) {
    return dt.toISOString().slice(0, 10).replaceAll("-", "");
  }

  function calRowHTML(e, pinned = false) {
    const st = status(e);
    const watched = isWatchedCode(e.code);
    const q = e.code ? state.cal.quotes[e.code] : null;
    const cur = q && q.price;
    const gap = e.price && cur ? ((e.price - cur) / cur) * 100 : null;
    const gapCls = gap === null ? "" : gap > 0 ? "up" : gap < 0 ? "down" : "";
    const period = e.sub === "listing" ? `${fmtMD(e.start)} 상장` : e.sub === "record" ? `${fmtMD(e.start)} 기준일` : e.start || e.end ? `${e.start ? fmtMD(e.start) : "?"} ~ ${e.end ? fmtMD(e.end) : "?"}${e.estimated ? ' <span class="est">(추정)</span>' : ""}` : "미정";
    const notes = [
      e.type === "tender" && e.offeror ? `공개매수자 ${esc(e.offeror)}` : "",
      e.partner ? `상대 ${esc(e.partner)}` : "",
      e.gm_date ? `주총 ${fmtMD(e.gm_date)}` : "",
      e.done ? "결과보고서 제출" : "",
      e.record_date && e.sub !== "record" ? `배정기준일 ${fmtMD(e.record_date)}` : "",
      e.sub === "record" ? "이날 주주명부 기준으로 신주 배정 — 권리락은 보통 하루 전 영업일" : "",
      e.sub === "cert" ? "신주인수권증서 상장(매매)기간" : "",
      e.withdrawn ? "결정 뒤 철회 공시 — 원문 확인" : "",
      e.source === "본문" ? "기간은 보고서 본문 기준" : "",
      e.memo ? esc(e.memo) : "",
      e.manual ? "직접 추가" : "",
    ].filter(Boolean).join(" · ");
    const kindLabel = e.type === "tender" ? "공개매수" : e.type === "capital" ? e.kind : `매수청구·${e.kind || ""}`.replace(/·$/, "");
    return `<tr class="${watched ? "watched" : ""} ${st.key === "done" && !pinned ? "past" : ""} ${pinned ? "pinned" : ""}">
      <td><span class="st ${st.key}">${st.label}</span>${pinned && e.announced ? `<div class="ann">공시 ${fmtMD(e.announced)}</div>` : ""}</td>
      <td class="kind-cell"><span class="kind ev ${e.type}">${esc(kindLabel)}</span></td>
      <td><div class="corp">
        ${e.code ? `<button class="star ${watched ? "on" : ""}" data-calstar="${esc(e.code)}" data-name="${esc(e.name)}">${watched ? "★" : "☆"}</button>` : ""}
        <span class="nm" title="${esc(e.name)}">${esc(e.name)}</span>${e.code ? `<span class="cd">${esc(e.code)}</span>` : ""}${state.cal.newIds.has(e.id) ? '<span class="badge new">NEW</span>' : ""}${flagBadges(e.code)}
      </div></td>
      <td class="period">${period}</td>
      <td class="num">${e.price ? e.price.toLocaleString() : "–"}</td>
      <td class="num">${cur ? cur.toLocaleString() : "–"}</td>
      <td class="num ${gapCls}">${gap === null ? "–" : `${gap > 0 ? "+" : ""}${gap.toFixed(2)}%`}</td>
      <td class="note">${notes}</td>
      <td>${e.manual
        ? `<button class="linkbtn del" data-caldel="${esc(e.id)}">삭제</button>`
        : `<a class="linkbtn" href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${esc(e.rcept_no)}" target="_blank" rel="noopener">원문</a>`}</td>
    </tr>`;
  }

  // 📌 최근 공시: 처음 공시된 날짜가 가장 최근인 공개매수·매수청구 각 3건을 리스트 맨 위에 고정.
  // 날짜 선택·체크박스 필터와 상관없이 항상 보인다(철회된 건만 뺀다).
  const PIN_PER_TYPE = 3;
  function pinnedHTML() {
    const pick = (type) => allEvents()
      .filter((e) => e.type === type && !e.withdrawn && e.announced)
      .sort((a, b) => b.announced.localeCompare(a.announced) || a.name.localeCompare(b.name))
      .slice(0, PIN_PER_TYPE);
    const list = [...pick("tender"), ...pick("buyback")];
    if (!list.length) return "";
    return `<tr class="pin-head"><td colspan="9">📌 최근 공시된 공개매수·매수청구 (각 ${PIN_PER_TYPE}건, 처음 공시일 기준)</td></tr>` +
      list.map((e) => calRowHTML(e, true)).join("") +
      '<tr class="pin-sep"><td colspan="9"></td></tr>';
  }

  function renderCal() {
    const cal = state.cal;
    if (!cal.month) cal.month = state.today.slice(0, 6);
    const events = visibleEvents();
    renderCalGrid(events);
    let rows = events;
    if (cal.selected) rows = rows.filter((e) => activeOn(e, cal.selected) || e.start === cal.selected || e.end === cal.selected);
    rows = [...rows].sort((a, b) => {
      const sa = status(a);
      const sb = status(b);
      if (sa.order !== sb.order) return sa.order - sb.order;
      if (sa.key === "done") return (b.end || "").localeCompare(a.end || "");
      return (sa.sort || "").localeCompare(sb.sort || "") || a.name.localeCompare(b.name);
    });
    $("calListTitle").textContent = cal.selected ? `${+cal.selected.slice(4, 6)}월 ${+cal.selected.slice(6, 8)}일 진행 중인 일정 ${rows.length}건` : `일정 ${rows.length}건`;
    $("calClearDay").hidden = !cal.selected;
    $("calPinned").innerHTML = pinnedHTML();
    $("calRows").innerHTML = rows.map((e) => calRowHTML(e)).join("");
    const empty = $("calEmpty");
    if (cal.error) { empty.hidden = false; empty.textContent = `불러오지 못했습니다: ${cal.error}`; }
    else if (cal.loading && !cal.events.length) { empty.hidden = false; empty.textContent = "DART에서 일정을 모으는 중… (처음엔 30초 정도 걸릴 수 있어요)"; }
    else if (!rows.length) { empty.hidden = false; empty.textContent = "해당하는 일정이 없습니다."; }
    else empty.hidden = true;
    if (!cal.loading) {
      $("calMeta").textContent = cal.loadedAt
        ? `공개매수 ${allEvents().filter((e) => e.type === "tender").length} · 매수청구 ${allEvents().filter((e) => e.type === "buyback").length} · 증자 ${new Set(allEvents().filter((e) => e.type === "capital").map((e) => e.name)).size}건 · ${cal.loadedAt} 업데이트${cal.failed ? ` · ${cal.failed}건 실패` : ""}`
        : "";
    }
  }

  function bindCalendar() {
    $("calPrev").addEventListener("click", () => { state.cal.month = shiftMonth(state.cal.month, -1); renderCal(); });
    $("calNext").addEventListener("click", () => { state.cal.month = shiftMonth(state.cal.month, 1); renderCal(); });
    $("calThisMonth").addEventListener("click", () => { state.cal.month = state.today.slice(0, 6); renderCal(); });
    for (const id of ["calTender", "calBuyback", "calCapital", "calHidePast", "calWatchOnly"]) {
      $(id).addEventListener("change", () => { saveCalPrefs(); renderCal(); });
    }
    loadCalPrefs();
    $("calRefresh").addEventListener("click", () => loadCalendar(true));
    $("calGrid").addEventListener("click", (e) => {
      const day = e.target.closest("[data-day]");
      if (!day) return;
      state.cal.selected = state.cal.selected === day.dataset.day ? null : day.dataset.day;
      renderCal();
    });
    $("calClearDay").addEventListener("click", () => { state.cal.selected = null; renderCal(); });
    $("calRows").addEventListener("click", (e) => {
      const star = e.target.closest("[data-calstar]");
      if (star) {
        const code = star.dataset.calstar;
        const w = state.watch.find((x) => x.code === code);
        if (w) removeWatch(watchKey(w));
        else addWatch({ code, name: star.dataset.name });
        renderCal();
        renderWatch();
        return;
      }
      const del = e.target.closest("[data-caldel]");
      if (del && confirm("이 일정을 삭제할까요?")) {
        state.cal.manual = state.cal.manual.filter((m) => m.id !== del.dataset.caldel);
        saveManual();
        renderCal();
      }
    });
    $("calAddBtn").addEventListener("click", () => { $("calAddForm").hidden = !$("calAddForm").hidden; });
    $("caCancel").addEventListener("click", () => { $("calAddForm").hidden = true; });
    $("calAddForm").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const start = fromInput($("caStart").value) || null;
      const end = fromInput($("caEnd").value) || null;
      if (start && end && start > end) { toast("시작일이 종료일보다 늦습니다."); return; }
      const type = $("caType").value;
      const code = $("caCode").value.trim().toUpperCase();
      state.cal.manual.push({
        id: `manual:${Date.now()}`,
        type,
        kind: type === "tender" ? "공개매수" : "",
        name: $("caName").value.trim(),
        code,
        start,
        end,
        price: Number($("caPrice").value.replace(/[^\d]/g, "")) || null,
        memo: $("caMemo").value.trim(),
      });
      saveManual();
      $("calAddForm").reset();
      $("calAddForm").hidden = true;
      if (code && !state.cal.quotes[code]) await loadQuotes();
      if (start) state.cal.month = start.slice(0, 6);
      renderCal();
    });
  }

  // 체크박스 상태는 브라우저에 기억한다
  const CAL_PREFS_KEY = "disclosure-portal.calendar-prefs.v1";
  const CAL_PREF_IDS = ["calTender", "calBuyback", "calCapital", "calHidePast", "calWatchOnly"];
  function saveCalPrefs() {
    try { localStorage.setItem(CAL_PREFS_KEY, JSON.stringify(Object.fromEntries(CAL_PREF_IDS.map((id) => [id, $(id).checked])))); } catch { /* 무시 */ }
  }
  function loadCalPrefs() {
    try {
      const p = JSON.parse(localStorage.getItem(CAL_PREFS_KEY) || "{}");
      for (const id of CAL_PREF_IDS) if (typeof p[id] === "boolean") $(id).checked = p[id];
    } catch { /* 무시 */ }
  }

  function shiftMonth(m, n) {
    const d = new Date(Date.UTC(+m.slice(0, 4), +m.slice(4, 6) - 1 + n, 1));
    return d.toISOString().slice(0, 7).replace("-", "");
  }

  // ---------- 링크 가져오기/내보내기 ----------
  function importFromHash() {
    const m = location.hash.match(/[#&]w=([^&]+)/);
    if (!m) return;
    const entries = decodeURIComponent(m[1]).split(",").map((s) => s.trim()).filter(Boolean);
    let added = 0;
    for (const e of entries) {
      const entry = /^\d{6}$/.test(e) ? { code: e, name: "" } : { name: e };
      if (addWatch(entry)) added++;
    }
    history.replaceState(null, "", location.pathname + location.search);
    if (added) toast(`관심종목 ${added}개를 가져왔습니다.`);
  }

  function fillWatchNames() {
    // 코드로만 들어온 항목에 회사명 채우기
    let changed = false;
    for (const w of state.watch) {
      if (w.code && !w.name) {
        for (const it of state.items.values()) {
          if (it.stock_code === w.code) { w.name = it.corp_name; changed = true; break; }
        }
      }
    }
    if (changed) saveWatch();
  }

  // ---------- 이벤트 ----------
  function bind() {
    $("rows").addEventListener("click", (e) => {
      const b = e.target.closest("[data-star]");
      if (!b) return;
      const it = state.items.get(b.dataset.star);
      if (it) toggleWatchFor(it);
    });

    $("watchList").addEventListener("click", (e) => {
      const rm = e.target.closest("[data-remove]");
      if (rm) { removeWatch(rm.dataset.remove); render(); return; }
      const li = e.target.closest("li[data-key]");
      if (!li) return;
      state.selected = state.selected === li.dataset.key ? null : li.dataset.key;
      setTab("watch");
    });

    $("addForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("addInput").value.trim();
      if (!v) return;
      let entry;
      if (/^\d{6}$/.test(v)) {
        const hit = [...state.items.values()].find((it) => it.stock_code === v);
        entry = { code: v, name: hit ? hit.corp_name : "" };
      } else {
        const hit = [...state.items.values()].find((it) => it.corp_name === v);
        entry = hit && hit.stock_code ? { code: hit.stock_code, name: hit.corp_name } : { name: v };
      }
      if (!addWatch(entry)) toast("이미 관심종목에 있습니다.");
      $("addInput").value = "";
      render();
    });

    $("exportBtn").addEventListener("click", async () => {
      if (!state.watch.length) { toast("내보낼 관심종목이 없습니다."); return; }
      const list = state.watch.map((w) => w.code || w.name).join(",");
      const url = `${location.origin}${location.pathname}#w=${encodeURIComponent(list)}`;
      try { await navigator.clipboard.writeText(url); toast("링크를 복사했습니다. 다른 기기에서 열면 관심종목이 추가됩니다."); }
      catch { prompt("이 링크를 복사하세요", url); }
    });

    document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.tab === "all") state.selected = null;
      setTab(b.dataset.tab);
    }));

    $("marketSeg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      state.market = b.dataset.m;
      document.querySelectorAll("#marketSeg button").forEach((x) => x.classList.toggle("on", x === b));
      state.limit = PAGE;
      render();
    });
    $("catBar").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-c]");
      if (!b) return;
      const c = b.dataset.c;
      if (c === "*") {
        state.cats = state.cats.has("*") ? new Set() : new Set(["*"]);
      } else {
        state.cats.delete("*");
        state.cats.has(c) ? state.cats.delete(c) : state.cats.add(c);
      }
      state.limit = PAGE;
      render();
    });
    ["fExchange", "fImportant", "fNoCorrection"].forEach((id) => $(id).addEventListener("change", () => { state.limit = PAGE; render(); }));
    $("search").addEventListener("input", () => { state.limit = PAGE; render(); });
    $("refreshBtn").addEventListener("click", manualRefresh);
    $("reconClose").addEventListener("click", () => { $("recon").hidden = true; });
    $("moreBtn").addEventListener("click", () => { state.limit += PAGE; render(); });

    $("dateInput").addEventListener("change", (e) => {
      const d = fromInput(e.target.value);
      if (/^\d{8}$/.test(d) && d <= state.today) loadDate(d);
      else $("dateInput").value = toInput(state.date);
    });
    $("prevDay").addEventListener("click", () => loadDate(shiftDate(state.date, -1)));
    $("nextDay").addEventListener("click", () => { if (state.date < state.today) loadDate(shiftDate(state.date, 1)); });
    $("todayBtn").addEventListener("click", () => loadDate(state.today));

    bindCalendar();
    $("viewNav").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-view]");
      if (b && b.dataset.view !== state.view) setView(b.dataset.view);
    });
    $("mkTabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-k]");
      if (!b) return;
      state.mk.kind = b.dataset.k;
      renderMarket();
    });
    $("mkMarketSeg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      state.mk.market = b.dataset.m;
      document.querySelectorAll("#mkMarketSeg button").forEach((x) => x.classList.toggle("on", x === b));
      renderMarket();
    });
    $("mkSearch").addEventListener("input", renderMarket);
    $("mkWatchOnly").addEventListener("change", renderMarket);
    $("mkRefresh").addEventListener("click", () => loadMarketLists(true));
    $("mkNews").addEventListener("click", async (e) => {
      if (e.target.closest("#mkAck")) ackSeen();
      if (e.target.closest("#mkNotify") && "Notification" in window) {
        const r = await Notification.requestPermission();
        toast(r === "granted" ? "브라우저 알림을 켰습니다. 이 페이지가 열려 있으면 새 지정 종목을 알려드립니다." : "알림이 허용되지 않았습니다.");
        renderNews();
      }
    });
    $("mkRows").addEventListener("click", (e) => {
      const star = e.target.closest("[data-mkstar]");
      if (star) {
        const code = star.dataset.mkstar;
        const w = state.watch.find((x) => x.code === code);
        if (w) removeWatch(watchKey(w));
        else addWatch({ code, name: star.dataset.name });
        renderMarket();
        renderWatch();
        return;
      }
      const disc = e.target.closest("[data-disc]");
      if (disc) {
        $("search").value = disc.dataset.disc;
        setView("disc");
        if (state.mode === "day") toast("오늘 공시에서 찾았습니다. 지난 공시까지 보려면 위의 '기간'을 누르세요.");
      }
    });

    $("modeSeg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-mode]");
      if (!b || b.dataset.mode === state.mode) return;
      if (b.dataset.mode === "day") {
        loadDate(state.today);
      } else {
        const r = state.range || presetRange("month");
        loadRange(r.from, r.to);
      }
      saveView();
    });
    $("rangePresets").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-p]");
      if (!b) return;
      const r = presetRange(b.dataset.p);
      $("rangeFrom").value = toInput(r.from);
      $("rangeTo").value = toInput(r.to);
    });
    $("rangeLoad").addEventListener("click", () => loadRange(fromInput($("rangeFrom").value), fromInput($("rangeTo").value)));

    // 탭으로 돌아오면 "새 공시" 표시 초기화
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { document.title = "공시 포털"; setTimeout(() => state.freshIds.clear(), 4000); }
    });
    window.addEventListener("storage", (e) => { if (e.key === WATCH_KEY) { state.watch = loadWatch(); render(); } });
  }

  // ---------- 수동 새로고침(대사) ----------
  // DART에서 보고 있는 날짜 전체를 캐시 없이 다시 받아 지금 화면과 비교하고, 화면을 DART
  // 원본과 똑같이 맞춘다(빠진 건 채우고, DART에 없는 건 뺀다).
  async function manualRefresh() {
    const btn = $("refreshBtn");
    if (btn.disabled || (state.mode === "day" && !state.date)) return;
    const date = state.date;
    const range = state.range;
    const isRange = state.mode === "range";
    const token = ++loadToken; // 진행 중인 폴링 결과는 버린다
    const before = new Map(state.items);
    busy = true;
    btn.disabled = true;
    btn.classList.add("busy");
    $("refreshText").textContent = "대사 중…";
    try {
      const list = isRange
        ? await fetchRange(range.from, range.to, true, (d, t) => { $("refreshText").textContent = `대사 중 ${Math.round((d / t) * 100)}%`; })
        : await fetchAll(date, true);
      if (token !== loadToken) return;
      const t = nowKst();
      const dartIds = new Set(list.map((it) => it.rcept_no));
      const added = list.filter((it) => inScope(it) && !before.has(it.rcept_no)).map((it) => it.rcept_no);
      const gone = [...before.values()].filter((it) => inScope(it) && !dartIds.has(it.rcept_no));
      const beforeScoped = [...before.values()].filter(inScope).length;
      const markNew = before.size > 0;
      setAll(list, (it) => (before.has(it.rcept_no) ? before.get(it.rcept_no).first_seen : markNew && !isRange && date === state.today ? t : null));
      if (isRange) $("rangeProgress").textContent = `${list.length.toLocaleString()}건`;
      state.freshIds = new Set(markNew ? added : []);
      state.lastChecked = t;
      state.error = null;
      render();
      showRecon({ dart_count: list.filter(inScope).length, checked_at: t }, added, gone, null, beforeScoped);
    } catch (e) {
      if (token === loadToken) showRecon(null, [], [], e.message);
    } finally {
      if (token === loadToken) busy = false;
      btn.disabled = false;
      btn.classList.remove("busy");
      $("refreshText").textContent = "새로고침";
      updateLive();
    }
  }

  function showRecon(res, added, gone, error, beforeCount) {
    const box = $("recon");
    box.hidden = false;
    if (error) {
      box.className = "recon err";
      $("reconBody").innerHTML = `<b>새로고침 실패</b> · ${esc(error)}`;
      return;
    }
    const shown = scoped().length;
    const match = !added.length && !gone.length;
    box.className = `recon ${match ? "ok" : "diff"}`;
    const names = (ids) => ids.slice(0, 5).map((id) => {
      const it = state.items.get(id);
      return it ? `${esc(it.corp_name)} – ${esc(it.report_nm)}` : esc(id);
    }).join("<br>");
    const lines = [
      `<b>${match ? "✓ 일치" : "차이 있음 → DART 기준으로 맞춤"}</b> · ${res.checked_at} 대사 · ` +
      `DART 원본(코스피·코스닥) <b class="num">${res.dart_count.toLocaleString()}</b>건 / ` +
      `화면 <b class="num">${shown.toLocaleString()}</b>건 (새로고침 전 ${beforeCount.toLocaleString()}건)`,
    ];
    if (added.length) lines.push(`새로 채워진 공시 <b>${added.length}</b>건 (파란색 표시)<br>${names(added)}${added.length > 5 ? "<br>…" : ""}`);
    if (gone.length) {
      lines.push(`DART 목록에 없어 화면에서 뺀 공시 <b>${gone.length}</b>건 (철회 등)<br>` +
        gone.slice(0, 5).map((it) => `${esc(it.corp_name)} – ${esc(it.report_nm)}`).join("<br>"));
    }
    $("reconBody").innerHTML = lines.join("<br>");
  }

  function setTab(tab) {
    state.tab = tab;
    state.limit = PAGE;
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    render();
  }

  async function init() {
    importFromHash();
    bind();
    renderWatch();
    state.today = todayKst();
    $("dateInput").max = toInput(state.today);
    try {
      const st = await fetchJSON("/api/status");
      state.apiKeySet = st.api_key_set;
      buildCatBar(st.categories || []);
    } catch (e) {
      state.error = `서버 연결 실패 · ${e.message}`;
    }
    $("rangeFrom").max = $("rangeTo").max = toInput(state.today);
    const view = loadView();
    if (view.mode === "range" && view.range && !validRange(view.range.from, view.range.to)) await loadRange(view.range.from, view.range.to);
    else await loadDate(state.today);
    fillWatchNames();
    render();
    state.mk.seen = loadSeen();
    state.cal.manual = loadManual();
    // 캘린더를 한 번이라도 연 뒤에는 15분마다 새 공개매수·매수청구 공시를 다시 확인한다
    setInterval(() => { if (state.cal.loadedAt && !state.cal.loading) loadCalendar(false); }, 15 * 60_000);
    loadMarketLists(false); // 공시 목록의 경고·관리 표시와 신규 지정 알림용(5분마다 다시 확인)
    setInterval(() => loadMarketLists(false), MK_REFRESH_MS);
    state.timer = setInterval(poll, REFRESH_MS);
  }

  init();
})();
