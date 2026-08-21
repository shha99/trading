// 전략 페이지 - 200EMA + 켈트너 하단 눌림목 복귀
(function () {
  "use strict";

  const LC = window.LightweightCharts;
  const SYMBOLS = ["BTCUSDT", "ETHUSDT"];
  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];
  const CANDLE_LIMIT = 320;

  const state = {
    symbol: "BTCUSDT",
    timeframe: "1h", // 검증된 조합 (백테스트 성적표에서 다른 조합도 선택 가능)
    stats: null,
    selected: { symbol: "BTCUSDT", timeframe: "1h" },
  };

  const el = {
    symbolTabs: document.getElementById("symbolTabs"),
    timeframeTabs: document.getElementById("timeframeTabs"),
    condTrend: document.getElementById("condTrend"),
    condTrendValue: document.getElementById("condTrendValue"),
    condPullback: document.getElementById("condPullback"),
    condPullbackValue: document.getElementById("condPullbackValue"),
    condReclaim: document.getElementById("condReclaim"),
    condReclaimValue: document.getElementById("condReclaimValue"),
    condSummary: document.getElementById("condSummary"),
    condSummaryValue: document.getElementById("condSummaryValue"),
    chartMain: document.getElementById("chartMain"),
    statsMeta: document.getElementById("statsMeta"),
    scorecardTable: document.getElementById("scorecardTable"),
    detailLabel: document.getElementById("detailLabel"),
    splitTable: document.getElementById("splitTable"),
    yearlyTable: document.getElementById("yearlyTable"),
    backtestSignalsTable: document.getElementById("backtestSignalsTable"),
    liveSignalsTable: document.getElementById("liveSignalsTable"),
  };

  function renderTabs() {
    el.symbolTabs.innerHTML = "";
    SYMBOLS.forEach((sym) => {
      const btn = document.createElement("button");
      btn.textContent = sym.replace("USDT", "");
      if (sym === state.symbol) btn.classList.add("active");
      btn.onclick = () => { state.symbol = sym; onContextChanged(); };
      el.symbolTabs.appendChild(btn);
    });
    el.timeframeTabs.innerHTML = "";
    TIMEFRAMES.forEach((tf) => {
      const btn = document.createElement("button");
      btn.textContent = tf;
      if (tf === state.timeframe) btn.classList.add("active");
      btn.onclick = () => { state.timeframe = tf; onContextChanged(); };
      el.timeframeTabs.appendChild(btn);
    });
  }

  // ------------------------------------------------------------------
  // 진입조건 패널
  // ------------------------------------------------------------------
  async function loadLiveStatus() {
    const res = await fetch(`/api/strategy/live-status?symbol=${state.symbol}&timeframe=${state.timeframe}`);
    const status = await res.json();

    if (!status.ready) {
      [el.condTrend, el.condPullback, el.condReclaim, el.condSummary].forEach((c) => c.className = "condition-card");
      el.condTrendValue.textContent = el.condPullbackValue.textContent = el.condReclaimValue.textContent = "데이터 부족";
      el.condSummaryValue.textContent = status.reason || "-";
      return;
    }

    const c = status.conditions;
    setCondition(el.condTrend, el.condTrendValue, c.trend_above_200ema,
      `종가 ${fmt(status.values.close)} / 200EMA ${fmt(status.values.ema_trend)}`);
    setCondition(el.condPullback, el.condPullbackValue, c.prev_bar_pulled_back_below_keltner_lower,
      `직전종가 ${fmt(status.values.prev_close)} / 하단 ${fmt(status.values.prev_keltner_lower)}`);
    setCondition(el.condReclaim, el.condReclaimValue, c.curr_bar_reclaimed_keltner_lower,
      `종가 ${fmt(status.values.close)} / 하단 ${fmt(status.values.keltner_lower)}`);

    el.condSummary.className = "condition-card summary " + (status.all_met ? "met" : "unmet");
    el.condSummaryValue.textContent = status.all_met ? "✅ 진입 조건 충족" : "대기 중";
  }

  function setCondition(card, valueEl, met, text) {
    card.className = "condition-card " + (met ? "met" : "unmet");
    valueEl.textContent = (met ? "✅ " : "❌ ") + text;
  }

  function fmt(n) {
    return n == null ? "-" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 });
  }

  // ------------------------------------------------------------------
  // 차트: 캔들 + 200EMA + 켈트너 하단 + 과거 매수 시그널
  // ------------------------------------------------------------------
  let chart, candleSeries, seriesMarkers, emaSeries, keltnerSeries;

  function initChart() {
    chart = LC.createChart(el.chartMain, {
      layout: { background: { color: "#1c2129" }, textColor: "#c9d1d9" },
      grid: { vertLines: { color: "#232b36" }, horzLines: { color: "#232b36" } },
      rightPriceScale: { borderColor: "#2a313c" },
      timeScale: { borderColor: "#2a313c", timeVisible: true, secondsVisible: false },
      width: el.chartMain.clientWidth, height: el.chartMain.clientHeight,
    });
    candleSeries = chart.addSeries(LC.CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    emaSeries = chart.addSeries(LC.LineSeries, { color: "#e6c200", lineWidth: 1, title: "200EMA" });
    keltnerSeries = chart.addSeries(LC.LineSeries, { color: "#4098ff", lineWidth: 1, title: "켈트너 하단" });
    seriesMarkers = LC.createSeriesMarkers(candleSeries, []);
    new ResizeObserver(() => chart.resize(el.chartMain.clientWidth, el.chartMain.clientHeight)).observe(el.chartMain);
  }

  async function loadChart() {
    const candlesRes = await fetch(`/api/candles?symbol=${state.symbol}&timeframe=${state.timeframe}&limit=${CANDLE_LIMIT}`);
    const candles = await candlesRes.json();
    candleSeries.setData(candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));

    const emaRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=EMA&limit=${CANDLE_LIMIT}&params=${encodeURIComponent(JSON.stringify({ timeperiod: 200 }))}`);
    const emaValues = await emaRes.json();
    if (emaValues.real) emaSeries.setData(emaValues.real.filter((p) => p.value !== null));

    const keltnerRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=KELTNER&limit=${CANDLE_LIMIT}`);
    const keltnerValues = await keltnerRes.json();
    if (keltnerValues.lower) keltnerSeries.setData(keltnerValues.lower.filter((p) => p.value !== null));

    // 과거 매수 시그널 마커 (해당 심볼/시간대의 백테스트 트레이드 진입점)
    const result = state.stats && state.stats[state.symbol] && state.stats[state.symbol][state.timeframe];
    if (result && result.recent_trades) {
      const markers = result.recent_trades.map((t) => ({
        time: Math.floor(new Date(t.entry_time.replace(" ", "T") + "Z").getTime() / 1000),
        position: "belowBar", color: "#26a69a", shape: "arrowUp", text: "BUY",
      }));
      seriesMarkers.setMarkers(markers);
    }
  }

  // ------------------------------------------------------------------
  // 백테스트 성적표
  // ------------------------------------------------------------------
  async function loadStats() {
    const res = await fetch("/api/strategy/stats");
    state.stats = await res.json();
    renderScorecard();
    renderDetail(state.selected.symbol, state.selected.timeframe);
  }

  function renderScorecard() {
    const meta = state.stats._meta;
    if (!meta) {
      el.statsMeta.textContent = "아직 백테스트 성적이 계산되지 않았습니다. build_stats.py를 실행하세요.";
      el.scorecardTable.innerHTML = "";
      return;
    }
    el.statsMeta.textContent =
      `생성: ${meta.generated_at} · 학습 ${meta.train_start}~${meta.train_end} · 검증 ~${meta.validation_end} · 전략: ${meta.strategy}`;

    const symbols = Object.keys(state.stats).filter((k) => k !== "_meta");
    const timeframes = TIMEFRAMES.filter((tf) => symbols.some((s) => state.stats[s][tf]));

    let html = "<tr><th>심볼</th>" + timeframes.map((tf) => `<th>${tf}</th>`).join("") + "</tr>";
    symbols.forEach((sym) => {
      html += `<tr><td>${sym}</td>`;
      timeframes.forEach((tf) => {
        const r = state.stats[sym][tf];
        if (!r || r.error) { html += "<td>-</td>"; return; }
        const v = r.validation;
        const cls = v.total_r > 0 ? "up" : v.total_r < 0 ? "down" : "";
        const isSelected = sym === state.selected.symbol && tf === state.selected.timeframe;
        html += `<td class="clickable ${cls}${isSelected ? " selected" : ""}" data-symbol="${sym}" data-timeframe="${tf}">` +
          `win ${(v.win_rate ?? 0) * 100 | 0}% / R ${v.total_r ?? 0} (${v.trades ?? 0}건)</td>`;
      });
      html += "</tr>";
    });
    el.scorecardTable.innerHTML = html;

    el.scorecardTable.querySelectorAll("td.clickable").forEach((td) => {
      td.addEventListener("click", () => {
        state.selected = { symbol: td.dataset.symbol, timeframe: td.dataset.timeframe };
        renderScorecard();
        renderDetail(state.selected.symbol, state.selected.timeframe);
      });
    });
  }

  function renderDetail(symbol, timeframe) {
    el.detailLabel.textContent = `${symbol} ${timeframe}`;
    const result = state.stats[symbol] && state.stats[symbol][timeframe];
    if (!result || result.error) {
      el.splitTable.innerHTML = el.yearlyTable.innerHTML = "<tr><td>데이터 없음</td></tr>";
      el.backtestSignalsTable.innerHTML = "";
      return;
    }

    const cols = ["trades", "win_rate", "total_r", "avg_r", "best_r", "worst_r"];
    const colLabels = { trades: "건수", win_rate: "승률", total_r: "총 R", avg_r: "평균 R", best_r: "최고 R", worst_r: "최악 R" };

    let splitHtml = "<tr><th>구간</th>" + cols.map((c) => `<th>${colLabels[c]}</th>`).join("") + "</tr>";
    [["전체", result.overall], ["학습", result.train], ["검증", result.validation]].forEach(([label, s]) => {
      splitHtml += `<tr><td>${label}</td>` + cols.map((c) => `<td class="${cellClass(c, s[c])}">${cellText(c, s[c])}</td>`).join("") + "</tr>";
    });
    el.splitTable.innerHTML = splitHtml;

    let yearlyHtml = "<tr><th>연도</th>" + cols.map((c) => `<th>${colLabels[c]}</th>`).join("") + "</tr>";
    Object.keys(result.yearly).sort().forEach((year) => {
      const s = result.yearly[year];
      yearlyHtml += `<tr><td>${year}</td>` + cols.map((c) => `<td class="${cellClass(c, s[c])}">${cellText(c, s[c])}</td>`).join("") + "</tr>";
    });
    el.yearlyTable.innerHTML = yearlyHtml;

    let signalsHtml = "<tr><th>진입시각</th><th>진입가</th><th>손절</th><th>익절</th><th>결과</th><th>R</th></tr>";
    (result.recent_trades || []).slice().reverse().forEach((t) => {
      signalsHtml += `<tr><td>${t.entry_time}</td><td>${fmt(t.entry_price)}</td><td>${fmt(t.stop_price)}</td>` +
        `<td>${fmt(t.target_price)}</td><td>${t.exit_reason}</td><td class="${t.r_multiple >= 0 ? "up" : "down"}">${t.r_multiple}</td></tr>`;
    });
    el.backtestSignalsTable.innerHTML = signalsHtml;
  }

  function cellClass(col, value) {
    if (!["total_r", "avg_r", "best_r", "worst_r"].includes(col)) return "";
    return value > 0 ? "up" : value < 0 ? "down" : "";
  }

  function cellText(col, value) {
    if (value === undefined) return "-";
    if (col === "win_rate") return `${Math.round(value * 100)}%`;
    return value;
  }

  // ------------------------------------------------------------------
  // 실시간 감지된 시그널 (봇 가동 이후)
  // ------------------------------------------------------------------
  async function loadLiveSignals() {
    const res = await fetch("/api/strategy/signals/recent?limit=30");
    const signals = await res.json();
    if (!signals.length) {
      el.liveSignalsTable.innerHTML = "<tr><td>아직 감지된 시그널이 없습니다 (봇을 켜두면 여기 쌓입니다)</td></tr>";
      return;
    }
    let html = "<tr><th>시각</th><th>심볼</th><th>시간대</th><th>진입가</th><th>자동매매</th><th>상태</th></tr>";
    signals.forEach((s) => {
      const status = s.trade ? `${s.trade.status}${s.trade.realized_pnl_usdt != null ? ` (${s.trade.realized_pnl_usdt} USDT)` : ""}` : "-";
      html += `<tr><td>${s.timestamp}</td><td>${s.symbol}</td><td>${s.timeframe}</td><td>${fmt(s.entry_price)}</td>` +
        `<td>${s.auto_traded === "YES" ? "✅" : "-"}</td><td>${status}</td></tr>`;
    });
    el.liveSignalsTable.innerHTML = html;
  }

  // ------------------------------------------------------------------
  // 초기화
  // ------------------------------------------------------------------
  async function onContextChanged() {
    renderTabs();
    await loadLiveStatus();
    await loadChart();
  }

  async function init() {
    renderTabs();
    initChart();
    await loadStats(); // 차트의 시그널 마커가 state.stats를 참조하므로 먼저 로드
    await loadChart();
    await loadLiveStatus();
    await loadLiveSignals();
    setInterval(loadLiveStatus, 15000);
  }

  init();
})();
