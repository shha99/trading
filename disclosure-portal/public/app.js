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
      halts: { items: [], detail: {}, loading: false, loadedAt: null, error: null },
    },
    cal: {
      month: null, events: [], quotes: {}, loadedAt: null, loading: false, selected: null, error: null, newIds: new Set(), changes: new Map(),
      manual: [],
    },
    ai: { loading: false, loadedAt: null, error: null, stats: null, ranked: [] },
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
    idx: new Map(),   // 종목코드 → "KS200" | "KQ150"
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
    if (r.status === 401 && body.login) { // 로그인이 풀렸으면 로그인 화면으로
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      throw new Error("로그인이 필요합니다.");
    }
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

  // ---------- KIND 합치기 ----------
  // DART와 KIND 어느 한쪽에만 올라온 공시도 반드시 보여준다. KIND 접수번호는 DART 접수번호와 같아서
  // 같은 공시는 하나로 합쳐지고, KIND에만 있는 공시(거래소가 KIND에만 낸 안내 등)는 KIND 목록의
  // 순서(시각)를 따라 DART 목록 사이에 끼워 넣는다.
  async function fetchKindDay(date, fresh) {
    try {
      const r = await fetchJSON(`/api/kind/day?date=${date}${fresh ? "&fresh=1" : ""}`);
      state.kindStatus = { ok: true, at: nowKst() };
      return r.items || [];
    } catch (e) {
      state.kindStatus = { ok: false, at: nowKst(), error: e.message };
      return null; // KIND가 안 돼도 DART 목록은 보여준다
    }
  }

