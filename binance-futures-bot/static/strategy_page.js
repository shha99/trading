// 전략 페이지 - 검증된 전략 3종 전환 가능 (/strategy, /vf 둘 다 이 스크립트를 씀):
//   1) keltner: 200EMA + 켈트너 하단 눌림목 복귀 (자동매매 화이트리스트 대상 - BTCUSDT:1h)
//   2) confluence: 큰 양봉+볼린저 동시 돌파 (본전 이동 트레일링) - BTCUSDT 1시간봉 전용
//   3) wick: 볼린저 꼬리터치 되돌림+RSI 확인 (본전 이동 트레일링) - BTC/ETH 15분·5분봉
// 전부 data/*.json에 학습/검증/연도별로 저장된 백테스트를 보여준다는 점은
// 같지만, 지표(R배수 vs %수익률)·차트 오버레이·실시간 조건판정 유무·자동매매
// 연결 여부가 달라서 PROFILES로 분리해둔다. confluence/wick은 둘 다
// lab_backtest.py의 %수익률 트레이드 스키마를 그대로 쓰므로 공통 로직은
// PCT_PROFILE_DEFAULTS에 모아두고 각자 필요한 부분만 덮어쓴다.
(function () {
  "use strict";

  const LC = window.LightweightCharts;
  const SYMBOLS = ["BTCUSDT", "ETHUSDT"];
  const CANDLE_LIMIT = 320;

  function fmt(n) {
    return n == null ? "-" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 });
  }

  function toUnixSeconds(entryTime) {
    return Math.floor(new Date(entryTime.replace(" ", "T") + "Z").getTime() / 1000);
  }

  function renderSignalsRows(trades, rowFn) {
    return trades.slice().reverse().map(rowFn).join("");
  }

  // confluence/wick 둘 다 lab_backtest.py의 %수익률 트레이드 스키마
  // (trades/win_rate/total_pct/avg_pct_per_trade/best_pct/worst_pct,
  // direction/entry_price/exit_price/exit_reason/pct_return)를 그대로
  // 쓰므로 표/마커/포맷 로직을 한 군데 모아 각 프로필에서 펼쳐(spread) 쓴다.
  const PCT_PROFILE_DEFAULTS = {
    metricCols: ["trades", "win_rate", "total_pct", "avg_pct_per_trade", "best_pct", "worst_pct"],
    colLabels: {
      trades: "건수", win_rate: "승률", total_pct: "총 수익률(%)",
      avg_pct_per_trade: "평균%/거래", best_pct: "최고%", worst_pct: "최악%",
    },
    totalKey: "total_pct",
    formatMetric(col, value) {
      if (value === undefined) return "-";
      if (col === "win_rate") return `${Math.round(value * 100)}%`;
      if (col === "trades") return value;
      return `${value}%`;
    },
    scoreFormat(v) {
      const avg = (v.avg_pct_per_trade ?? 0).toFixed(2);
      return `win ${(v.win_rate ?? 0) * 100 | 0}% / ${avg}%/거래 (${v.trades ?? 0}건)`;
    },
    buildMarkers(trades) {
      return trades.map((t) => t.direction === "LONG"
        ? { time: toUnixSeconds(t.entry_time), position: "belowBar", color: "#26a69a", shape: "arrowUp", text: "LONG" }
        : { time: toUnixSeconds(t.entry_time), position: "aboveBar", color: "#ef5350", shape: "arrowDown", text: "SHORT" });
    },
    renderSignalsTable(trades) {
      let html = "<tr><th>진입시각</th><th>방향</th><th>진입가</th><th>청산가</th><th>결과</th><th>수익률</th></tr>";
      html += renderSignalsRows(trades, (t) =>
        `<tr><td>${t.entry_time}</td><td>${t.direction}</td><td>${fmt(t.entry_price)}</td><td>${fmt(t.exit_price)}</td>` +
        `<td>${t.exit_reason}</td><td class="${t.pct_return >= 0 ? "up" : "down"}">${t.pct_return}%</td></tr>`);
      return html;
    },
  };

  const PROFILES = {
    keltner: {
      key: "keltner",
      title: "전략: 200EMA + 켈트너 하단 눌림목 복귀",
      switchLabel: "켈트너 하단 복귀 (1h)",
      timeframes: ["15m", "1h", "4h", "1d"],
      defaultTimeframe: "1h",
      statsUrl: "/api/strategy/stats",
      dataKey: null, // API가 이미 {symbol:{tf:...}, _meta} 평평한 구조로 줌
      hasLiveStatus: true,
      hasLiveSignals: true,
      overlay: "keltner",
      metricCols: ["trades", "win_rate", "total_r", "avg_r", "best_r", "worst_r"],
      colLabels: { trades: "건수", win_rate: "승률", total_r: "총 R", avg_r: "평균 R", best_r: "최고 R", worst_r: "최악 R" },
      totalKey: "total_r",
      formatMetric(col, value) {
        if (value === undefined) return "-";
        if (col === "win_rate") return `${Math.round(value * 100)}%`;
        return value;
      },
      scoreFormat(v) {
        return `win ${(v.win_rate ?? 0) * 100 | 0}% / R ${v.total_r ?? 0} (${v.trades ?? 0}건)`;
      },
      metaText(meta) {
        return `생성: ${meta.generated_at} · 학습 ${meta.train_start}~${meta.train_end} · 검증 ~${meta.validation_end} · 전략: ${meta.strategy}`;
      },
      buildMarkers(trades) {
        return trades.map((t) => ({
          time: toUnixSeconds(t.entry_time), position: "belowBar", color: "#26a69a", shape: "arrowUp", text: "BUY",
        }));
      },
      renderSignalsTable(trades) {
        let html = "<tr><th>진입시각</th><th>진입가</th><th>손절</th><th>익절</th><th>결과</th><th>R</th></tr>";
        html += renderSignalsRows(trades, (t) =>
          `<tr><td>${t.entry_time}</td><td>${fmt(t.entry_price)}</td><td>${fmt(t.stop_price)}</td>` +
          `<td>${fmt(t.target_price)}</td><td>${t.exit_reason}</td><td class="${t.r_multiple >= 0 ? "up" : "down"}">${t.r_multiple}</td></tr>`);
        return html;
      },
      limitations: [
        "규칙은 <strong>BTC 1시간봉</strong>에서 찾은 것이다. 다른 시간대/종목 성적은 위 표로 직접 확인할 것.",
        "<strong>ETH 1시간봉에서는 검증 구간에서 손실</strong>이었다.",
        "<strong>2022년 하락장에서 -9.8% 손실</strong> — 상승장 편향이 있는 전략이다.",
        "표본이 적어 우연일 가능성이 남는다. 114개 조합을 시험해 고른 것이라 <strong>다중검정/과최적화 편향</strong>도 있다.",
        "여기 구현된 시그널은 참고용 스크리닝 도구이며, 매매 추천이나 투자 조언이 아니다.",
      ],
    },

    confluence: {
      key: "confluence",
      dataKey: "big_candle_bollinger_confluence",
      title: "전략: 큰 양봉+볼린저 동시 돌파 (본전 이동 트레일링)",
      switchLabel: "콘플루언스 (1h)",
      timeframes: ["1h"],
      defaultTimeframe: "1h",
      statsUrl: "/api/lab/validated-stats",
      hasLiveStatus: false,
      hasLiveSignals: false,
      overlay: "confluence",
      entryDescription:
        "같은 봉에서 ① 큰 양봉 돌파(종가>50EMA, 몸통이 최근 20봉 평균의 2배 이상) 와 ② 볼린저 상단 돌파가 " +
        "동시에 나올 때만 롱 진입(이중 확인). 청산은 손절 -3×ATR → 가격이 +0.5×ATR 이익을 보면 본전(진입가)으로 " +
        "손절선 이동 → 이후 고점 -0.3×ATR 트레일링(익절 상한 없음). BTCUSDT 1시간봉 학습·검증 구간 모두 견조하지만 " +
        "ETHUSDT 1시간봉은 검증구간이 마이너스라 BTC 1시간봉 한정으로만 쓴다. 자동매매 엔진에는 아직 연결돼 있지 " +
        "않다(문서/정책 수준 - 실제 주문 실행 없음).",
      ...PCT_PROFILE_DEFAULTS,
      metaText(meta) {
        return `생성: ${meta.generated_at} · 학습 ${meta.train_start}~${meta.train_end} · 검증 ~${meta.validation_end} · ` +
          `전략: big_candle_bollinger_confluence (본전 이동 트레일링)`;
      },
      limitations: [
        "이 전략은 <strong>BTCUSDT 1시간봉</strong>에서 검증됐다(5년 이상 히스토리, 학습·검증 구간 모두 플러스).",
        "<strong>ETHUSDT 1시간봉은 검증 구간이 마이너스</strong>라 BTC 1시간봉 한정으로만 쓴다.",
        "백테스트는 <strong>슬리피지(체결 지연/갭)를 반영하지 않는다</strong> — 실제 최악 단일 거래 손실은 2022-05 " +
          "LUNA 사태급 변동성에서 -38.56%까지 나온 적이 있다(이론상 손절폭보다 훨씬 큼).",
        "<strong>아직 자동매매 엔진(app/signal_engine.py)에 연결돼 있지 않다</strong> — 화이트리스트는 여전히 켈트너 " +
          "전략(BTCUSDT:1h) 기본값 그대로다.",
        "여기 구현된 시그널은 참고용 스크리닝 도구이며, 매매 추천이나 투자 조언이 아니다.",
      ],
    },

    wick: {
      key: "wick",
      dataKey: "bollinger_wick_breakeven_trail",
      title: "전략: 볼린저 꼬리터치 되돌림 (본전 이동 트레일링)",
      switchLabel: "볼린저 꼬리터치 되돌림 (15m/5m)",
      timeframes: ["15m", "5m"],
      defaultTimeframe: "15m",
      statsUrl: "/api/lab/validated-stats",
      hasLiveStatus: false,
      hasLiveSignals: false,
      overlay: "bollinger",
      entryDescription:
        "볼린저 밴드(20봉, ±2σ)에 꼬리가 '신선하게'(직전 봉엔 안 닿았다가 이번 봉에 처음) 닿고, 같은 봉의 " +
        "RSI(14)도 과매도(≤40)/과매수(≥60)일 때만 반대 방향으로 진입 — 하단 터치+과매도 → 롱, 상단 터치+과매수 " +
        "→ 숏. 청산은 손절 -3×ATR → 가격이 +0.5×ATR 이익을 보면 본전(진입가)으로 손절선 이동 → 이후 고점/저점 " +
        "±0.3×ATR 트레일링(익절 상한 없음). BTC/ETH·15분/5분봉·5년 이상 학습·검증 구간 전부 견조하게 검증됐지만, " +
        "자동매매 엔진에는 아직 연결돼 있지 않다(문서/정책 수준 - 실제 주문 실행 없음).",
      ...PCT_PROFILE_DEFAULTS,
      metaText(meta) {
        return `생성: ${meta.generated_at} · 학습 ${meta.train_start}~${meta.train_end} · 검증 ~${meta.validation_end} · ` +
          `전략: bollinger_wick_breakeven_trail (RSI 확인 + 본전 이동 트레일링)`;
      },
      limitations: [
        "이 전략은 <strong>BTCUSDT/ETHUSDT 15분·5분봉</strong>에서 검증됐다(5년 이상 히스토리, 학습·검증 구간 모두 플러스).",
        "진입에 <strong>RSI(14) 과매도(≤40)/과매수(≥60) 확인</strong>이 추가돼(수수료가 고정값이라 시그널 품질을 " +
          "높이는 쪽으로 개선) 거래 수는 줄었지만 승률·거래당 수익률이 네 조합 전부에서 개선됐다.",
        "백테스트는 <strong>슬리피지(체결 지연/갭)를 반영하지 않는다</strong> — 본전 이동 전에 갭으로 손절선이 뚫리면 " +
          "이론상 손절폭보다 훨씬 큰 손실이 날 수 있다(스트레스 테스트 최악 사례 -18.6%~-43.8%).",
        "거래 표본이 수천~수만 건이라 '원금 100% 복리'로 계산하면 숫자가 비현실적으로 부푼다 — 실전에서는 거래당 " +
          "계좌 자본의 일부(1~5%)만 리스크에 거는 자금관리가 필수다.",
        "<strong>펀딩비(포지션을 8시간 정산 시점 너머로 들고 갈 때 붙는 비용)는 평균 보유시간이 짧아(중앙값 " +
          "10~30분) 주된 위험 요인이 아님을 확인했다</strong> — 진짜 병목은 여전히 거래 1건당 수수료다.",
        "<strong>아직 자동매매 엔진(app/signal_engine.py)에 연결돼 있지 않다</strong> — 화이트리스트는 여전히 켈트너 " +
          "전략(BTCUSDT:1h) 기본값 그대로고, 이 전략의 '본전 이동 트레일링' 청산은 진입 후에도 손절 주문을 계속 " +
          "옮겨줘야 해서 별도의 포지션 감시 루프가 필요하다.",
        "여기 구현된 시그널은 참고용 스크리닝 도구이며, 매매 추천이나 투자 조언이 아니다.",
      ],
    },
  };

  // /vf 페이지처럼 기본 탭을 켈트너가 아닌 다른 전략으로 열고 싶으면, HTML에서
  // 스크립트 로드 전에 window.DEFAULT_STRATEGY_TAB = "wick" 같은 값을 지정한다.
  const INITIAL_STRATEGY = (window.DEFAULT_STRATEGY_TAB && PROFILES[window.DEFAULT_STRATEGY_TAB]) ? window.DEFAULT_STRATEGY_TAB : "keltner";
  const INITIAL_TIMEFRAME = PROFILES[INITIAL_STRATEGY].defaultTimeframe;
  const state = {
    activeStrategy: INITIAL_STRATEGY,
    symbol: "BTCUSDT",
    timeframe: INITIAL_TIMEFRAME,
    stats: null,
    selected: { symbol: "BTCUSDT", timeframe: INITIAL_TIMEFRAME },
  };

  function currentProfile() {
    return PROFILES[state.activeStrategy];
  }

  const el = {
    brandTitle: document.getElementById("brandTitle"),
    strategyTabs: document.getElementById("strategyTabs"),
    symbolTabs: document.getElementById("symbolTabs"),
    timeframeTabs: document.getElementById("timeframeTabs"),
    conditionPanel: document.getElementById("conditionPanel"),
    condTrend: document.getElementById("condTrend"),
    condTrendValue: document.getElementById("condTrendValue"),
    condPullback: document.getElementById("condPullback"),
    condPullbackValue: document.getElementById("condPullbackValue"),
    condReclaim: document.getElementById("condReclaim"),
    condReclaimValue: document.getElementById("condReclaimValue"),
    condSummary: document.getElementById("condSummary"),
    condSummaryValue: document.getElementById("condSummaryValue"),
    infoPanel: document.getElementById("infoPanel"),
    infoPanelValue: document.getElementById("infoPanelValue"),
    chartMain: document.getElementById("chartMain"),
    statsMeta: document.getElementById("statsMeta"),
    scorecardTable: document.getElementById("scorecardTable"),
    detailLabel: document.getElementById("detailLabel"),
    splitTable: document.getElementById("splitTable"),
    yearlyTable: document.getElementById("yearlyTable"),
    backtestSignalsTable: document.getElementById("backtestSignalsTable"),
    liveSignalsTable: document.getElementById("liveSignalsTable"),
    limitationsList: document.getElementById("limitationsList"),
  };

  function renderTabs() {
    const profile = currentProfile();

    el.strategyTabs.innerHTML = "";
    Object.values(PROFILES).forEach((p) => {
      const btn = document.createElement("button");
      btn.textContent = p.switchLabel;
      if (p.key === state.activeStrategy) btn.classList.add("active");
      btn.onclick = () => switchStrategy(p.key);
      el.strategyTabs.appendChild(btn);
    });

    el.symbolTabs.innerHTML = "";
    SYMBOLS.forEach((sym) => {
      const btn = document.createElement("button");
      btn.textContent = sym.replace("USDT", "");
      if (sym === state.symbol) btn.classList.add("active");
      btn.onclick = () => { state.symbol = sym; onContextChanged(); };
      el.symbolTabs.appendChild(btn);
    });

    el.timeframeTabs.innerHTML = "";
    profile.timeframes.forEach((tf) => {
      const btn = document.createElement("button");
      btn.textContent = tf;
      if (tf === state.timeframe) btn.classList.add("active");
      btn.onclick = () => { state.timeframe = tf; onContextChanged(); };
      el.timeframeTabs.appendChild(btn);
    });
  }

  function renderLimitations() {
    el.limitationsList.innerHTML = currentProfile().limitations.map((li) => `<li>${li}</li>`).join("");
  }

  async function switchStrategy(key) {
    if (state.activeStrategy === key) return;
    state.activeStrategy = key;
    const profile = currentProfile();

    document.title = profile.title;
    el.brandTitle.textContent = profile.title;
    if (!profile.timeframes.includes(state.timeframe)) state.timeframe = profile.defaultTimeframe;
    state.selected = { symbol: state.symbol, timeframe: state.timeframe };

    renderTabs();
    renderLimitations();
    togglePanels();
    await loadStats(); // 차트 마커가 state.stats를 참조하므로 먼저 로드
    await loadChart();
    if (profile.hasLiveStatus) await loadLiveStatus();
    if (profile.hasLiveSignals) await loadLiveSignals();
    else renderNoLiveSignals();
  }

  function togglePanels() {
    const profile = currentProfile();
    el.conditionPanel.style.display = profile.hasLiveStatus ? "" : "none";
    el.infoPanel.style.display = profile.hasLiveStatus ? "none" : "";
    if (!profile.hasLiveStatus) el.infoPanelValue.textContent = profile.entryDescription;
  }

  // ------------------------------------------------------------------
  // 진입조건 패널 (켈트너 전용 - 실시간 조건판정 API가 이 전략에만 있음)
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

  // ------------------------------------------------------------------
  // 차트: 캔들 + (켈트너: 200EMA+하단 / 볼린저: 상하단 밴드) + 과거 트레이드 마커
  // ------------------------------------------------------------------
  let chart, candleSeries, seriesMarkers, emaSeries, keltnerSeries, bbUpperSeries, bbLowerSeries;

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
    bbUpperSeries = chart.addSeries(LC.LineSeries, { color: "#c678dd", lineWidth: 1, title: "볼린저 상단" });
    bbLowerSeries = chart.addSeries(LC.LineSeries, { color: "#61afef", lineWidth: 1, title: "볼린저 하단" });
    seriesMarkers = LC.createSeriesMarkers(candleSeries, []);
    new ResizeObserver(() => chart.resize(el.chartMain.clientWidth, el.chartMain.clientHeight)).observe(el.chartMain);
  }

  async function loadChart() {
    const profile = currentProfile();
    const candlesRes = await fetch(`/api/candles?symbol=${state.symbol}&timeframe=${state.timeframe}&limit=${CANDLE_LIMIT}`);
    const candles = await candlesRes.json();
    candleSeries.setData(candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));

    if (profile.overlay === "keltner") {
      emaSeries.applyOptions({ title: "200EMA" });
      bbUpperSeries.setData([]);
      bbLowerSeries.setData([]);
      const emaRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=EMA&limit=${CANDLE_LIMIT}&params=${encodeURIComponent(JSON.stringify({ timeperiod: 200 }))}`);
      const emaValues = await emaRes.json();
      if (emaValues.real) emaSeries.setData(emaValues.real.filter((p) => p.value !== null));

      const keltnerRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=KELTNER&limit=${CANDLE_LIMIT}`);
      const keltnerValues = await keltnerRes.json();
      if (keltnerValues.lower) keltnerSeries.setData(keltnerValues.lower.filter((p) => p.value !== null));
    } else if (profile.overlay === "confluence") {
      // 콘플루언스 전략의 두 진입 조건(50EMA 위 + 볼린저 상단 돌파)을 그대로 오버레이
      emaSeries.applyOptions({ title: "50EMA" });
      keltnerSeries.setData([]);
      const emaRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=EMA&limit=${CANDLE_LIMIT}&params=${encodeURIComponent(JSON.stringify({ timeperiod: 50 }))}`);
      const emaValues = await emaRes.json();
      if (emaValues.real) emaSeries.setData(emaValues.real.filter((p) => p.value !== null));

      const bbRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=BBANDS&limit=${CANDLE_LIMIT}&params=${encodeURIComponent(JSON.stringify({ timeperiod: 20, nbdevup: 2, nbdevdn: 2 }))}`);
      const bbValues = await bbRes.json();
      if (bbValues.upperband) bbUpperSeries.setData(bbValues.upperband.filter((p) => p.value !== null));
      if (bbValues.lowerband) bbLowerSeries.setData(bbValues.lowerband.filter((p) => p.value !== null));
    } else {
      emaSeries.setData([]);
      keltnerSeries.setData([]);
      const bbRes = await fetch(`/api/indicator-values?symbol=${state.symbol}&timeframe=${state.timeframe}&id=BBANDS&limit=${CANDLE_LIMIT}&params=${encodeURIComponent(JSON.stringify({ timeperiod: 20, nbdevup: 2, nbdevdn: 2 }))}`);
      const bbValues = await bbRes.json();
      if (bbValues.upperband) bbUpperSeries.setData(bbValues.upperband.filter((p) => p.value !== null));
      if (bbValues.lowerband) bbLowerSeries.setData(bbValues.lowerband.filter((p) => p.value !== null));
    }

    // 과거 트레이드 마커(진입점) - 해당 심볼/시간대의 백테스트 결과에서 가져옴
    const result = state.stats && state.stats[state.symbol] && state.stats[state.symbol][state.timeframe];
    seriesMarkers.setMarkers(result && result.recent_trades ? profile.buildMarkers(result.recent_trades) : []);
  }

  // ------------------------------------------------------------------
  // 백테스트 성적표
  // ------------------------------------------------------------------
  async function loadStats() {
    const profile = currentProfile();
    const res = await fetch(profile.statsUrl);
    const raw = await res.json();
    state.stats = profile.dataKey ? { ...(raw[profile.dataKey] || {}), _meta: raw._meta } : raw;
    renderScorecard();
    renderDetail(state.selected.symbol, state.selected.timeframe);
  }

  function renderScorecard() {
    const profile = currentProfile();
    const meta = state.stats._meta;
    if (!meta) {
      el.statsMeta.textContent = "아직 백테스트 성적이 계산되지 않았습니다.";
      el.scorecardTable.innerHTML = "";
      return;
    }
    el.statsMeta.textContent = profile.metaText(meta);

    const symbols = Object.keys(state.stats).filter((k) => k !== "_meta");
    const timeframes = profile.timeframes.filter((tf) => symbols.some((s) => state.stats[s] && state.stats[s][tf]));

    let html = "<tr><th>심볼</th>" + timeframes.map((tf) => `<th>${tf}</th>`).join("") + "</tr>";
    symbols.forEach((sym) => {
      html += `<tr><td>${sym}</td>`;
      timeframes.forEach((tf) => {
        const r = state.stats[sym][tf];
        if (!r || r.error) { html += "<td>-</td>"; return; }
        const v = r.validation;
        const total = v[profile.totalKey] ?? 0;
        const cls = total > 0 ? "up" : total < 0 ? "down" : "";
        const isSelected = sym === state.selected.symbol && tf === state.selected.timeframe;
        html += `<td class="clickable ${cls}${isSelected ? " selected" : ""}" data-symbol="${sym}" data-timeframe="${tf}">` +
          `${profile.scoreFormat(v)}</td>`;
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
    const profile = currentProfile();
    el.detailLabel.textContent = `${symbol} ${timeframe}`;
    const result = state.stats[symbol] && state.stats[symbol][timeframe];
    if (!result || result.error) {
      el.splitTable.innerHTML = el.yearlyTable.innerHTML = "<tr><td>데이터 없음</td></tr>";
      el.backtestSignalsTable.innerHTML = "";
      return;
    }

    const cols = profile.metricCols;
    const colLabels = profile.colLabels;

    let splitHtml = "<tr><th>구간</th>" + cols.map((c) => `<th>${colLabels[c]}</th>`).join("") + "</tr>";
    [["전체", result.overall], ["학습", result.train], ["검증", result.validation]].forEach(([label, s]) => {
      splitHtml += `<tr><td>${label}</td>` + cols.map((c) => `<td class="${cellClass(c, s[c])}">${profile.formatMetric(c, s[c])}</td>`).join("") + "</tr>";
    });
    el.splitTable.innerHTML = splitHtml;

    let yearlyHtml = "<tr><th>연도</th>" + cols.map((c) => `<th>${colLabels[c]}</th>`).join("") + "</tr>";
    Object.keys(result.yearly).sort().forEach((year) => {
      const s = result.yearly[year];
      yearlyHtml += `<tr><td>${year}</td>` + cols.map((c) => `<td class="${cellClass(c, s[c])}">${profile.formatMetric(c, s[c])}</td>`).join("") + "</tr>";
    });
    el.yearlyTable.innerHTML = yearlyHtml;

    el.backtestSignalsTable.innerHTML = profile.renderSignalsTable(result.recent_trades || []);
  }

  function cellClass(col, value) {
    if (col === "trades" || col === "win_rate") return "";
    return value > 0 ? "up" : value < 0 ? "down" : "";
  }

  // ------------------------------------------------------------------
  // 실시간 감지된 시그널 (봇 가동 이후) - 켈트너 전용, 자동매매 엔진에
  // 연결된 유일한 전략이라 이 API도 그 전략의 시그널만 기록한다.
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

  function renderNoLiveSignals() {
    el.liveSignalsTable.innerHTML =
      "<tr><td>이 전략은 아직 자동매매 엔진에 연결돼 있지 않아 실시간 감지 기록이 없습니다 (백테스트 트레이드만 참고).</td></tr>";
  }

  // ------------------------------------------------------------------
  // 초기화
  // ------------------------------------------------------------------
  async function onContextChanged() {
    renderTabs();
    if (currentProfile().hasLiveStatus) await loadLiveStatus();
    await loadChart();
  }

  async function init() {
    const profile = currentProfile();
    document.title = profile.title;
    el.brandTitle.textContent = profile.title;
    renderTabs();
    renderLimitations();
    togglePanels();
    initChart();
    await loadStats(); // 차트의 시그널 마커가 state.stats를 참조하므로 먼저 로드
    await loadChart();
    await loadLiveStatus();
    await loadLiveSignals();
    setInterval(() => { if (currentProfile().hasLiveStatus) loadLiveStatus(); }, 15000);
  }

  init();
})();
