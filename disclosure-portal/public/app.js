// 공시 포털 프론트엔드 — 빌드 없이 쓰는 바닐라 JS.
// 관심종목은 localStorage에 저장한다(재배포와 무관하게 이 브라우저에 유지).
(() => {
  "use strict";

  const WATCH_KEY = "disclosure-portal.watchlist.v1";
  const REFRESH_MS = 30_000;
  const PAGE = 300;

  const $ = (id) => document.getElementById(id);
  const state = {
    today: null,      // 오늘(KST) YYYYMMDD
    date: null,       // 보고 있는 날짜
    items: new Map(), // rcept_no -> item
    seq: 0,
    lastChecked: null, // 마지막으로 DART를 확인한 시각(KST HH:MM:SS)
    error: null,
    apiKeySet: null,
    tab: "all",
    market: "",
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
    state.date = date;
    state.items = new Map();
    state.seq = 0;
    state.limit = PAGE;
    state.freshIds.clear();
    state.error = null;
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

  // 오늘 화면이면 1페이지부터 받아, 이미 아는 공시가 섞인 페이지에서 멈춘다(평소 요청 1번)
  async function poll() {
    const today = todayKst();
    if (today !== state.today) {
      const wasToday = state.date === state.today;
      state.today = today;
      $("dateInput").max = toInput(today);
      if (wasToday) return loadDate(today); // 자정이 지나면 새 날짜로
    }
    if (busy || state.date !== state.today) return;
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
        state.freshIds.add(it.rcept_no);
      }
      state.lastChecked = t;
      state.error = null;
      if (fresh.length) {
        document.title = `(${state.freshIds.size}) 공시 포털`;
        const watched = fresh.filter((it) => watchOf(it));
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
      banner.textContent = `DART 조회 오류: ${state.error}`;
    } else {
      banner.hidden = true;
    }
    if (state.date !== state.today) setLive("", "지난 날짜 보는 중");
    else if (state.error) setLive("err", "갱신 실패 · 30초 뒤 다시 시도");
    else if (state.lastChecked) setLive("ok", `실시간 · 30초마다 확인 · 마지막 ${state.lastChecked}`);
    else setLive("", "불러오는 중…");
  }

  function setLive(cls, text) {
    $("live").className = `live ${cls}`;
    $("liveText").textContent = text;
  }

  // ---------- 필터/렌더 ----------
  function filtered() {
    const q = $("search").value.trim().toLowerCase();
    const ex = $("fExchange").checked;
    const imp = $("fImportant").checked;
    const noFix = $("fNoCorrection").checked;
    const sel = state.selected && state.watch.find((w) => watchKey(w) === state.selected);
    const out = [];
    for (const it of state.items.values()) {
      if (state.tab === "watch") {
        const w = watchOf(it);
        if (!w || (sel && w !== sel)) continue;
      }
      if (state.market && it.market !== state.market) continue;
      if (ex && !it.is_exchange) continue;
      if (imp && !it.important) continue;
      if (noFix && it.is_correction) continue;
      if (q && !(it.corp_name.toLowerCase().includes(q) || it.report_nm.toLowerCase().includes(q) || it.stock_code.includes(q))) continue;
      out.push(it);
    }
    return out.sort((a, b) => b.seq - a.seq); // seq = DART 게시 순서(접수번호는 시간순이 아님)
  }

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function rowHTML(it) {
    const watched = !!watchOf(it);
    const cls = [watched ? "watched" : "", state.freshIds.has(it.rcept_no) ? "fresh" : ""].join(" ");
    const time = it.first_seen ? it.first_seen.slice(0, 5) : "–";
    const tags = [
      it.is_exchange ? `<span class="tag krx" title="${esc(it.flags.join(", "))}">KRX</span>` : "",
      ...it.tags.map((t) => `<span class="tag ${it.important ? "imp" : ""}">${esc(t)}</span>`),
      it.is_correction ? `<span class="tag fix">정정</span>` : "",
    ].join("");
    return `<tr class="${cls}" data-id="${it.rcept_no}">
      <td class="time">${time}</td>
      <td class="mkt-cell"><span class="mkt ${esc(it.market)}">${esc(it.market)}</span></td>
      <td><div class="corp">
        <button class="star ${watched ? "on" : ""}" data-star="${it.rcept_no}" title="${watched ? "관심종목 해제" : "관심종목 추가"}">${watched ? "★" : "☆"}</button>
        <span class="nm" title="${esc(it.corp_name)}">${esc(it.corp_name)}</span>
        ${it.stock_code ? `<span class="cd">${esc(it.stock_code)}</span>` : ""}
      </div></td>
      <td class="title"><a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.report_nm)}</a>${tags}</td>
      <td class="flr" title="${esc(it.flr_nm)}">${esc(it.flr_nm)}</td>
    </tr>`;
  }

  function render() {
    const list = filtered();
    $("rows").innerHTML = list.slice(0, state.limit).map(rowHTML).join("");
    $("moreBtn").hidden = list.length <= state.limit;
    if (!list.length) {
      const msg = state.tab === "watch" && !state.watch.length
        ? "관심종목이 없습니다. 목록의 ☆를 눌러 추가하세요."
        : state.items.size ? "조건에 맞는 공시가 없습니다." : "공시가 없습니다.";
      showEmpty(msg);
    } else {
      $("empty").hidden = true;
    }
    let watchedCount = 0;
    for (const it of state.items.values()) if (watchOf(it)) watchedCount++;
    $("allCount").textContent = state.items.size.toLocaleString();
    $("watchFeedCount").textContent = watchedCount.toLocaleString();
    renderWatch();
  }

  function showEmpty(msg) {
    $("empty").hidden = false;
    $("empty").textContent = msg;
  }

  function renderWatch() {
    const counts = new Map();
    for (const it of state.items.values()) {
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
    for (const it of state.items.values()) if (!seen.has(it.corp_name)) seen.set(it.corp_name, it.stock_code);
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
    if (btn.disabled || !state.date) return;
    const date = state.date;
    const token = ++loadToken; // 진행 중인 폴링 결과는 버린다
    const before = new Map(state.items);
    busy = true;
    btn.disabled = true;
    btn.classList.add("busy");
    $("refreshText").textContent = "대사 중…";
    try {
      const list = await fetchAll(date, true);
      if (token !== loadToken) return;
      const t = nowKst();
      const dartIds = new Set(list.map((it) => it.rcept_no));
      const added = list.filter((it) => !before.has(it.rcept_no)).map((it) => it.rcept_no);
      const gone = [...before.values()].filter((it) => !dartIds.has(it.rcept_no));
      const markNew = before.size > 0;
      setAll(list, (it) => (before.has(it.rcept_no) ? before.get(it.rcept_no).first_seen : markNew && date === state.today ? t : null));
      state.freshIds = new Set(markNew ? added : []);
      state.lastChecked = t;
      state.error = null;
      render();
      showRecon({ dart_count: list.length, checked_at: t }, added, gone, null, before.size);
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
    const shown = state.items.size;
    const match = !added.length && !gone.length;
    box.className = `recon ${match ? "ok" : "diff"}`;
    const names = (ids) => ids.slice(0, 5).map((id) => {
      const it = state.items.get(id);
      return it ? `${esc(it.corp_name)} – ${esc(it.report_nm)}` : esc(id);
    }).join("<br>");
    const lines = [
      `<b>${match ? "✓ 일치" : "차이 있음 → DART 기준으로 맞춤"}</b> · ${res.checked_at} 대사 · ` +
      `DART 원본 <b class="num">${res.dart_count.toLocaleString()}</b>건 / ` +
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
    } catch (e) {
      state.error = `서버 연결 실패 · ${e.message}`;
    }
    await loadDate(state.today);
    fillWatchNames();
    render();
    state.timer = setInterval(poll, REFRESH_MS);
  }

  init();
})();