// 같은 공시인지: KIND와 DART는 같은 공시라도 접수번호가 다를 수 있다.
  //  - 거래소 공시: DART 80/90번대(코스피/코스닥) ↔ KIND 00번대, 뒤 4자리는 같다
  //  - 일반 공시: 번호가 아예 다르기도 하다 → 같은 날·같은 회사·같은 제목이면 같은 공시로 본다
  //    (제목은 [기재정정] 같은 꼬리표·띄어쓰기·"주요사항보고서( )" 껍데기를 떼고, 그래도 다르면 괄호 설명까지 떼고 비교)
  const corpKey = (n) => String(n || "").replace(/\s/g, "");
  const numKey = (r) => (/^\d{8}(?:00|70|80|90)\d{4}$/.test(r) ? `${r.slice(0, 8)}~${r.slice(10)}` : r);
  const titleKey = (t) => {
    const x = String(t || "").replace(/\[[^\]]*\]/g, "").replace(/[\sㆍ·.,]/g, "");
    const m = x.match(/^주요사항보고서\((.*)\)$/);
    return m ? m[1] : x;
  };
  const looseKey = (t) => titleKey(t).replace(/\([^()]*\)/g, "").replace(/[()]/g, "");
  function filingIndex(list, get = (x) => x) {
    const ix = new Map();
    for (const x of list) {
      const f = get(x);
      const c = corpKey(f.corp_name);
      const day = f.rcept_no.slice(0, 8);
      for (const k of [`n|${numKey(f.rcept_no)}|${c}`, `t|${day}|${c}|${titleKey(f.report_nm)}`, `l|${day}|${c}|${looseKey(f.report_nm)}`]) if (!ix.has(k)) ix.set(k, x);
    }
    return ix;
  }
  function findSame(ix, f) {
    const c = corpKey(f.corp_name);
    const day = f.rcept_no.slice(0, 8);
    return ix.get(`n|${numKey(f.rcept_no)}|${c}`) || ix.get(`t|${day}|${c}|${titleKey(f.report_nm)}`) || ix.get(`l|${day}|${c}|${looseKey(f.report_nm)}`) || null;
  }

  function mergeKind(dart, kind) {
    if (!kind || !kind.length) return dart;
    const ix = filingIndex(dart);
    const ids = new Set(dart.map((it) => it.rcept_no));
    // 다음에 나오는(더 이른) "DART에도 있는" 공시 앞에 넣는다
    const before = new Map(); // dart rcept → 그 앞에 넣을 KIND 전용 공시들
    let pending = [];
    for (const k of kind) {
      const d = findSame(ix, k);
      if (d) {
        if (pending.length) { before.set(d.rcept_no, [...(before.get(d.rcept_no) || []), ...pending]); pending = []; }
        if (!d.kind_time) d.kind_time = k.kind_time;
        continue;
      }
      // 번호만 겹치고 다른 공시면 키를 바꿔 둘 다 남긴다
      pending.push(ids.has(k.rcept_no) ? { ...k, rcept_no: `${k.rcept_no}K` } : k);
    }
    const out = [];
    for (const it of dart) {
      if (before.has(it.rcept_no)) out.push(...before.get(it.rcept_no));
      out.push(it);
    }
    return [...out, ...pending];
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
      const [dart, kind] = await Promise.all([fetchAll(date, false), fetchKindDay(date, false)]);
      if (token !== loadToken) return;
      const list = mergeKind(dart, kind);
      // 처음 불러온 것은 감지 시각을 비워둔다(KIND 전용은 KIND 게시 시각)
      setAll(list, (it) => (it.source === "KIND" && it.kind_time ? `${it.kind_time}:00` : null));
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
    const jobs = days.flatMap((d) => ["Y", "K", "KIND"].map((c) => ({ d, c })));
    const got = new Map();
    let next = 0;
    let done = 0;
    let failed = null;
    const run = async () => {
      while (next < jobs.length && !failed) {
        const { d, c } = jobs[next++];
        try {
          if (c === "KIND") { // KIND는 실패해도 DART 목록은 그대로(받은 날짜만 합친다)
            got.set(d + c, await fetchKindDay(d, fresh && d === state.today));
            onProgress(++done, jobs.length);
            continue;
          }
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
    const kindFailed = days.filter((d) => got.get(d + "KIND") === null).length;
    if (kindFailed) state.kindStatus = { ok: false, at: nowKst(), error: `${kindFailed}일 KIND 확인 실패` };
    return days.flatMap((d) => mergeKind(interleave(got.get(d + "Y") || [], got.get(d + "K") || []), got.get(d + "KIND")));
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
      setAll(list, (it) => (it.source === "KIND" && it.kind_time ? `${it.kind_time}:00` : null));
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
  let pollCount = 0;
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
      // KIND도 1분에 한 번 확인 — DART에 없는 공시(거래소가 KIND에만 낸 것)를 바로 채운다
      if (++pollCount % 2 === 1) {
        const kind = await fetchKindDay(state.date, false);
        if (kind) {
          const merged = mergeKind([...state.items.values()].sort((a, b) => b.seq - a.seq), kind);
          for (const it of merged) if (!known.has(it.rcept_no)) { known.add(it.rcept_no); fresh.unshift(it); }
        }
      }
      if (token !== loadToken) return;
      const t = nowKst();
      // KIND로 먼저 보인 공시가 DART에 다른 번호로 올라오면 새 공시로 치지 않고 DART 것으로 바꿔 끼운다
      const kindOnly = [...state.items.values()].filter((it) => it.source === "KIND");
      const kix = filingIndex(kindOnly);
      for (let i = fresh.length - 1; i >= 0; i--) {
        const it = fresh[i];
        const prevKind = it.source !== "KIND" ? findSame(kix, it) : null;
        if (prevKind && state.items.has(prevKind.rcept_no)) {
          state.items.delete(prevKind.rcept_no);
          it.seq = prevKind.seq;
          it.first_seen = prevKind.first_seen;
          it.kind_time = prevKind.kind_time;
          state.items.set(it.rcept_no, it);
          if (state.freshIds.delete(prevKind.rcept_no)) state.freshIds.add(it.rcept_no);
          fresh.splice(i, 1);
          continue;
        }
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
    const k = state.kindStatus;
    const kindText = !k ? "" : k.ok ? ` · KIND 확인 ${k.at.slice(0, 5)}` : ` · KIND 확인 실패(DART만 표시 중)`;
    $("discMeta").textContent = !t ? ""
      : (state.mode === "range" ? `${count.toLocaleString()}건 · ${t} 불러옴`
        : state.date === state.today ? `${count.toLocaleString()}건 · ${t} 업데이트 (DART 30초·KIND 1분마다 확인)`
          : `${count.toLocaleString()}건 · ${t} 불러옴`) + kindText;
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
      it.source === "KIND" ? `<span class="tag kindsrc" title="KIND에서 직접 확인, DART 반영 전">KIND</span>` : "",
    ].join("");
    return `<tr class="${cls}" data-id="${it.rcept_no}">
      <td class="time">${time}</td>
      <td class="mkt-cell"><span class="mkt ${esc(it.market)}">${esc(it.market)}</span></td>
      <td><div class="corp">
        <button class="star ${watched ? "on" : ""}" data-star="${it.rcept_no}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
        <span class="nm" title="${esc(it.corp_name)}">${hl(it.corp_name, st)}</span>
        ${it.stock_code ? `<span class="cd">${hl(it.stock_code, st)}</span>` : ""}${idxBadge(it.stock_code)}${flagBadges(it.stock_code)}
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
    // 접혔을 때 바에 보이는 칩(이름 + 오늘 건수). 누르면 그 종목 공시만 본다
    const chips = state.watch.slice(0, 8).map((w) => {
      const k = watchKey(w);
      const n = counts.get(k) || 0;
      return `<span class="wchip ${state.selected === k ? "sel" : ""}" data-chip="${esc(k)}">${esc(w.name || w.code)}${n ? ` <b>${n}</b>` : ""}</span>`;
    });
    if (state.watch.length > 8) chips.push(`<span class="wchip more">+${state.watch.length - 8}</span>`);
    $("watchChips").innerHTML = state.watch.length ? chips.join("") : '<span class="wchip more">눌러서 종목 추가</span>';

    $("watchList").innerHTML = state.watch.map((w) => {
      const k = watchKey(w);
      const n = counts.get(k) || 0;
      return `<li data-key="${esc(k)}" class="${state.selected === k ? "sel" : ""}">
        <span class="nm">${esc(w.name || w.code)}</span>
        ${w.code && w.name ? `<span class="cd">${esc(w.code)}</span>` : ""}${idxBadge(w.code)}
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
    loadHaltNotices(fresh); // 기다리지 않는다
  }

  // ---------- 최근 7일 거래정지 공시 ----------
  // 현재 정지 목록(네이버)에는 잠깐 걸렸다 풀린 정지가 안 남는다. DART 거래소 시장조치 공시와
  // KIND에만 올라온 정지 공시를 모아 본문의 정지일시·만료/해제일시·사유를 보여준다.
  async function loadHaltNotices(fresh) {
    const hn = state.mk.halts;
    hn.loading = true;
    renderHaltNotices();
    try {
      const days = Array.from({ length: 7 }, (_, i) => shiftDate(state.today, -i));
      const [dart, ...kindDays] = await Promise.all([
        fetchJSON(`/api/market/halt-notices${fresh ? "?fresh=1" : ""}`),
        ...days.map((d) => fetchJSON(`/api/kind/day?date=${d}${fresh && d === state.today ? "&fresh=1" : ""}`).then((r) => r.items || [], () => [])),
      ]);
      const items = [...dart.items];
      const ix = filingIndex(items, (x) => ({ rcept_no: x.rcept_no, corp_name: x.corp_name, report_nm: x.title }));
      const have = new Set(items.map((x) => x.rcept_no));
      for (const k of kindDays.flat()) { // KIND에만 있는 정지 공시(같은 공시는 번호가 달라도 하나로)
        if (have.has(k.rcept_no) || findSame(ix, k) || !/매매거래정지|정지해제/.test(k.report_nm.replace(/\s/g, "")) || !inScope(k)) continue;
        have.add(k.rcept_no);
        items.push({ rcept_no: k.rcept_no, corp_name: k.corp_name, stock_code: k.stock_code, market: k.market, title: k.report_nm, date: k.rcept_no.slice(0, 8), time: k.kind_time, kind_only: true, url: k.url });
      }
      items.sort((a, b) => b.rcept_no.localeCompare(a.rcept_no));
      hn.items = items;
      renderHaltNotices();
      // 본문(정지일시·만료/해제일시) — 종목마다 따로, 오래 캐시된다
      await runPool(items.filter((x) => !x.kind_only && !hn.detail[x.rcept_no]).map((x) => async () => {
        hn.detail[x.rcept_no] = (await fetchJSON(`/api/market/halt-notice?rcept_no=${x.rcept_no}`)).notice;
      }), 6, () => {});
      hn.loadedAt = nowKst();
      hn.error = null;
    } catch (e) {
      hn.error = e.message;
    } finally {
      hn.loading = false;
      renderHaltNotices();
    }
  }

  const fmtDT = (v) => (v ? `${+v.slice(4, 6)}/${+v.slice(6, 8)}${v.length > 8 ? ` ${v.slice(9)}` : ""}` : "");
  function haltStatus(x, d) {
    const now = `${state.today} ${nowKst().slice(0, 5)}`;
    const halted = (state.mk.lists.halt || []).some((it) => it.code === x.stock_code);
    if (!d) return halted ? ["on", "정지 중"] : ["", "확인 중"];
    if (d.kind === "release") return d.release && d.release > now ? ["soon", "해제 예정"] : ["done", "해제"];
    if (d.kind === "both") return ["done", "정지 후 해제"];
    if (halted) return ["on", "정지 중"];
    if (d.start && d.start > now) return ["soon", "정지 예정"];
    return ["done", "해제됨(거래 중)"];
  }

  function renderHaltNotices() {
    const box = $("mkHaltBox");
    const show = ["halt", "all"].includes(state.mk.kind);
    box.hidden = !show;
    if (!show) return;
    const hn = state.mk.halts;
    const q = $("mkSearch").value.trim().toLowerCase();
    const mkt = state.mk.market;
    const rows = hn.items.filter((x) => (!mkt || x.market === mkt) && (!q || `${x.corp_name} ${x.stock_code} ${x.title}`.toLowerCase().includes(q)) &&
      (!$("mkWatchOnly").checked || isWatchedCode(x.stock_code)));
    $("mkHaltRows").innerHTML = rows.map((x) => {
      const d = hn.detail[x.rcept_no];
      const [cls, label] = haltStatus(x, d);
      const period = !d ? (x.kind_only ? `KIND ${x.time || ""}` : "…")
        : d.kind === "release" ? `해제 ${fmtDT(d.release)}`
        : `${fmtDT(d.start) || "?"} ~ ${d.release ? fmtDT(d.release) : d.end ? fmtDT(d.end) : esc(d.end_text || "미정")}`;
      const url = x.url || `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${x.rcept_no}`;
      return `<tr class="${isWatchedCode(x.stock_code) ? "watched" : ""}">
        <td><span class="st ${cls}">${label}</span></td>
        <td><div class="corp"><span class="nm">${esc(x.corp_name)}</span><span class="cd">${esc(x.stock_code)}</span>${idxBadge(x.stock_code)}${x.market ? `<span class="mkt ${esc(x.market)}">${esc(x.market)}</span>` : ""}</div></td>
        <td><a href="${esc(url)}" target="_blank" rel="noopener">${esc(x.title.replace(/\s+/g, " "))}</a>${x.kind_only ? ' <span class="tag kindsrc">KIND</span>' : ""}</td>
        <td>${d && d.reason ? esc(d.reason) : ""}</td>
        <td>${period}</td>
        <td class="date">${fmtMD(x.date)}</td>
      </tr>`;
    }).join("");
    $("mkHaltEmpty").hidden = rows.length > 0;
    $("mkHaltEmpty").textContent = hn.loading ? "불러오는 중…" : hn.error ? `불러오지 못했습니다: ${hn.error}` : "최근 7일 거래정지 공시가 없습니다.";
    $("mkHaltMeta").textContent = hn.loadedAt ? `${rows.length}건 · ${hn.loadedAt} 확인` : hn.loading ? "불러오는 중…" : "";
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
  // 코스피200·코스닥150 구성종목 표시(종목명 오른쪽 작게)
  function idxBadge(code) {
    const k = code && state.idx.get(code);
    return k ? `<span class="idx ${k === "KS200" ? "ks" : "kq"}" title="${k === "KS200" ? "코스피200 구성종목" : "코스닥150 구성종목"}">${k}</span>` : "";
  }
  async function loadIndexMembers() {
    try {
      const r = await fetchJSON("/api/index-members");
      for (const c of r.ks200 || []) state.idx.set(c, "KS200");
      for (const c of r.kq150 || []) state.idx.set(c, "KQ150");
    } catch { return; } // 없어도 나머지는 그대로
    renderWatch();
    if (state.view === "disc") render();
    else if (state.view === "market") renderMarket();
    else if (state.view === "cal") renderCal();
    else if (state.view === "ai") renderAi();
  }

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
        <span class="nm" title="${esc(it.name)}">${hl(it.name, st)}</span><span class="cd">${hl(it.code, st)}</span>${idxBadge(it.code)}
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
    renderHaltNotices();
  }

  // ---------- AI분석: 과거 이벤트 뒤 실제 변동성 통계로 지금 이벤트 종목의 변동성 예상 ----------
  // 과거 몇 년치 통계(모델)는 /ai-model.json(scripts/build-ai-model.mjs로 미리 만든 파일).
  // 서버(/api/ai/*)는 최근 공시와 지금 종목의 측정값만 준다. 확률·순위는 여기서 계산한다.
  const AI_LABEL = {
    cb: "CB(전환사채) 발행", bw: "BW(신주인수권부사채) 발행", eb: "EB(교환사채) 발행", rcps: "RCPS·우선주 유상증자",
    conv_start: "CB·BW·EB 전환(행사)청구 시작", lockup3p: "제3자배정 신주 보호예수 해제(추정)",
    conv_exercise: "전환·행사청구 공시(신주 상장)", refix_adj: "전환·행사가액 조정(리픽싱 등)", cb_resale: "자기 CB·BW 재매각",
    rights: "유상증자 결정", exright_b: "권리락(무상증자)", exright_r: "권리락(유상증자)", bonus: "무상증자 결정", reduce: "감자 결정", merger: "합병·분할·영업양수도 결정(매수청구권)",
    tender: "공개매수", split: "액면분할 결정", consolidate: "액면병합 결정", relist: "액면변경 후 변경상장(거래재개)",
    delist: "정리매매", halt: "거래정지(사유 발생)", resume: "거래재개(정지 해제)", delist_warn: "관리종목·상장폐지 우려 안내",
    lockup1: "상장 1개월 의무보유 해제", lockup3: "상장 3개월 의무보유 해제", lockup6: "상장 6개월 의무보유 해제", lockup12: "상장 1년 의무보유 해제",
  };
  const AI_IPO_BLOCKS = 14; // 신규상장 찾기: 30일 블록 14개 ≈ 14개월(1년 락업까지)
  const AI_MIN_N = 5; // 과거 사례가 이보다 적은 종류는 순위에서 뺀다
  const AI_TOP = 20;
  const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const pct = (x, d = 0) => (x === null || x === undefined || !Number.isFinite(x) ? "–" : `${(x * 100).toFixed(d)}%`);
  const aiBlockOf = (d) => Math.floor(Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8)) / 86400000 / 30);
  const strHash = (s) => { let h = 0; for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h; };

  async function runPool(jobs, n, onDone) {
    const out = new Array(jobs.length);
    let next = 0;
    let done = 0;
    await Promise.all(Array.from({ length: Math.min(n, jobs.length) }, async () => {
      while (next < jobs.length) {
        const i = next++;
        try { out[i] = await jobs[i](); } catch (e) { out[i] = { error: e.message }; }
        onDone(++done, jobs.length);
      }
    }));
    return out;
  }

  async function loadAi(fresh) {
    const ai = state.ai;
    ai.loading = true;
    ai.error = null;
    renderAi();
    const meta = (t) => { $("aiMeta").textContent = t; };
    try {
      // 0) 미리 만든 과거 통계(모델)
      meta("모델 불러오는 중…");
      if (!ai.model) ai.model = await fetchJSON("/ai-model.json").catch(() => { throw new Error("과거 통계(모델) 파일을 준비 중입니다. 잠시 뒤 다시 열어주세요."); });
      // 1) 최근 공시(이벤트는 최근 2블록, 신규상장은 14블록)
      const cur = aiBlockOf(state.today);
      const urls = [];
      for (let k = cur - 1; k <= cur; k++) for (const g of ["b", "i1", "i3"]) urls.push(`/api/ai/history?k=${k}&g=${g}${fresh && k === cur ? "&fresh=1" : ""}`);
      for (let k = cur - AI_IPO_BLOCKS; k <= cur; k++) urls.push(`/api/ai/history?k=${k}&g=c${fresh && k === cur ? "&fresh=1" : ""}`);
      const hist = await runPool(urls.map((u) => () => fetchJSON(u)), 6, (d, t) => meta(`최근 공시 확인 중 ${d}/${t}`));
      const failedBlocks = hist.filter((h) => h.error).length;
      if (failedBlocks === hist.length) throw new Error(hist[0].error);
      // 같은 종목·같은 종류가 20일 안에 또 나오면(블록 경계·정정) 처음 것만
      const all = hist.flatMap((h) => h.events || []).sort((a, b) => a.d.localeCompare(b.d) || a.r.localeCompare(b.r));
      const lastSeen = new Map();
      const events = [];
      for (const e of all) {
        const key = `${e.t}:${e.c}`;
        const p = lastSeen.get(key);
        lastSeen.set(key, e.d);
        if (p && daysDiff(p, e.d) < 20) continue;
        events.push(e);
      }
      const ipo = new Map(hist.flatMap((h) => h.ipo || []).map((x) => [x.c, x]));
      // 최근 메자닌(CB·BW·EB)·유상증자: 발행 규모(발행주식총수 대비 %)와 RCPS·우선주 여부
      // (최근 두 블록 전부 — 모델을 만든 뒤 새로 생긴 오버행도 잡으려고)
      const sized = events.filter((e) => ["rights", "cb", "bw", "eb"].includes(e.t) && e.cc);
      await runPool(sized.map((e) => async () => {
        const k = await fetchJSON(`/api/ai/dilution?t=${e.t}&corp=${e.cc}&r=${e.r}`);
        e.pct = k.pct;
        e.refix = k.refix;
        e.method = k.method;
        e.bgd = k.bgd;
        e.edd = k.edd;
        if (k.rcps) e.t = "rcps";
      }), 6, (d, t) => meta(`발행 규모 확인 중 ${d}/${t}`));
      // 2) 캘린더의 예정 일정(앞으로 30일): 액면변경 변경상장, 증자 권리락(배정기준일 전 거래일)
      if (!state.cal.loadedAt) {
        meta("캘린더 일정 확인 중…");
        if (!state.cal.loading) await loadCalendar(false);
        while (state.cal.loading) await new Promise((r) => setTimeout(r, 300));
      }
      const soon = (d) => d && daysDiff(state.today, d) >= -14 && daysDiff(state.today, d) <= AI_AHEAD;
      const okCode = (e) => /^[0-9A-Z]{6}$/.test(e.code || "");
      const relists = state.cal.events.filter((e) => e.type === "split" && e.sub === "relist" && okCode(e) && soon(e.start))
        .map((e) => ({ t: "relist", code: e.code, name: e.name, market: e.market, date: e.start, rcept: e.rcept_no }));
      const exrights = state.cal.events.filter((e) => e.type === "capital" && e.sub === "record" && okCode(e) && soon(prevWeekday(e.start)))
        .map((e) => ({ t: /무상/.test(e.kind) ? "exright_b" : "exright_r", code: e.code, name: e.name, market: e.market, date: prevWeekday(e.start), rcept: e.rcept_no }));
      // 오버행(잠재 물량): 모델(과거 3년 발행분) + 최근 공시
      ai.over = aiOverhang(ai.model, events);
      const overPlanned = [];
      for (const o of ai.over) for (const it of o.items) {
        if (it.bgd && soon(it.bgd)) overPlanned.push({ t: "conv_start", code: o.c, name: o.n, market: o.m, date: it.bgd, pct: it.pct, src: it.t });
        if (it.rel && soon(it.rel)) overPlanned.push({ t: "lockup3p", code: o.c, name: o.n, market: o.m, date: it.rel, pct: it.pct, src: it.t });
      }
      const planned = [...relists, ...exrights, ...overPlanned];
      // 3) 종목별 주가 측정값 — 종목코드 해시로 묶어 요청(묶음이 매일 거의 같아 서버 캐시가 잘 맞는다)
      const want = new Map();
      const add = (code, tok) => { if (!want.has(code)) want.set(code, new Set()); want.get(code).add(tok); };
      for (const e of events) if (daysDiff(e.d, state.today) <= 14) add(e.c, e.d);
      for (const c of ipo.keys()) add(c, "L");
      for (const e of planned) add(e.code, e.date <= state.today ? e.date : state.today);
      for (const o of ai.over.slice(0, 40)) add(o.c, state.today);
      const buckets = new Map();
      for (const [code, toks] of want) {
        const b = strHash(code) % 64;
        if (!buckets.has(b)) buckets.set(b, []);
        buckets.get(b).push(...[...toks].map((t) => `${code}:${t}`));
      }
      const qs = [];
      for (const list of buckets.values()) {
        let part = [];
        let codes = new Set();
        for (const tok of list.sort()) {
          const c = tok.slice(0, 6);
          if (!codes.has(c) && codes.size >= 35) { qs.push(part); part = []; codes = new Set(); }
          codes.add(c);
          part.push(tok);
        }
        if (part.length) qs.push(part);
      }
      const res = await runPool(qs.map((q) => () => fetchJSON(`/api/ai/metrics?q=${q.join(",")}`)), 6, (d, t) => meta(`주가 측정 중 ${d}/${t}`));
      const stocks = Object.assign({}, ...res.map((r) => r.stocks || {}));
      Object.assign(ai, aiCompute(ai.model, events, ipo, stocks, planned));
      ai.recent = events.filter((e) => daysDiff(e.d, state.today) <= 60);
      ai.partial = failedBlocks + res.filter((r) => r.error).length;
      ai.loadedAt = nowKst();
    } catch (e) {
      ai.error = e.message;
    } finally {
      ai.loading = false;
      renderAi();
    }
  }

  function aiCompute(model, events, ipo, stocks, planned) {
    const today = state.today;
    const floatOf = (code) => (model.floats && model.floats[code] ? model.floats[code][0] : null);
    const big = model.big;
    const stats = {};
    for (const [t, st] of Object.entries(model.types)) stats[t] = { ...st, t };
    // 지금 이벤트가 걸린 종목: 최근 14일 안에 나온 이벤트(10거래일 진행 중) + 앞으로 2주 안 락업 해제·변경상장
    const cands = [];
    const addCand = (t, code, name, market, date, rcept, evDate = date, extra = {}) => {
      const typeSt = stats[t];
      if (!typeSt || typeSt.n < AI_MIN_N || !typeSt.zq) return;
      // 구간 통계가 충분하면 그걸 쓴다: 발행 규모(희석률) → 유통주식 비율 → 종류 전체 순
      const fl = floatOf(code);
      const b = typeSt.buckets && extra.pct !== null && extra.pct !== undefined ? typeSt.buckets[aiBucketOf(extra.pct)] : null;
      const fb = typeSt.fbuckets && fl !== null ? typeSt.fbuckets[aiFloatBucketOf(fl)] : null;
      const st = b && b.zq ? { ...typeSt, ...b, bucket: b.label } : fb && fb.zq ? { ...typeSt, ...fb, bucket: fb.label } : typeSt;
      const ev = t.startsWith("lockup") ? (stocks[code]?.lockups || []).find((l) => l.d === date) : stocks[code]?.ev?.[evDate];
      const own = ev?.pre || stocks[code]?.now_base; // 이벤트 직전 평소 변동성(없으면 최근 60거래일)
      const sigma = own || st.medBase;
      const scale = sigma * Math.sqrt(10);
      cands.push({
        t, code, name, market, date, rcept, sigma, own: Boolean(own), sofar: ev?.sofar || null, ...extra, st, fl,
        prob: st.zq.filter((z) => z * scale >= big).length / st.zq.length, // 과거 사례 분포(백분위)에서 ±20% 넘는 비율
        exp: st.zq[50] * scale,
      });
    };
    for (const e of events) {
      const ago = daysDiff(e.d, today);
      if (ago >= 0 && ago <= 14) addCand(e.t, e.c, e.n, e.m, e.d, e.r, e.d, { pct: e.pct ?? null, refix: e.refix, method: e.method });
    }
    for (const [code, st] of Object.entries(stocks)) {
      for (const l of st.lockups || []) {
        const dd = daysDiff(today, l.d);
        if (dd >= -14 && dd <= AI_AHEAD) addCand(`lockup${l.months}`, code, ipo.get(code)?.n || code, ipo.get(code)?.m || "", l.d, "");
      }
    }
    for (const e of planned) addCand(e.t, e.code, e.name, e.market, e.date, e.rcept, e.date <= today ? e.date : today, { pct: e.pct ?? null });
    // 진행 중(이벤트일이 오늘이거나 지남) / 예정(앞으로) 따로. 한 종목에 이벤트가 여럿이면 확률이 가장 높은 것으로.
    const rank = (list) => {
      const byCode = new Map();
      for (const c of list) {
        const p = byCode.get(c.code);
        if (!p) { byCode.set(c.code, { ...c, others: [] }); continue; }
        if (c.prob > p.prob || (c.prob === p.prob && c.exp > p.exp)) byCode.set(c.code, { ...c, others: [p, ...p.others].map(({ others, ...x }) => x) });
        else if (!(p.t === c.t && p.date === c.date)) p.others.push(c);
      }
      return [...byCode.values()].sort((a, b) => b.prob - a.prob || b.exp - a.exp);
    };
    for (const o of state.ai.over || []) o.sigma = stocks[o.c]?.now_base ?? null;
    return { stats, now: rank(cands.filter((c) => c.date <= today)), next: rank(cands.filter((c) => c.date > today)) };
  }

  const AI_BUCKETS = [[0, 5], [5, 10], [10, 20], [20, Infinity]]; // src/ai.js DILUTION_BUCKETS와 같게
  const AI_FLOAT_BUCKETS = [[0, 0.2], [0.2, 0.35], [0.35, 0.5], [0.5, 1.01]]; // src/ai.js FLOAT_BUCKETS와 같게
  const aiFloatBucketOf = (f) => AI_FLOAT_BUCKETS.findIndex(([lo, hi]) => f >= lo && f < hi);
  const aiBucketOf = (pct) => AI_BUCKETS.findIndex(([lo, hi]) => pct >= lo && pct < hi);

  const AI_AHEAD = 30; // 예정 목록: 앞으로 30일
  function prevWeekday(d) { // 배정기준일 전 거래일 ≈ 권리락일(공휴일은 무시)
    if (!d) return null;
    let x = shiftDate(d, -1);
    while ([0, 6].includes(new Date(Date.UTC(+x.slice(0, 4), +x.slice(4, 6) - 1, +x.slice(6, 8))).getUTCDay())) x = shiftDate(x, -1);
    return x;
  }

  function aiWhen(d) {
    const dd = daysDiff(state.today, d);
    return dd > 0 ? `D-${dd}` : dd === 0 ? "오늘" : `${-dd}일 전`;
  }

  function renderAi() {
    const ai = state.ai;
    $("aiRefresh").disabled = ai.loading;
    if (!ai.loading) $("aiMeta").textContent = ai.error ? "" : ai.loadedAt
      ? `${ai.loadedAt} 기준${ai.partial ? ` · 일부 ${ai.partial}건 실패` : ""}` : "";
    const err = $("aiError");
    err.hidden = !ai.error;
    if (ai.error) err.textContent = `불러오지 못했습니다: ${ai.error}`;
    if (!ai.stats) { $("aiNowRows").innerHTML = ""; $("aiNextRows").innerHTML = ""; $("aiStats").innerHTML = ""; return; }
    const fill = (rows, body, empty, msg) => {
      $(body).innerHTML = rows.slice(0, AI_TOP).map(aiRowHTML).join("");
      $(empty).hidden = rows.length > 0;
      $(empty).textContent = msg;
    };
    fill(ai.now, "aiNowRows", "aiNowEmpty", "최근 14일 안에 시작된 이벤트 종목이 없습니다.");
    fill(ai.next, "aiNextRows", "aiNextEmpty", `앞으로 ${AI_AHEAD}일 안에 예정된 이벤트 종목이 없습니다.`);
    aiRenderOverhang(ai);
    aiRenderLowFloat(ai);
    $("aiNowCount").textContent = ai.now.length ? `${ai.now.length}종목 중` : "";
    $("aiNextCount").textContent = ai.next.length ? `${ai.next.length}종목 중` : "";
    aiRenderStats(ai);
  }

  // 오버행: 전환기간이 남은 CB·BW·EB와 보호예수가 풀리는(풀린) 제3자배정 신주를 발행주식 대비 %로 합친다.
  // 비율은 각 발행 당시 발행주식총수 기준이고, 이미 전환된 물량은 빼지 못한다(DART에 구조화된 잔액 자료가 없음).
  function aiOverhang(model, events) {
    const today = state.today;
    const by = new Map();
    const get = (c, n, m) => { if (!by.has(c)) by.set(c, { c, n, m, total: 0, items: [], sig: {} }); return by.get(c); };
    for (const o of model.overhang || []) {
      if (!["코스피", "코스닥"].includes(o.m)) continue;
      const x = get(o.c, o.n, o.m);
      for (const it of o.items) if (!(it.edd && it.edd < today)) x.items.push(it);
      Object.assign(x.sig, o.sig || {});
    }
    const addMonths = (d, n) => { const x = new Date(Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1 + n, +d.slice(6, 8))); return ymdUTC(x); };
    for (const e of events) {
      if (["conv_exercise", "refix_adj", "cb_resale"].includes(e.t)) {
        const x = by.get(e.c);
        if (x && (!x.sig[e.t] || x.sig[e.t] < e.d)) x.sig[e.t] = e.d;
        continue;
      }
      if (e.pct === null || e.pct === undefined || !["코스피", "코스닥"].includes(e.m)) continue;
      let it = null;
      if (["cb", "bw", "eb"].includes(e.t) && !(e.edd && e.edd < today)) it = { t: e.t, d: e.d, pct: e.pct, bgd: e.bgd, edd: e.edd };
      if (["rights", "rcps"].includes(e.t) && /제3자/.test(e.method || "")) it = { t: e.t, d: e.d, pct: e.pct, rel: addMonths(e.d, 13), method: e.method };
      if (!it) continue;
      const x = get(e.c, e.n, e.m);
      if (!x.items.some((y) => y.t === it.t && y.d === it.d)) x.items.push(it);
    }
    for (const x of by.values()) {
      x.total = x.items.reduce((a, it) => a + it.pct, 0);
      const f = model.floats && model.floats[x.c] ? model.floats[x.c][0] : null;
      x.float = f;
      x.totalFloat = f > 0.01 ? x.total / f : null; // 유통주식 대비(대주주 물량은 안 판다고 보면 실제 부담)
    }
    // 유통주식 대비로 줄 세운다(유통 비율을 모르면 발행주식 대비)
    const key = (x) => x.totalFloat ?? x.total;
    return [...by.values()].filter((x) => x.items.length).sort((a, b) => key(b) - key(a));
  }

  // 저유통 종목: 유통주식 비율이 낮은 순. 최근 60일·앞으로 30일 이벤트와 오버행을 같이 보여준다.
  function aiRenderLowFloat(ai) {
    const fl = ai.model?.floats || {};
    const evBy = new Map();
    for (const c of [...ai.now, ...ai.next]) { if (!evBy.has(c.code)) evBy.set(c.code, []); evBy.get(c.code).push(c, ...(c.others || [])); }
    for (const e of ai.recent || []) { if (!evBy.has(e.c)) evBy.set(e.c, []); evBy.get(e.c).push({ t: e.t, date: e.d }); }
    const overBy = new Map((ai.over || []).map((o) => [o.c, o]));
    const list = Object.entries(fl).filter(([, [f, , m]]) => f >= 0.01 && ["코스피", "코스닥"].includes(m)).sort((a, b) => a[1][0] - b[1][0]);
    const ymd2 = (d) => `${d.slice(2, 4)}.${d.slice(4, 6)}.${d.slice(6, 8)}`;
    $("aiLowRows").innerHTML = list.slice(0, AI_TOP).map(([code, [f, name, m]], i) => {
      const watched = isWatchedCode(code);
      const evs = [...new Map((evBy.get(code) || []).map((e) => [`${e.t}:${e.date}`, e])).values()].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 4);
      const o = overBy.get(code);
      return `<tr class="${watched ? "watched" : ""}">
        <td class="num ai-rank">${i + 1}</td>
        <td><div class="corp">
          <button class="star ${watched ? "on" : ""}" data-aistar="${esc(code)}" data-name="${esc(name)}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
          <span class="nm" title="${esc(name)}">${esc(name)}</span><span class="cd">${esc(code)}</span>${idxBadge(code)}<span class="mkt ${esc(m)}">${esc(m)}</span>
        </div></td>
        <td class="num"><b class="ai-hot">${(f * 100).toFixed(1)}%</b></td>
        <td class="ai-parts">${evs.length ? evs.map((e) => `${esc(AI_LABEL[e.t] || e.t)} <span>${ymd2(e.date)} · ${aiWhen(e.date)}</span>`).join("<br>") : '<span>최근 60일·앞으로 30일 이벤트 없음</span>'}</td>
        <td class="num">${o ? `${(o.totalFloat ?? o.total).toFixed(1)}%` : "–"}</td>
      </tr>`;
    }).join("");
    const fs = ai.model?.floatStats || [];
    $("aiLowNote").innerHTML = fs.length ? `과거 5년 이벤트를 유통주식 비율별로 보면 — ${fs.map((b) => `유통 ${b.label}: 평소 하루 변동성 ${pct(b.medBase, 1)}, 이벤트 뒤 ±20% ${pct(b.big)}(평소 ${pct(b.ctrlBig)}) [${b.n.toLocaleString()}건]`).join(" · ")}` : "";
  }

  function aiRenderOverhang(ai) {
    const T = { cb: "CB", bw: "BW", eb: "EB", rights: "제3자배정 증자", rcps: "RCPS" };
    const ym = (d) => (d ? `${d.slice(2, 4)}.${d.slice(4, 6)}` : "?");
    const ymd2 = (d) => `${d.slice(2, 4)}.${d.slice(4, 6)}.${d.slice(6, 8)}`;
    const rows = (ai.over || []).slice(0, AI_TOP).map((o, i) => {
      const watched = isWatchedCode(o.c);
      const parts = o.items.sort((a, b) => b.pct - a.pct).map((it) => it.rel
        ? `${T[it.t]} ${it.pct.toFixed(1)}% <span>(보호예수 해제 ${ym(it.rel)} 무렵)</span>`
        : `${T[it.t]} ${it.pct.toFixed(1)}% <span>(${it.bgd && it.bgd > state.today ? `전환 시작 ${ym(it.bgd)}` : "전환 가능"}${it.edd ? ` ~${ym(it.edd)}` : ""})</span>`).join("<br>");
      const next = o.items.flatMap((it) => [it.bgd, it.rel]).filter((d) => d && d > state.today).sort()[0];
      const sig = [["conv_exercise", "전환청구"], ["refix_adj", "가액조정"], ["cb_resale", "자기CB매도"]].filter(([k]) => o.sig[k]).map(([k, l]) => `${l} ${ymd2(o.sig[k])}`).join(" · ");
      return `<tr class="${watched ? "watched" : ""}">
        <td class="num ai-rank">${i + 1}</td>
        <td><div class="corp">
          <button class="star ${watched ? "on" : ""}" data-aistar="${esc(o.c)}" data-name="${esc(o.n)}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
          <span class="nm" title="${esc(o.n)}">${esc(o.n)}</span><span class="cd">${esc(o.c)}</span>${idxBadge(o.c)}<span class="mkt ${esc(o.m)}">${esc(o.m)}</span>
        </div></td>
        <td class="num"><b class="${(o.totalFloat ?? o.total) >= 30 ? "ai-hot" : ""}">${o.totalFloat !== null ? `${o.totalFloat.toFixed(1)}%` : "–"}</b></td>
        <td class="num">${o.total.toFixed(1)}%</td>
        <td class="num">${o.float !== null ? `${Math.round(o.float * 100)}%` : "–"}</td>
        <td class="ai-parts">${parts}</td>
        <td>${next ? `${ymd2(next)} · ${aiWhen(next)}` : "–"}</td>
        <td class="ai-sig">${sig || "–"}</td>
        <td class="num">${o.sigma ? pct(o.sigma, 1) : "–"}</td>
      </tr>`;
    });
    $("aiOverRows").innerHTML = rows.join("");
    $("aiOverEmpty").hidden = rows.length > 0;
    $("aiOverEmpty").textContent = "전환기간이 남은 메자닌·보호예수 해제 예정 신주가 있는 종목이 없습니다.";
    $("aiOverCount").textContent = ai.over && ai.over.length ? `${ai.over.length}종목 중` : "";
    // 근거: 전환청구 시작·보호예수 해제·전환청구 공시 뒤 과거 결과
    const ev = ["conv_start", "lockup3p", "conv_exercise", "refix_adj", "cb_resale"].filter((t) => ai.stats[t]).map((t) => {
      const st = ai.stats[t];
      return `${esc(AI_LABEL[t])} ${st.n}건: ±20% ${pct(st.big)}(평소 ${pct(st.ctrlBig)}), 10일 뒤 하락 ${pct(1 - st.up)}`;
    });
    $("aiOverNote").innerHTML = ev.length ? `과거 5년 결과 — ${ev.join(" · ")}` : "";
  }

  function aiRowHTML(r, i) {
      const st = r.st;
      const watched = isWatchedCode(r.code);
      const ex = st.examples.map((x) => `${x.n} ${fmtMD(x.d)} 최대 ${pct(x.move)} / 10일 뒤 ${x.ret > 0 ? "+" : ""}${pct(x.ret)}`).join("\n");
      const size = r.pct !== null && r.pct !== undefined
        ? `<div class="ai-size ${r.pct >= 10 ? "big" : ""}">발행주식 대비 <b>${r.pct.toFixed(1)}%</b>${r.refix ? " · 리픽싱" : ""}${r.method ? ` · ${esc(r.method)}` : ""}${st.bucket ? ` <span>(${esc(st.bucket)} 구간 통계 · 10일 뒤 하락 ${pct(1 - st.up)})</span>` : ""}</div>`
        : st.bucket ? `<div class="ai-size"><span>${esc(st.bucket)} 구간 통계 적용</span></div>` : "";
      const sofar = r.sofar ? `<div class="ai-others">진행 ${r.sofar.days}거래일째 · 지금까지 최대 ±${pct(r.sofar.move)}</div>` : "";
      const others = r.others.length ? `<div class="ai-others">같이 걸린 이벤트: ${r.others.map((o) => `${esc(AI_LABEL[o.t])}(${pct(o.prob)})`).join(", ")}</div>` : "";
      const link = r.rcept ? `<a href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${r.rcept}" target="_blank" rel="noopener">공시</a>` : "";
      return `<tr class="${watched ? "watched" : ""}">
        <td class="num ai-rank">${i + 1}</td>
        <td><div class="corp">
          <button class="star ${watched ? "on" : ""}" data-aistar="${esc(r.code)}" data-name="${esc(r.name)}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
          <span class="nm" title="${esc(r.name)}">${esc(r.name)}</span><span class="cd">${esc(r.code)}</span>${idxBadge(r.code)}
          ${r.market ? `<span class="mkt ${esc(r.market)}">${esc(r.market)}</span>` : ""}
          ${r.fl !== null && r.fl !== undefined ? `<span class="ai-fl ${r.fl < 0.2 ? "low" : ""}" title="유통주식 비율(최대주주·특수관계인·자사주 제외)">유통 ${Math.round(r.fl * 100)}%</span>` : ""}
        </div></td>
        <td><span class="ai-type t-${r.t.replace(/\d+$/, "")}">${esc(AI_LABEL[r.t])}</span> <span class="ai-when">${fmtMD(r.date)} · ${aiWhen(r.date)}</span> ${link}${size}${sofar}${others}</td>
        <td class="num"><div class="ai-bar"><span style="width:${Math.round(r.prob * 100)}%"></span></div><b>${pct(r.prob)}</b></td>
        <td class="num">±${pct(r.exp)}</td>
        <td class="num" title="${r.own ? "이벤트 직전 60거래일 하루 변동성(표준편차)" : "주가 이력이 짧아 같은 종류 사례의 중앙값을 썼습니다"}">${pct(r.sigma, 1)}${r.own ? "" : "*"}</td>
        <td class="num">${st.lift ? `${st.lift.toFixed(1)}배` : "–"}</td>
        <td class="num">${pct(st.up)}</td>
        <td class="num" title="${esc(`최근 사례\n${ex}`)}">${st.n}건</td>
      </tr>`;
  }

  function aiRenderStats(ai) {
    // 발행 규모별 표
    $("aiSize").innerHTML = ["cb", "bw", "eb", "rcps", "rights"].filter((t) => ai.stats[t]?.buckets).flatMap((t) =>
      ai.stats[t].buckets.map((b, i) => `<tr class="${b.zq ? "" : "past"} ${i === 0 ? "grp" : ""}">
        <td>${i === 0 ? esc(AI_LABEL[t]) : ""}</td>
        <td>${esc(b.label)}</td>
        <td class="num">${b.n}</td>
        <td class="num">${b.zq ? `<b>${pct(b.big)}</b>` : "–"}</td>
        <td class="num">${b.zq ? pct(b.ctrlBig) : "–"}</td>
        <td class="num">${b.zq ? pct(1 - b.up) : "–"}</td>
        <td class="num">${b.zq ? `${b.medRet > 0 ? "+" : ""}${pct(b.medRet, 1)}` : "–"}</td>
      </tr>`)).join("");
    // 근거 표
    $("aiStats").innerHTML = Object.keys(AI_LABEL).filter((t) => ai.stats[t]).map((t) => {
      const st = ai.stats[t];
      return `<tr class="${st.n < AI_MIN_N ? "past" : ""}">
        <td>${esc(AI_LABEL[t])}${st.n < AI_MIN_N ? " <small>(사례 부족 — 순위 제외)</small>" : ""}</td>
        <td class="num">${st.n}</td>
        <td class="num"><b>${pct(st.big)}</b></td>
        <td class="num">${st.ctrlBig === null ? "–" : pct(st.ctrlBig)}</td>
        <td class="num">±${pct(st.medMove, 1)}</td>
        <td class="num">${st.lift ? `${st.lift.toFixed(2)}배` : "–"}</td>
        <td class="num">${pct(st.up)}</td>
      </tr>`;
    }).join("");
  }

  function setView(view) {
    state.view = view;
    for (const b of document.querySelectorAll("#viewNav button")) b.classList.toggle("on", b.dataset.view === view);
    const disc = view === "disc";
    $("discView").hidden = !disc;
    $("marketView").hidden = view !== "market";
    $("calView").hidden = view !== "cal";
    $("aiView").hidden = view !== "ai";
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
      } else if (view === "ai") {
        renderAi();
        if (!state.ai.loadedAt && !state.ai.loading) loadAi(false);
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
      // 순서 주의: 후보(합병 등·공개매수), 증자, 거래소 공시, KIND — 아래 구조분해와 같은 순서
      const fetched = await Promise.all([
        fetchJSON(`/api/events/candidates?x=1${q}`),
        // 증자: 최근 3개월·3~6개월 전을 나눠 받아 합친다(결정이 오래돼도 남은 일정·뒤에 나온 정정 서류를 놓치지 않게)
        Promise.all([0, 1].map((w) => fetchJSON(`/api/events/capital-candidates?w=${w}${q}`).catch(() => ({ items: [], docs: {} }))))
          .then(([a, b]) => mergeCapitalCandidates(a, b)), // 증자는 실패해도 나머지는 보여준다
        fetchJSON(`/api/events/exchange-candidates?x=1${q}`).catch(() => ({ items: [] })),
        fresh ? fetchJSON(`/api/kind/day?date=${state.today}&fresh=1`).catch((e) => ({ error: e.message })) : Promise.resolve(null),
      ]);
      const [c, cap, exch, kindToday] = fetched;
      // 최근 7일 KIND 공시(거래소 안내 포함)에서 증자 일정 관련 후속 공시를 찾는다 — 결정 뒤 추가·정정된 일정을 놓치지 않게
      const kindWeek = (await Promise.all(Array.from({ length: 7 }, (_, i) => shiftDate(state.today, -i)).map((d) =>
        fetchJSON(`/api/kind/day?date=${d}${fresh && d === state.today ? "&fresh=1" : ""}`).then((r) => r.items || [], () => [])))).flat();
      const noticesBy = new Map(); // 종목코드 → 관련 공시(최신순)
      for (const it of kindWeek) {
        const title = it.report_nm.replace(/\s/g, "");
        if (!it.stock_code || !CAP_NOTICE.test(title) || /철회/.test(title)) continue;
        if (!noticesBy.has(it.stock_code)) noticesBy.set(it.stock_code, []);
        noticesBy.get(it.stock_code).push({ rcept_no: it.rcept_no, title: it.report_nm, date: it.rcept_no.slice(0, 8), time: it.kind_time, name: it.corp_name, market: it.market, url: it.url });
      }
      for (const [code, list] of noticesBy) noticesBy.set(code, [...new Map(list.map((x) => [x.rcept_no, x])).values()].sort((a, b) => b.rcept_no.localeCompare(a.rcept_no)));
      // 액면변경(분할·병합): 거래소 공시가 많아 30일씩 세 번 나눠 받고, 회사별 최신 결정만 남긴다
      const splitLists = await Promise.all([0, 1, 2].map((w) => fetchJSON(`/api/events/split-candidates?w=${w}${q}`).catch(() => ({ items: [] }))));
      const splits = new Map();
      for (const list of [...splitLists].reverse()) for (const x of list.items) splits.set(x.corp_code, { ...(splits.get(x.corp_code) || {}), ...x, first_rcept_no: (splits.get(x.corp_code) || x).first_rcept_no });
      // 거래소 공시로 먼저 뜬 결정: 같은 회사·같은 종류의 DART 주요사항보고서가 이미 있으면 그쪽을 쓴다
      const haveDeal = new Set(c.deals.map((d) => `${d.corp_code}:${d.kind}`));
      const deals = [...c.deals, ...exch.items.filter((x) => !haveDeal.has(`${x.corp_code}:${x.kind}`))];
      const jobs = [
        // 액면변경: 신주 효력발생일(기준일), 매매거래정지 기간, 신주권 상장예정일
        ...[...splits.values()].filter((x) => !x.withdrawn).map((x) => async () => {
          const got = await fetchWithPrev(`/api/events/split?rcept_no=${x.rcept_no}${q}`, x.rcept_no, x.prev_rcept_no);
          if (!got) return [{ type: "split", sub: "pending", id: `split:${x.corp_code}:wait`, kind: `${x.kind}(원문 대기)`, name: x.corp_name, code: x.stock_code, market: x.market, rcept_no: x.rcept_no, announced: x.first_rcept_no.slice(0, 8), start: null, end: null, waiting: true, split_kind: x.kind }];
          const e = got.event;
          const par = e.par_before && e.par_after ? `액면 ${e.par_before.toLocaleString()}→${e.par_after.toLocaleString()}원` : "";
          const base = { type: "split", name: x.corp_name, code: x.stock_code, market: x.market, rcept_no: x.rcept_no, announced: x.first_rcept_no.slice(0, 8), par, gm_date: e.gm_date, split_kind: x.kind };
          const out = [];
          if (e.effective) out.push({ ...base, id: `split:${x.corp_code}:eff`, sub: "effective", kind: `효력발생일·${x.kind}`, start: e.effective, end: e.effective });
          if (e.halt_start) out.push({ ...base, id: `split:${x.corp_code}:halt`, sub: "halt", kind: `거래정지·${x.kind}`, start: e.halt_start, end: e.halt_end || e.halt_start });
          if (e.listing) out.push({ ...base, id: `split:${x.corp_code}:list`, sub: "relist", kind: `변경상장·${x.kind}`, start: e.listing, end: e.listing });
          return out;
        }),
        // 증자: 신주인수권증서 상장기간, 신주 상장예정일 → 일정 두 개로 나눈다
        ...cap.items.map((x) => async () => {
          // 결정 뒤 나온 공모 서류(docs)와 최근 거래소 후속 공시(n)가 바뀌면 캐시를 건너뛰고 다시 읽는다
          const notices = noticesBy.get(x.stock_code) || [];
          const docs = x.docs && x.docs.length ? `&docs=${x.docs.join(",")}` : "";
          const n = notices.length ? `&n=${notices[0].rcept_no}` : "";
          const r = await fetchWithPrev(`/api/events/capital?rcept_no=${x.rcept_no}&corp_code=${x.corp_code}${docs}${n}${q}`, x.rcept_no, null);
          if (!r) return [{ type: "capital", sub: "pending", id: `cap:${x.corp_code}:${x.kind}:wait`, kind: `${x.kind}(원문 대기)`, name: x.corp_name, code: x.stock_code, market: x.market, rcept_no: x.rcept_no, announced: x.first_rcept_no.slice(0, 8), start: null, end: null, waiting: true, notices }];
          const e = r.event;
          const base = { type: "capital", name: x.corp_name, code: x.stock_code, market: x.market, rcept_no: x.rcept_no, announced: x.first_rcept_no.slice(0, 8), record_date: e.record_date, notices, from_docs: e.from || null };
          const out = [];
          if (e.cert_start) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:cert`, sub: "cert", kind: `증서상장·${x.kind}`, start: e.cert_start, end: e.cert_end || e.cert_start });
          if (e.listing) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:list`, sub: "listing", kind: `신주상장·${x.kind}`, start: e.listing, end: e.listing });
          if (e.record_date) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:rec`, sub: "record", kind: `${x.kind === "감자" ? "감자기준일" : "배정기준일"}·${x.kind}`, start: e.record_date, end: e.record_date });
          // 감자: 매매거래정지 예정기간(구주권 제출·병합 처리)
          if (e.halt_start) out.push({ ...base, id: `cap:${x.corp_code}:${x.kind}:halt`, sub: "halt", kind: `거래정지·${x.kind}`, start: e.halt_start, end: e.halt_end || e.halt_start, gm_date: e.gm_date });
          return out;
        }),
        ...deals.map((d) => async () => {
          const r = await fetchWithPrev(`/api/events/deal?api=${d.api}&corp_code=${d.corp_code}&rcept_no=${d.rcept_no}${q}`, d.rcept_no, d.prev_rcept_no);
          if (!r) return { type: "buyback", kind: d.kind, id: `deal:${d.corp_code}:${d.api === "doc" ? d.kind : d.api}`, name: d.corp_name, code: d.stock_code, market: d.market, rcept_no: d.rcept_no, announced: (d.first_rcept_no || d.rcept_no).slice(0, 8), start: null, end: null, waiting: true, from_exchange: d.source === "거래소" };
          const e = r.event;
          if (!e || e.none) return null;
          return { ...e, kind: d.api === "doc" ? d.kind : e.kind, from_exchange: d.source === "거래소", id: `deal:${d.corp_code}:${d.api === "doc" ? d.kind : d.api}`, name: d.corp_name, code: d.stock_code, market: d.market, withdrawn: Boolean(d.withdrawn_rcept_no), announced: (d.first_rcept_no || d.rcept_no).slice(0, 8) };
        }),
        ...c.tenders.map((t) => async () => {
          const r = await fetchWithPrev(`/api/events/tender?rcept_no=${t.rcept_no}${q}`, t.rcept_no, t.first_rcept_no !== t.rcept_no ? t.first_rcept_no : null);
          if (!r) return { type: "tender", id: `tender:${t.first_rcept_no}`, announced: t.first_rcept_no.slice(0, 8), kind: "공개매수", name: t.corp_name, code: t.stock_code, market: t.market, offeror: t.offeror, done: t.done, rcept_no: t.rcept_no, start: null, end: null, waiting: true };
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
      // 새로고침 때 KIND 오늘 공시에서 DART에 아직 안 들어온 공개매수·매수청구·무상증자 결정을 "미정"으로 먼저 표시
      cal.kindNote = null;
      if (kindToday) {
        if (kindToday.error) cal.kindNote = `KIND 확인 실패`;
        else {
          const known = new Set([...c.deals, ...exch.items, ...cap.items, ...c.tenders].flatMap((x) => [x.rcept_no, x.first_rcept_no]).filter(Boolean));
          const have = new Set(out.map((e) => `${e.name}|${e.type}`));
          const pending = [];
          for (const it of kindToday.items) {
            if (!inScope(it) || known.has(it.rcept_no)) continue;
            const title = it.report_nm.replace(/\s/g, "");
            if (/자회사|종속회사|철회/.test(title)) continue;
            const type = /공개매수신고서/.test(title) ? "tender"
              : /(회사합병|회사분할합병|주식교환|영업양수|영업양도|회사분할|중요한자산양수도)[ㆍ·이전]*결정/.test(title) ? "buyback"
              : /(무상증자|유무상증자)결정/.test(title) ? "capital" : null;
            if (!type || have.has(`${it.corp_name}|${type}`)) continue;
            have.add(`${it.corp_name}|${type}`);
            pending.push({
              type, id: `kind:${it.rcept_no}`, kind: `${it.report_nm} (KIND)`, name: it.corp_name, code: it.stock_code, market: it.market,
              start: null, end: null, rcept_no: it.rcept_no, from_kind: true, announced: it.rcept_no.slice(0, 8), sub: type === "capital" ? "pending" : undefined,
            });
          }
          out.push(...pending);
          cal.kindNote = `KIND 확인 ${nowKst().slice(0, 5)}${pending.length ? ` · DART 반영 전 ${pending.length}건` : ""}`;
        }
      }
      // 증자 관련 거래소 후속 공시가 있는데 캘린더에 그 회사 증자 일정이 없으면 "확인 필요"로 띄운다(빠뜨리지 않게)
      const capCodes = new Set(out.filter((e) => e.type === "capital").map((e) => e.code));
      for (const [code, list] of noticesBy) {
        if (capCodes.has(code)) continue;
        const nt = list.find((x) => CAP_SCHEDULE_NOTICE.test(x.title.replace(/\s/g, ""))); // 결정 공시는 DART 쪽에서 잡는다
        if (!nt) continue;
        out.push({
          type: "capital", sub: "pending", id: `kindcap:${code}`, kind: `${nt.title} (KIND)`, name: nt.name, code, market: nt.market,
          start: null, end: null, rcept_no: nt.rcept_no, from_kind: true, announced: nt.date, notices: list,
        });
      }
      // 지난번에 본 일정과 비교해 바뀐 일정 표시(날짜·가격)
      const snap = loadCalSnap();
      const changed = [];
      cal.changes = new Map();
      for (const e of out) {
        const now = calSig(e);
        const was = snap[e.id];
        if (was !== undefined && was !== now) { cal.changes.set(e.id, was); changed.push(e); }
        snap[e.id] = now;
      }
      saveCalSnap(snap);
      if (changed.length) toast(`📅 일정 변경 ${changed.length}건: ${changed.slice(0, 3).map((e) => `${e.name}(${e.kind})`).join(", ")}`);
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

  // 공시 본문이 아직 DART에 없을 때(거래소 공시 직후 등): 전 보고서로 다시 시도하고, 그것도 없으면 null
  const docMissing = (e) => /파일이 존재하지|원문을 받지 못/.test(String(e && e.message));
  async function fetchWithPrev(url, rcept, prev) {
    try { return await fetchJSON(url); } catch (e) {
      if (!docMissing(e)) throw e;
      if (prev) { try { return { ...(await fetchJSON(url.replace(rcept, prev))), from_prev: true }; } catch (e2) { if (!docMissing(e2)) throw e2; } }
      return null;
    }
  }
  const WAIT_NOTE = "원문 대기 — DART 본문이 아직 없어 다음 새로고침 때 다시 읽음";

  // 두 기간 후보 합치기: 같은 회사·종류면 최근 기간의 최신 보고서 + 옛 기간의 처음 공시, 공모 서류는 두 기간 모두
  function mergeCapitalCandidates(recent, older) {
    const by = new Map();
    for (const x of older.items || []) by.set(`${x.corp_code}:${x.kind}`, x);
    for (const x of recent.items || []) {
      const k = `${x.corp_code}:${x.kind}`;
      const o = by.get(k);
      by.set(k, o ? { ...x, first_rcept_no: o.first_rcept_no } : x);
    }
    const docs = {};
    for (const src of [older.docs || {}, recent.docs || {}]) for (const [c, list] of Object.entries(src)) docs[c] = [...(docs[c] || []), ...list];
    const items = [...by.values()];
    for (const x of items) { // src/events.js attachCapitalDocs와 같은 규칙
      const d = [...new Set(docs[x.corp_code] || [])].filter((r) => r >= x.first_rcept_no.slice(0, 8)).sort().reverse().slice(0, 3);
      if (d.length) x.docs = d;
    }
    return { items };
  }

  // 증자 일정과 관련된 거래소 후속 공시 제목(신주인수권증서 상장·폐지, 신주 추가상장, 발행가액 확정, 권리락 등)
  const CAP_NOTICE = /신주인수권증서|추가상장\(?(?:유상|무상|유무상)|유상증자|무상증자|권리락|신주발행가액|발행가액(?:확정|결정)|감자|자본감소/;
  const CAP_SCHEDULE_NOTICE = /신주인수권증서|추가상장|권리락|발행가액|감자|자본감소/;
  const CAL_SNAP_KEY = "disclosure-portal.calendar-snap.v1";
  const calSig = (e) => [e.start || "", e.end || "", e.price || ""].join("|");
  function loadCalSnap() {
    try { return JSON.parse(localStorage.getItem(CAL_SNAP_KEY) || "{}") || {}; } catch { return {}; }
  }
  function saveCalSnap(snap) {
    try { localStorage.setItem(CAL_SNAP_KEY, JSON.stringify(snap)); } catch { /* 저장 불가 */ }
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
      (e.type === "tender" ? tender : e.type === "capital" ? $("calCapital").checked : e.type === "split" ? $("calSplit").checked : buyback) &&
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
        if (e.sub === "listing" || e.sub === "record" || e.sub === "effective" || e.sub === "relist") { // 하루짜리 일정
          const label = { listing: "◆ {n} 신주상장", record: "● {n} 배정기준일", effective: "● {n} 액면변경 기준일", relist: "◆ {n} 변경상장" }[e.sub].replace("{n}", esc(e.name));
          const solid = e.sub === "listing" || e.sub === "relist";
          if (e.start === d) chips.push(`<span class="ev ${e.type} ${solid ? "end" : ""} ${w}" title="${esc(e.name)} ${esc(e.kind)}">${label}</span>`);
          continue;
        }
        const tag = e.sub === "cert" ? " 증서" : e.sub === "halt" ? " 거래정지" : "";
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

  function calChangeBadge(e) {
    const was = state.cal.changes && state.cal.changes.get(e.id);
    if (was === undefined) return "";
    const [s0, e0, p0] = was.split("|");
    const f = (d) => (d ? fmtMD(d) : "미정");
    const before = `${f(s0)}${e0 && e0 !== s0 ? `~${f(e0)}` : ""}${p0 ? ` · ${Number(p0).toLocaleString()}원` : ""}`;
    return `<span class="badge chg" title="이전: ${esc(before)}">변경</span>`;
  }

  function calRowHTML(e, pinned = false) {
    const st = status(e);
    const watched = isWatchedCode(e.code);
    const q = e.code ? state.cal.quotes[e.code] : null;
    const cur = q && q.price;
    const gap = e.price && cur ? ((e.price - cur) / cur) * 100 : null;
    const gapCls = gap === null ? "" : gap > 0 ? "up" : gap < 0 ? "down" : "";
    const period = e.sub === "listing" || e.sub === "relist" ? `${fmtMD(e.start)} 상장` : e.sub === "record" || e.sub === "effective" ? `${fmtMD(e.start)} 기준일` : e.start || e.end ? `${e.start ? fmtMD(e.start) : "?"} ~ ${e.end ? fmtMD(e.end) : "?"}${e.estimated ? ' <span class="est">(추정)</span>' : ""}` : "미정";
    const notes = [
      e.type === "tender" && e.offeror ? `공개매수자 ${esc(e.offeror)}` : "",
      e.partner ? `상대 ${esc(e.partner)}` : "",
      e.gm_date ? `주총 ${fmtMD(e.gm_date)}` : "",
      e.done ? "결과보고서 제출" : "",
      e.record_date && e.sub !== "record" ? `배정기준일 ${fmtMD(e.record_date)}` : "",
      e.sub === "record" ? "이날 주주명부 기준으로 신주 배정 — 권리락은 보통 하루 전 영업일" : "",
      e.sub === "cert" ? "신주인수권증서 상장(매매)기간" : "",
      e.par || "",
      e.sub === "effective" ? "신주 효력발생일 — 이날 기준으로 주식 수가 바뀜" : "",
      e.sub === "halt" ? "매매거래정지 기간" : "",
      e.withdrawn ? "결정 뒤 철회 공시 — 원문 확인" : "",
      e.from_kind ? "KIND 공시 — DART 반영 전이라 일정 확인 중" :
      e.from_exchange ? "거래소 공시 기준(DART 주요사항보고서 나오면 대체)" : e.source === "본문" ? "기간은 보고서 본문 기준" : "",
      e.waiting ? WAIT_NOTE : "",
      e.from_docs ? "일정은 정정·투자설명서 기준" : "",
      e.notices && e.notices.length ? `거래소 후속: ${e.notices.slice(0, 2).map((n) => `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}(${fmtMD(n.date)})</a>`).join(", ")}` : "",
      e.memo ? esc(e.memo) : "",
      e.manual ? "직접 추가" : "",
    ].filter(Boolean).join(" · ");
    const kindLabel = e.type === "tender" ? "공개매수" : e.type === "capital" || e.type === "split" ? e.kind : `매수청구·${e.kind || ""}`.replace(/·$/, "");
    return `<tr class="${watched ? "watched" : ""} ${st.key === "done" && !pinned ? "past" : ""} ${pinned ? "pinned" : ""}">
      <td><span class="st ${st.key}">${st.label}</span>${pinned && e.announced ? `<div class="ann">공시 ${fmtMD(e.announced)}</div>` : ""}</td>
      <td class="kind-cell"><span class="kind ev ${e.type}">${esc(kindLabel)}</span></td>
      <td><div class="corp">
        ${e.code ? `<button class="star ${watched ? "on" : ""}" data-calstar="${esc(e.code)}" data-name="${esc(e.name)}">${watched ? "★" : "☆"}</button>` : ""}
        <span class="nm" title="${esc(e.name)}">${esc(e.name)}</span>${e.code ? `<span class="cd">${esc(e.code)}</span>` : ""}${idxBadge(e.code)}${state.cal.newIds.has(e.id) ? '<span class="badge new">NEW</span>' : ""}${calChangeBadge(e)}${flagBadges(e.code)}
      </div></td>
      <td class="period">${period}</td>
      <td class="num">${e.price ? e.price.toLocaleString() : "–"}</td>
      <td class="num">${cur ? cur.toLocaleString() : "–"}</td>
      <td class="num ${gapCls}">${gap === null ? "–" : `${gap > 0 ? "+" : ""}${gap.toFixed(2)}%`}</td>
      <td class="note">${notes}</td>
      <td>${e.manual
        ? `<button class="linkbtn del" data-caldel="${esc(e.id)}">삭제</button>`
        : `<a class="linkbtn" href="${e.from_kind ? "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=" : "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="}${esc(e.rcept_no)}" target="_blank" rel="noopener">원문</a>`}</td>
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
        ? `공개매수 ${allEvents().filter((e) => e.type === "tender").length} · 매수청구 ${allEvents().filter((e) => e.type === "buyback").length} · 증자 ${new Set(allEvents().filter((e) => e.type === "capital").map((e) => e.name)).size} · 액면변경 ${new Set(allEvents().filter((e) => e.type === "split").map((e) => e.name)).size}건 · ${cal.loadedAt} 업데이트${cal.kindNote ? ` · ${cal.kindNote}` : ""}${cal.failed ? ` · ${cal.failed}건 실패` : ""}`
        : "";
    }
  }

  function bindCalendar() {
    $("calPrev").addEventListener("click", () => { state.cal.month = shiftMonth(state.cal.month, -1); renderCal(); });
    $("calNext").addEventListener("click", () => { state.cal.month = shiftMonth(state.cal.month, 1); renderCal(); });
    $("calThisMonth").addEventListener("click", () => { state.cal.month = state.today.slice(0, 6); renderCal(); });
    for (const id of CAL_PREF_IDS) {
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
  const CAL_PREF_IDS = ["calTender", "calBuyback", "calCapital", "calSplit", "calHidePast", "calWatchOnly"];
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

    $("watchToggle").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-chip]");
      if (chip) { // 칩을 누르면 펼치지 않고 그 종목만 본다
        state.selected = state.selected === chip.dataset.chip ? null : chip.dataset.chip;
        setTab(state.selected ? "watch" : state.tab);
        return;
      }
      setWatchOpen(!$("watchPanel").classList.contains("open"));
      if ($("watchPanel").classList.contains("open")) $("addInput").focus();
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
    $("aiRefresh").addEventListener("click", () => loadAi(true));
    $("aiView").addEventListener("click", (e) => {
      const star = e.target.closest("[data-aistar]");
      if (!star) return;
      const code = star.dataset.aistar;
      const w = state.watch.find((x) => x.code === code);
      if (w) removeWatch(watchKey(w));
      else addWatch({ code, name: star.dataset.name });
      renderAi();
      renderWatch();
    });
    initWatchOpen();
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
      // 오늘이 포함되면 KIND 오늘 공시도 같이 받아 DART에 아직 안 올라온 공시를 채운다(접수번호가 같다)
      // 기간 조회는 날마다 KIND를 이미 합쳐서 받는다. 하루 화면은 그날 KIND를 캐시 없이 받아 합친다.
      const [merged, kindItems] = await Promise.all([
        isRange
          ? fetchRange(range.from, range.to, true, (d, t) => { $("refreshText").textContent = `대사 중 ${Math.round((d / t) * 100)}%`; })
          : fetchAll(date, true),
        isRange ? Promise.resolve(undefined) : fetchKindDay(date, true),
      ]);
      if (token !== loadToken) return;
      const t = nowKst();
      const list = isRange ? merged : mergeKind(merged, kindItems);
      const dart = list.filter((it) => it.source !== "KIND");
      const kindOnly = list.filter((it) => it.source === "KIND");
      const kind = isRange ? { items: kindOnly, range: true } : kindItems === null ? { error: state.kindStatus?.error || "실패" } : { items: kindItems };
      const dartIds = new Set(list.map((it) => it.rcept_no));
      const added = list.filter((it) => inScope(it) && !before.has(it.rcept_no)).map((it) => it.rcept_no);
      const gone = [...before.values()].filter((it) => inScope(it) && !dartIds.has(it.rcept_no));
      const beforeScoped = [...before.values()].filter(inScope).length;
      const markNew = before.size > 0;
      setAll(list, (it) => (before.has(it.rcept_no) ? before.get(it.rcept_no).first_seen : it.kind_time ? `${it.kind_time}:00` : markNew && !isRange && date === state.today ? t : null));
      if (isRange) $("rangeProgress").textContent = `${list.length.toLocaleString()}건`;
      state.freshIds = new Set(markNew ? added : []);
      state.lastChecked = t;
      state.error = null;
      render();
      const kindNote = kind.error ? `실패 (${kind.error})`
        : kind.range ? `KIND에만 있는 공시 ${kindOnly.filter(inScope).length}건 포함`
        : `${kind.items.filter(inScope).length.toLocaleString()}건 · DART에 없는 공시 ${kindOnly.filter(inScope).length}건${kindOnly.filter(inScope).length ? " 추가" : ""}`;
      showRecon({ dart_count: dart.filter(inScope).length, checked_at: t, kind: kindNote }, added, gone, null, beforeScoped);
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
      `DART 원본(코스피·코스닥) <b class="num">${res.dart_count.toLocaleString()}</b>건` +
      ` (그중 거래소·KIND 공시 ${scoped().filter((it) => it.is_exchange).length.toLocaleString()}건)` +
      `${res.kind ? ` · KIND 직접 확인(코스피·코스닥) ${res.kind}` : ""} / ` +
      `화면 <b class="num">${shown.toLocaleString()}</b>건 (새로고침 전 ${beforeCount.toLocaleString()}건)`,
    ];
    if (added.length) lines.push(`새로 채워진 공시 <b>${added.length}</b>건 (파란색 표시)<br>${names(added)}${added.length > 5 ? "<br>…" : ""}`);
    if (gone.length) {
      lines.push(`DART 목록에 없어 화면에서 뺀 공시 <b>${gone.length}</b>건 (철회 등)<br>` +
        gone.slice(0, 5).map((it) => `${esc(it.corp_name)} – ${esc(it.report_nm)}`).join("<br>"));
    }
    $("reconBody").innerHTML = lines.join("<br>");
  }

  // 관심종목 패널 펼침/접힘 — 브라우저에 기억(기본은 접힘)
  const WATCH_OPEN_KEY = "disclosure-portal.watch-open.v1";
  function setWatchOpen(open) {
    $("watchPanel").classList.toggle("open", open);
    $("watchToggle").setAttribute("aria-expanded", String(open));
    document.querySelector(".layout").classList.toggle("watch-collapsed", !open);
    try { localStorage.setItem(WATCH_OPEN_KEY, open ? "1" : "0"); } catch { /* 무시 */ }
  }
  function initWatchOpen() {
    let open = false;
    try { open = localStorage.getItem(WATCH_OPEN_KEY) === "1"; } catch { /* 무시 */ }
    setWatchOpen(open);
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
      if (st.user) { $("userName").textContent = st.user; $("userBox").hidden = false; }
      buildCatBar(st.categories || []);
      loadIndexMembers(); // 기다리지 않는다 — 오면 다시 그린다
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
