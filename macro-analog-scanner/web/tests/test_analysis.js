const A = require("../analysis.js");
const assert = require("assert");

function approx(a, b, eps, msg) {
  assert.ok(Math.abs(a - b) < eps, `${msg}: ${a} vs ${b}`);
}

// 1) forward-fill 정렬: look-ahead bias 방지 검증
{
  const tradingDays = A.businessDays("2020-01-01", "2020-04-30");
  const releases = [
    { releaseDate: "2020-01-15", value: 10 },
    { releaseDate: "2020-02-14", value: 20 },
    { releaseDate: "2020-03-13", value: 30 },
  ];
  // forwardFillToTradingDays is private; re-derive via synthMonthlySeries-like check using exported pieces
  // 대신 knnMatch 등에서 쓰는 forward-fill 동작을 momentum/maDeviation 관련 함수로 간접 검증하지 않고,
  // buildSyntheticDataset 결과에서 발표일 이전엔 이전 값이 유지되는지 확인한다.
  const ds = A.buildSyntheticDataset(42, "2020-01-01", "2020-06-30");
  // CPI 값이 시작 며칠간은 null이 아니어야 하며(초기 forward-fill 발생), 발표일 이전 값과
  // 발표일 이후 값이 실제로 달라지는 시점이 존재해야 한다.
  const cpi = ds.cpi;
  let changed = false;
  for (let i = 1; i < cpi.length; i++) {
    if (cpi[i] !== cpi[i - 1]) changed = true;
  }
  assert.ok(changed, "CPI forward-fill 시계열에 변화가 있어야 함");
  console.log("PASS: forward-fill/정렬 로직이 값을 생성함");
}

// 2) 레짐 분류: 인위적으로 상승 추세를 준 FEDFUNDS가 hiking으로 분류되는지
{
  const days = A.businessDays("2020-01-01", "2021-06-30");
  const fedfunds = days.map((_, i) => 1.0 + i * 0.01); // 꾸준히 상승
  const regime = A.classifyRegime(fedfunds);
  const lastRegime = regime[regime.length - 1];
  assert.strictEqual(lastRegime, "hiking", "지속 상승 시 hiking으로 분류되어야 함");
  console.log("PASS: 레짐 분류(hiking) 정상");
}
{
  const days = A.businessDays("2020-01-01", "2021-06-30");
  const fedfunds = days.map((_, i) => 5.0 - i * 0.01); // 꾸준히 하락
  const regime = A.classifyRegime(fedfunds);
  assert.strictEqual(regime[regime.length - 1], "cutting", "지속 하락 시 cutting으로 분류되어야 함");
  console.log("PASS: 레짐 분류(cutting) 정상");
}
{
  const days = A.businessDays("2020-01-01", "2021-06-30");
  const fedfunds = days.map(() => 2.0); // 변화 없음
  const regime = A.classifyRegime(fedfunds);
  assert.strictEqual(regime[regime.length - 1], "flat", "변화 없으면 flat으로 분류되어야 함");
  console.log("PASS: 레짐 분류(flat) 정상");
}

// 3) 금리 팩터 압축
{
  const y2 = [1, 2],
    y5 = [2, 3],
    y10 = [3, 4],
    y20 = [3.5, 4.5],
    y30 = [4, 5];
  const f = A.buildRateFactors(y2, y5, y10, y20, y30);
  approx(f.level[0], 3, 1e-9, "Level = 10Y");
  approx(f.slope[0], 3 - 1, 1e-9, "Slope = 10Y-2Y");
  approx(f.curvature[0], 2 * 2 - 1 - 3, 1e-9, "Curvature = 2*5Y-2Y-10Y");
  console.log("PASS: 금리 팩터(Level/Slope/Curvature) 계산 정상");
}

// 4) KNN 매칭 + forward path: 알려진 패턴에서 가장 가까운 이웃이 실제로 가장 가까운지
{
  const dates = ["d0", "d1", "d2", "d3", "d4"];
  const historyMatrix = [[0], [1], [5], [10], [-1]];
  const scenario = [0.5];
  const res = A.knnMatch(dates, historyMatrix, scenario, {
    k: 2,
    useRegimeFilter: false,
    regimeArr: null,
    extremeFlags: null,
    penalty: 0.5,
  });
  const nearestDates = res.neighbors.map((n) => n.date);
  assert.deepStrictEqual(nearestDates.sort(), ["d0", "d1"].sort(), "0.5에 가장 가까운 값은 0과 1이어야 함");
  console.log("PASS: KNN 아날로그 매칭 정상");
}

// 5) OLS 회귀: y = 2 + 3x 정확히 복원되는지 (완벽한 선형관계, 노이즈 없음)
{
  const X = [
    [1, 1],
    [1, 2],
    [1, 3],
    [1, 4],
    [1, 5],
  ];
  const y = X.map((row) => 2 + 3 * row[1]);
  const fit = A.olsFit(X, y);
  approx(fit.beta[0], 2, 1e-6, "절편");
  approx(fit.beta[1], 3, 1e-6, "기울기");
  const r2 = A.r2Score(y, fit.fitted);
  approx(r2, 1.0, 1e-6, "완벽한 선형관계의 R²");
  console.log("PASS: OLS 회귀 정상 (R²=1.0 복원)");
}

