/*
 * analysis.js — 과거 유사국면(analog) 매칭 + 조건부 회귀 엔진 (순수 JS, DOM 의존 없음)
 *
 * 이 파일은 브라우저(아티팩트)와 Node(유닛테스트) 양쪽에서 그대로 동작한다.
 * 모든 "데이터"는 시연용 합성(seeded random) 데이터이며, 실제 시장/거시데이터가
 * 아니다 — 그러나 정렬(look-ahead bias 방지), 레짐 분류, 금리팩터 압축, KNN 매칭,
 * HAC/Huber 회귀, VIF, walk-forward OOS 같은 "로직"은 스펙대로 정확히 구현한다.
 */
(function (root) {
  "use strict";

  // ---------------------------------------------------------------------
  // 0. 유틸: 결정론적 PRNG, 날짜 유틸
  // ---------------------------------------------------------------------

  // mulberry32 — 시드 고정 결정론적 PRNG (매 로드마다 동일한 "합성 역사"를 생성)
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gaussian(rng) {
    // Box-Muller
    let u = 0,
      v = 0;
    while (u === 0) u = rng();
    while (v === 0) v = rng();
    return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  }

  function toISO(d) {
    return d.toISOString().slice(0, 10);
  }

  function addDays(d, n) {
    const r = new Date(d.getTime());
    r.setUTCDate(r.getUTCDate() + n);
    return r;
  }

  // 주말만 제외한 "거래일" 캘린더 (공휴일은 데모 단순화를 위해 무시)
  function businessDays(startISO, endISO) {
    const start = new Date(startISO + "T00:00:00Z");
    const end = new Date(endISO + "T00:00:00Z");
    const out = [];
    let cur = start;
    while (cur <= end) {
      const dow = cur.getUTCDay();
      if (dow !== 0 && dow !== 6) out.push(toISO(cur));
      cur = addDays(cur, 1);
    }
    return out;
  }

  function mean(arr) {
    return arr.reduce((a, b) => a + b, 0) / arr.length;
  }
  function std(arr, m) {
    const mu = m === undefined ? mean(arr) : m;
    const v = arr.reduce((a, b) => a + (b - mu) * (b - mu), 0) / (arr.length - 1);
    return Math.sqrt(Math.max(v, 0));
  }

  // ---------------------------------------------------------------------
  // 1. 데이터 레이어 (합성) — look-ahead-safe 정렬 로직 포함
  // ---------------------------------------------------------------------

  /**
   * 월간 지표(예: CPI)를 합성 생성한다. 각 값은 "기준월"이 아니라 "실제 발표일"에
   * 귀속시키고, 다음 발표 전까지 forward-fill한다 — look-ahead bias 방지의 핵심.
   */
  function synthMonthlySeries(tradingDays, rng, opts) {
    const { startValue, monthlyVol, releaseLagDays, driftFn, floor, ceil } = opts;
    // 월별 기준일(매월 1일) 목록 생성
    const firstDay = tradingDays[0];
    const lastDay = tradingDays[tradingDays.length - 1];
    const months = [];
    let cur = new Date(firstDay.slice(0, 7) + "-01T00:00:00Z");
    const last = new Date(lastDay + "T00:00:00Z");
    while (cur <= last) {
      months.push(toISO(cur));
      cur = new Date(Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 1));
    }

    let value = startValue;
    const releases = []; // {releaseDate, value, periodMonth}
    for (let i = 0; i < months.length; i++) {
      const periodMonth = months[i];
      const drift = driftFn ? driftFn(i, value) : 0;
      value = value + drift + gaussian(rng) * monthlyVol;
      if (floor !== undefined) value = Math.max(floor, value);
      if (ceil !== undefined) value = Math.min(ceil, value);
      const releaseDate = toISO(addDays(new Date(periodMonth + "T00:00:00Z"), 30 + releaseLagDays));
      releases.push({ releaseDate, value, periodMonth });
    }
    return forwardFillToTradingDays(tradingDays, releases);
  }

  function synthWeeklySeries(tradingDays, rng, opts) {
    const { startValue, weeklyVol, releaseLagDays, driftFn, floor } = opts;
    const firstDay = new Date(tradingDays[0] + "T00:00:00Z");
    const lastDay = new Date(tradingDays[tradingDays.length - 1] + "T00:00:00Z");
    const releases = [];
    let value = startValue;
    let cur = firstDay;
    let i = 0;
    while (cur <= lastDay) {
      const drift = driftFn ? driftFn(i, value) : 0;
      value = value + drift + gaussian(rng) * weeklyVol;
      if (floor !== undefined) value = Math.max(floor, value);
      const releaseDate = toISO(addDays(cur, releaseLagDays));
      releases.push({ releaseDate, value, periodMonth: toISO(cur) });
      cur = addDays(cur, 7);
      i++;
    }
    return forwardFillToTradingDays(tradingDays, releases);
  }

  /** 발표일 기준 정렬 + 다음 발표 전까지 forward-fill (look-ahead-safe) */
  function forwardFillToTradingDays(tradingDays, releases) {
    const out = new Array(tradingDays.length).fill(null);
    let ri = 0;
    let currentVal = null;
    for (let ti = 0; ti < tradingDays.length; ti++) {
      const day = tradingDays[ti];
      while (ri < releases.length && releases[ri].releaseDate <= day) {
        currentVal = releases[ri].value;
        ri++;
      }
      out[ti] = currentVal;
    }
    return out;
  }

  function synthDailySeries(tradingDays, rng, opts) {
    const { startValue, dailyVol, driftFn, floor, ceil } = opts;
    let value = startValue;
    const out = new Array(tradingDays.length);
    for (let i = 0; i < tradingDays.length; i++) {
      const drift = driftFn ? driftFn(i, value) : 0;
      value = value + drift + gaussian(rng) * dailyVol;
      if (floor !== undefined) value = Math.max(floor, value);
      if (ceil !== undefined) value = Math.min(ceil, value);
      out[i] = value;
    }
    return out;
  }

  // 알려진 위기 구간 (데모용 예시 라벨 — 실제 날짜와 대략 유사하게 배치했을 뿐,
  // 아래 가격/거시 수치 자체는 모두 합성값이다)
  const KNOWN_CRISIS_WINDOWS = [
    { start: "2000-03-01", end: "2002-10-01", label: "닷컴버블 붕괴 (예시 구간)", troughDepth: -0.45, troughFrac: 0.72, endRetain: 0.7 },
    { start: "2008-09-01", end: "2009-03-01", label: "글로벌 금융위기 (예시 구간)", troughDepth: -0.45, troughFrac: 0.62, endRetain: 0.6 },
    { start: "2020-02-15", end: "2020-04-15", label: "코로나 크래시 (예시 구간)", troughDepth: -0.32, troughFrac: 0.4, endRetain: 0.15 },
    { start: "2022-01-01", end: "2022-10-15", label: "긴축 쇼크 (예시 구간)", troughDepth: -0.24, troughFrac: 0.82, endRetain: 0.85 },
  ];

  function isInCrisisWindow(dateISO) {
    for (const w of KNOWN_CRISIS_WINDOWS) {
      if (dateISO >= w.start && dateISO <= w.end) return w.label;
    }
    return null;
  }

  /**
   * 위기 구간마다 "고점→저점→(부분)회복"의 결정론적 로그수익률 충격 곡선을 만든다.
   * 시드(rng)에 따라 우연히 하락이 안 보이는 경우를 없애기 위해, 하락 자체는
   * 결정론적으로 주입하고 그 위에 랜덤워크 노이즈만 얹는다. betaMult로 지수별
   * 민감도(나스닥 > S&P500 > 다우)를 반영한다.
   */
  function buildCrisisShockIncrements(tradingDays, betaMult) {
    const n = tradingDays.length;
    const cum = new Array(n).fill(0); // 누적 로그충격
    for (const w of KNOWN_CRISIS_WINDOWS) {
      const i0 = tradingDays.findIndex((d) => d >= w.start);
      let i1 = -1;
      for (let i = tradingDays.length - 1; i >= 0; i--) {
        if (tradingDays[i] <= w.end) {
          i1 = i;
          break;
        }
      }
      if (i0 < 0 || i1 < 0 || i1 <= i0) continue;
      const span = i1 - i0;
      const troughIdx = i0 + Math.round(span * w.troughFrac);
      const troughLog = Math.log(1 + w.troughDepth) * betaMult;
      const endLog = Math.log(1 + w.troughDepth * w.endRetain) * betaMult;
      for (let i = i0; i <= i1; i++) {
        let v;
        if (i <= troughIdx) {
          const t = troughIdx === i0 ? 1 : (i - i0) / (troughIdx - i0);
          v = troughLog * t;
        } else {
          const t = i1 === troughIdx ? 1 : (i - troughIdx) / (i1 - troughIdx);
          v = troughLog + (endLog - troughLog) * t;
        }
        cum[i] = v;
      }
      // 구간 종료 후에도 종료 시점 레벨을 유지(다음 구간까지 새로 좁혀지지 않음)
      for (let i = i1 + 1; i < n; i++) {
        if (isInCrisisWindow(tradingDays[i])) break; // 다음 위기 구간이 시작되면 그쪽 로직이 덮어씀
        cum[i] = endLog;
      }
    }
    // 누적 로그충격을 일별 증분으로 변환
    const inc = new Array(n).fill(0);
    let prev = 0;
    for (let i = 0; i < n; i++) {
      inc[i] = cum[i] - prev;
      prev = cum[i];
    }
    return inc;
  }

  /**
   * FEDFUNDS를 레짐 스위칭(hiking/cutting/flat) 프로세스로 합성 생성.
   * 각 국면은 6~24개월 지속. 순수 랜덤워크가 아니라 국면 구조를 갖게 해
   * 레짐 분류기가 감지할 "진짜 신호"가 존재하게 한다.
   */
  function synthFedFunds(tradingDays, rng) {
    const monthsTotal = Math.ceil(tradingDays.length / 21) + 2;
    const monthlyVals = [];
    let level = 3 + rng() * 3; // 시작 3~6%
    let m = 0;
    while (m < monthsTotal) {
      const regime = ["hiking", "cutting", "flat"][Math.floor(rng() * 3)];
      const duration = 6 + Math.floor(rng() * 18);
      const perMonthDrift = regime === "hiking" ? 0.22 : regime === "cutting" ? -0.28 : 0.0;
      for (let k = 0; k < duration && m < monthsTotal; k++, m++) {
        level = level + perMonthDrift + gaussian(rng) * 0.06;
        level = Math.max(0.0, Math.min(9.0, level));
        monthlyVals.push(level);
      }
    }
    // 월별 값을 발표(월말+약 3일 후 FOMC 발표 가정)로 간주해 forward-fill
    const releases = monthlyVals.map((v, i) => ({
      releaseDate: toISO(addDays(new Date(tradingDays[0] + "T00:00:00Z"), i * 30 + 3)),
      value: v,
    }));
    return forwardFillToTradingDays(tradingDays, releases);
  }

  /** FEDFUNDS 추세 기반 레짐 분류: 최근 6개월(약 126거래일) 순변화 */
  function classifyRegime(fedfundsDaily) {
    const WINDOW = 126;
    const out = new Array(fedfundsDaily.length).fill("flat");
    for (let i = 0; i < fedfundsDaily.length; i++) {
      const j = Math.max(0, i - WINDOW);
      const now = fedfundsDaily[i];
      const then = fedfundsDaily[j];
      if (now == null || then == null) continue;
      const chg = now - then;
      if (chg > 0.25) out[i] = "hiking";
      else if (chg < -0.25) out[i] = "cutting";
      else out[i] = "flat";
    }
    return out;
  }

  /** 금리 커브 압축: Level=10Y, Slope=10Y-2Y, Curvature=2*5Y-2Y-10Y */
  function buildRateFactors(y2, y5, y10, y20, y30) {
    const n = y10.length;
    const level = new Array(n),
      slope = new Array(n),
      curvature = new Array(n);
    for (let i = 0; i < n; i++) {
      level[i] = y10[i];
      slope[i] = y10[i] - y2[i];
      curvature[i] = 2 * y5[i] - y2[i] - y10[i];
    }
    return { level, slope, curvature };
  }

  function synthRatesFromFedFunds(tradingDays, fedfunds, rng) {
    const n = tradingDays.length;
    const y2 = new Array(n),
      y5 = new Array(n),
      y10 = new Array(n),
      y20 = new Array(n),
      y30 = new Array(n);
    let termPremium10 = 1.0,
      termPremium20 = 1.6,
      termPremium30 = 1.9,
      curveWiggle5 = 0.3;
    for (let i = 0; i < n; i++) {
      termPremium10 += gaussian(rng) * 0.01;
      termPremium20 += gaussian(rng) * 0.008;
      termPremium30 += gaussian(rng) * 0.007;
      curveWiggle5 += gaussian(rng) * 0.006;
      termPremium10 = Math.max(-0.5, Math.min(3.0, termPremium10));
      termPremium20 = Math.max(termPremium10, Math.min(3.5, termPremium20));
      termPremium30 = Math.max(termPremium20, Math.min(3.8, termPremium30));
      const ff = fedfunds[i] == null ? 3 : fedfunds[i];
      y2[i] = Math.max(0, ff + gaussian(rng) * 0.03 + termPremium10 * 0.15);
      y10[i] = Math.max(0, ff + termPremium10);
      y20[i] = Math.max(y10[i], ff + termPremium20);
      y30[i] = Math.max(y20[i], ff + termPremium30);
      y5[i] = Math.max(0, (y2[i] + y10[i]) / 2 + curveWiggle5 * 0.3);
    }
    return { y2, y5, y10, y20, y30 };
  }

  /**
   * 지수(SPX/IXIC/DJI) 합성 가격 경로 생성. 알려진 위기 구간에는 결정론적
   * 고점-저점-회복 충격 곡선(buildCrisisShockIncrements)을 주입해 시드와 무관하게
   * "위기 라벨과 실제 하락이 항상 일치"하도록 하고, 그 구간의 변동성도 높인다.
   * 나스닥은 베타/변동성을 더 높게 설정해 "성장주일수록 민감" 서사를 만든다.
   */
  function synthIndexPrices(tradingDays, rng, betaMult, volMult, baseDrift, baseVol) {
    const n = tradingDays.length;
    const shock = buildCrisisShockIncrements(tradingDays, betaMult);
    const prices = new Array(n);
    let price = 100;
    for (let i = 0; i < n; i++) {
      const crisis = isInCrisisWindow(tradingDays[i]);
      const vol = crisis ? baseVol * volMult * 1.5 : baseVol * volMult;
      const ret = baseDrift + shock[i] + gaussian(rng) * vol;
      price = price * Math.exp(ret);
      prices[i] = price;
    }
    return prices;
  }

  /**
   * 전체 합성 데이터셋을 만든다. seed 고정 → 항상 동일한 결과.
   */
  function buildSyntheticDataset(seed, startISO, endISO) {
    const rng = mulberry32(seed);
    const tradingDays = businessDays(startISO, endISO);

    const fedfunds = synthFedFunds(tradingDays, rng);
    const regime = classifyRegime(fedfunds);
    const rates = synthRatesFromFedFunds(tradingDays, fedfunds, rng);
    const rateFactors = buildRateFactors(rates.y2, rates.y5, rates.y10, rates.y20, rates.y30);

    const cpi = synthMonthlySeries(tradingDays, rng, {
      startValue: 2.5,
      monthlyVol: 0.18,
      releaseLagDays: 13,
      driftFn: (i, v) => (v > 5 ? -0.05 : v < 1 ? 0.05 : 0),
      floor: -2,
      ceil: 12,
    });
    const ppi = synthMonthlySeries(tradingDays, rng, {
      startValue: 2.0,
      monthlyVol: 0.28,
      releaseLagDays: 12,
      driftFn: (i, v) => (v > 6 ? -0.06 : v < 0 ? 0.06 : 0),
      floor: -4,
      ceil: 15,
    });
    const icsa = synthWeeklySeries(tradingDays, rng, {
      startValue: 300,
      weeklyVol: 14,
      releaseLagDays: 5,
      driftFn: (i, v) => (v > 450 ? -3 : v < 200 ? 3 : 0),
      floor: 150,
    });
    const wti = synthDailySeries(tradingDays, rng, {
      startValue: 55,
      dailyVol: 1.1,
      driftFn: (i, v) => (v > 110 ? -0.15 : v < 20 ? 0.15 : 0),
      floor: 8,
      ceil: 150,
    });
    const dxy = synthDailySeries(tradingDays, rng, {
      startValue: 95,
      dailyVol: 0.28,
      driftFn: (i, v) => (v > 115 ? -0.02 : v < 80 ? 0.02 : 0),
      floor: 70,
      ceil: 130,
    });

    const spx = synthIndexPrices(tradingDays, rng, 1.0, 1.0, 0.0003, 0.0072);
    const ixic = synthIndexPrices(tradingDays, rng, 1.35, 1.15, 0.00038, 0.0072);
    const dji = synthIndexPrices(tradingDays, rng, 0.85, 0.82, 0.00026, 0.0072);

    return {
      tradingDays,
      fedfunds,
      regime,
      rates,
      rateFactors,
      cpi,
      ppi,
      icsa,
      wti,
      dxy,
      prices: { SPX: spx, IXIC: ixic, DJI: dji },
      meta: {
        seed,
        startISO,
        endISO,
        note: "합성(가짜) 데이터 — 실제 시장/거시 데이터 아님",
        seriesAvailableFrom: {
          SPX: startISO,
          IXIC: startISO,
          DJI: startISO,
          CPI: startISO,
          PPI: startISO,
          WTI: startISO,
          DXY: startISO,
          ICSA: startISO,
          FEDFUNDS: startISO,
        },
      },
    };
  }

  // ---------------------------------------------------------------------
  // 2. 파생 변수: 서프라이즈 프록시, 모멘텀, 밸류에이션, 극값 플래그
  // ---------------------------------------------------------------------

  const SURPRISE_MODE_CUTOFF = "1998-01-01"; // 스펙에 명시된 컨센서스 데이터 가용 시점(개념 시연용)

  /** 발표치 - 직전 12개월 이동평균 (컨센서스 서베이 데이터 대체용 프록시) */
  function surpriseProxy(series, windowDays) {
    const w = windowDays || 252;
    const out = new Array(series.length).fill(null);
    for (let i = 0; i < series.length; i++) {
      if (series[i] == null) continue;
      const j0 = Math.max(0, i - w);
      const win = series.slice(j0, i).filter((v) => v != null);
      if (win.length < Math.min(20, w / 4)) continue;
      out[i] = series[i] - mean(win);
    }
    return out;
  }

  function momentum(prices, lookback) {
    const out = new Array(prices.length).fill(null);
    for (let i = 0; i < prices.length; i++) {
      const j = i - lookback;
      if (j < 0) continue;
      out[i] = prices[i] / prices[j] - 1;
    }
    return out;
  }

  function maDeviation(prices, window) {
    const out = new Array(prices.length).fill(null);
    let sum = 0;
    for (let i = 0; i < prices.length; i++) {
      sum += prices[i];
      if (i >= window) sum -= prices[i - window];
      if (i >= window - 1) {
        const ma = sum / window;
        out[i] = prices[i] / ma - 1;
      }
    }
    return out;
  }

  /** 변화량 분포 기준 ±3시그마 또는 상하위 1% 이탈 시 통계적 극값으로 플래그 */
  function flagStatisticalExtremes(series) {
    const changes = [];
    for (let i = 1; i < series.length; i++) {
      if (series[i] != null && series[i - 1] != null) changes.push(series[i] - series[i - 1]);
    }
    const mu = mean(changes);
    const sd = std(changes, mu);
    const sorted = [...changes].sort((a, b) => a - b);
    const lo = sorted[Math.floor(sorted.length * 0.01)];
    const hi = sorted[Math.ceil(sorted.length * 0.99) - 1];
    const flags = new Array(series.length).fill(false);
    for (let i = 1; i < series.length; i++) {
      if (series[i] == null || series[i - 1] == null) continue;
      const chg = series[i] - series[i - 1];
      const zExtreme = sd > 0 && Math.abs(chg - mu) / sd > 3;
      const pctExtreme = chg <= lo || chg >= hi;
      flags[i] = zExtreme || pctExtreme;
    }
    return flags;
  }

  // ---------------------------------------------------------------------
  // 3. 표준화, KNN 아날로그 매칭
  // ---------------------------------------------------------------------

  function zscoreFit(values) {
    const clean = values.filter((v) => v != null && Number.isFinite(v));
    const mu = mean(clean);
    const sd = std(clean, mu) || 1e-9;
    return { mean: mu, std: sd };
  }
  function zscoreApply(v, fit) {
    if (v == null) return null;
    return (v - fit.mean) / fit.std;
  }

  /**
   * KNN 아날로그 매칭.
   * @param {number[][]} historyMatrix - [nDates][nFeatures], 표준화 전 원값
   * @param {number[]} scenarioVector - 사용자 시나리오 원값 (같은 순서)
   * @param {object} opts - { k, regimeArr, targetRegime, useRegimeFilter, extremeFlags, penalty }
   */
  function knnMatch(dates, historyMatrix, scenarioVector, opts) {
    const nFeatures = scenarioVector.length;
    const fits = [];
    for (let f = 0; f < nFeatures; f++) {
      fits.push(zscoreFit(historyMatrix.map((row) => row[f])));
    }
    const zScenario = scenarioVector.map((v, f) => zscoreApply(v, fits[f]));

    const candidates = [];
    for (let i = 0; i < historyMatrix.length; i++) {
      if (opts.excludeIdx && opts.excludeIdx.has(i)) continue;
      if (opts.useRegimeFilter && opts.regimeArr[i] !== opts.targetRegime) continue;
      const row = historyMatrix[i];
      if (row.some((v) => v == null)) continue;
      let d2 = 0;
      let valid = true;
      for (let f = 0; f < nFeatures; f++) {
        const z = zscoreApply(row[f], fits[f]);
        if (z == null || zScenario[f] == null) {
          valid = false;
          break;
        }
        d2 += (z - zScenario[f]) * (z - zScenario[f]);
      }
      if (!valid) continue;
      candidates.push({ idx: i, date: dates[i], dist: Math.sqrt(d2) });
    }
    candidates.sort((a, b) => a.dist - b.dist);
    const top = candidates.slice(0, opts.k);
    for (const c of top) {
      const isExtreme = !!(opts.extremeFlags && opts.extremeFlags[c.idx]);
      const crisisLabel = isInCrisisWindow(c.date);
      c.extreme = isExtreme || !!crisisLabel;
      c.crisisLabel = crisisLabel;
      c.regime = opts.regimeArr ? opts.regimeArr[c.idx] : null;
      c.weight = c.extreme ? opts.penalty : 1.0;
    }
    return { neighbors: top, totalCandidates: candidates.length, fits };
  }

  /** 각 analog 시점에서 D+1..D+N까지의 누적수익률 경로(퍼센트, 시작=0) */
  function forwardPathsFor(neighbors, prices, forwardWindow) {
    const paths = [];
    for (const nb of neighbors) {
      const base = prices[nb.idx];
      const path = [];
      let ok = true;
      for (let h = 1; h <= forwardWindow; h++) {
        const j = nb.idx + h;
        if (j >= prices.length) {
          ok = false;
          break;
        }
        path.push(prices[j] / base - 1);
      }
      if (ok) paths.push({ ...nb, path });
    }
    return paths;
  }

  /** 가중 percentile (weight 기반) — 극값 페널티 가중치를 반영한 분포 산출 */
  function weightedPercentile(values, weights, p) {
    const idx = values.map((v, i) => i).sort((a, b) => values[a] - values[b]);
    const totalW = weights.reduce((a, b) => a + b, 0);
    let cum = 0;
    for (const i of idx) {
      cum += weights[i];
      if (cum / totalW >= p) return values[i];
    }
    return values[idx[idx.length - 1]];
  }

  function pathBands(pathsWithWeights, forwardWindow) {
    const bands = { p10: [], p25: [], p50: [], p75: [], p90: [] };
    for (let h = 0; h < forwardWindow; h++) {
      const vals = pathsWithWeights.map((p) => p.path[h]);
      const wts = pathsWithWeights.map((p) => p.weight);
      bands.p10.push(weightedPercentile(vals, wts, 0.1));
      bands.p25.push(weightedPercentile(vals, wts, 0.25));
      bands.p50.push(weightedPercentile(vals, wts, 0.5));
      bands.p75.push(weightedPercentile(vals, wts, 0.75));
      bands.p90.push(weightedPercentile(vals, wts, 0.9));
    }
    return bands;
  }

  // ---------------------------------------------------------------------
  // 4. 선형대수 (OLS / HAC / Huber / VIF에 공용으로 필요)
  // ---------------------------------------------------------------------

  function matTranspose(A) {
    const r = A.length,
      c = A[0].length;
    const T = Array.from({ length: c }, () => new Array(r));
    for (let i = 0; i < r; i++) for (let j = 0; j < c; j++) T[j][i] = A[i][j];
    return T;
  }
  function matMul(A, B) {
    const r = A.length,
      k = A[0].length,
      c = B[0].length;
    const R = Array.from({ length: r }, () => new Array(c).fill(0));
    for (let i = 0; i < r; i++) {
      for (let kk = 0; kk < k; kk++) {
        const a = A[i][kk];
        if (a === 0) continue;
        for (let j = 0; j < c; j++) R[i][j] += a * B[kk][j];
      }
    }
    return R;
  }
  function matVec(A, v) {
    return A.map((row) => row.reduce((s, a, j) => s + a * v[j], 0));
  }

  /** Gauss-Jordan 역행렬 (정방행렬). 특이행렬이면 근사를 위해 작은 릿지를 더한다. */
  function matInverse(A) {
    const n = A.length;
    const M = A.map((row, i) => [...row, ...Array.from({ length: n }, (_, j) => (i === j ? 1 : 0))]);
    for (let col = 0; col < n; col++) {
      let pivot = col;
      for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[pivot][col])) pivot = r;
      if (Math.abs(M[pivot][col]) < 1e-10) {
        M[col][col] += 1e-8; // ridge fallback for near-singular matrices
      } else {
        [M[col], M[pivot]] = [M[pivot], M[col]];
      }
      const pv = M[col][col];
      for (let j = 0; j < 2 * n; j++) M[col][j] /= pv;
      for (let r = 0; r < n; r++) {
        if (r === col) continue;
        const factor = M[r][col];
        if (factor === 0) continue;
        for (let j = 0; j < 2 * n; j++) M[r][j] -= factor * M[col][j];
      }
    }
    return M.map((row) => row.slice(n));
  }

  /** X: [n][k] (첫 컬럼 절편=1 포함), y: [n]. 반환: beta, residuals, fitted, XtXinv */
  function olsFit(X, y) {
    const Xt = matTranspose(X);
    const XtX = matMul(Xt, X);
    const XtXinv = matInverse(XtX);
    const Xty = matVec(Xt, y);
    const beta = matVec(XtXinv, Xty);
    const fitted = X.map((row) => row.reduce((s, x, j) => s + x * beta[j], 0));
    const resid = y.map((yi, i) => yi - fitted[i]);
    return { beta, resid, fitted, XtXinv };
  }

  /**
   * Newey-West HAC 표준오차. lag = 겹치는 window로 인한 자기상관을 반영한
   * Bartlett 커널 lag 수 (보통 forward window - 1 이상 권장).
   */
  function neweyWestSE(X, resid, XtXinv, lag) {
    const n = X.length,
      k = X[0].length;
    // S0 = sum_i u_i^2 x_i x_i'
    const S = Array.from({ length: k }, () => new Array(k).fill(0));
    for (let i = 0; i < n; i++) {
      const u = resid[i];
      for (let a = 0; a < k; a++) for (let b = 0; b < k; b++) S[a][b] += u * u * X[i][a] * X[i][b];
    }
    for (let l = 1; l <= lag; l++) {
      const w = 1 - l / (lag + 1); // Bartlett kernel
      for (let i = l; i < n; i++) {
        const ui = resid[i],
          uj = resid[i - l];
        for (let a = 0; a < k; a++) {
          for (let b = 0; b < k; b++) {
            const term = w * ui * uj * X[i][a] * X[i - l][b];
            S[a][b] += term;
            S[b][a] += term;
          }
        }
      }
    }
    // Var(beta) = (X'X)^-1 S (X'X)^-1
    const mid = matMul(XtXinv, S);
    const V = matMul(mid, XtXinv);
    const se = V.map((row, i) => Math.sqrt(Math.max(row[i], 0)));
    return { se, V };
  }

  /**
   * Student-t 분포의 양측 p-value를 incomplete beta 함수로 직접 계산한다:
   * P(|T| > |t|) = I_x(df/2, 1/2), x = df/(df+t²). (Abramowitz-Stegun 8.25.4)
   * 이 값 자체가 이미 "양측" 확률이므로 별도의 2*(1-p) 변환이 필요 없다 —
   * 예전 구현은 여기에 잘못된 변환을 씌워 p-value가 항상 1로 saturate되는
   * 버그가 있었다 (Node 유닛테스트로 재현 후 수정).
   */
  function tTwoSidedTailProb(t, df) {
    const x = df / (df + t * t);
    return incompleteBeta(x, df / 2, 0.5);
  }
  function incompleteBeta(x, a, b) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    const bt = Math.exp(logGamma(a + b) - logGamma(a) - logGamma(b) + a * Math.log(x) + b * Math.log(1 - x));
    if (x < (a + 1) / (a + b + 2)) return (bt * betacf(x, a, b)) / a;
    return 1 - (bt * betacf(1 - x, b, a)) / b;
  }
  function betacf(x, a, b) {
    const MAXIT = 100,
      EPS = 3e-7;
    let qab = a + b,
      qap = a + 1,
      qam = a - 1;
    let c = 1,
      d = 1 - (qab * x) / qap;
    if (Math.abs(d) < 1e-30) d = 1e-30;
    d = 1 / d;
    let h = d;
    for (let m = 1; m <= MAXIT; m++) {
      const m2 = 2 * m;
      let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
      d = 1 + aa * d;
      if (Math.abs(d) < 1e-30) d = 1e-30;
      c = 1 + aa / c;
      if (Math.abs(c) < 1e-30) c = 1e-30;
      d = 1 / d;
      h *= d * c;
      aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
      d = 1 + aa * d;
      if (Math.abs(d) < 1e-30) d = 1e-30;
      c = 1 + aa / c;
      if (Math.abs(c) < 1e-30) c = 1e-30;
      d = 1 / d;
      const del = d * c;
      h *= del;
      if (Math.abs(del - 1) < EPS) break;
    }
    return h;
  }
  function logGamma(x) {
    const g = 7;
    const c = [
      0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
      12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
    ];
    if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - logGamma(1 - x);
    x -= 1;
    let a = c[0];
    const t = x + g + 0.5;
    for (let i = 1; i < g + 2; i++) a += c[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }
  function twoSidedPValue(t, df) {
    if (!Number.isFinite(t) || df <= 0) return 1;
    const p = tTwoSidedTailProb(Math.abs(t), df);
    return Math.max(0, Math.min(1, p));
  }

  /** Huber IRLS 회귀 — 소수 극단치에 대한 민감도를 낮춘 로버스트 추정 */
  function huberFit(X, y, delta, iters) {
    const n = y.length;
    let w = new Array(n).fill(1);
    let result = olsFit(X, y);
    for (let it = 0; it < (iters || 15); it++) {
      const Xw = X.map((row, i) => row.map((x) => x * Math.sqrt(w[i])));
      const yw = y.map((yi, i) => yi * Math.sqrt(w[i]));
      result = olsFit(Xw, yw);
      const fitted = X.map((row) => row.reduce((s, x, j) => s + x * result.beta[j], 0));
      const resid = y.map((yi, i) => yi - fitted[i]);
      const s = std(resid) || 1e-9;
      w = resid.map((r) => {
        const a = Math.abs(r / s);
        return a <= delta ? 1 : delta / a;
      });
    }
    const fitted = X.map((row) => row.reduce((s, x, j) => s + x * result.beta[j], 0));
    const resid = y.map((yi, i) => yi - fitted[i]);
    return { beta: result.beta, resid, fitted };
  }

  /** VIF: 각 변수를 나머지 변수로 회귀했을 때의 R²로부터 1/(1-R²) */
  function computeVIF(X, colNames) {
    // X는 절편 포함 [n][k]; 변수는 인덱스 1..k-1 (0은 절편)
    const n = X.length,
      k = X[0].length;
    const vifs = [];
    for (let target = 1; target < k; target++) {
      const y = X.map((row) => row[target]);
      const Xother = X.map((row) => [1, ...row.filter((_, j) => j !== 0 && j !== target)]);
      if (Xother[0].length <= 1) {
        vifs.push({ name: colNames[target - 1], vif: 1 });
        continue;
      }
      const fit = olsFit(Xother, y);
      const yMean = mean(y);
      const ssTot = y.reduce((s, v) => s + (v - yMean) * (v - yMean), 0);
      const ssRes = fit.resid.reduce((s, r) => s + r * r, 0);
      const r2 = ssTot > 0 ? 1 - ssRes / ssTot : 0;
      const vif = 1 / Math.max(1e-6, 1 - Math.min(r2, 0.999999));
      vifs.push({ name: colNames[target - 1], vif });
    }
    return vifs;
  }

  function r2Score(y, fitted) {
    const yMean = mean(y);
    const ssTot = y.reduce((s, v) => s + (v - yMean) * (v - yMean), 0);
    const ssRes = y.reduce((s, v, i) => s + (v - fitted[i]) * (v - fitted[i]), 0);
    return ssTot > 0 ? 1 - ssRes / ssTot : 0;
  }

  // ---------------------------------------------------------------------
  // 5. Walk-forward Out-of-sample 검증
  // ---------------------------------------------------------------------

  /**
   * 5년 롤링 학습 -> 다음 1년 예측을 반복. in-sample R²와 OOS R²/적중률(부호 일치)을 비교.
   */
  function walkForwardValidate(X, y, dateIdxYears, trainYears, testYears) {
    const n = y.length;
    const results = { inSampleR2: [], oosR2: [], oosHit: [], folds: 0 };
    const yearsSpan = dateIdxYears[n - 1] - dateIdxYears[0];
    if (yearsSpan < trainYears + testYears) {
      return { inSampleR2: null, oosR2: null, oosHitRate: null, folds: 0, insufficientData: true };
    }
    let startYear = dateIdxYears[0];
    let totalOosSS = 0,
      totalOosSSTot = 0,
      hits = 0,
      hitTotal = 0;
    let inSampleR2s = [];
    while (startYear + trainYears + testYears <= dateIdxYears[n - 1] + 1e-9) {
      const trainEnd = startYear + trainYears;
      const testEnd = trainEnd + testYears;
      const trainIdx = [],
        testIdx = [];
      for (let i = 0; i < n; i++) {
        if (dateIdxYears[i] >= startYear && dateIdxYears[i] < trainEnd) trainIdx.push(i);
        else if (dateIdxYears[i] >= trainEnd && dateIdxYears[i] < testEnd) testIdx.push(i);
      }
      if (trainIdx.length > X[0].length + 5 && testIdx.length > 3) {
        const Xtr = trainIdx.map((i) => X[i]);
        const ytr = trainIdx.map((i) => y[i]);
        const fit = olsFit(Xtr, ytr);
        const fittedTr = Xtr.map((row) => row.reduce((s, x, j) => s + x * fit.beta[j], 0));
        inSampleR2s.push(r2Score(ytr, fittedTr));

        const yMeanTr = mean(ytr);
        for (const i of testIdx) {
          const pred = X[i].reduce((s, x, j) => s + x * fit.beta[j], 0);
          totalOosSS += (y[i] - pred) * (y[i] - pred);
          totalOosSSTot += (y[i] - yMeanTr) * (y[i] - yMeanTr);
          if (Math.sign(pred) === Math.sign(y[i])) hits++;
          hitTotal++;
        }
        results.folds++;
      }
      startYear += testYears;
    }
    const oosR2 = totalOosSSTot > 0 ? 1 - totalOosSS / totalOosSSTot : null;
    return {
      inSampleR2: inSampleR2s.length ? mean(inSampleR2s) : null,
      oosR2,
      oosHitRate: hitTotal ? hits / hitTotal : null,
      folds: results.folds,
      insufficientData: false,
    };
  }

  // ---------------------------------------------------------------------
  // export
  // ---------------------------------------------------------------------
  const Analysis = {
    mulberry32,
    gaussian,
    businessDays,
    mean,
    std,
    buildSyntheticDataset,
    KNOWN_CRISIS_WINDOWS,
    isInCrisisWindow,
    classifyRegime,
    buildRateFactors,
    surpriseProxy,
    momentum,
    maDeviation,
    flagStatisticalExtremes,
    zscoreFit,
    zscoreApply,
    knnMatch,
    forwardPathsFor,
    pathBands,
    weightedPercentile,
    olsFit,
    neweyWestSE,
    huberFit,
    computeVIF,
    r2Score,
    twoSidedPValue,
    walkForwardValidate,
    matInverse,
    matMul,
    matTranspose,
    SURPRISE_MODE_CUTOFF,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = Analysis;
  } else {
    root.Analysis = Analysis;
  }
})(typeof window !== "undefined" ? window : globalThis);
