// /trading - 실제 투자 가능한 형태로 구성한 매매 현황판. 순수 조회 전용 -
// 이 페이지 자체는 어떤 설정도 바꾸지 않는다(API 키 입력란도 없음). 전부
// 기존 API(/api/health, /api/positions/open, /api/trades, /api/risk/status,
// /api/paper-trading/status)를 재사용한다 - 새 백엔드 엔드포인트 없음.
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
    riskStats: document.getElementById("riskStats"),
    openPositionsMeta: document.getElementById("openPositionsMeta"),
    openPositionsTable: document.getElementById("openPositionsTable"),
    recentTradesTable: document.getElementById("recentTradesTable"),
    paperMeta: document.getElementById("paperMeta"),
    paperStats: document.getElementById("paperStats"),
  };

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

  async function loadAll() {
    await Promise.all([loadHealth(), loadRisk(), loadOpenPositions(), loadRecentTrades(), loadPaperStatus()]);
  }

  loadAll();
  setInterval(loadAll, 15000);
})();
