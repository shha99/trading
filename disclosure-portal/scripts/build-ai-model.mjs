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
import { DILUTION_API, DILUTION_BUCKETS, FLOAT_BUCKETS, blockOf, bucketOf, fetchAiHistory, fetchChart, fetchDilution, fetchFloat, floatBucketOf, stockMetrics } from "../src/ai.js";

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
// 메자닌(CB·BW·EB)·유상증자 발행 규모(희석률). 유상증자 중 RCPS·우선주(기타주식) 발행은 따로 분류.
done = 0;
const sized = events.filter((e) => DILUTION_API[e.t] && e.cc);
await pool(sized, 4, async (e) => {
  const k = await cachedJson(`dil-${e.t}-${e.r}.json`, async () => {
    for (let attempt = 0; ; attempt++) {
      try { return await fetchDilution(KEY, e.t, e.cc, e.r); } catch (err) {
        if (attempt >= 2) return { pct: null, error: err.message };
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
  });
  e.pct = k.pct;
  e.bgd = k.bgd || null;
  e.edd = k.edd || null;
  e.method = k.method || "";
  if (k.rcps) e.t = "rcps";
  if (++done % 500 === 0) log(`발행 규모 ${done}/${sized.length}`);
});
log(`발행 규모 확인 ${sized.filter((e) => e.pct !== null && e.pct !== undefined).length}/${sized.length}`);

// 오버행(잠재 물량)에서 나오는 파생 이벤트
//   conv_start: CB·BW·EB 전환(행사)청구 시작일
//   lockup3p: 제3자배정 증자 신주 1년 보호예수 해제 무렵(결정일 + 13개월로 추정: 납입·상장까지 약 1개월)
const addMonths = (d, n) => new Date(Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1 + n, +d.slice(6, 8))).toISOString().slice(0, 10).replaceAll("-", "");
const thirdParty = (e) => (e.t === "rights" || e.t === "rcps") && /제3자/.test(e.method || "");
const derived = [];
for (const e of sized) {
  if (["cb", "bw", "eb"].includes(e.t) && e.bgd && e.bgd <= today) derived.push({ ...e, t: "conv_start", d: e.bgd, src: e.t });
  if (thirdParty(e)) { const d = addMonths(e.d, 13); if (d <= today) derived.push({ ...e, t: "lockup3p", d, src: e.t }); }
}
events.push(...derived);
log(`파생 이벤트(전환청구 시작·보호예수 해제) ${derived.length}건`);

// 지금 남아 있는 오버행: 전환기간이 안 끝난 CB·BW·EB + 보호예수 해제 전후(±) 제3자배정 신주
const lastSignal = new Map(); // 종목 → 최근 전환청구·가액조정·자기CB매도 공시
for (const e of events) if (["conv_exercise", "refix_adj", "cb_resale"].includes(e.t)) {
  const p = lastSignal.get(e.c) || {};
  if (!p[e.t] || p[e.t] < e.d) lastSignal.set(e.c, { ...p, [e.t]: e.d });
}
const over = new Map();
for (const e of sized) {
  if (e.pct === null || e.pct === undefined) continue;
  let item = null;
  if (["cb", "bw", "eb"].includes(e.t) && (e.edd ? e.edd >= today : e.d >= addMonths(today, -36))) item = { t: e.t, d: e.d, pct: e.pct, bgd: e.bgd, edd: e.edd };
  if (thirdParty(e)) { const rel = addMonths(e.d, 13); if (rel >= addMonths(today, -1)) item = { t: e.t, d: e.d, pct: e.pct, rel, method: e.method }; }
  if (!item) continue;
  if (!over.has(e.c)) over.set(e.c, { c: e.c, n: e.n, m: e.m, total: 0, items: [], ...(lastSignal.get(e.c) ? { sig: lastSignal.get(e.c) } : {}) });
  const o = over.get(e.c);
  o.items.push(item);
  o.total = Math.round((o.total + e.pct) * 100) / 100;
}
const overhang = [...over.values()].sort((a, b) => b.total - a.total).slice(0, 1000);
log(`오버행 종목 ${over.size}개(상위 ${overhang.length} 저장)`);

// 유통주식 비율(지금 상장된 종목만, 가장 최근 정기보고서 기준). 과거 사례에도 지금 비율을 근사값으로 쓴다.
const corpOf = new Map();
const nameOf = new Map();
for (const e of events) if (e.cc && ["코스피", "코스닥"].includes(e.m)) { corpOf.set(e.c, e.cc); nameOf.set(e.c, { n: e.n, m: e.m }); }
done = 0;
const floats = {};
await pool([...corpOf], 4, async ([code, cc]) => {
  const f = await cachedJson(`float-${cc}-${today.slice(0, 6)}.json`, async () => {
    for (let attempt = 0; ; attempt++) {
      try { return (await fetchFloat(KEY, cc, today)) || { none: true }; } catch (err) {
        if (attempt >= 2) return { none: true, error: err.message };
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
  });
  if (!f.none) floats[code] = f.float;
  if (++done % 300 === 0) log(`유통주식 ${done}/${corpOf.size}`);
});
log(`유통주식 비율 ${Object.keys(floats).length}/${corpOf.size}종목`);
for (const o of overhang) {
  if (floats[o.c] > 0) { o.float = floats[o.c]; o.totalFloat = Math.round((o.total / floats[o.c]) * 10) / 10; }
}

// 2) 주가
const by = new Map();
for (const e of events) { if (!by.has(e.c)) by.set(e.c, { dates: new Set(), listing: false }); by.get(e.c).dates.add(e.d); }
for (const c of ipo.keys()) { if (!by.has(c)) by.set(c, { dates: new Set(), listing: false }); by.get(c).listing = true; }
done = 0;
const stockList = [...by];
const metrics = Object.fromEntries(await pool(stockList, 8, async ([code, want]) => {
  const tag = [...want.dates].sort().join("").length + (want.listing ? "L" : "") + [...want.dates].sort().slice(-1)[0];
  const m = await cachedJson(`m-${COUNT}-${code}-${tag}.json`, async () => {
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
  if (x?.m?.base) samples.push({ t: e.t, c: e.c, n: e.n, d: e.d, pct: e.pct ?? null, fl: floats[e.c] ?? null, ...x.m, k: x.k });

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
    medRet: r4(median(list.map((x) => x.ret))),
    medBase: r4(median(list.map((x) => x.base))),
    years,
    zq: quantiles(z),
    examples: [...list].sort((a, b) => b.d.localeCompare(a.d)).slice(0, 5).map((x) => ({ n: x.n, c: x.c, d: x.d, move: x.move, ret: x.ret })),
  };
}

const types = {};
const byType = new Map();
for (const s of samples) { if (!byType.has(s.t)) byType.set(s.t, []); byType.get(s.t).push(s); }
const BUCKETED = new Set([...Object.keys(DILUTION_API), "conv_start", "lockup3p"]); // 발행 규모(희석률) 구간을 나누는 종류
const MIN_BUCKET = 30; // 이보다 적은 구간은 종류 전체 통계를 쓴다
const bucketLabel = ([lo, hi]) => (hi === Infinity ? `${lo}% 이상` : `${lo}~${hi}%`);
for (const [t, list] of byType) {
  types[t] = summarize(list);
  // 유통주식 비율 구간별(모든 종류)
  types[t].fbuckets = FLOAT_BUCKETS.map((b, i) => {
    const sub = list.filter((x) => floatBucketOf(x.fl) === i);
    const label = `유통 ${Math.round(b[0] * 100)}~${Math.min(100, Math.round(b[1] * 100))}%`;
    if (sub.length < MIN_BUCKET) return { label, n: sub.length };
    const { examples, years, ...rest } = summarize(sub);
    return { label, ...rest };
  });
  if (BUCKETED.has(t)) {
    types[t].buckets = DILUTION_BUCKETS.map((b, i) => {
      const sub = list.filter((x) => bucketOf(x.pct) === i);
      if (sub.length < MIN_BUCKET) return { label: bucketLabel(b), n: sub.length };
      const { examples, years, ...rest } = summarize(sub);
      return { label: bucketLabel(b), ...rest };
    });
  }
}

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
  const fz = FLOAT_BUCKETS.map((_, i) => { const sub = train.filter((x) => floatBucketOf(x.fl) === i); return sub.length >= MIN_BUCKET ? quantiles(sub.map(zOf)) : null; });
  const bz = BUCKETED.has(t) ? DILUTION_BUCKETS.map((_, i) => { const sub = train.filter((x) => bucketOf(x.pct) === i); return sub.length >= MIN_BUCKET ? quantiles(sub.map(zOf)) : null; }) : [];
  for (const s of list.filter((x) => x.d >= cutoff)) {
    const p = probFrom(bz[bucketOf(s.pct)] || fz[floatBucketOf(s.fl)] || zq, s.base * Math.sqrt(10));
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

// 유통주식 비율별 근거: 평소 하루 변동성, 이벤트 뒤 ±20% 비율, 이벤트 강도(평소 대비)
const floatStats = FLOAT_BUCKETS.map((b, i) => {
  const sub = samples.filter((x) => floatBucketOf(x.fl) === i);
  const ctrl = sub.filter((x) => x.k?.base);
  return {
    label: `${Math.round(b[0] * 100)}~${Math.min(100, Math.round(b[1] * 100))}%`, n: sub.length,
    stocks: Object.values(floats).filter((f) => floatBucketOf(f) === i).length,
    medBase: r4(median(sub.map((x) => x.base))),
    big: sub.length ? r4(sub.filter((x) => x.move >= BIG).length / sub.length) : null,
    ctrlBig: ctrl.length ? r4(ctrl.filter((x) => x.k.move >= BIG).length / ctrl.length) : null,
    medZ: r4(median(sub.map(zOf))),
  };
});
const model = {
  built: today, years: YEARS, from: events[0]?.d, to: today, big: BIG, window: 10,
  samples: samples.length, stocks: Object.keys(metrics).length, events: events.length,
  validation, types, overhang, floatStats,
  // 종목코드 → [유통주식 비율, 이름, 시장]
  floats: Object.fromEntries(Object.entries(floats).map(([c, f]) => [c, [Math.round(f * 1000) / 1000, nameOf.get(c)?.n || c, nameOf.get(c)?.m || ""]])),
};
const out = new URL("../public/ai-model.json", import.meta.url);
fs.writeFileSync(out, JSON.stringify(model));
log(`저장: ${out.pathname} (${Math.round(fs.statSync(out).size / 1024)}KB) 사례 ${samples.length}`);
console.log(JSON.stringify({ ...model, overhang: overhang.slice(0, 15), types: Object.fromEntries(Object.entries(types).map(([t, v]) => [t, { n: v.n, big: v.big, ctrlBig: v.ctrlBig, lift: v.lift, years: v.years }])) }, null, 1));
