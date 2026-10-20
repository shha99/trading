// AI분석 모델(과거 이벤트 통계) 만들기 — 가끔 손으로 돌려 public/ai-model.json을 갱신한다.
//
//   DART_API_KEY=... node scripts/build-ai-model.mjs [년수=5] [캐시폴더]
//
// 1) DART 공시 목록에서 지난 N년의 이벤트를 모은다(지금은 상장폐지된 회사 포함).
// 2) 종목마다 네이버 일봉을 한 번씩 받아 이벤트 뒤 10거래일 최대 변동폭 등을 잰다.
// 3) 이벤트 종류마다 "이벤트 강도"(최대 변동폭 ÷ (평소 하루 변동성 × √10)) 분포를 백분위로 저장한다.
//    사이트는 지금 종목의 평소 변동성에 이 분포를 곱해 ±20% 이상 움직일 확률을 낸다.
// 4) 검증: 마지막 1년을 빼고 만든 분포로 그 1년을 예측해 실제 적중률과 비교한다(과적합 확인).
// 키는 환경변수로만 받는다 — 저장소에 넣지 말 것. 이 스크립트는 Worker에 배포되지 않는다.
import fs from "node:fs";
import path from "node:path";
import { blockOf, fetchAiHistory, fetchChart, stockMetrics } from "../src/ai.js";

const KEY = process.env.DART_API_KEY;
if (!KEY) { console.error("DART_API_KEY 환경변수가 필요합니다."); process.exit(1); }
const YEARS = Number(process.argv[2] || 5);
const CACHE = process.argv[3] || null;
const COUNT = Math.ceil(YEARS * 250) + 150; // N년 + 평소 변동성 60일 + 여유 (거래일)
const BIG = 0.2;
const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date()).replaceAll("-", "");
const cur = blockOf(today);
const first = cur - Math.ceil((YEARS * 365) / 30);
const log = (...a) => console.error(new Date().toISOString().slice(11, 19), ...a);

async function cachedJson(name, make) {
  const f = CACHE && path.join(CACHE, name);
  if (f && fs.existsSync(f)) return JSON.parse(fs.readFileSync(f, "utf8"));
  const v = await make();
  if (f) fs.writeFileSync(f, JSON.stringify(v));
  return v;
}

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); } }));
  return out;
}

// 1) 이벤트
const blocks = [];
for (let k = first; k <= cur; k++) for (const g of ["b", "i1", "i3"]) blocks.push([k, g]);
let done = 0;
const hist = await pool(blocks, 3, ([k, g]) => cachedJson(`hist-${k}-${g}.json`, async () => {
  for (let attempt = 0; ; attempt++) {
    try { return await fetchAiHistory(KEY, k, g, today, fetch, { delisted: true }); } catch (e) {
      if (attempt >= 3) { log("실패", k, g, e.message); return { events: [], ipo: [], failed: true }; }
      await new Promise((r) => setTimeout(r, 5000 * (attempt + 1)));
    }
  }
}).then((r) => { if (++done % 20 === 0) log(`공시 ${done}/${blocks.length}`); return r; }));
const all = hist.flatMap((h) => h.events).sort((a, b) => a.d.localeCompare(b.d) || a.r.localeCompare(b.r));
const lastSeen = new Map();
const events = [];
const ms = (d) => Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8));
for (const e of all) {
  const key = `${e.t}:${e.c}`;
  const p = lastSeen.get(key);
  lastSeen.set(key, e.d);
  if (p && (ms(e.d) - ms(p)) / 86400000 < 20) continue;
  events.push(e);
}
const ipo = new Map(hist.flatMap((h) => h.ipo).map((x) => [x.c, x]));
log(`이벤트 ${events.length}건, 신규상장 후보 ${ipo.size}종목, 실패 블록 ${hist.filter((h) => h.failed).length}`);

