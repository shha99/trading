// 실시간 차트 대시보드 - 순수 JS, 빌드 단계 없음 (lightweight-charts는
// /static/vendor/lightweight-charts.js로 벤더링된 standalone 빌드 사용).
(function () {
  "use strict";

  const LC = window.LightweightCharts;
  const SYMBOLS = ["BTCUSDT", "ETHUSDT"];
  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];
  const TF_MS = { "15m": 15 * 60e3, "1h": 60 * 60e3, "4h": 4 * 60 * 60e3, "1d": 24 * 60 * 60e3 };
  const STORAGE_KEY = "binance-futures-bot.dashboard.v1";
  const CANDLE_LIMIT = 320;

  // ------------------------------------------------------------------
  // 상태
  // ------------------------------------------------------------------
  const state = {
    symbol: "BTCUSDT",
    timeframe: "1h",
    layout: "vertical", // vertical | grid6 | fit
    enabled: {}, // { [indicatorId]: { params: {...} } }  (패턴 포함)
    catalog: [], // /api/indicators 응답
    catalogById: {},
    candles: [],
  };

  function loadPersisted() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.layout) state.layout = saved.layout;
      if (saved.enabled) state.enabled = saved.enabled;
    } catch (e) { /* 저장된 값이 없거나 깨졌으면 기본값 사용 */ }
  }

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ layout: state.layout, enabled: state.enabled }));
    } catch (e) { /* 프라이빗 모드 등에서 실패해도 무시 */ }
  }

  // ------------------------------------------------------------------
  // DOM 참조
  // ------------------------------------------------------------------
  const el = {
    symbolTabs: document.getElementById("symbolTabs"),
    timeframeTabs: document.getElementById("timeframeTabs"),
    wsStatus: document.getElementById("wsStatus"),
    statPrice: document.getElementById("statPrice"),
    statChangePct: document.getElementById("statChangePct"),
    statHighLow: document.getElementById("statHighLow"),
    statQuoteVolume: document.getElementById("statQuoteVolume"),
    statCountdown: document.getElementById("statCountdown"),
    chartMain: document.getElementById("chartMain"),
    subpaneContainer: document.getElementById("subpaneContainer"),
    crosshairPanel: document.getElementById("crosshairPanel"),
    layoutSwitch: document.getElementById("layoutSwitch"),
    indicatorSearch: document.getElementById("indicatorSearch"),
    categoryFilter: document.getElementById("categoryFilter"),
    indicatorList: document.getElementById("indicatorList"),
    btnEnableAll: document.getElementById("btnEnableAll"),
    btnDisableAll: document.getElementById("btnDisableAll"),
  };

  // ------------------------------------------------------------------
  // 탭(심볼/시간대) 렌더링
  // ------------------------------------------------------------------
  function renderTabs() {
    el.symbolTabs.innerHTML = "";
    SYMBOLS.forEach((sym) => {
      const btn = document.createElement("button");
      btn.textContent = sym.replace("USDT", "");
      if (sym === state.symbol) btn.classList.add("active");
      btn.onclick = () => { state.symbol = sym; onSymbolOrTimeframeChanged(); };
      el.symbolTabs.appendChild(btn);
    });

    el.timeframeTabs.innerHTML = "";
    TIMEFRAMES.forEach((tf) => {
      const btn = document.createElement("button");
      btn.textContent = tf;
      if (tf === state.timeframe) btn.classList.add("active");
      btn.onclick = () => { state.timeframe = tf; onSymbolOrTimeframeChanged(); };
      el.timeframeTabs.appendChild(btn);
    });
  }

  // ------------------------------------------------------------------
  // 차트 (메인: 캔들+거래량+오버레이 지표 / 보조창: 세로 pane 또는 grid6 미니차트)
  // ------------------------------------------------------------------
  let mainChart, candleSeries, volumeSeries, seriesMarkers;
  const overlaySeries = {}; // indicatorId -> [series, ...]
  const subpanePanes = {}; // indicatorId -> paneIndex (vertical/fit 모드)
  const gridCharts = {}; // indicatorId -> { chart, series:[...] } (grid6 모드)
  let syncingRange = false;

  function initMainChart() {
    mainChart = LC.createChart(el.chartMain, chartOptions());
    candleSeries = mainChart.addSeries(LC.CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    volumeSeries = mainChart.addSeries(LC.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
    });
    mainChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    seriesMarkers = LC.createSeriesMarkers(candleSeries, []);

    mainChart.subscribeCrosshairMove(onCrosshairMove);
    window.addEventListener("resize", handleContainerResize);
    new ResizeObserver(handleContainerResize).observe(el.chartMain);
  }

  // ResizeObserver가 우리 스스로 el.chartMain.style.height를 바꿀 때도
  // 발동하는데, 그때 mainChart.resize()만 다시 부르면 pane별 높이 배분이
  // (라이브러리 내부적으로) 흐트러지는 걸 확인해서, 항상 우리가 원하는
  // pane 비율(applyPaneHeights)까지 같이 재적용한다.
  let lastSubCount = 0;

  function handleContainerResize() {
    if (state.layout === "grid6") {
      mainChart.resize(el.chartMain.clientWidth, el.chartMain.clientHeight);
    } else {
      applyPaneHeights(lastSubCount);
    }
  }

  function chartOptions() {
    return {
      layout: { background: { color: "#1c2129" }, textColor: "#c9d1d9" },
      grid: { vertLines: { color: "#232b36" }, horzLines: { color: "#232b36" } },
      rightPriceScale: { borderColor: "#2a313c" },
      timeScale: { borderColor: "#2a313c", timeVisible: true, secondsVisible: false },
      crosshair: { mode: LC.CrosshairMode.Normal },
      autoSize: false,
      width: el.chartMain.clientWidth,
      height: el.chartMain.clientHeight,
    };
  }

  function candlesToSeriesData(candles) {
    return candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }));
  }

  function volumeToSeriesData(candles) {
    return candles.map((c) => ({
      time: c.time, value: c.volume,
      color: c.close >= c.open ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)",
    }));
  }

  function applyCandlesToChart(candles) {
    state.candles = candles;
    candleSeries.setData(candlesToSeriesData(candles));
    volumeSeries.setData(volumeToSeriesData(candles));
  }

  function applyIncomingCandles(candles) {
    // lightweight-charts의 series.update()는 "마지막 봉과 같은 시각"(교체)
    // 이거나 "마지막 봉보다 미래"(추가)일 때만 허용된다 - 그보다 과거 시각을
    // 넣으면 "Cannot update oldest data" 에러가 난다. WS가 주는 최근 2봉 중
    // 하나는 우리가 이미 갖고 있는 마지막 봉보다 "과거"인 경우가 흔해서
    // (예: [직전 완결봉, 진행중 봉] 인데 우리는 이미 진행중 봉까지 반영한
    // 상태), 이미 반영된 것보다 오래된 봉은 걸러내고 나머지만 적용한다.
    const lastKnown = state.candles.length ? state.candles[state.candles.length - 1].time : -Infinity;
    candles
      .filter((c) => c.time >= lastKnown)
      .forEach((candle) => {
        if (state.candles.length && state.candles[state.candles.length - 1].time === candle.time) {
          state.candles[state.candles.length - 1] = candle;
        } else {
          state.candles.push(candle);
        }
        candleSeries.update({ time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close });
        volumeSeries.update({
          time: candle.time, value: candle.volume,
          color: candle.close >= candle.open ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)",
        });
      });
  }

  // ------------------------------------------------------------------
  // 데이터 로딩
  // ------------------------------------------------------------------
  async function loadCandles() {
    const res = await fetch(`/api/candles?symbol=${state.symbol}&timeframe=${state.timeframe}&limit=${CANDLE_LIMIT}`);
    const candles = await res.json();
    applyCandlesToChart(candles);
  }

  async function fetchIndicatorValues(id, params) {
    const url = `/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=${id}` +
      `&limit=${CANDLE_LIMIT}&params=${encodeURIComponent(JSON.stringify(params || {}))}`;
    const res = await fetch(url);
    return res.json();
  }

  // ------------------------------------------------------------------
  // 지표 카탈로그 / 사이드바
  // ------------------------------------------------------------------
  let activeCategoryFilter = null; // null = 전체

  async function loadCatalog() {
    const res = await fetch("/api/indicators");
    state.catalog = await res.json();
    state.catalogById = {};
    state.catalog.forEach((e) => { state.catalogById[e.id] = e; });
    renderCategoryFilter();
    renderIndicatorList();
  }

  function renderCategoryFilter() {
    const categories = [...new Set(state.catalog.map((e) => e.category))];
    el.categoryFilter.innerHTML = "";
    const allLabel = makeCategoryChip("전체", null);
    el.categoryFilter.appendChild(allLabel);
    categories.forEach((cat) => el.categoryFilter.appendChild(makeCategoryChip(cat, cat)));
  }

  function makeCategoryChip(label, value) {
    const wrapper = document.createElement("label");
    if (activeCategoryFilter === value) wrapper.classList.add("active");
    wrapper.innerHTML = `<input type="checkbox" ${activeCategoryFilter === value ? "checked" : ""}/> ${label}`;
    wrapper.querySelector("input").onchange = () => {
      activeCategoryFilter = value;
      renderCategoryFilter();
      renderIndicatorList();
    };
    return wrapper;
  }

  function filteredCatalog() {
    const term = el.indicatorSearch.value.trim().toLowerCase();
    return state.catalog.filter((e) => {
      if (activeCategoryFilter && e.category !== activeCategoryFilter) return false;
      if (!term) return true;
      return e.id.toLowerCase().includes(term) || e.label.toLowerCase().includes(term);
    });
  }

  function renderIndicatorList() {
    const list = filteredCatalog();
    el.indicatorList.innerHTML = "";
    let lastCategory = null;
    list.forEach((entry) => {
      if (entry.category !== lastCategory) {
        const h = document.createElement("div");
        h.className = "indicator-category-label";
        h.textContent = entry.category;
        el.indicatorList.appendChild(h);
        lastCategory = entry.category;
      }
      el.indicatorList.appendChild(makeIndicatorRow(entry));
    });
  }

  function makeIndicatorRow(entry) {
    const row = document.createElement("div");
    row.className = "indicator-row" + (state.enabled[entry.id] ? " enabled" : "");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = !!state.enabled[entry.id];
    checkbox.onchange = () => toggleIndicator(entry, checkbox.checked, row);

    const label = document.createElement("span");
    label.className = "ind-label";
    label.textContent = entry.label;
    label.title = `${entry.id} (${entry.pane === "overlay" ? "가격창 겹침" : "보조창"})`;

    const badge = document.createElement("span");
    badge.className = "ind-badge";
    badge.dataset.role = "scale-badge";

    row.appendChild(checkbox);
    row.appendChild(label);
    row.appendChild(badge);

    if (Object.keys(entry.params || {}).length > 0) {
      const gear = document.createElement("button");
      gear.className = "gear-btn";
      gear.textContent = "⚙";
      gear.onclick = (ev) => { ev.stopPropagation(); toggleParamEditor(entry, row); };
      row.appendChild(gear);
    }

    return row;
  }

  function toggleParamEditor(entry, row) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains("param-editor")) {
      existing.remove();
      return;
    }
    const current = (state.enabled[entry.id] && state.enabled[entry.id].params) || entry.params;
    const editor = document.createElement("div");
    editor.className = "param-editor";
    Object.keys(entry.params).forEach((key) => {
      const wrap = document.createElement("label");
      wrap.textContent = key;
      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.value = current[key];
      input.onchange = () => {
        const params = (state.enabled[entry.id] && state.enabled[entry.id].params) || { ...entry.params };
        params[key] = Number(input.value);
        if (state.enabled[entry.id]) {
          state.enabled[entry.id].params = params;
          persist();
          refreshOneIndicator(entry.id);
        }
      };
      wrap.appendChild(input);
      editor.appendChild(wrap);
    });
    row.after(editor);
  }

  function toggleIndicator(entry, on, row) {
    if (on) {
      state.enabled[entry.id] = { params: { ...entry.params } };
      row.classList.add("enabled");
    } else {
      delete state.enabled[entry.id];
      row.classList.remove("enabled");
    }
    persist();

    // 보조창(subpane) 지표를 켜고 끌 때만 pane 구조가 바뀐다. ensureNativePanes는
    // 매번 전체 pane을 다시 만들기 때문에(단순함을 위한 선택), 그 안에 있던
    // 다른 지표들의 시리즈 데이터도 같이 사라진다 - 그래서 pane을 다시 만들 때는
    // 켜져있는 지표 전체를 다시 그려야 한다(방금 토글한 것만이 아니라).
    if (entry.pane === "subpane" && !entry.is_pattern) {
      rebuildSubpaneLayout();
      refreshAllIndicators();
    } else {
      refreshOneIndicator(entry.id);
    }
  }

  el.indicatorSearch.addEventListener("input", renderIndicatorList);
  el.btnEnableAll.addEventListener("click", () => {
    filteredCatalog().forEach((entry) => { state.enabled[entry.id] = { params: { ...entry.params } }; });
    persist();
    renderIndicatorList();
    rebuildSubpaneLayout();
    refreshAllIndicators();
  });
  el.btnDisableAll.addEventListener("click", () => {
    state.enabled = {};
    persist();
    renderIndicatorList();
    rebuildSubpaneLayout();
  });

  // ------------------------------------------------------------------
  // 오버레이/보조창 지표 렌더링
  // ------------------------------------------------------------------
  const OVERLAY_COLORS = ["#e6c200", "#4098ff", "#ff7f50", "#b47cff", "#40e0c0", "#ff69b4"];

  function clearSeries(map) {
    Object.values(map).forEach((arr) => arr.forEach((s) => { try { mainChart.removeSeries(s); } catch (e) {} }));
    for (const k in map) delete map[k];
  }

  function enabledOverlayEntries() {
    return Object.keys(state.enabled)
      .map((id) => state.catalogById[id])
      .filter((e) => e && e.pane === "overlay" && !e.is_pattern);
  }

  function enabledSubpaneEntries() {
    return Object.keys(state.enabled)
      .map((id) => state.catalogById[id])
      .filter((e) => e && e.pane === "subpane" && !e.is_pattern);
  }

  function enabledPatternEntries() {
    return Object.keys(state.enabled).map((id) => state.catalogById[id]).filter((e) => e && e.is_pattern);
  }

  async function refreshAllIndicators() {
    await Promise.all([
      ...enabledOverlayEntries().map((e) => renderOverlayIndicator(e)),
      ...enabledSubpaneEntries().map((e) => renderSubpaneIndicator(e)),
      renderPatternMarkers(),
    ]);
  }

  async function refreshOneIndicator(id) {
    const entry = state.catalogById[id];
    if (!entry) return;
    if (!state.enabled[id]) {
      if (overlaySeries[id]) { overlaySeries[id].forEach((s) => mainChart.removeSeries(s)); delete overlaySeries[id]; }
      if (entry.is_pattern) renderPatternMarkers();
      return;
    }
    if (entry.is_pattern) { await renderPatternMarkers(); return; }
    if (entry.pane === "overlay") await renderOverlayIndicator(entry);
    else await renderSubpaneIndicator(entry);
  }

  async function renderOverlayIndicator(entry) {
    const params = state.enabled[entry.id].params;
    const values = await fetchIndicatorValues(entry.id, params);
    if (values.error) return;

    if (overlaySeries[entry.id]) overlaySeries[entry.id].forEach((s) => mainChart.removeSeries(s));
    const priceScaleId = pickPriceScaleId(entry.id, values);
    const series = Object.keys(values).map((outName, i) =>
      mainChart.addSeries(LC.LineSeries, {
        color: OVERLAY_COLORS[i % OVERLAY_COLORS.length], lineWidth: 1,
        priceScaleId, title: `${entry.label}${Object.keys(values).length > 1 ? "." + outName : ""}`,
      })
    );
    Object.keys(values).forEach((outName, i) => {
      series[i].setData(values[outName].filter((p) => p.value !== null));
    });
    overlaySeries[entry.id] = series;
    updateScaleBadge(entry.id, priceScaleId !== "right");
  }

  function pickPriceScaleId(id, values) {
    // 가격과 스케일이 안 맞는 오버레이 지표는 별도 y축(왼쪽)에 둬서 캔들이 안 눌리게 한다.
    const closes = state.candles.map((c) => c.close).filter((v) => v != null);
    if (!closes.length) return "right";
    const priceMedian = median(closes);
    const allValues = Object.values(values).flatMap((arr) => arr.map((p) => p.value).filter((v) => v !== null));
    if (!allValues.length || !priceMedian) return "right";
    const valueMedian = median(allValues.map(Math.abs));
    const ratio = valueMedian / Math.abs(priceMedian);
    if (ratio < 0.2 || ratio > 5) return `scale_${id}`;
    return "right";
  }

  function median(arr) {
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function updateScaleBadge(id, separate) {
    const rows = el.indicatorList.querySelectorAll(".indicator-row");
    rows.forEach((row) => {
      const label = row.querySelector(".ind-label");
      if (label && label.title.startsWith(id + " ")) {
        const badge = row.querySelector('[data-role="scale-badge"]');
        if (badge) badge.textContent = separate ? "별도축" : "";
      }
    });
  }

  async function renderPatternMarkers() {
    const entries = enabledPatternEntries();
    if (!entries.length) { seriesMarkers.setMarkers([]); return; }
    const results = await Promise.all(entries.map((e) => fetchIndicatorValues(e.id, {})));
    const markers = [];
    results.forEach((values, idx) => {
      const series = values.integer || [];
      series.forEach((p) => {
        if (!p.value) return;
        const bullish = p.value > 0;
        markers.push({
          time: p.time,
          position: bullish ? "belowBar" : "aboveBar",
          color: bullish ? "#26a69a" : "#ef5350",
          shape: bullish ? "arrowUp" : "arrowDown",
          text: entries[idx].label,
        });
      });
    });
    markers.sort((a, b) => a.time - b.time);
    seriesMarkers.setMarkers(markers);
  }

  // ------------------------------------------------------------------
  // 보조창 레이아웃: 세로/한화면 = 메인차트 native pane, 6개씩 = 별도 미니차트
  // ------------------------------------------------------------------
  function rebuildSubpaneLayout() {
    const entries = enabledSubpaneEntries();

    if (state.layout === "grid6") {
      removeAllNativePanes();
      el.chartMain.style.height = ""; // CSS 기본값(440px)으로 복귀
      mainChart.resize(el.chartMain.clientWidth, el.chartMain.clientHeight);
      el.subpaneContainer.className = "subpane-container grid6";
      el.subpaneContainer.innerHTML = "";
      Object.keys(gridCharts).forEach((id) => delete gridCharts[id]);
      entries.forEach((entry) => createGridMiniChart(entry));
    } else {
      Object.values(gridCharts).forEach((g) => g.chart.remove());
      for (const k in gridCharts) delete gridCharts[k];
      el.subpaneContainer.className = "subpane-container";
      el.subpaneContainer.innerHTML = "";
      ensureNativePanes(entries);
    }
    renderIndicatorList(); // 배지/enabled 표시 갱신
  }

  function removeAllNativePanes() {
    // pane을 지우면 그 안의 series도 함께 사라지므로, overlaySeries에 남아있는
    // 참조를 먼저 정리해야 나중에 removeSeries(stale)로 에러가 나지 않는다.
    Object.keys(subpanePanes).forEach((id) => { delete overlaySeries[id]; });
    const panes = mainChart.panes();
    for (let i = panes.length - 1; i >= 1; i--) mainChart.removePane(i);
    for (const k in subpanePanes) delete subpanePanes[k];
  }

  function ensureNativePanes(entries) {
    // 매번 전체 재구성 - 보조지표 개수가 많지 않아 비용이 작고, pane
    // 인덱스가 꼬일 걱정이 없다.
    removeAllNativePanes();
    entries.forEach((entry) => {
      const pane = mainChart.addPane();
      subpanePanes[entry.id] = pane.paneIndex();
    });
    applyPaneHeights(entries.length);
  }

  const MAIN_PANE_HEIGHT = 320;
  const SUBPANE_HEIGHT = 160;

  function applyPaneHeights(subCount) {
    lastSubCount = subCount;
    // #chartMain은 CSS로 440px 고정인데, pane을 그 안에 그냥 추가하면
    // 메인(가격) pane이 짜부라진다. "세로"/"한 화면" 모드는 컨테이너 자체의
    // 실제 높이를 다시 계산해서 늘려줘야 한다 (세로는 다 보이게 늘리고 페이지
    // 스크롤로, 한 화면은 뷰포트 안에 다 들어오게 비율로 나눔).
    let totalHeight;
    if (state.layout === "fit") {
      const top = el.chartMain.getBoundingClientRect().top;
      totalHeight = Math.max(window.innerHeight - top - 16, 260);
    } else {
      totalHeight = MAIN_PANE_HEIGHT + SUBPANE_HEIGHT * subCount;
    }

    el.chartMain.style.height = totalHeight + "px";
    mainChart.resize(el.chartMain.clientWidth, totalHeight);

    if (subCount === 0) return;
    if (state.layout === "fit") {
      const mainShare = Math.max(Math.floor(totalHeight * 0.4), 120);
      const subShare = Math.max(Math.floor((totalHeight - mainShare) / subCount), 60);
      mainChart.panes()[0].setHeight(mainShare);
      for (let i = 1; i <= subCount; i++) mainChart.panes()[i].setHeight(subShare);
    } else {
      mainChart.panes()[0].setHeight(MAIN_PANE_HEIGHT);
      for (let i = 1; i <= subCount; i++) mainChart.panes()[i].setHeight(SUBPANE_HEIGHT);
    }
  }

  function createGridMiniChart(entry) {
    const box = document.createElement("div");
    box.className = "subpane-box";
    const title = document.createElement("div");
    title.className = "subpane-title";
    title.textContent = entry.label;
    box.appendChild(title);
    el.subpaneContainer.appendChild(box);

    const chart = LC.createChart(box, {
      ...chartOptions(),
      width: box.clientWidth, height: 160,
      timeScale: { visible: false }, rightPriceScale: { borderColor: "#2a313c" },
    });
    gridCharts[entry.id] = { chart, series: [] };

    // 메인 차트와 보이는 시간 범위 동기화
    mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && !syncingRange) chart.timeScale().setVisibleLogicalRange(range);
    });

    renderSubpaneIndicator(entry);
  }

  async function renderSubpaneIndicator(entry) {
    const params = state.enabled[entry.id].params;
    const values = await fetchIndicatorValues(entry.id, params);
    if (values.error) return;

    if (state.layout === "grid6") {
      const g = gridCharts[entry.id];
      if (!g) return;
      g.series.forEach((s) => g.chart.removeSeries(s));
      g.series = Object.keys(values).map((outName, i) =>
        g.chart.addSeries(LC.LineSeries, { color: OVERLAY_COLORS[i % OVERLAY_COLORS.length], lineWidth: 1 })
      );
      Object.keys(values).forEach((outName, i) => g.series[i].setData(values[outName].filter((p) => p.value !== null)));
    } else {
      const paneIndex = subpanePanes[entry.id];
      if (paneIndex === undefined) return;
      if (overlaySeries[entry.id]) overlaySeries[entry.id].forEach((s) => mainChart.removeSeries(s));
      const series = Object.keys(values).map((outName, i) =>
        mainChart.addSeries(LC.LineSeries, {
          color: OVERLAY_COLORS[i % OVERLAY_COLORS.length], lineWidth: 1,
          title: `${entry.label}${Object.keys(values).length > 1 ? "." + outName : ""}`,
        }, paneIndex)
      );
      Object.keys(values).forEach((outName, i) => series[i].setData(values[outName].filter((p) => p.value !== null)));
      overlaySeries[entry.id] = series;
    }
  }

  document.querySelectorAll("#layoutSwitch button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.layout = btn.dataset.layout;
      persist();
      document.querySelectorAll("#layoutSwitch button").forEach((b) => b.classList.toggle("active", b === btn));
      rebuildSubpaneLayout();
      refreshAllIndicators(); // 레이아웃이 바뀌면 pane/미니차트가 새로 만들어져서 다시 그려야 함
    });
  });

  // ------------------------------------------------------------------
  // 크로스헤어 정보 패널
  // ------------------------------------------------------------------
  function onCrosshairMove(param) {
    if (!param || !param.time || !param.seriesData || !param.seriesData.has(candleSeries)) {
      el.crosshairPanel.textContent = "OHLCV 정보는 차트 위에 마우스를 올리면 표시됩니다.";
      return;
    }
    const bar = param.seriesData.get(candleSeries);
    const dir = bar.close >= bar.open ? "up" : "down";
    const t = new Date(param.time * 1000).toISOString().replace("T", " ").slice(0, 16);
    el.crosshairPanel.innerHTML =
      `${t} &nbsp; O <span class="${dir}">${fmt(bar.open)}</span> ` +
      `H <span class="${dir}">${fmt(bar.high)}</span> L <span class="${dir}">${fmt(bar.low)}</span> ` +
      `C <span class="${dir}">${fmt(bar.close)}</span>`;
  }

  function fmt(n) { return Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 }); }

  // ------------------------------------------------------------------
  // 통계 카드 / 카운트다운
  // ------------------------------------------------------------------
  function updateStatCards(price, ticker24h) {
    if (price) el.statPrice.textContent = fmt(price.mid);
    if (ticker24h) {
      const pct = Number(ticker24h.priceChangePercent);
      el.statChangePct.textContent = `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
      el.statChangePct.className = "stat-value " + (pct >= 0 ? "up" : "down");
      el.statHighLow.textContent = `${fmt(ticker24h.highPrice)} / ${fmt(ticker24h.lowPrice)}`;
      el.statQuoteVolume.textContent = Number(ticker24h.quoteVolume).toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
  }

  function startCountdown() {
    setInterval(() => {
      const ms = TF_MS[state.timeframe];
      const now = Date.now();
      const next = Math.ceil(now / ms) * ms;
      const remain = Math.max(0, next - now);
      const s = Math.floor(remain / 1000);
      el.statCountdown.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }, 1000);
  }

  // ------------------------------------------------------------------
  // WebSocket
  // ------------------------------------------------------------------
  let ws, wsReconnectDelay = 1000;
  let lastIndicatorRefreshAt = 0;

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws/live`);
    ws.onopen = () => { el.wsStatus.textContent = "실시간 연결됨"; el.wsStatus.className = "ws-status connected"; wsReconnectDelay = 1000; };
    ws.onclose = () => {
      el.wsStatus.textContent = "연결 끊김 - 재시도 중"; el.wsStatus.className = "ws-status disconnected";
      setTimeout(connectWS, wsReconnectDelay);
      wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      if (data.type !== "tick") return;
      updateStatCards(data.prices[state.symbol], data.ticker24h[state.symbol]);
      const candles = data.candles[state.symbol] && data.candles[state.symbol][state.timeframe];
      if (candles && candles.length) {
        applyIncomingCandles(candles);
        const now = Date.now();
        if (now - lastIndicatorRefreshAt > 2500) { // 너무 자주 재계산하지 않도록 살짝 스로틀
          lastIndicatorRefreshAt = now;
          refreshAllIndicators();
        }
      }
    };
  }

  // ------------------------------------------------------------------
  // 초기화
  // ------------------------------------------------------------------
  async function onSymbolOrTimeframeChanged() {
    renderTabs();
    await loadCandles();
    await refreshAllIndicators();
  }

  async function init() {
    loadPersisted();
    renderTabs();
    document.querySelectorAll("#layoutSwitch button").forEach((b) => b.classList.toggle("active", b.dataset.layout === state.layout));
    initMainChart();
    await loadCandles();
    await loadCatalog();
    rebuildSubpaneLayout();
    await refreshAllIndicators();
    startCountdown();
    connectWS();
  }

  init();
})();