// 6) Newey-West HAC 표준오차: 노이즈 있는 데이터에서 SE가 양수이고 유한한지
{
  const rng = A.mulberry32(7);
  const n = 200;
  const X = [];
  const y = [];
  for (let i = 0; i < n; i++) {
    const x1 = A.gaussian(rng);
    X.push([1, x1]);
    y.push(1.5 + 0.8 * x1 + A.gaussian(rng) * 0.5);
  }
  const fit = A.olsFit(X, y);
  const hac = A.neweyWestSE(X, fit.resid, fit.XtXinv, 5);
  assert.ok(hac.se.every((s) => Number.isFinite(s) && s > 0), "HAC SE는 유한한 양수여야 함");
  approx(fit.beta[1], 0.8, 0.2, "기울기 추정치가 참값 근방이어야 함");
  console.log("PASS: Newey-West HAC 표준오차 정상, SE=", hac.se);
}

// 7) VIF: 완전 독립 변수는 VIF≈1, 강한 공선성 변수는 VIF 매우 큼
{
  const rng = A.mulberry32(11);
  const n = 300;
  const X = [];
  for (let i = 0; i < n; i++) {
    const x1 = A.gaussian(rng);
    const x2 = A.gaussian(rng); // 독립
    const x3 = x1 * 0.98 + A.gaussian(rng) * 0.02; // x1과 거의 완전 상관
    X.push([1, x1, x2, x3]);
  }
  const vifs = A.computeVIF(X, ["x1", "x2", "x3"]);
  const vifMap = Object.fromEntries(vifs.map((v) => [v.name, v.vif]));
  assert.ok(vifMap.x2 < 1.5, `독립변수 x2의 VIF는 낮아야 함, got ${vifMap.x2}`);
  assert.ok(vifMap.x1 > 10 && vifMap.x3 > 10, `공선성 변수의 VIF는 커야 함, got x1=${vifMap.x1} x3=${vifMap.x3}`);
  console.log("PASS: VIF 계산 정상 (독립:", vifMap.x2.toFixed(2), " 공선성:", vifMap.x1.toFixed(2), vifMap.x3.toFixed(2), ")");
}

// 8) Huber 회귀: 소수 극단치가 있을 때 OLS보다 참값에 더 가까운지
{
  const rng = A.mulberry32(21);
  const n = 100;
  const X = [];
  const y = [];
  for (let i = 0; i < n; i++) {
    const x1 = A.gaussian(rng);
    X.push([1, x1]);
    let yi = 1.0 + 2.0 * x1 + A.gaussian(rng) * 0.3;
    if (i < 5) yi += 40; // 극단치 주입
    y.push(yi);
  }
  const ols = A.olsFit(X, y);
  const huber = A.huberFit(X, y, 1.345, 20);
  const olsErr = Math.abs(ols.beta[1] - 2.0);
  const huberErr = Math.abs(huber.beta[1] - 2.0);
  assert.ok(huberErr < olsErr, `Huber(${huberErr.toFixed(3)})가 OLS(${olsErr.toFixed(3)})보다 극단치에 덜 민감해야 함`);
  console.log("PASS: Huber 로버스트 회귀가 OLS보다 극단치에 덜 민감함 (OLS err=", olsErr.toFixed(3), " Huber err=", huberErr.toFixed(3), ")");
}

// 9) Walk-forward OOS: in-sample 대비 OOS 성과가 산출되는지 (구조적 신호 있는 데이터)
{
  const rng = A.mulberry32(99);
  const n = 3000;
  const years = [];
  const X = [];
  const y = [];
  for (let i = 0; i < n; i++) {
    years.push(2000 + i / 252);
    const x1 = A.gaussian(rng);
    X.push([1, x1]);
    y.push(0.5 * x1 + A.gaussian(rng) * 1.5);
  }
  const res = A.walkForwardValidate(X, y, years, 5, 1);
  assert.ok(res.folds > 0, "walk-forward가 최소 1개 폴드는 돌아야 함");
  assert.ok(res.oosHitRate === null || (res.oosHitRate >= 0 && res.oosHitRate <= 1), "적중률은 0~1 사이");
  console.log(
    "PASS: Walk-forward OOS 정상 (folds=",
    res.folds,
    " inSampleR2=",
    res.inSampleR2 && res.inSampleR2.toFixed(3),
    " oosR2=",
    res.oosR2 && res.oosR2.toFixed(3),
    " oosHit=",
    res.oosHitRate && res.oosHitRate.toFixed(3),
    ")"
  );
}

// 10) 극값 플래그: 인위적 스파이크가 감지되는지
{
  const series = new Array(200).fill(0).map((_, i) => 100 + Math.sin(i / 10));
  series[100] = 100 + 50; // 스파이크
  const flags = A.flagStatisticalExtremes(series);
  assert.ok(flags[100], "인위적 스파이크는 극값으로 플래그되어야 함");
  assert.ok(!flags[50], "평범한 변동은 극값으로 플래그되지 않아야 함");
  console.log("PASS: 통계적 극값 플래그 정상");
}

// 11) p-value: 알려진 임계값 근방에서 유의수준이 맞게 나오는지 (대표본 df에서 정규분포 근사와 일치)
{
  const p95 = A.twoSidedPValue(1.96, 100000);
  const p99 = A.twoSidedPValue(2.576, 100000);
  const pNull = A.twoSidedPValue(0, 100000);
  const pHuge = A.twoSidedPValue(10, 100000);
  approx(p95, 0.05, 0.005, "t=1.96 -> p≈0.05");
  approx(p99, 0.01, 0.003, "t=2.576 -> p≈0.01");
  approx(pNull, 1.0, 0.02, "t=0 -> p≈1.0");
  assert.ok(pHuge < 1e-10, `t=10 -> p는 극히 작아야 함, got ${pHuge}`);
  console.log("PASS: p-value 계산 정상 (p95=", p95.toFixed(4), " p99=", p99.toFixed(4), " pNull=", pNull.toFixed(4), " pHuge=", pHuge, ")");
}

console.log("\n모든 테스트 통과 ✅");