// 2) 주가
const by = new Map();
for (const e of events) { if (!by.has(e.c)) by.set(e.c, { dates: new Set(), listing: false }); by.get(e.c).dates.add(e.d); }
for (const c of ipo.keys()) { if (!by.has(c)) by.set(c, { dates: new Set(), listing: false }); by.get(c).listing = true; }
done = 0;
const stockList = [...by];
const metrics = Object.fromEntries(await pool(stockList, 8, async ([code, want]) => {
  const m = await cachedJson(`m-${COUNT}-${code}.json`, async () => {
    for (let attempt = 0; ; attempt++) {
      try { return stockMetrics(await fetchChart(code, fetch, COUNT), [...want.dates], want.listing, today, COUNT); } catch (e) {
        if (attempt >= 2) return { error: e.message };
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
  });
  if (++done % 200 === 0) log(`주가 ${done}/${stockList.length}`);
  return [code, m];
}));

// 3) 사례
const samples = [];
for (const e of events) {
  const x = metrics[e.c]?.ev?.[e.d];
  if (x?.m?.base) samples.push({ t: e.t, c: e.c, n: e.n, d: e.d, ...x.m, k: x.k });
}
for (const [code, st] of Object.entries(metrics)) {
  for (const l of st.lockups || []) if (l.m?.base) samples.push({ t: `lockup${l.months}`, c: code, n: ipo.get(code)?.n || code, d: l.d, ...l.m, k: l.k });
}
const zOf = (m) => m.move / (m.base * Math.sqrt(10));
const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const h = s.length >> 1; return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2; };
const quantiles = (a) => { const s = [...a].sort((x, y) => x - y); return Array.from({ length: 101 }, (_, i) => Math.round(s[Math.round((i / 100) * (s.length - 1))] * 1000) / 1000); };
const r4 = (x) => (x === null || !Number.isFinite(x) ? null : Math.round(x * 10000) / 10000);
// 분포(백분위)로 확률: z × scale ≥ BIG 인 비율
const probFrom = (zq, scale) => { let c = 0; for (const z of zq) if (z * scale >= BIG) c++; return c / zq.length; };

function summarize(list) {
  const z = list.map(zOf);
  const ctrl = list.filter((x) => x.k?.base);
  const zc = ctrl.map((x) => zOf(x.k));
  const years = {};
  for (const x of list) years[x.d.slice(0, 4)] = (years[x.d.slice(0, 4)] || 0) + 1;
  return {
    n: list.length,
    big: r4(list.filter((x) => x.move >= BIG).length / list.length),
    ctrlBig: ctrl.length >= 5 ? r4(ctrl.filter((x) => x.k.move >= BIG).length / ctrl.length) : null,
    medMove: r4(median(list.map((x) => x.move))),
    lift: zc.length >= 5 ? r4(median(z) / median(zc)) : null,
    up: r4(list.filter((x) => x.ret > 0).length / list.length),
    medBase: r4(median(list.map((x) => x.base))),
    years,
    zq: quantiles(z),
    examples: [...list].sort((a, b) => b.d.localeCompare(a.d)).slice(0, 5).map((x) => ({ n: x.n, c: x.c, d: x.d, move: x.move, ret: x.ret })),
  };
}

const types = {};
const byType = new Map();
for (const s of samples) { if (!byType.has(s.t)) byType.set(s.t, []); byType.get(s.t).push(s); }
for (const [t, list] of byType) types[t] = summarize(list);

// 4) 검증: 마지막 1년을 빼고 만든 분포로 그 1년 예측
const cutoff = String(Number(today.slice(0, 4)) - 1) + today.slice(4);
const bins = Array.from({ length: 10 }, () => ({ n: 0, pred: 0, hit: 0 }));
let brier = 0;
let brierBase = 0;
let nTest = 0;
const trainAll = samples.filter((s) => s.d < cutoff);
const baseRate = trainAll.filter((s) => s.move >= BIG).length / trainAll.length;
for (const [t, list] of byType) {
  const train = list.filter((s) => s.d < cutoff);
  if (train.length < 20) continue;
  const zq = quantiles(train.map(zOf));
  for (const s of list.filter((x) => x.d >= cutoff)) {
    const p = probFrom(zq, s.base * Math.sqrt(10));
    const y = s.move >= BIG ? 1 : 0;
    const b = bins[Math.min(9, Math.floor(p * 10))];
    b.n++; b.pred += p; b.hit += y;
    brier += (p - y) ** 2; brierBase += (baseRate - y) ** 2; nTest++;
  }
}
const validation = {
  train_until: cutoff, tests: nTest,
  brier: r4(brier / nTest), brier_base: r4(brierBase / nTest), // 낮을수록 좋다. base = 모든 종목에 같은 확률(평균)을 준 경우
  bins: bins.map((b, i) => ({ range: `${i * 10}~${i * 10 + 10}%`, n: b.n, predicted: b.n ? r4(b.pred / b.n) : null, actual: b.n ? r4(b.hit / b.n) : null })),
};

const model = {
  built: today, years: YEARS, from: events[0]?.d, to: today, big: BIG, window: 10,
  samples: samples.length, stocks: Object.keys(metrics).length, events: events.length,
  validation, types,
};
const out = new URL("../public/ai-model.json", import.meta.url);
fs.writeFileSync(out, JSON.stringify(model));
log(`저장: ${out.pathname} (${Math.round(fs.statSync(out).size / 1024)}KB) 사례 ${samples.length}`);
console.log(JSON.stringify({ ...model, types: Object.fromEntries(Object.entries(types).map(([t, v]) => [t, { n: v.n, big: v.big, ctrlBig: v.ctrlBig, lift: v.lift, years: v.years }])) }, null, 1));
