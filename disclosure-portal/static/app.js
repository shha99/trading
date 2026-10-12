// 공시 포털 프론트엔드 — 빌드 없이 쓰는 바닐라 JS.
// 관심종목은 localStorage에 저장한다(서버 재시작/재배포와 무관하게 유지).
(() => {
  "use strict";

  const WATCH_KEY = "disclosure-portal.watchlist.v1";
  const REFRESH_MS = 30_000;
  const PAGE = 300;

  const $ = (id) => document.getElementById(id);
  const state = {
    today: null,      // 서버 기준 오늘(KST) YYYYMMDD
    date: null,       // 보고 있는 날짜
    items: new Map(), // rcept_no -> item
    seq: 0,
    boot: null,       // 서버 기동 id — 바뀌면 seq가 다시 시작된 것
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
  async function fetchJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    return body;
  }

  async function loadDate(date) {
    state.date = date;
    state.items = new Map();
    state.seq = 0;
    state.limit = PAGE;
    state.freshIds.clear();
    $("dateInput").value = toInput(date);
    $("nextDay").disabled = date >= state.today;
    $("rows").innerHTML = "";
    showEmpty("불러오는 중…");
    try {
      const data = await fetchJSON(`/api/disclosures?date=${date}`);
      if (state.date !== date) return;
      ingest(data, false);
    } catch (e) {
      if (state.date !== date) return;
      showEmpty(`불러오지 못했습니다: ${e.message}`);
      setLive("err", "조회 실패");
      return;
    }
    render();
  }

  async function refresh() {
    try {
      const st = await fetchJSON("/api/status");
      if (state.today && st.today !== state.today && state.date === state.today) {
        // 자정이 지나면 새 날짜로 넘어간다
        state.today = st.today;
        return loadDate(st.today);
      }
      state.today = st.today;
      if (state.date !== state.today) { applyStatus(st); return; }
      const data = await fetchJSON(`/api/disclosures?after_seq=${state.seq}`);
      if (state.date !== data.date) return;
      if (data.boot !== state.boot) return loadDate(state.date); // 서버 재시작 — 처음부터 다시
      ingest(data, state.seq > 0); // 첫 수집분은 "새 공시"로 치지 않는다
      render();
    } catch (e) {
      setLive("err", `서버 연결 실패 · ${e.message}`);
    }
  }

  function ingest(data, live) {
    const newWatched = [];
    for (const it of data.items) {
      if (state.items.has(it.rcept_no)) continue;
      state.items.set(it.rcept_no, it);
      if (live) {
        state.freshIds.add(it.rcept_no);
        if (watchOf(it)) newWatched.push(it);
      }
    }
    state.seq = Math.max(state.seq, data.seq);
    state.boot = data.boot;
    applyStatus(data.status);
    if (live && state.freshIds.size) {
      document.title = `(${state.freshIds.size}) 공시 포털`;
    }
    if (newWatched.length) {
      const it = newWatched[0];
      toast(`★ ${it.corp_name}: ${it.report_nm}${newWatched.length > 1 ? ` 외 ${newWatched.length - 1}건` : ""}`);
    }
  }

  function applyStatus(st) {
    const banner = $("banner");
    if (!st.api_key_set) {
      banner.hidden = false;
      banner.textContent = "DART_API_KEY가 설정되지 않았습니다. opendart.fss.or.kr에서 인증키를 발급받아 서버 환경변수(Render → Environment)에 넣어주세요.";
    } else if (st.last_error) {
      banner.hidden = false;
      banner.textContent = `DART 조회 오류: ${st.last_error}`;
    } else {
      banner.hidden = true;
    }
    if (state.date !== state.today) {
      setLive("", "지난 날짜 보는 중");
    } else if (st.last_error || !st.api_key_set) {
      setLive("err", "갱신 실패");
    } else if (st.last_polled) {
      setLive("ok", `실시간 · 마지막 확인 ${st.last_polled.slice(11, 19)}`);
    } else {
      setLive("", "첫 수집 중…");
    }
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
    const time = it.first_seen ? it.first_seen.slice(11, 16) : "–";
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
  // 서버가 DART에서 그 날짜 전체를 다시 받아 빠진 공시를 채우고, 화면은 목록을 통째로
  // 다시 받아 "새로고침 전 화면"과 비교한다.
  async function manualRefresh() {
    const btn = $("refreshBtn");
    if (btn.disabled || !state.date) return;
    const date = state.date;
    const before = new Set(state.items.keys());
    btn.disabled = true;
    btn.classList.add("busy");
    $("refreshText").textContent = "대사 중…";
    try {
      const r = await fetch(`/api/refresh?date=${date}`, { method: "POST", cache: "no-store" });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      await loadDate(date);
      if (state.date !== date) return;
      const after = new Set(state.items.keys());
      const added = [...after].filter((id) => !before.has(id));
      const gone = [...before].filter((id) => !after.has(id));
      added.forEach((id) => state.freshIds.add(id));
      render();
      showRecon(res, added, gone, null, before.size);
    } catch (e) {
      showRecon(null, [], [], e.message);
    } finally {
      btn.disabled = false;
      btn.classList.remove("busy");
      $("refreshText").textContent = "새로고침";
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
    const match = res.dart_count === shown && !added.length && !gone.length;
    box.className = `recon ${match ? "ok" : "diff"}`;
    const names = (ids) => ids.slice(0, 5).map((id) => {
      const it = state.items.get(id);
      return it ? `${esc(it.corp_name)} – ${esc(it.report_nm)}` : esc(id);
    }).join("<br>");
    const lines = [
      `<b>${match ? "✓ 일치" : "차이 있음"}</b> · ${res.checked_at.slice(11, 19)} 대사 · ` +
      `DART 원본 <b class="num">${res.dart_count.toLocaleString()}</b>건 / ` +
      `화면 <b class="num">${shown.toLocaleString()}</b>건 (새로고침 전 ${beforeCount.toLocaleString()}건)`,
    ];
    if (added.length) lines.push(`새로 채워진 공시 <b>${added.length}</b>건 (파란색 표시)<br>${names(added)}${added.length > 5 ? "<br>…" : ""}`);
    if (res.not_in_dart && res.not_in_dart.length) lines.push(`DART 목록에서 빠진 공시 ${res.not_in_dart.length}건 (철회·재분류 등 — 화면에는 남겨둠)`);
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
    try {
      const st = await fetchJSON("/api/status");
      state.today = st.today;
      $("dateInput").max = toInput(st.today);
    } catch (e) {
      setLive("err", `서버 연결 실패 · ${e.message}`);
      showEmpty("서버에 연결할 수 없습니다.");
      return;
    }
    await loadDate(state.today);
    fillWatchNames();
    render();
    state.timer = setInterval(refresh, REFRESH_MS);
  }

  init();
})();
