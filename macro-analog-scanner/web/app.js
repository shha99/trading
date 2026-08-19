/* app.js — UI 오케스트레이션 (analysis.js의 순수 로직을 DOM에 연결) */
(function () {
  "use strict";
  const A = window.Analysis;

  // ---------------------------------------------------------------------
  // 상수 / 메타데이터
  // ---------------------------------------------------------------------
  const SEED = 7;
  const START = "1990-01-01";
  const END = new Date().toISOString().slice(0, 10);
  const SURPRISE_CUTOFF = A.SURPRISE_MODE_CUTOFF; // "1998-01-01"

  const INDEX_META = [
    { key: "SPX", label: "S&P 500", color: "--series-spx" },
    { key: "IXIC", label: "Nasdaq", color: "--series-ixic" },
    { key: "DJI", label: "Dow Jones", color: "--series-dji" },
  ];

  const VARIABLES = [
    { key: "cpi", label: "CPI (YoY)", unit: "%", get: (ds) => ds.cpi },
    { key: "ppi", label: "PPI (YoY)", unit: "%", get: (ds) => ds.ppi },
    { key: "wti", label: "WTI 유가", unit: "$", get: (ds) => ds.wti },
    { key: "dxy", label: "달러인덱스", unit: "", get: (ds) => ds.dxy },
    { key: "rate_level", label: "금리 Level (10Y)", unit: "%", get: (ds) => ds.rateFactors.level },
    { key: "rate_slope", label: "금리 Slope (10Y-2Y)", unit: "%p", get: (ds) => ds.rateFactors.slope },
    { key: "rate_curve", label: "금리 Curvature", unit: "%p", get: (ds) => ds.rateFactors.curvature },
    { key: "icsa", label: "신규 실업수당청구", unit: "k", get: (ds) => ds.icsa },
  ];

  const REGIME_LABEL = { hiking: "긴축기", cutting: "완화기", flat: "횡보기" };
  const REGIME_ORDER = ["hiking", "cutting", "flat"];

  // ---------------------------------------------------------------------
  // 전역 상태
  // ---------------------------------------------------------------------
  let DS = null; // 합성 데이터셋
  let SPX_MOM = null,
    SPX_VAL = null; // 항상 포함되는 통제변수(모멘텀/밸류에이션) - SPX 기준
  let EXTREME_FLAGS = {}; // key -> bool[]

  const state = {
    poolMode: "surprise", // 'surprise' | 'level'
    selectedVars: new Set(["cpi", "rate_slope"]),
    varTransform: {}, // key -> 'level' | 'delta'
    scenarioValues: {}, // key -> number
    regimeFilterOn: true,
    targetRegime: "flat",
    excludeCrisis: false,
    forwardWindow: 20,
    analogN: 30,
    extremePenalty: 0.5,
    mode: "analog", // 'analog' | 'regression'
    highlightIndex: null, // null = 전체 표시
  };

  let lastRunResult = null; // 마지막 분석 실행 결과 (차트/테이블이 참조)

  // ---------------------------------------------------------------------
  // 초기화: 합성 데이터셋 생성
  // ---------------------------------------------------------------------
  function boot() {
    DS = A.buildSyntheticDataset(SEED, START, END);
    SPX_MOM = A.momentum(DS.prices.SPX, 63);
    SPX_VAL = A.maDeviation(DS.prices.SPX, 252);

    for (const v of VARIABLES) {
      state.varTransform[v.key] = "level";
      const series = v.get(DS);
      EXTREME_FLAGS[v.key] = A.flagStatisticalExtremes(series);
    }
    for (const idxMeta of INDEX_META) {
      EXTREME_FLAGS["ret_" + idxMeta.key] = A.flagStatisticalExtremes(DS.prices[idxMeta.key]);
    }

    // 현재(마지막 시점) 레짐 자동 분류
    const currentRegime = DS.regime[DS.regime.length - 1];
    state.targetRegime = currentRegime;

    // 시나리오 기본값 = 각 변수의 "현재" 값 (선택된 pool 기준)
    for (const v of VARIABLES) {
      state.scenarioValues[v.key] = lastFeatureValue(v.key);
    }

    document.getElementById("meta-range").textContent = `데이터 구간 ${DS.tradingDays[0]} ~ ${DS.tradingDays.at(-1)} (합성)`;
    document.getElementById("meta-days").textContent = `거래일 수 ${DS.tradingDays.length.toLocaleString()}`;
    // DOM 생성(renderShell)과 최초 분석 실행(runAnalysis)은 ui.js 로드 후
    // index.html 하단 부트스트랩 스크립트에서 순서대로 호출한다.
  }

  // ---------------------------------------------------------------------
  // 피처 시리즈 계산 (pool 모드 + level/delta 변환 적용)
  // ---------------------------------------------------------------------
  function poolStartIndex() {
    if (state.poolMode === "level") return 0;
    return DS.tradingDays.findIndex((d) => d >= SURPRISE_CUTOFF);
  }

  function rawOrSurprise(series) {
    if (state.poolMode === "surprise") return A.surpriseProxy(series, 252);
    return series;
  }

  function featureSeriesFor(varKey) {
    const def = VARIABLES.find((v) => v.key === varKey);
    const base = rawOrSurprise(def.get(DS));
    const transform = state.varTransform[varKey] || "level";
    if (transform === "level") return base;
    const out = new Array(base.length).fill(null);
    for (let i = 1; i < base.length; i++) {
      if (base[i] != null && base[i - 1] != null) out[i] = base[i] - base[i - 1];
    }
    return out;
  }

  function lastFeatureValue(varKey) {
    const s = featureSeriesFor(varKey);
    for (let i = s.length - 1; i >= 0; i--) {
      if (s[i] != null) return round(s[i], 3);
    }
    return 0;
  }

  function round(v, d) {
    const m = Math.pow(10, d);
    return Math.round(v * m) / m;
  }

  // ---------------------------------------------------------------------
  // 분석 실행 (Analog + Regression 둘 다 계산)
  // ---------------------------------------------------------------------
  function runAnalysis() {
    const runBtn = document.getElementById("run-btn");
    if (runBtn) {
      runBtn.disabled = true;
      runBtn.textContent = "계산 중…";
    }
    setStatus("계산 중… (거시국면 탐색 + 회귀 진단)");
    // 무거운 동기 연산 전에 "계산 중" UI가 먼저 페인트되도록 한 틱 양보
    setTimeout(() => {
      try {
        const result = computeAll();
        lastRunResult = result;
        window.__macroUI.renderResults(result);
      } catch (err) {
        console.error(err);
        setStatus("오류: " + err.message);
      } finally {
        if (runBtn) {
          runBtn.disabled = false;
          runBtn.textContent = "분석 실행";
        }
      }
    }, 30);
  }

  function setStatus(text) {
    const el = document.getElementById("status-line");
    if (el) el.textContent = text;
  }

  function computeAll() {
    const selectedVarKeys = Array.from(state.selectedVars);
    const featureKeys = ["mom63", "maDev252", ...selectedVarKeys];

    // pool 필터: poolMode에 따른 시작 인덱스 + (선택) 위기구간 제외
    const startIdx = poolStartIndex();
    const featureSeriesMap = { mom63: SPX_MOM, maDev252: SPX_VAL };
    for (const k of selectedVarKeys) featureSeriesMap[k] = featureSeriesFor(k);

    // 최근 forwardWindow 거래일은 D+N 실현수익률이 아직 존재하지 않으므로(그리고 "오늘"
    // 자신이 스스로의 아날로그로 잡히는 자기매칭을 막기 위해) 후보 풀에서 제외한다.
    const lastEligible = DS.tradingDays.length - state.forwardWindow - 1;
    const poolIdx = []; // -> 원본 ds 인덱스
    for (let i = startIdx; i <= lastEligible; i++) {
      if (state.excludeCrisis && A.isInCrisisWindow(DS.tradingDays[i])) continue;
      poolIdx.push(i);
    }

    const dates = poolIdx.map((i) => DS.tradingDays[i]);
    const regimeArr = poolIdx.map((i) => DS.regime[i]);
    const historyMatrix = poolIdx.map((i) => featureKeys.map((k) => featureSeriesMap[k][i]));
    const combinedExtreme = poolIdx.map((i) => {
      if (selectedVarKeys.some((k) => EXTREME_FLAGS[k] && EXTREME_FLAGS[k][i])) return true;
      return false;
    });

    const scenarioVector = featureKeys.map((k) => {
      if (k === "mom63") return SPX_MOM[SPX_MOM.length - 1];
      if (k === "maDev252") return SPX_VAL[SPX_VAL.length - 1];
      return state.scenarioValues[k];
    });

    // ---- Analog (KNN) ----
    const knn = A.knnMatch(dates, historyMatrix, scenarioVector, {
      k: state.analogN,
      useRegimeFilter: state.regimeFilterOn,
      regimeArr,
      targetRegime: state.targetRegime,
      extremeFlags: combinedExtreme,
      penalty: state.extremePenalty,
    });
    // neighbor.idx 는 poolIdx 로컬 인덱스 -> 원본 인덱스로 변환
    for (const nb of knn.neighbors) nb.origIdx = poolIdx[nb.idx];

    const analogByIndex = {};
    for (const im of INDEX_META) {
      const nbForPaths = knn.neighbors.map((nb) => ({ ...nb, idx: nb.origIdx }));
      const paths = A.forwardPathsFor(nbForPaths, DS.prices[im.key], state.forwardWindow);
      const bands = paths.length ? A.pathBands(paths, state.forwardWindow) : null;
      analogByIndex[im.key] = { paths, bands };
    }

    // 위기구간 포함/제외 비교용 (현재 toggle과 반대 조건으로 한 번 더)
    const poolIdxAlt = [];
    for (let i = startIdx; i <= lastEligible; i++) {
      if (!state.excludeCrisis && A.isInCrisisWindow(DS.tradingDays[i])) continue;
      poolIdxAlt.push(i);
    }
    const datesAlt = poolIdxAlt.map((i) => DS.tradingDays[i]);
    const regimeArrAlt = poolIdxAlt.map((i) => DS.regime[i]);
    const historyMatrixAlt = poolIdxAlt.map((i) => featureKeys.map((k) => featureSeriesMap[k][i]));
    const extremeAlt = poolIdxAlt.map((i) => combinedExtremeAt(i, selectedVarKeys));
    const knnAlt = A.knnMatch(datesAlt, historyMatrixAlt, scenarioVector, {
      k: state.analogN,
      useRegimeFilter: state.regimeFilterOn,
      regimeArr: regimeArrAlt,
      targetRegime: state.targetRegime,
      extremeFlags: extremeAlt,
      penalty: state.extremePenalty,
    });
    for (const nb of knnAlt.neighbors) nb.origIdx = poolIdxAlt[nb.idx];
    const altPaths = A.forwardPathsFor(
      knnAlt.neighbors.map((nb) => ({ ...nb, idx: nb.origIdx })),
      DS.prices.SPX,
      state.forwardWindow
    );
    const withCrisisMeanFinal = mean_(analogSetFinalReturns(analogByIndex.SPX.paths));
    const altMeanFinal = mean_(analogSetFinalReturns(altPaths));
    const crisisComparison = state.excludeCrisis
      ? { current: "제외", currentMean: withCrisisMeanFinal, otherMean: altMeanFinal, otherLabel: "포함 시" }
      : { current: "포함", currentMean: withCrisisMeanFinal, otherMean: altMeanFinal, otherLabel: "제외 시" };

    // ---- Regression (OLS + HAC, 지평별 독립) ----
    const regDesign = buildRegressionDesign(poolIdx, featureKeys, selectedVarKeys, regimeArr);
    const regressionByIndex = {};
    for (const im of INDEX_META) {
      regressionByIndex[im.key] = computeRegressionForIndex(im.key, poolIdx, regDesign, state.forwardWindow);
    }

    // ---- Walk-forward OOS (대표 지평 = D+N, SPX 기준으로 대표 표시 + 지수별도 계산) ----
    const oosByIndex = {};
    for (const im of INDEX_META) {
      oosByIndex[im.key] = computeWalkForward(im.key, poolIdx, regDesign, state.forwardWindow);
    }

    return {
      featureKeys,
      selectedVarKeys,
      knn,
      analogByIndex,
      crisisComparison,
      regDesign,
      regressionByIndex,
      oosByIndex,
      scenarioVector,
      poolSize: poolIdx.length,
    };
  }

  function combinedExtremeAt(i, selectedVarKeys) {
    return selectedVarKeys.some((k) => EXTREME_FLAGS[k] && EXTREME_FLAGS[k][i]);
  }
  function analogSetFinalReturns(paths) {
    return paths.map((p) => p.path[p.path.length - 1]).filter((v) => v != null);
  }
  function mean_(arr) {
    if (!arr.length) return null;
    return arr.reduce((a, b) => a + b, 0) / arr.length;
  }

  /** 회귀 설계행렬 공용 부분(절편+통제+선택변수+레짐더미+상호작용)을 미리 만든다 */
  function buildRegressionDesign(poolIdx, featureKeys, selectedVarKeys, regimeArr) {
    const featureSeriesMap = { mom63: SPX_MOM, maDev252: SPX_VAL };
    for (const k of selectedVarKeys) featureSeriesMap[k] = featureSeriesFor(k);

    const colNames = ["mom63", "maDev252", ...selectedVarKeys, "regime_hiking", "regime_cutting"];
    for (const k of selectedVarKeys) {
      colNames.push(`hiking_x_${k}`);
      colNames.push(`cutting_x_${k}`);
    }

    const rows = [];
    const validRowOrigIdx = [];
    for (let li = 0; li < poolIdx.length; li++) {
      const i = poolIdx[li];
      const regime = regimeArr[li];
      const dHiking = regime === "hiking" ? 1 : 0;
      const dCutting = regime === "cutting" ? 1 : 0;
      const base = [SPX_MOM[i], SPX_VAL[i]];
      let valid = base.every((v) => v != null);
      const varVals = selectedVarKeys.map((k) => featureSeriesMap[k][i]);
      if (varVals.some((v) => v == null)) valid = false;
      if (!valid) continue;
      const row = [1, ...base, ...varVals, dHiking, dCutting];
      for (const v of varVals) {
        row.push(dHiking * v);
        row.push(dCutting * v);
      }
      rows.push(row);
      validRowOrigIdx.push(i);
    }
    return { colNames, rows, validRowOrigIdx };
  }

  function computeRegressionForIndex(indexKey, poolIdx, regDesign, forwardWindow) {
    const prices = DS.prices[indexKey];
    const perHorizon = [];
    // 유효 행 중, D+h가 데이터 범위를 넘지 않는 것만 사용 (지평마다 표본이 약간씩 다름)
    for (let h = 1; h <= forwardWindow; h++) {
      const X = [];
      const y = [];
      for (let r = 0; r < regDesign.rows.length; r++) {
        const origIdx = regDesign.validRowOrigIdx[r];
        const j = origIdx + h;
        if (j >= prices.length) continue;
        X.push(regDesign.rows[r]);
        y.push(prices[j] / prices[origIdx] - 1);
      }
      if (X.length < X[0].length + 10) {
        perHorizon.push(null);
        continue;
      }
      const fit = A.olsFit(X, y);
      const lag = Math.max(1, h - 1);
      const hac = A.neweyWestSE(X, fit.resid, fit.XtXinv, lag);
      const r2 = A.r2Score(y, fit.fitted);
      const n = y.length;
      const k = X[0].length;
      const df = Math.max(1, n - k);
      const tstats = fit.beta.map((b, j) => (hac.se[j] > 0 ? b / hac.se[j] : 0));
      const pvals = tstats.map((t) => A.twoSidedPValue(t, df));
      const yStd = A.std(y);
      perHorizon.push({ h, beta: fit.beta, se: hac.se, tstats, pvals, r2, n, k, resid: fit.resid, yStd });
    }
    return { perHorizon };
  }

  function computeWalkForward(indexKey, poolIdx, regDesign, forwardWindow) {
    const prices = DS.prices[indexKey];
    const h = forwardWindow; // 대표 지평 = D+N
    const X = [],
      y = [],
      years = [];
    for (let r = 0; r < regDesign.rows.length; r++) {
      const origIdx = regDesign.validRowOrigIdx[r];
      const j = origIdx + h;
      if (j >= prices.length) continue;
      X.push(regDesign.rows[r]);
      y.push(prices[j] / prices[origIdx] - 1);
      years.push(Number(DS.tradingDays[origIdx].slice(0, 4)) + Number(DS.tradingDays[origIdx].slice(5, 7)) / 12);
    }
    if (X.length < X[0]?.length * 3) return { insufficientData: true };
    return A.walkForwardValidate(X, y, years, 5, 1);
  }

  function computeDiagnostics(indexKey, poolIdx, regDesign) {
    // 대표 지평(D+N) 데이터로 VIF + Huber-vs-OLS 비교
    const prices = DS.prices[indexKey];
    const h = state.forwardWindow;
    const X = [],
      y = [];
    for (let r = 0; r < regDesign.rows.length; r++) {
      const origIdx = regDesign.validRowOrigIdx[r];
      const j = origIdx + h;
      if (j >= prices.length) continue;
      X.push(regDesign.rows[r]);
      y.push(prices[j] / prices[origIdx] - 1);
    }
    if (X.length < 20) return null;
    const ols = A.olsFit(X, y);
    const huber = A.huberFit(X, y, 1.345, 15);
    const vif = A.computeVIF(X, regDesign.colNames);
    return { ols, huber, vif, n: X.length };
  }

  // 나머지(렌더링, DOM 바인딩)는 ui.js에서 계속
  window.__macroApp = {
    state,
    DS: () => DS,
    VARIABLES,
    INDEX_META,
    REGIME_LABEL,
    REGIME_ORDER,
    boot,
    runAnalysis,
    featureSeriesFor,
    lastFeatureValue,
    computeDiagnostics,
    poolStartIndex,
    SURPRISE_CUTOFF,
    round,
  };
})();
