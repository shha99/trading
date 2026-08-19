/* ui.js — DOM 렌더링 (analysis.js 결과를 화면에 그린다) */
(function () {
  "use strict";
  const App = window.__macroApp;
  const A = window.Analysis;
  const state = App.state;

  const SVGNS = "http://www.w3.org/2000/svg";
  const fmtPct = (v, d) => (v == null || Number.isNaN(v) ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d == null ? 1 : d) + "%");
  const fmtNum = (v, d) => (v == null || Number.isNaN(v) ? "—" : v.toFixed(d == null ? 2 : d));
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function hexToRgba(hex, alpha) {
    const h = hex.replace("#", "");
    const r = parseInt(h.substring(0, 2), 16);
    const g = parseInt(h.substring(2, 4), 16);
    const b = parseInt(h.substring(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // =======================================================================
  // 셸(고정 레이아웃) 렌더
  // =======================================================================
  function renderShell() {
    const root = document.getElementById("app-root");
    root.innerHTML = `
      <div class="layout">
        <aside class="sidebar">
          ${sidebarPoolPanel()}
          ${sidebarVarsPanel()}
          ${sidebarConditionsPanel()}
          ${sidebarRunPanel()}
        </aside>
        <main class="main-stage">
          <section class="panel" id="chart-panel">
            <div class="panel__header">
              <div>
                <div class="panel__title">Forward Path — D+0 ~ D+N</div>
                <div class="panel__subtitle" id="chart-subtitle">오늘 종가 = 100 기준 정규화</div>
              </div>
              <div class="mode-toggle" id="mode-toggle"></div>
            </div>
            <div class="panel__body">
              <div class="legend-row" id="legend-row"></div>
              <div class="chart-wrap" id="chart-wrap"></div>
              <div class="stat-strip" id="stat-strip"></div>
              <p class="summary-text" id="summary-text"></p>
            </div>
          </section>

          <section class="panel" id="detail-panel">
            <div class="panel__header">
              <div class="panel__title" id="detail-title">Analog 표본</div>
              <div class="crisis-compare" id="crisis-compare"></div>
            </div>
            <div class="panel__body" id="detail-body"></div>
          </section>
        </main>
      </div>
    `;
    bindSidebarEvents();
    renderModeToggle();
  }

  function sidebarPoolPanel() {
    return `
      <div class="panel ctrl-panel">
        <div class="panel__header"><div class="panel__title">표본 구간</div></div>
        <div class="panel__body">
          <label class="radio-row">
            <input type="radio" name="pool" value="surprise" ${state.poolMode === "surprise" ? "checked" : ""} />
            <span><strong>1998~ 서프라이즈 모드</strong><br /><span class="hint">발표치 − 직전 12개월 평균(프록시). 신뢰도 높음, 기본값</span></span>
          </label>
          <label class="radio-row">
            <input type="radio" name="pool" value="level" ${state.poolMode === "level" ? "checked" : ""} />
            <span><strong>1990~ 레벨 모드</strong><br /><span class="hint">변수의 절대 수준값 사용. 참고용, 두 모드 표본은 섞이지 않음</span></span>
          </label>
        </div>
      </div>`;
  }

  function sidebarVarsPanel() {
    const rows = App.VARIABLES.map((v) => {
      const checked = state.selectedVars.has(v.key);
      const transform = state.varTransform[v.key];
      const val = state.scenarioValues[v.key];
      return `
        <div class="var-row ${checked ? "var-row--on" : ""}" data-var="${v.key}">
          <label class="var-row__head">
            <input type="checkbox" data-role="var-check" data-var="${v.key}" ${checked ? "checked" : ""} />
            <span>${v.label}</span>
          </label>
          ${
            checked
              ? `
          <div class="var-row__body">
            <div class="segmented" data-role="var-transform" data-var="${v.key}">
              <button type="button" class="${transform === "level" ? "is-active" : ""}" data-val="level">레벨</button>
              <button type="button" class="${transform === "delta" ? "is-active" : ""}" data-val="delta">모멘텀(전기차)</button>
            </div>
            <label class="scenario-input">
              시나리오 값
              <input type="number" step="0.01" data-role="scenario-val" data-var="${v.key}" value="${val}" />
              <span class="unit">${v.unit}</span>
            </label>
          </div>`
              : ""
          }
        </div>`;
    }).join("");
    const warn =
      state.selectedVars.size >= 3
        ? `<div class="warn-box">⚠ 변수 ${state.selectedVars.size}개 동시 선택 — 표본이 급격히 줄어들 수 있습니다 (아래 표본 수 확인).</div>`
        : "";
    return `
      <div class="panel ctrl-panel">
        <div class="panel__header"><div class="panel__title">거시변수 선택 &amp; 시나리오</div></div>
        <div class="panel__body">
          ${warn}
          ${rows}
        </div>
      </div>`;
  }

  function sidebarConditionsPanel() {
    const cur = App.REGIME_LABEL[App.DS().regime[App.DS().regime.length - 1]];
    return `
      <div class="panel ctrl-panel">
        <div class="panel__header"><div class="panel__title">레짐 &amp; 조건</div></div>
        <div class="panel__body">
          <label class="toggle-row">
            <input type="checkbox" data-role="regime-filter" ${state.regimeFilterOn ? "checked" : ""} />
            <span>같은 레짐에서만 매칭</span>
          </label>
          <label class="select-row">
            가정 통화정책 국면 <span class="hint">(자동분류 현재: ${cur})</span>
            <select data-role="target-regime">
              ${App.REGIME_ORDER.map((r) => `<option value="${r}" ${state.targetRegime === r ? "selected" : ""}>${App.REGIME_LABEL[r]}</option>`).join("")}
            </select>
          </label>
          <label class="toggle-row">
            <input type="checkbox" data-role="exclude-crisis" ${state.excludeCrisis ? "checked" : ""} />
            <span>위기구간 제외 (1987/2008/2020/2022 예시)</span>
          </label>
          <label class="select-row">
            Forward window
            <select data-role="forward-window">
              ${[1, 5, 10, 20].map((n) => `<option value="${n}" ${state.forwardWindow === n ? "selected" : ""}>D+${n}</option>`).join("")}
            </select>
          </label>
          <label class="slider-row">
            Analog N (이웃 수) <span class="num" data-role="analogN-val">${state.analogN}</span>
            <input type="range" min="10" max="80" step="1" value="${state.analogN}" data-role="analogN" />
          </label>
          <label class="slider-row">
            극값 페널티 가중치 <span class="num" data-role="penalty-val">${state.extremePenalty.toFixed(2)}</span>
            <input type="range" min="0.1" max="1" step="0.05" value="${state.extremePenalty}" data-role="penalty" />
          </label>
        </div>
      </div>`;
  }

  function sidebarRunPanel() {
    return `
      <div class="panel ctrl-panel run-panel">
        <div class="panel__body">
          <button class="run-btn" id="run-btn">분석 실행</button>
          <div class="status-line" id="status-line">대기 중</div>
        </div>
      </div>`;
  }

  function renderModeToggle() {
    const el = document.getElementById("mode-toggle");
    el.innerHTML = `
      <div class="segmented segmented--mode">
        <button type="button" data-mode="analog" class="${state.mode === "analog" ? "is-active" : ""}">Analog</button>
        <button type="button" data-mode="regression" class="${state.mode === "regression" ? "is-active" : ""}">회귀</button>
      </div>`;
    el.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.mode = btn.dataset.mode;
        document.getElementById("meta-mode").textContent = "MODE: " + (state.mode === "analog" ? "ANALOG" : "REGRESSION");
        renderModeToggle();
        if (window.__lastResult) renderResults(window.__lastResult);
      });
    });
  }

  function markStale() {
    const btn = document.getElementById("run-btn");
    const status = document.getElementById("status-line");
    if (btn) btn.classList.add("run-btn--stale");
    if (status) status.textContent = "설정이 변경되었습니다 — 분석 실행을 눌러 갱신하세요";
  }

  function bindSidebarEvents() {
    const root = document.getElementById("app-root");
    root.addEventListener("change", (e) => {
      const t = e.target;
      if (t.name === "pool") {
        state.poolMode = t.value;
        for (const v of App.VARIABLES) state.scenarioValues[v.key] = App.lastFeatureValue(v.key);
        renderShell();
        markStale();
      } else if (t.dataset.role === "var-check") {
        const k = t.dataset.var;
        if (t.checked) state.selectedVars.add(k);
        else state.selectedVars.delete(k);
        state.scenarioValues[k] = App.lastFeatureValue(k);
        renderShell();
        markStale();
      } else if (t.dataset.role === "scenario-val") {
        state.scenarioValues[t.dataset.var] = parseFloat(t.value);
        markStale();
      } else if (t.dataset.role === "regime-filter") {
        state.regimeFilterOn = t.checked;
        markStale();
      } else if (t.dataset.role === "exclude-crisis") {
        state.excludeCrisis = t.checked;
        markStale();
      } else if (t.dataset.role === "target-regime") {
        state.targetRegime = t.value;
        markStale();
      } else if (t.dataset.role === "forward-window") {
        state.forwardWindow = parseInt(t.value, 10);
        markStale();
      }
    });
    root.addEventListener("input", (e) => {
      const t = e.target;
      if (t.dataset.role === "analogN") {
        state.analogN = parseInt(t.value, 10);
        document.querySelector('[data-role="analogN-val"]').textContent = state.analogN;
        markStale();
      } else if (t.dataset.role === "penalty") {
        state.extremePenalty = parseFloat(t.value);
        document.querySelector('[data-role="penalty-val"]').textContent = state.extremePenalty.toFixed(2);
        markStale();
      }
    });
    root.addEventListener("click", (e) => {
      const seg = e.target.closest('[data-role="var-transform"] button');
      if (seg) {
        const k = seg.parentElement.dataset.var;
        state.varTransform[k] = seg.dataset.val;
        renderShell();
        markStale();
        return;
      }
      if (e.target.id === "run-btn") {
        App.runAnalysis();
      }
    });
  }

  // =======================================================================
  // 결과 렌더
  // =======================================================================
  function renderResults(result) {
    window.__lastResult = result;
    renderLegend();
    renderChart(result);
    renderStatStrip(result);
    renderSummaryText(result);
    renderDetailPanel(result);
    setStatusFromResult(result);
  }

  function setStatusFromResult(result) {
    document.getElementById("status-line").textContent = `표본 ${result.poolSize.toLocaleString()}개 중 이웃 ${result.knn.neighbors.length}개 매칭 · 완료`;
    const btn = document.getElementById("run-btn");
    if (btn) btn.classList.remove("run-btn--stale");
  }

  function renderLegend() {
    const el = document.getElementById("legend-row");
    el.innerHTML = App.INDEX_META.map(
      (im) => `
      <button type="button" class="legend-item ${state.highlightIndex && state.highlightIndex !== im.key ? "legend-item--dim" : ""}" data-idx="${im.key}">
        <span class="legend-swatch" style="background:var(${im.color})"></span>${im.label}
      </button>`
    ).join("");
    el.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.highlightIndex = state.highlightIndex === btn.dataset.idx ? null : btn.dataset.idx;
        renderResults(window.__lastResult);
      });
    });
  }

  function renderStatStrip(result) {
    const el = document.getElementById("stat-strip");
    const regimeTxt = state.regimeFilterOn ? `${App.REGIME_LABEL[state.targetRegime]} 국면만` : "전체 국면";
    let extra = "";
    if (state.mode === "regression") {
      const rep = result.regressionByIndex.SPX.perHorizon[state.forwardWindow - 1];
      const oos = result.oosByIndex.SPX;
      extra = `
        <div class="stat-cell"><span class="stat-cell__label">R² (D+${state.forwardWindow}, SPX)</span><span class="stat-cell__val num">${rep ? fmtNum(rep.r2, 3) : "—"}</span></div>
        <div class="stat-cell"><span class="stat-cell__label">OOS R² (SPX)</span><span class="stat-cell__val num">${oos && !oos.insufficientData ? fmtNum(oos.oosR2, 3) : "표본부족"}</span></div>
        <div class="stat-cell"><span class="stat-cell__label">OOS 적중률</span><span class="stat-cell__val num">${oos && !oos.insufficientData && oos.oosHitRate != null ? fmtPct(oos.oosHitRate, 0) : "—"}</span></div>
      `;
    }
    el.innerHTML = `
      <div class="stat-cell"><span class="stat-cell__label">Analog 표본(N)</span><span class="stat-cell__val num">${result.knn.neighbors.length}</span></div>
      <div class="stat-cell"><span class="stat-cell__label">후보 풀 크기</span><span class="stat-cell__val num">${result.poolSize.toLocaleString()}</span></div>
      <div class="stat-cell"><span class="stat-cell__label">레짐 필터</span><span class="stat-cell__val">${regimeTxt}</span></div>
      <div class="stat-cell"><span class="stat-cell__label">위기구간</span><span class="stat-cell__val">${state.excludeCrisis ? "제외" : "포함"}</span></div>
      ${extra}
    `;
    if (result.oosByIndex.SPX && !result.oosByIndex.SPX.insufficientData) {
      const oos = result.oosByIndex.SPX;
      const rep = result.regressionByIndex.SPX.perHorizon[state.forwardWindow - 1];
      if (state.mode === "regression" && rep && oos.oosR2 != null && rep.r2 > 0 && oos.oosR2 < rep.r2 * 0.4) {
        const warnEl = document.createElement("div");
        warnEl.className = "warn-box warn-box--full";
        warnEl.textContent = "⚠ Out-of-sample 성과가 in-sample보다 현저히 낮습니다 — 이 시나리오 조합은 과거 데이터에 끼워맞춰졌을(overfit) 가능성이 있습니다.";
        el.after(warnEl);
      }
    }
  }

  function renderSummaryText(result) {
    const el = document.getElementById("summary-text");
    const finals = {};
    for (const im of App.INDEX_META) {
      if (state.mode === "analog") {
        const b = result.analogByIndex[im.key].bands;
        finals[im.key] = b ? b.p50[b.p50.length - 1] : null;
      } else {
        const rep = result.regressionByIndex[im.key].perHorizon[state.forwardWindow - 1];
        finals[im.key] = rep ? rep.beta.reduce((s, b, j) => s + b * result.regDesignScenarioRow?.[j] || 0, 0) : null;
      }
    }
    if (state.mode === "regression") {
      // 시나리오 행 벡터로 예측치 계산
      const scenarioRow = buildScenarioDesignRow(result);
      for (const im of App.INDEX_META) {
        const rep = result.regressionByIndex[im.key].perHorizon[state.forwardWindow - 1];
        finals[im.key] = rep ? dot(rep.beta, scenarioRow) : null;
      }
    }
    const order = [...App.INDEX_META].sort((a, b) => (finals[b.key] ?? -99) - (finals[a.key] ?? -99));
    const most = order[0],
      least = order[order.length - 1];
    const noteOld = state.poolMode === "level" ? " (1990년대 초 analog 시점은 다우/나스닥 구성이 현재와 크게 달랐다는 점을 감안하십시오.)" : "";
    if (most && least && finals[most.key] != null && finals[least.key] != null) {
      el.textContent = `D+${state.forwardWindow} 기준 예상수익률: ${most.label} ${fmtPct(finals[most.key])} vs ${least.label} ${fmtPct(finals[least.key])} — ${most.key === "IXIC" ? "나스닥이 시나리오에 더 민감하게 반응(듀레이션 성격 시사)" : most.label + "가 상대적으로 더 민감"}.${noteOld}`;
    } else {
      el.textContent = "표본이 부족해 요약을 계산할 수 없습니다. 조건을 완화해 보세요." + noteOld;
    }
  }

  function dot(beta, row) {
    let s = 0;
    for (let i = 0; i < beta.length; i++) s += beta[i] * row[i];
    return s;
  }
  function buildScenarioDesignRow(result) {
    const selected = result.selectedVarKeys;
    const scenarioMomVal = [App.DS().regime]; // unused placeholder
    const mom = window.__macroApp && 0;
    const row = [1, lastVal("mom63"), lastVal("maDev252")];
    for (const k of selected) row.push(state.scenarioValues[k]);
    const dHiking = state.targetRegime === "hiking" ? 1 : 0;
    const dCutting = state.targetRegime === "cutting" ? 1 : 0;
    row.push(dHiking, dCutting);
    for (const k of selected) {
      row.push(dHiking * state.scenarioValues[k]);
      row.push(dCutting * state.scenarioValues[k]);
    }
    return row;
  }
  function lastVal(key) {
    // mom63/maDev252 현재값은 app.js 내부 클로저에 있어 재계산 (동일 로직 재사용)
    const ds = App.DS();
    if (key === "mom63") {
      const m = A.momentum(ds.prices.SPX, 63);
      return m[m.length - 1];
    }
    const v = A.maDeviation(ds.prices.SPX, 252);
    return v[v.length - 1];
  }

  // =======================================================================
  // 차트 (SVG)
  // =======================================================================
  function renderChart(result) {
    const wrap = document.getElementById("chart-wrap");
    const W = 920,
      H = 420,
      ML = 52,
      MR = 20,
      MT = 16,
      MB = 30;
    const plotW = W - ML - MR;
    const plotH = H - MT - MB;
    const N = state.forwardWindow;

    // 시리즈별 (h=0..N) 값 구성
    const series = {};
    let yMin = 100,
      yMax = 100;
    for (const im of App.INDEX_META) {
      let center = [100],
        band68lo = [100],
        band68hi = [100],
        band95lo = [100],
        band95hi = [100];
      if (state.mode === "analog") {
        const b = result.analogByIndex[im.key].bands;
        if (b) {
          for (let h = 0; h < N; h++) {
            center.push(100 * (1 + b.p50[h]));
            band68lo.push(100 * (1 + b.p25[h]));
            band68hi.push(100 * (1 + b.p75[h]));
            band95lo.push(100 * (1 + b.p10[h]));
            band95hi.push(100 * (1 + b.p90[h]));
          }
        }
      } else {
        const scenarioRow = buildScenarioDesignRow(result);
        for (let h = 1; h <= N; h++) {
          const rep = result.regressionByIndex[im.key].perHorizon[h - 1];
          if (!rep) {
            center.push(center[center.length - 1]);
            band68lo.push(band68lo[band68lo.length - 1]);
            band68hi.push(band68hi[band68hi.length - 1]);
            band95lo.push(band95lo[band95lo.length - 1]);
            band95hi.push(band95hi[band95hi.length - 1]);
            continue;
          }
          const point = dot(rep.beta, scenarioRow);
          // 잔차표준편차 기반 지평별 신뢰구간 (HAC 분산 + 잔차 변동 반영 근사)
          const seApprox = rep.yStd / Math.sqrt(rep.n) + (rep.se[0] || 0);
          center.push(100 * (1 + point));
          band68lo.push(100 * (1 + point - seApprox));
          band68hi.push(100 * (1 + point + seApprox));
          band95lo.push(100 * (1 + point - 1.96 * seApprox * 1.4));
          band95hi.push(100 * (1 + point + 1.96 * seApprox * 1.4));
        }
      }
      series[im.key] = { center, band68lo, band68hi, band95lo, band95hi };
      for (const arr of [band95lo, band95hi]) {
        for (const v of arr) {
          if (v != null && Number.isFinite(v)) {
            yMin = Math.min(yMin, v);
            yMax = Math.max(yMax, v);
          }
        }
      }
    }
    const pad = (yMax - yMin) * 0.12 || 2;
    yMin -= pad;
    yMax += pad;

    const xScale = (h) => ML + (h / N) * plotW;
    const yScale = (v) => MT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    function pathD(arr) {
      return arr.map((v, h) => `${h === 0 ? "M" : "L"} ${xScale(h).toFixed(2)} ${yScale(v).toFixed(2)}`).join(" ");
    }
    function bandD(lo, hi) {
      let d = `M ${xScale(0).toFixed(2)} ${yScale(hi[0]).toFixed(2)} `;
      for (let h = 1; h < hi.length; h++) d += `L ${xScale(h).toFixed(2)} ${yScale(hi[h]).toFixed(2)} `;
      for (let h = lo.length - 1; h >= 0; h--) d += `L ${xScale(h).toFixed(2)} ${yScale(lo[h]).toFixed(2)} `;
      return d + "Z";
    }

    // 그리드 + 축
    const yTicks = 5;
    let gridSvg = "";
    for (let t = 0; t <= yTicks; t++) {
      const v = yMin + (t / yTicks) * (yMax - yMin);
      const y = yScale(v);
      gridSvg += `<line x1="${ML}" y1="${y.toFixed(1)}" x2="${W - MR}" y2="${y.toFixed(1)}" stroke="var(--gridline)" stroke-width="1" />`;
      gridSvg += `<text x="${ML - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end" class="chart-axis-label">${v.toFixed(0)}</text>`;
    }
    let xAxisSvg = "";
    const xTickStep = N <= 5 ? 1 : Math.ceil(N / 8);
    for (let h = 0; h <= N; h += xTickStep) {
      const x = xScale(h);
      xAxisSvg += `<text x="${x.toFixed(1)}" y="${H - 8}" text-anchor="middle" class="chart-axis-label">D+${h}</text>`;
    }
    // 0% 기준선(=100) 강조
    const zeroY = yScale(100);
    gridSvg += `<line x1="${ML}" y1="${zeroY.toFixed(1)}" x2="${W - MR}" y2="${zeroY.toFixed(1)}" stroke="var(--baseline)" stroke-width="1.3" stroke-dasharray="3 3" />`;

    let bandsSvg = "";
    let linesSvg = "";
    for (const im of App.INDEX_META) {
      const s = series[im.key];
      const dim = state.highlightIndex && state.highlightIndex !== im.key;
      const colorVar = `var(${im.color})`;
      const opacity95 = dim ? 0.04 : 0.12;
      const opacity68 = dim ? 0.07 : 0.24;
      bandsSvg += `<path class="band-anim" d="${bandD(s.band95lo, s.band95hi)}" fill="${colorVar}" fill-opacity="${opacity95}" stroke="none"></path>`;
      bandsSvg += `<path class="band-anim" d="${bandD(s.band68lo, s.band68hi)}" fill="${colorVar}" fill-opacity="${opacity68}" stroke="none"></path>`;
      linesSvg += `<path class="line-anim" data-idx="${im.key}" d="${pathD(s.center)}" fill="none" stroke="${colorVar}" stroke-width="${dim ? 1.4 : 2.4}" stroke-opacity="${dim ? 0.35 : 1}" stroke-linecap="round" stroke-linejoin="round"></path>`;
      // D+N 끝점 마커 + 라벨
      const endX = xScale(N),
        endY = yScale(s.center[s.center.length - 1]);
      linesSvg += `<circle cx="${endX.toFixed(1)}" cy="${endY.toFixed(1)}" r="${dim ? 2.5 : 4}" fill="${colorVar}" fill-opacity="${dim ? 0.4 : 1}"></circle>`;
    }

    wrap.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" class="fp-chart" role="img" aria-label="지수별 forward path 차트">
        <g>${gridSvg}</g>
        <g>${bandsSvg}</g>
        <g>${linesSvg}</g>
        <g>${xAxisSvg}</g>
        <rect id="chart-overlay" x="${ML}" y="${MT}" width="${plotW}" height="${plotH}" fill="transparent"></rect>
        <line id="crosshair" x1="0" y1="${MT}" x2="0" y2="${MT + plotH}" stroke="var(--ink-muted)" stroke-width="1" stroke-dasharray="2 2" opacity="0"></line>
      </svg>
      <div id="chart-tooltip" class="chart-tooltip" hidden></div>
    `;

    wireChartInteraction(wrap, series, xScale, yScale, ML, plotW, N);
    animateChart(wrap);
  }

  function wireChartInteraction(wrap, series, xScale, yScale, ML, plotW, N) {
    const overlay = wrap.querySelector("#chart-overlay");
    const crosshair = wrap.querySelector("#crosshair");
    const tooltip = wrap.querySelector("#chart-tooltip");
    overlay.addEventListener("mousemove", (e) => {
      const svg = wrap.querySelector("svg");
      const rect = svg.getBoundingClientRect();
      const scaleX = 920 / rect.width;
      const localX = (e.clientX - rect.left) * scaleX;
      let h = Math.round(((localX - ML) / plotW) * N);
      h = Math.max(0, Math.min(N, h));
      const x = xScale(h);
      crosshair.setAttribute("x1", x);
      crosshair.setAttribute("x2", x);
      crosshair.setAttribute("opacity", "1");
      const lines = App.INDEX_META.map((im) => {
        const v = series[im.key].center[h];
        return `<div class="chart-tooltip__row"><span class="chart-tooltip__swatch" style="background:var(${im.color})"></span>${im.label} <span class="num">${v.toFixed(1)}</span> <span class="num chart-tooltip__pct">${(v - 100 >= 0 ? "+" : "") + (v - 100).toFixed(1)}%</span></div>`;
      }).join("");
      tooltip.innerHTML = `<div class="chart-tooltip__head">D+${h}</div>${lines}`;
      tooltip.hidden = false;
      const wrapRect = wrap.getBoundingClientRect();
      tooltip.style.left = Math.min(e.clientX - wrapRect.left + 12, wrapRect.width - 190) + "px";
      tooltip.style.top = Math.max(e.clientY - wrapRect.top - 60, 0) + "px";
    });
    overlay.addEventListener("mouseleave", () => {
      crosshair.setAttribute("opacity", "0");
      tooltip.hidden = true;
    });
  }

  function animateChart(wrap) {
    const lines = wrap.querySelectorAll(".line-anim");
    const bands = wrap.querySelectorAll(".band-anim");
    bands.forEach((b) => {
      b.style.opacity = "0";
      b.style.transition = "opacity 700ms ease";
    });
    requestAnimationFrame(() => {
      bands.forEach((b) => (b.style.opacity = "1"));
    });
    if (reducedMotion) return;
    lines.forEach((line) => {
      const len = line.getTotalLength();
      line.style.strokeDasharray = String(len);
      line.style.strokeDashoffset = String(len);
      line.getBoundingClientRect(); // force reflow
      line.style.transition = "stroke-dashoffset 1100ms cubic-bezier(.4,.1,.2,1)";
      requestAnimationFrame(() => {
        line.style.strokeDashoffset = "0";
      });
    });
  }

  // =======================================================================
  // 하단 상세 패널 (Analog 표 / 회귀 진단)
  // =======================================================================
  function renderDetailPanel(result) {
    const title = document.getElementById("detail-title");
    const body = document.getElementById("detail-body");
    const compareEl = document.getElementById("crisis-compare");
    const c = result.crisisComparison;
    compareEl.innerHTML = `<span class="hint">위기구간 ${c.current} 평균 D+${state.forwardWindow} 수익률(SPX) <span class="num">${fmtPct(c.currentMean)}</span> · ${c.otherLabel} <span class="num">${fmtPct(c.otherMean)}</span></span>`;

    if (state.mode === "analog") {
      title.textContent = `Analog 표본 (${result.knn.neighbors.length}개, 거리순)`;
      body.innerHTML = renderAnalogTable(result);
      attachTooltips(body);
    } else {
      title.textContent = `회귀 진단 — 대표 지평 D+${state.forwardWindow}`;
      body.innerHTML = renderRegressionPanel(result);
    }
  }

  function renderAnalogTable(result) {
    const rows = result.knn.neighbors
      .map((nb) => {
        const regimeTxt = App.REGIME_LABEL[nb.regime] || "—";
        const extremeMark = nb.extreme ? `<span class="extreme-mark" title="${nb.crisisLabel || "통계적 극값(±3σ 또는 상하위 1%)"}">**</span>` : "";
        const cells = App.INDEX_META.map((im) => {
          const p = result.analogByIndex[im.key].paths.find((pp) => pp.date === nb.date);
          const v = p ? p.path[p.path.length - 1] : null;
          return `<td class="num">${fmtPct(v)}</td>`;
        }).join("");
        return `<tr>
          <td class="num">${nb.date}${extremeMark}</td>
          <td>${regimeTxt}</td>
          <td class="num">${nb.dist.toFixed(2)}</td>
          <td class="num">${nb.weight.toFixed(2)}</td>
          ${cells}
        </tr>`;
      })
      .join("");
    return `
      <div class="table-scroll">
      <table class="data-table">
        <thead><tr>
          <th>날짜</th><th>레짐</th><th>거리(z)</th><th>가중치</th>
          ${App.INDEX_META.map((im) => `<th>D+${state.forwardWindow} ${im.label}</th>`).join("")}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      </div>
      <p class="hint" style="margin-top:8px">** 표시는 통계적 극값(변화량 ±3σ 또는 상하위 1%) 또는 알려진 위기구간에 해당하는 날짜입니다. 마우스오버 시 사유가 표시됩니다.</p>
    `;
  }
  function attachTooltips() {
    /* title 속성으로 네이티브 툴팁 처리 — 별도 JS 불필요 */
  }

  function renderRegressionPanel(result) {
    const diag = {};
    for (const im of App.INDEX_META) {
      diag[im.key] = App.computeDiagnostics(im.key, null, result.regDesign);
    }
    const spx = diag.SPX;
    if (!spx) return `<p class="hint">표본이 부족해 회귀 진단을 계산할 수 없습니다.</p>`;

    const rows = result.regDesign.colNames
      .map((name, j) => {
        const jj = j + 1; // beta[0]=절편
        const olsB = spx.ols.beta[jj];
        const huberB = spx.huber.beta[jj];
        const vifEntry = spx.vif[j];
        const rep = result.regressionByIndex.SPX.perHorizon[state.forwardWindow - 1];
        const se = rep ? rep.se[jj] : null;
        const p = rep ? rep.pvals[jj] : null;
        const vifWarn = vifEntry && vifEntry.vif > 10;
        const diffPct = Math.abs(olsB - huberB) / (Math.abs(olsB) + 1e-6);
        const diffWarn = diffPct > 0.5 && Math.abs(olsB) > 1e-4;
        return `<tr class="${diffWarn ? "row-warn" : ""}">
          <td>${labelForCol(name)}</td>
          <td class="num">${fmtNum(olsB, 4)}</td>
          <td class="num">${fmtNum(huberB, 4)}</td>
          <td class="num">${se != null ? fmtNum(se, 4) : "—"}</td>
          <td class="num">${p != null ? fmtNum(p, 3) : "—"}${p != null && p < 0.05 ? " *" : ""}</td>
          <td class="num ${vifWarn ? "cell-warn" : ""}">${vifEntry ? fmtNum(vifEntry.vif, 1) : "—"}</td>
        </tr>`;
      })
      .join("");

    const oosRows = App.INDEX_META.map((im) => {
      const oos = result.oosByIndex[im.key];
      const rep = result.regressionByIndex[im.key].perHorizon[state.forwardWindow - 1];
      if (!oos || oos.insufficientData) return `<tr><td>${im.label}</td><td colspan="3" class="hint">표본 부족 (walk-forward 최소 6년 필요)</td></tr>`;
      return `<tr>
        <td>${im.label}</td>
        <td class="num">${rep ? fmtNum(rep.r2, 3) : "—"}</td>
        <td class="num">${fmtNum(oos.oosR2, 3)}</td>
        <td class="num">${oos.oosHitRate != null ? fmtPct(oos.oosHitRate, 0) : "—"}</td>
      </tr>`;
    }).join("");

    return `
      <p class="hint">계수는 지평별 독립 회귀로 계산됩니다. 표준오차는 Newey-West(HAC, lag=D+${state.forwardWindow}-1). 아래 계수·VIF·Huber 비교는 계산비용을 감안해 대표 지평(D+${state.forwardWindow}, S&amp;P500 기준)으로 표시합니다 — 지평별 R²/신뢰구간은 상단 차트의 회귀 모드에 전 지평 반영되어 있습니다.</p>
      <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>변수</th><th>계수(OLS)</th><th>계수(Huber)</th><th>HAC SE</th><th>p-value</th><th>VIF</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      </div>
      <p class="hint" style="margin-top:6px">VIF&gt;10(강조 표시) 또는 OLS·Huber 계수 차이가 50%를 넘는 행(강조 표시)은 소수 극단 이벤트에 추정치가 민감할 수 있음을 의미합니다. n=${spx.n.toLocaleString()}</p>

      <h4 class="detail-subhead">Walk-forward Out-of-sample (5년 학습 → 1년 검증, 대표 지평 D+${state.forwardWindow})</h4>
      <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>지수</th><th>In-sample R²</th><th>OOS R²</th><th>OOS 적중률</th></tr></thead>
        <tbody>${oosRows}</tbody>
      </table>
      </div>
    `;
  }

  function labelForCol(name) {
    const map = {
      mom63: "3개월 모멘텀(SPX, 통제)",
      maDev252: "12개월 이평 괴리율(SPX, 통제)",
      regime_hiking: "레짐: 긴축기 더미",
      regime_cutting: "레짐: 완화기 더미",
    };
    if (map[name]) return map[name];
    const varDef = App.VARIABLES.find((v) => v.key === name);
    if (varDef) return varDef.label;
    const m = name.match(/^(hiking|cutting)_x_(.+)$/);
    if (m) {
      const vd = App.VARIABLES.find((v) => v.key === m[2]);
      return `${m[1] === "hiking" ? "긴축기" : "완화기"} × ${vd ? vd.label : m[2]}`;
    }
    return name;
  }

  window.__macroUI = { renderShell, renderResults };
  App.renderShellRef = renderShell;
})();
