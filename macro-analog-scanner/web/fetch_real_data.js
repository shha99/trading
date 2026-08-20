/*
 * fetch_real_data.js — 실제 시장/거시 데이터를 수집해 analysis.js가 바로 쓸 수 있는
 * 형태의 캐시 JSON을 만든다.
 *
 * 가격: Yahoo Finance 차트 API (SPX/IXIC/DJI)
 * 거시: FRED API (CPI/PPI/WTI/DXY/금리 5종/ICSA/FEDFUNDS)
 *
 * look-ahead bias 방지: 월간/주간 지표는 "기준월/기준주"가 아니라 실제 세상에
 * 공개됐을 것으로 추정되는 발표일(표준 발표 주기 기반 근사치)에 귀속시켜
 * forward-fill한다. FRED의 실시간 vintage(ALFRED) API까지는 쓰지 않았음을
 * README/UI에 명시한다 — 이 근사치의 한계도 정직하게 공개하는 것이 이 툴의 원칙.
 *
 * 사용법: FRED_API_KEY=xxxx node fetch_real_data.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

// .env 간단 로더 (외부 패키지 의존 없이)
function loadEnvFile(p) {
  if (!fs.existsSync(p)) return;
  const lines = fs.readFileSync(p, "utf-8").split("\n");
  for (const line of lines) {
    const m = line.match(/^([A-Z_]+)=(.*)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
  }
}
loadEnvFile(path.join(__dirname, ".env"));

const FRED_API_KEY = process.env.FRED_API_KEY;
if (!FRED_API_KEY) {
  console.error("FRED_API_KEY가 설정되어 있지 않습니다. .env 파일 또는 환경변수로 지정하세요.");
  process.exit(1);
}

const START_DATE = "1990-01-01";
const END_DATE = new Date().toISOString().slice(0, 10);

const YAHOO_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36";

// Node의 내장 fetch(undici)는 HTTPS_PROXY 환경변수를 자동으로 타지 않아 이 환경의
// 아웃바운드 egress 프록시를 우회하지 못한다(그래서 별도 allowlist에 막힘). curl은
// 표준적으로 HTTPS_PROXY를 따르고 이미 정상 동작이 확인됐으므로 서브프로세스로 위임한다.
const { execFileSync } = require("child_process");
function fetchJSON(url, headers) {
  const args = ["-sS", "-m", "30", "--fail"];
  for (const [k, v] of Object.entries(headers || {})) {
    args.push("-H", `${k}: ${v}`);
  }
  args.push(url);
  const out = execFileSync("curl", args, { maxBuffer: 1024 * 1024 * 200 });
  return JSON.parse(out.toString("utf-8"));
}

// ---------------------------------------------------------------------
// 1) 가격 (Yahoo Finance)
// ---------------------------------------------------------------------
async function fetchYahooDaily(symbol) {
  const p1 = Math.floor(new Date(START_DATE + "T00:00:00Z").getTime() / 1000);
  const p2 = Math.floor(Date.now() / 1000);
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?period1=${p1}&period2=${p2}&interval=1d`;
  const data = await fetchJSON(url, { "User-Agent": YAHOO_UA });
  const result = data.chart.result[0];
  const ts = result.timestamp;
  const closes = result.indicators.quote[0].close;
  const dates = [];
  const values = [];
  for (let i = 0; i < ts.length; i++) {
    if (closes[i] == null) continue;
    const d = new Date(ts[i] * 1000);
    dates.push(d.toISOString().slice(0, 10));
    values.push(closes[i]);
  }
  return { dates, values };
}

// ---------------------------------------------------------------------
// 2) 거시 (FRED)
// ---------------------------------------------------------------------
async function fetchFredSeries(seriesId) {
  const url = `https://api.stlouisfed.org/fred/series/observations?series_id=${seriesId}&file_type=json&observation_start=${START_DATE}&observation_end=${END_DATE}&api_key=${FRED_API_KEY}`;
  const data = await fetchJSON(url);
  const out = [];
  for (const obs of data.observations) {
    if (obs.value === ".") continue; // FRED는 결측치를 "."로 표기
    out.push({ date: obs.date, value: parseFloat(obs.value) });
  }
  return out;
}

// 표준 발표 주기 기반 근사 발표 지연일 (실제 ALFRED vintage 데이터는 아님 — 근사치)
const RELEASE_LAG_DAYS = {
  CPIAUCSL: 13, // BLS CPI: 익월 중순 발표
  PPIACO: 13, // BLS PPI: 익월 중순 발표
  ICSA: 5, // DOL 신규실업수당청구: 해당 주 종료 후 목요일 발표 (주간 데이터 기준일+5일 근사)
  FEDFUNDS: 2, // 월평균 유효연방기금금리: 월초 며칠 내 계산 가능(정책발표 자체는 실시간 관측 가능)
  DCOILWTICO: 1,
  DTWEXBGS: 1,
  DGS2: 1,
  DGS5: 1,
  DGS10: 1,
  DGS20: 1,
  DGS30: 1,
};

/** YoY(전년동월대비, %) 시계열로 변환 — CPI/PPI는 레벨 지수라 YoY로 바꿔야 "CPI 상승률"이 된다 */
function toYoY(obs) {
  const byDate = new Map(obs.map((o) => [o.date, o.value]));
  const out = [];
  for (let i = 12; i < obs.length; i++) {
    const cur = obs[i];
    // 정확히 12개월 전 관측치를 찾는다 (월별 시계열이라 인덱스 -12로 충분)
    const prev = obs[i - 12];
    if (prev.value === 0) continue;
    out.push({ date: cur.date, value: (cur.value / prev.value - 1) * 100 });
  }
  return out;
}

