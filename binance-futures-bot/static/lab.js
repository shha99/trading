// 전략 실험실 - 켈트너(검증됨) + 후보 7종 비교
(function () {
  "use strict";

  const SYMBOLS = ["BTCUSDT", "ETHUSDT"];
  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  const state = {
    symbol: "BTCUSDT",
    timeframe: "1h",
    catalog: [],
    stats: null, // /api/lab/stats 응답 전체 ({catalog, stats, _meta})
    selectedKey: null,
  };

  const el = {
    symbolTabs: document.getElementById("symbolTabs"),
    timeframeTabs: document.getElementById("timeframeTabs"),
    labTitle: document.getElementById("labTitle"),
    labSubtitle: document.getElementById("labSubtitle"),
    labGrid: document.getElementById("labGrid"),
    labWarning: document.getElementById("labWarning"),
    labDetail: document.getElementById("labDetail"),
  };

  function fmtPct(v) {
    if (v === undefined || v === null) return "-";
    return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
  }

  function pctClass(v) {
    if (v === undefined || v === null || v === 0) return "flat";
    return v > 0 ? "up" : "down";
  }

  function renderTabs() {
    el.symbolTabs.innerHTML = "";
    SYMBOLS.forEach((sym) => {
      const btn = document.createElement("button");
      btn.textContent = sym.replace("USDT", "");
      if (sym === state.symbol) btn.classList.add("active");
      btn.onclick = () => { state.symbol = sym; renderTabs(); renderAll(); };
      el.symbolTabs.appendChild(btn);
    });
    el.timeframeTabs.innerHTML = "";
    TIMEFRAMES.forEach((tf) => {
      const btn = document.createElement("button");
      btn.textContent = tf;
      if (tf === state.timeframe) btn.classList.add("active");
      btn.onclick = () => { state.timeframe = tf; renderTabs(); renderAll(); };
      el.timeframeTabs.appendChild(btn);
    });
  }

  function statFor(symbol, timeframe, key) {
    const byTf = state.stats && state.stats.stats && state.stats.stats[symbol];
    const byStrategy = byTf && byTf[timeframe];
    return byStrategy ? byStrategy[key] : undefined;
  }

  // ------------------------------------------------------------------
  // 카드 그리드
  // ------------------------------------------------------------------
  function renderGrid() {
    el.labGrid.innerHTML = "";
    state.catalog.forEach((entry, idx) => {
      const s = statFor(state.symbol, state.timeframe, entry.key);
      const card = document.createElement("div");
      card.className = "lab-card" + (entry.key === state.selectedKey ? " selected" : "");
      card.onclick = () => { state.selectedKey = entry.key; renderGrid(); renderDetail(); renderWarning(); };

      const badgeText = !s || s.error ? "데이터 없음" : s.trades === 0 ? "거래 없음" : `${fmtPct(s.avg_pct_per_trade)} / 거래`;
      const badgeClass = !s || s.error || !s.trades ? "flat" : pctClass(s.avg_pct_per_trade);

      card.innerHTML = `
        <div class="lab-card-top">
          <div class="lab-card-index">${idx + 1}</div>
          <div class="lab-card-name">${entry.label}</div>
          <div class="lab-card-badge ${badgeClass}">${badgeText}</div>
        </div>
        <div class="lab-card-category">${entry.category}</div>
        <div class="lab-card-desc">${entry.description}</div>
      `;
      el.labGrid.appendChild(card);
    });
  }

  // ------------------------------------------------------------------
  // 경고 배너: 이 전략이 설계된 시간대와 지금 보는 시간대가 다르면 표시
  // ------------------------------------------------------------------
  function renderWarning() {
    const entry = state.catalog.find((e) => e.key === state.selectedKey);
    if (!entry || entry.designed_timeframe === state.timeframe) {
      el.labWarning.hidden = true;
      return;
    }
    const current = statFor(state.symbol, state.timeframe, entry.key);
    const designed = statFor(state.symbol, entry.designed_timeframe, entry.key);
    if (!current || current.error || !designed || designed.error) {
      el.labWarning.hidden = true;
      return;
    }
    const currentLoss = (current.avg_pct_per_trade ?? 0) < 0;
    el.labWarning.hidden = false;
    el.labWarning.innerHTML =
      `⚠ 이 전략은 <strong>${entry.designed_timeframe}봉</strong> 기준으로 만들어졌습니다. ` +
      `지금 보는 <strong>${state.timeframe}</strong>에서는 거래당 <strong>${fmtPct(current.avg_pct_per_trade)}</strong> (${current.trades}회) · ` +
      `기준인 ${entry.designed_timeframe}봉은 <strong>${fmtPct(designed.avg_pct_per_trade)}</strong> (${designed.trades}회) — ` +
      (currentLoss
        ? "이 시간대에서는 <strong>손실</strong>이므로 참고용으로만 보세요."
        : "이 시간대에서도 플러스이긴 하지만, 검증된 시간대는 따로 있다는 점을 참고하세요.");
  }

  // ------------------------------------------------------------------
  // 상세 패널
  // ------------------------------------------------------------------
  function renderDetail() {
    const entry = state.catalog.find((e) => e.key === state.selectedKey);
    if (!entry) { el.labDetail.innerHTML = ""; return; }
    el.labTitle.textContent = entry.label;
    el.labSubtitle.textContent = entry.description;

    const s = statFor(state.symbol, state.timeframe, entry.key);
    if (!s || s.error) {
      el.labDetail.innerHTML = `<h2>${entry.label}</h2><p class="lab-detail-desc">이 조합은 아직 데이터가 없습니다. build_lab_stats.py를 실행하세요.</p>`;
      return;
    }
    if (!s.trades) {
      el.labDetail.innerHTML = `<h2>${entry.label}</h2><p class="lab-detail-desc">이 심볼·시간대에서는 조건을 만족한 거래가 한 건도 없었습니다.</p>`;
      return;
    }

    const stats = [
      ["거래 횟수", s.trades, ""],
      ["승률", `${Math.round((s.win_rate ?? 0) * 100)}%`, ""],
      ["거래당 평균", fmtPct(s.avg_pct_per_trade), pctClass(s.avg_pct_per_trade)],
      ["누적 수익률", fmtPct(s.total_pct), pctClass(s.total_pct)],
      ["최고", fmtPct(s.best_pct), pctClass(s.best_pct)],
      ["최악", fmtPct(s.worst_pct), pctClass(s.worst_pct)],
    ];
    const statCardsHtml = stats.map(([label, value, cls]) =>
      `<div class="lab-stat-card"><div class="lab-stat-label">${label}</div><div class="lab-stat-value ${cls}">${value}</div></div>`
    ).join("");

    const rows = (s.recent_trades || []).slice().reverse().map((t) => `
      <tr>
        <td>${t.entry_time}</td>
        <td>${t.direction === "LONG" ? "롱" : "숏"}</td>
        <td>${Number(t.entry_price).toLocaleString()}</td>
        <td>${Number(t.exit_price).toLocaleString()}</td>
        <td>${t.exit_reason}</td>
        <td class="${t.pct_return >= 0 ? "up" : "down"}">${fmtPct(t.pct_return)}</td>
      </tr>
    `).join("");

    el.labDetail.innerHTML = `
      <h2>${entry.label} <span style="font-weight:400;color:var(--text-dim);font-size:12px;">(${state.symbol} ${state.timeframe})</span></h2>
      <p class="lab-detail-desc">${entry.description}</p>
      <div class="lab-stat-row">${statCardsHtml}</div>
      <table class="lab-trades-table">
        <tr><th>진입시각</th><th>방향</th><th>진입가</th><th>청산가</th><th>청산사유</th><th>수익률</th></tr>
        ${rows || '<tr><td colspan="6">거래 이력 없음</td></tr>'}
      </table>
    `;
  }

  function renderAll() {
    renderGrid();
    renderDetail();
    renderWarning();
  }

  // ------------------------------------------------------------------
  // 초기화
  // ------------------------------------------------------------------
  async function init() {
    renderTabs();
    const [catalogRes, statsRes] = await Promise.all([
      fetch("/api/lab/strategies"),
      fetch("/api/lab/stats"),
    ]);
    state.catalog = await catalogRes.json();
    state.stats = await statsRes.json();
    state.selectedKey = state.catalog[0] && state.catalog[0].key;
    renderAll();
  }

  init();
})();
