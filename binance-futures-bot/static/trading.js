// /trading - 실제 투자 가능한 형태로 구성한 매매 현황판. 순수 조회 전용 -
// 이 페이지 자체는 어떤 설정도 바꾸지 않는다(API 키 입력란도 없음). 기존
// API(/api/health, /api/positions/open, /api/trades, /api/risk/status,
// /api/paper-trading/status)에 더해, 바이낸스 계좌를 직접 실시간 조회하는
// /api/binance/status도 쓴다(로컬 DB가 아니라 거래소에 직접 물어본 결과 -
// 어느 컴퓨터에서 실제로 매매하든 항상 정확한 실계좌 상태를 보여준다).
(function () {
  "use strict";

  function fmt(n) {
    return n == null ? "-" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 6 });
  }

  function strategyLabel(strategy) {
    return strategy === "bollinger_wick_breakeven_trail" ? "볼린저 꼬리터치+RSI" : "켈트너";
  }

  const el = {
    modeBadge: document.getElementById("modeBadge"),
    modeText: document.getElementById("modeText"),
    keltnerDot: document.getElementById("keltnerDot"),
    keltnerStatusText: document.getElementById("keltnerStatusText"),
    keltnerWhitelist: document.getElementById("keltnerWhitelist"),
    wickDot: document.getElementById("wickDot"),
    wickStatusText: document.getElementById("wickStatusText"),
    wickWhitelist: document.getElementById("wickWhitelist"),
    riskModeMeta: document.getElementById("riskModeMeta"),
    riskStats: document.getElementById("riskStats"),
    openPositionsMeta: document.getElementById("openPositionsMeta"),
    openPositionsTable: document.getElementById("openPositionsTable"),
    recentTradesTable: document.getElementById("recentTradesTable"),
    paperMeta: document.getElementById("paperMeta"),
    paperStats: document.getElementById("paperStats"),
    binanceLiveMeta: document.getElementById("binanceLiveMeta"),
    keltnerRemoteText: document.getElementById("keltnerRemoteText"),
    keltnerRemoteBtn: document.getElementById("keltnerRemoteBtn"),
    wickRemoteText: document.getElementById("wickRemoteText"),
    wickRemoteBtn: document.getElementById("wickRemoteBtn"),
    liveAlgoToggleBtn: document.getElementById("liveAlgoToggleBtn"),
    binanceTradesTable: document.getElementById("binanceTradesTable"),
    positionOverviewMain: document.getElementById("positionOverviewMain"),
    positionOverviewEmpty: document.getElementById("positionOverviewEmpty"),
    accountSummaryMeta: document.getElementById("accountSummaryMeta"),
    accountSummaryStats: document.getElementById("accountSummaryStats"),
  };

  const LC = window.LightweightCharts;
  // 심볼:시간대:방향 키 -> { box, chart, series, priceLines, infoEl } - 매
  // 폴링마다 새로 만들지 않고 재사용한다(차트를 새로 만들면 줌/스크롤
  // 위치가 매번 초기화됨).
  const positionCharts = {};

  function chartKey(p) {
    return `${p.symbol}:${p.timeframe}:${p.side}`;
  }

  function candlesToSeriesData(candles) {
    return candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }));
  }

  function ensurePositionChart(p) {
    const key = chartKey(p);
    if (positionCharts[key]) return positionCharts[key];

    const box = document.createElement("div");
    box.className = "position-chart-box";
    const title = document.createElement("div");
    title.className = "position-chart-title";
    const chartEl = document.createElement("div");
    chartEl.className = "position-chart";
    const infoEl = document.createElement("div");
    infoEl.className = "position-chart-info";
    box.appendChild(title);
    box.appendChild(chartEl);
    box.appendChild(infoEl);
    el.positionOverviewMain.appendChild(box);

    const chart = LC.createChart(chartEl, {
      layout: { background: { color: "#1c2129" }, textColor: "#c9d1d9" },
      grid: { vertLines: { color: "#232b36" }, horzLines: { color: "#232b36" } },
      rightPriceScale: { borderColor: "#2a313c" },
      timeScale: { borderColor: "#2a313c", timeVisible: true, secondsVisible: false },
      autoSize: false,
      width: chartEl.clientWidth || 320,
      height: 260,
    });
    const series = chart.addSeries(LC.CandlestickSeries, {
      upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    new ResizeObserver(() => chart.resize(chartEl.clientWidth || 320, 260)).observe(chartEl);

    const entry = { box, title, chart, series, infoEl, priceLines: [] };
    positionCharts[key] = entry;
    return entry;
  }

  function setPriceLines(entry, lines) {
    entry.priceLines.forEach((pl) => entry.series.removePriceLine(pl));
    entry.priceLines = lines
      .filter((l) => l.price != null)
      .map((l) =>
        entry.series.createPriceLine({
          price: l.price, color: l.color, lineWidth: 1,
          lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: l.title,
        })
      );
  }

  function pnlClass(v) {
    return v == null ? "" : v >= 0 ? "up" : "down";
  }

  // 로컬 기록(진입가/시간대/손절가 - 이 서버가 직접 관리)과 바이낸스 실시간
  // 조회(현재가/미실현손익 - 어느 컴퓨터에서 매매하든 정확함)를 심볼로 합쳐
  // 하나의 포지션 카드로 보여준다. 둘 다 "조회"일 뿐 어느 쪽도 주문을 내지
  // 않는다 - 트레이딩 로직은 전혀 건드리지 않음.
  async function renderPositionOverview(localPositions, liveBySymbol) {
    const liveKeys = new Set(localPositions.map(chartKey));

    Object.keys(positionCharts).forEach((key) => {
      if (!liveKeys.has(key)) {
        positionCharts[key].chart.remove();
        positionCharts[key].box.remove();
        delete positionCharts[key];
      }
    });

    el.positionOverviewEmpty.style.display = localPositions.length ? "none" : "";
    if (!localPositions.length) return;

    await Promise.all(
      localPositions.map(async (p) => {
        const entry = ensurePositionChart(p);
        const live = liveBySymbol[p.symbol] || null;
        const markPrice = live ? live.mark_price : null;
        const unrealizedPnl = live ? live.unrealized_pnl : null;
        const pnlPct =
          markPrice != null
            ? ((p.side === "BUY" || p.side === "LONG" ? 1 : -1) * (markPrice - p.entry_price) / p.entry_price * 100)
            : null;

        entry.title.textContent =
          `${p.symbol} ${p.timeframe} ${p.side === "BUY" ? "LONG" : p.side === "SELL" ? "SHORT" : p.side}` +
          ` (${strategyLabel(p.strategy)})`;

        entry.infoEl.innerHTML =
          `<span>진입가 <b>${fmt(p.entry_price)}</b></span>` +
          `<span>현재가 <b>${fmt(markPrice)}</b></span>` +
          `<span>수량 <b>${fmt(p.quantity)}</b></span>` +
          `<span>손절가 <b>${fmt(p.current_stop_price)}</b></span>` +
          `<span class="${pnlClass(unrealizedPnl)}">미실현손익 <b>${fmt(unrealizedPnl)} USDT${pnlPct != null ? ` (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%)` : ""}</b></span>`;

        try {
          const res = await fetch(`/api/candles?symbol=${p.symbol}&timeframe=${p.timeframe}&limit=100`);
          const candles = await res.json();
          if (!candles.length) return;
          entry.series.setData(candlesToSeriesData(candles));
          setPriceLines(entry, [
            { price: p.entry_price, color: "#ffb74d", title: "진입가" },
            { price: p.current_stop_price, color: "#ef5350", title: "손절가" },
            { price: p.target_price, color: "#26a69a", title: "익절가" },
          ]);
        } catch (e) {
          // 조용히 무시 - 다음 폴링에서 재시도
        }
      })
    );
  }

  async function loadPositionOverview() {
    try {
      const [localRes, liveRes] = await Promise.all([
        fetch("/api/positions/open"),
        fetch("/api/binance/status"),
      ]);
      const localPositions = await localRes.json();
      const live = await liveRes.json();

      const liveBySymbol = {};
      if (live.ready) {
        (live.open_positions || []).forEach((p) => {
          liveBySymbol[p.symbol] = p;
        });
      }
      await renderPositionOverview(localPositions, liveBySymbol);

      if (!live.ready) {
        el.accountSummaryMeta.textContent = "설정 안 됨" + (live.reason ? ` (${live.reason})` : "");
        el.accountSummaryStats.innerHTML = "";
        return;
      }
      el.accountSummaryMeta.textContent = live.testnet ? "테스트넷" : "⚠️ 실계좌";
      const a = live.account;
      let html =
        `<div class="mini-stat"><span>총 지갑 잔고</span><b>${fmt(a.total_wallet_balance)} USDT</b></div>` +
        `<div class="mini-stat ${pnlClass(a.total_unrealized_profit)}"><span>미실현 손익</span><b>${fmt(a.total_unrealized_profit)} USDT</b></div>` +
        `<div class="mini-stat"><span>가용 잔고</span><b>${fmt(a.available_balance)} USDT</b></div>`;
      if (a.cumulative_return_pct != null) {
        html += `<div class="mini-stat ${pnlClass(a.cumulative_return_pct)}"><span>누적 수익률</span><b>${a.cumulative_return_pct}%</b></div>`;
      }
      el.accountSummaryStats.innerHTML = html;
    } catch (e) {
      // 조용히 무시 - 다음 폴링에서 재시도
    }
  }

  async function loadHealth() {
    const res = await fetch("/api/health");
    const h = await res.json();

    el.modeBadge.textContent = h.testnet ? "TESTNET" : "실계좌(LIVE)";
    el.modeBadge.className = "mode-badge " + (h.testnet ? "testnet" : "live");
    el.modeText.textContent = h.testnet
      ? "테스트넷 모드 - 가상 자금으로만 체결됩니다."
      : "⚠️ 실계좌 모드 - 실제 돈으로 주문이 나갑니다.";

    setEngine(el.keltnerDot, el.keltnerStatusText, el.keltnerWhitelist, h.auto_trade_enabled, h.auto_trade_whitelist);
    setEngine(el.wickDot, el.wickStatusText, el.wickWhitelist, h.wick_auto_trade_enabled, h.wick_auto_trade_whitelist);

    el.riskModeMeta.textContent =
      h.risk_mode === "percent_balance"
        ? `잔고 비례(복리형) - 매 거래 가용잔고의 ${h.risk_percent_of_balance}%` +
          (h.risk_percent_max_usdt > 0 ? ` (상한 ${fmt(h.risk_percent_max_usdt)} USDT)` : " (상한 없음)")
        : `고정 금액 - 매 거래 ${fmt(h.risk_per_trade_usdt)} USDT`;
  }

  function setEngine(dotEl, textEl, listEl, enabled, whitelist) {
    dotEl.className = "engine-dot " + (enabled ? "on" : "off");
    textEl.textContent = enabled ? "자동매매 ON" : "자동매매 OFF";
    listEl.textContent = whitelist && whitelist.length ? whitelist.join(", ") : "없음";
  }

  async function loadRisk() {
    const res = await fetch("/api/risk/status");
    const r = await res.json();
    const cls = r.todays_realized_pnl_usdt >= 0 ? "up" : "down";
    el.riskStats.innerHTML =
      `<div class="paper-stat"><div class="paper-stat-label">오늘 실현손익</div><div class="paper-stat-value ${cls}">${fmt(r.todays_realized_pnl_usdt)} USDT</div></div>` +
      `<div class="paper-stat"><div class="paper-stat-label">일일 손실 한도</div><div class="paper-stat-value">${fmt(r.daily_loss_limit_usdt)} USDT</div></div>` +
      `<div class="paper-stat"><div class="paper-stat-label">킬스위치</div><div class="paper-stat-value ${r.kill_switch_active ? "down" : "up"}">${r.kill_switch_active ? "🔴 활성 (신규 진입 차단)" : "🟢 정상"}</div></div>`;
  }

  async function loadOpenPositions() {
    const res = await fetch("/api/positions/open");
    const positions = await res.json();
    el.openPositionsMeta.textContent = `${positions.length}건 열려있음`;
    if (!positions.length) {
      el.openPositionsTable.innerHTML = "<tr><td>열린 포지션이 없습니다.</td></tr>";
      return;
    }
    let html = "<tr><th>전략</th><th>심볼</th><th>시간대</th><th>방향</th><th>진입가</th><th>수량</th><th>현재 손절가</th><th>진입시각</th></tr>";
    positions.forEach((p) => {
      html += `<tr><td>${strategyLabel(p.strategy)}</td><td>${p.symbol}</td><td>${p.timeframe}</td><td>${p.side}</td>` +
        `<td>${fmt(p.entry_price)}</td><td>${fmt(p.quantity)}</td><td>${fmt(p.current_stop_price)}</td><td>${p.opened_at || "-"}</td></tr>`;
    });
    el.openPositionsTable.innerHTML = html;
  }

  async function loadRecentTrades() {
    const res = await fetch("/api/trades?limit=30");
    const trades = await res.json();
    if (!trades.length) {
      el.recentTradesTable.innerHTML = "<tr><td>아직 매매 기록이 없습니다.</td></tr>";
      return;
    }
    let html = "<tr><th>전략</th><th>심볼</th><th>시간대</th><th>방향</th><th>진입가</th><th>청산가</th><th>상태</th><th>손익(USDT)</th><th>청산시각</th></tr>";
    trades.forEach((t) => {
      const cls = t.realized_pnl_usdt == null ? "" : t.realized_pnl_usdt >= 0 ? "up" : "down";
      html += `<tr><td>${strategyLabel(t.strategy)}</td><td>${t.symbol}</td><td>${t.timeframe}</td><td>${t.side}</td>` +
        `<td>${fmt(t.entry_price)}</td><td>${fmt(t.exit_price)}</td><td>${t.status}</td>` +
        `<td class="${cls}">${fmt(t.realized_pnl_usdt)}</td><td>${t.closed_at || "-"}</td></tr>`;
    });
    el.recentTradesTable.innerHTML = html;
  }

  async function loadPaperStatus() {
    try {
      const res = await fetch("/api/paper-trading/status");
      const s = await res.json();
      if (!s.ready) {
        el.paperMeta.textContent = "모의투자 계좌를 준비하는 중입니다.";
        el.paperStats.innerHTML = "";
        return;
      }
      const cls = s.return_pct > 0 ? "up" : s.return_pct < 0 ? "down" : "";
      el.paperMeta.textContent = `${s.symbol} ${s.timeframe} · 시작 ${s.started_at} · 거래 ${s.trade_count}건 · 승률 ${s.win_rate != null ? s.win_rate + "%" : "-"}`;
      el.paperStats.innerHTML =
        `<div class="paper-stat"><div class="paper-stat-label">시작 잔고</div><div class="paper-stat-value">${Math.round(s.starting_balance).toLocaleString()}원</div></div>` +
        `<div class="paper-stat"><div class="paper-stat-label">현재 잔고</div><div class="paper-stat-value ${cls}">${Math.round(s.balance).toLocaleString()}원</div></div>` +
        `<div class="paper-stat"><div class="paper-stat-label">누적 수익률</div><div class="paper-stat-value ${cls}">${s.return_pct}%</div></div>`;
    } catch (e) {
      // 조용히 무시 - 다음 폴링에서 재시도
    }
  }

  async function loadBinanceLive() {
    try {
      const res = await fetch("/api/binance/status");
      const s = await res.json();
      if (!s.ready) {
        el.binanceLiveMeta.textContent =
          "설정 안 됨 - 이 서버 환경변수에 BINANCE_API_KEY/SECRET을 넣으면 켜집니다" +
          (s.reason ? ` (${s.reason})` : "");
        el.binanceTradesTable.innerHTML = "";
        return;
      }

      el.binanceLiveMeta.textContent = s.testnet
        ? "테스트넷 계좌 기준 (가상 자금)"
        : "⚠️ 실계좌 기준 (실제 자금)";

      if (!s.recent_trades.length) {
        el.binanceTradesTable.innerHTML = "<tr><td>아직 매매 기록이 없습니다.</td></tr>";
      } else {
        let html = "<tr><th>심볼</th><th>방향</th><th>진입가</th><th>청산가</th><th>수량</th><th>실현손익</th><th>청산시각</th></tr>";
        s.recent_trades.forEach((t) => {
          const cls = t.realized_pnl >= 0 ? "up" : "down";
          html += `<tr><td>${t.symbol}</td><td>${t.side}</td><td>${fmt(t.entry_price)}</td>` +
            `<td>${fmt(t.exit_price)}</td><td>${fmt(t.quantity)}</td><td class="${cls}">${fmt(t.realized_pnl)}</td>` +
            `<td>${t.closed_at ? new Date(t.closed_at).toLocaleString() : "-"}</td></tr>`;
        });
        el.binanceTradesTable.innerHTML = html;
      }
    } catch (e) {
      // 조용히 무시 - 다음 폴링에서 재시도
    }
  }

  function setRemoteButton(textEl, btnEl, enabled) {
    textEl.textContent = enabled ? "🟢 켜짐" : "🔴 꺼짐";
    btnEl.textContent = enabled ? "끄기" : "켜기";
    btnEl.className = "remote-btn " + (enabled ? "is-on" : "is-off");
    btnEl.disabled = false;
    btnEl.dataset.enabled = enabled ? "1" : "0";
  }

  function setLiveAlgoToggle(btnEl, enabled) {
    btnEl.textContent = enabled ? "ON" : "OFF";
    btnEl.className = "live-algo-btn " + (enabled ? "is-on" : "is-off");
    btnEl.disabled = false;
    btnEl.dataset.enabled = enabled ? "1" : "0";
  }

  async function loadRemoteControl() {
    try {
      const res = await fetch("/api/control/status");
      const s = await res.json();
      setRemoteButton(el.keltnerRemoteText, el.keltnerRemoteBtn, s.keltner_enabled);
      setRemoteButton(el.wickRemoteText, el.wickRemoteBtn, s.wick_enabled);
      setLiveAlgoToggle(el.liveAlgoToggleBtn, s.wick_enabled);
    } catch (e) {
      // 조용히 무시 - 다음 폴링에서 재시도
    }
  }

  async function toggleEngine(engine, btnEl) {
    const currentlyEnabled = btnEl.dataset.enabled === "1";
    btnEl.disabled = true;
    try {
      const res = await fetch("/api/control/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ engine, enabled: !currentlyEnabled }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(body.detail || "변경에 실패했습니다 (대시보드 로그인이 필요할 수 있습니다).");
      }
    } catch (e) {
      alert("변경 요청에 실패했습니다 - 네트워크를 확인하세요.");
    }
    await loadRemoteControl();
  }

  el.keltnerRemoteBtn.addEventListener("click", () => toggleEngine("keltner", el.keltnerRemoteBtn));
  el.wickRemoteBtn.addEventListener("click", () => toggleEngine("wick", el.wickRemoteBtn));
  el.liveAlgoToggleBtn.addEventListener("click", () => toggleEngine("wick", el.liveAlgoToggleBtn));

  async function loadAll() {
    await Promise.all([
      loadHealth(), loadRisk(), loadOpenPositions(), loadRecentTrades(),
      loadPaperStatus(), loadBinanceLive(), loadRemoteControl(), loadPositionOverview(),
    ]);
  }

  loadAll();
  setInterval(loadAll, 15000);
})();