function addDaysISO(iso, n) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** 관측치를 (기준일+발표지연) 기준으로 정렬한 뒤, 거래일 캘린더로 forward-fill */
function alignToTradingDays(tradingDays, obs, lagDays) {
  const releases = obs
    .map((o) => ({ releaseDate: addDaysISO(o.date, lagDays), value: o.value }))
    .sort((a, b) => (a.releaseDate < b.releaseDate ? -1 : 1));
  const out = new Array(tradingDays.length).fill(null);
  let ri = 0;
  let cur = null;
  for (let ti = 0; ti < tradingDays.length; ti++) {
    const day = tradingDays[ti];
    while (ri < releases.length && releases[ri].releaseDate <= day) {
      cur = releases[ri].value;
      ri++;
    }
    out[ti] = cur;
  }
  return out;
}

// ---------------------------------------------------------------------
// main
// ---------------------------------------------------------------------
async function main() {
  console.log("가격 데이터 수집 중 (Yahoo Finance)...");
  const [spx, ixic, dji] = await Promise.all([
    fetchYahooDaily("^GSPC"),
    fetchYahooDaily("^IXIC"),
    fetchYahooDaily("^DJI"),
  ]);
  console.log(`  SPX: ${spx.dates[0]} ~ ${spx.dates.at(-1)} (${spx.dates.length}일)`);
  console.log(`  IXIC: ${ixic.dates[0]} ~ ${ixic.dates.at(-1)} (${ixic.dates.length}일)`);
  console.log(`  DJI: ${dji.dates[0]} ~ ${dji.dates.at(-1)} (${dji.dates.length}일)`);

  // 거래일 캘린더는 SPX 기준(가장 이력이 길고 완전함)으로 삼는다
  const tradingDays = spx.dates;
  const spxMap = new Map(spx.dates.map((d, i) => [d, spx.values[i]]));
  const ixicMap = new Map(ixic.dates.map((d, i) => [d, ixic.values[i]]));
  const djiMap = new Map(dji.dates.map((d, i) => [d, dji.values[i]]));

  // 지수별로 결측일은 직전값 forward-fill (거래소별 휴장일 차이 보정)
  function seriesForCalendar(map) {
    const out = new Array(tradingDays.length).fill(null);
    let cur = null;
    for (let i = 0; i < tradingDays.length; i++) {
      if (map.has(tradingDays[i])) cur = map.get(tradingDays[i]);
      out[i] = cur;
    }
    return out;
  }
  const pricesSPX = seriesForCalendar(spxMap);
  const pricesIXIC = seriesForCalendar(ixicMap);
  const pricesDJI = seriesForCalendar(djiMap);

  console.log("\n거시 데이터 수집 중 (FRED)...");
  const seriesIds = ["CPIAUCSL", "PPIACO", "DCOILWTICO", "DTWEXBGS", "DGS2", "DGS5", "DGS10", "DGS20", "DGS30", "ICSA", "FEDFUNDS"];
  const raw = {};
  for (const id of seriesIds) {
    raw[id] = await fetchFredSeries(id);
    console.log(`  ${id}: ${raw[id][0]?.date} ~ ${raw[id].at(-1)?.date} (${raw[id].length}개 관측치)`);
  }

  const cpiYoY = toYoY(raw.CPIAUCSL);
  const ppiYoY = toYoY(raw.PPIACO);

  const cpi = alignToTradingDays(tradingDays, cpiYoY, RELEASE_LAG_DAYS.CPIAUCSL);
  const ppi = alignToTradingDays(tradingDays, ppiYoY, RELEASE_LAG_DAYS.PPIACO);
  const wti = alignToTradingDays(tradingDays, raw.DCOILWTICO, RELEASE_LAG_DAYS.DCOILWTICO);
  const dxy = alignToTradingDays(tradingDays, raw.DTWEXBGS, RELEASE_LAG_DAYS.DTWEXBGS);
  const icsa = alignToTradingDays(tradingDays, raw.ICSA, RELEASE_LAG_DAYS.ICSA);
  const fedfunds = alignToTradingDays(tradingDays, raw.FEDFUNDS, RELEASE_LAG_DAYS.FEDFUNDS);
  const y2 = alignToTradingDays(tradingDays, raw.DGS2, RELEASE_LAG_DAYS.DGS2);
  const y5 = alignToTradingDays(tradingDays, raw.DGS5, RELEASE_LAG_DAYS.DGS5);
  const y10 = alignToTradingDays(tradingDays, raw.DGS10, RELEASE_LAG_DAYS.DGS10);
  const y20 = alignToTradingDays(tradingDays, raw.DGS20, RELEASE_LAG_DAYS.DGS20);
  const y30 = alignToTradingDays(tradingDays, raw.DGS30, RELEASE_LAG_DAYS.DGS30);

  const seriesAvailableFrom = {
    SPX: spx.dates[0],
    IXIC: ixic.dates[0],
    DJI: dji.dates[0],
    CPI: cpiYoY[0]?.date,
    PPI: ppiYoY[0]?.date,
    WTI: raw.DCOILWTICO[0]?.date,
    DXY: raw.DTWEXBGS[0]?.date,
    ICSA: raw.ICSA[0]?.date,
    FEDFUNDS: raw.FEDFUNDS[0]?.date,
    DGS2: raw.DGS2[0]?.date,
    DGS5: raw.DGS5[0]?.date,
    DGS10: raw.DGS10[0]?.date,
    DGS20: raw.DGS20[0]?.date,
    DGS30: raw.DGS30[0]?.date,
  };

  const cache = {
    tradingDays,
    fedfunds,
    rates: { y2, y5, y10, y20, y30 },
    cpi,
    ppi,
    icsa,
    wti,
    dxy,
    prices: { SPX: pricesSPX, IXIC: pricesIXIC, DJI: pricesDJI },
    meta: {
      source: "real",
      fetchedAt: new Date().toISOString(),
      startISO: START_DATE,
      endISO: END_DATE,
      note: "Yahoo Finance(가격) + FRED(거시) 실제 데이터. 월간/주간 지표의 발표일은 표준 발표 주기 기반 근사치(ALFRED 실시간 vintage 데이터 아님).",
      releaseLagDays: RELEASE_LAG_DAYS,
      seriesAvailableFrom,
    },
  };

  const outPath = path.join(__dirname, "real_data_cache.json");
  fs.writeFileSync(outPath, JSON.stringify(cache));
  const stat = fs.statSync(outPath);
  console.log(`\n캐시 저장 완료: ${outPath} (${(stat.size / 1024 / 1024).toFixed(2)} MB)`);
}

main().catch((err) => {
  console.error("실패:", err);
  process.exit(1);
});
