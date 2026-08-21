// 수동 새로고침(3: 즉시 재스크리닝)용 Vercel 서버리스 함수.
//
// 정적 스냅샷 페이지가 baked-in한 데이터는 빌드 시점(주로 하루 전) 고정이라,
// "지금 화면에 표시된 종목들"만 Naver의 경량 현재가 엔드포인트로 다시 받아와
// 최신 종가로 즉시 재계산할 수 있게 해준다. 전종목(600개)을 매번 다 받으면
// 서버리스 함수 시간제한(Hobby 플랜 10초)에 걸릴 위험이 있어, 클라이언트가
// 넘겨주는 "현재 조회 결과에 등장한 종목"으로만 범위를 좁힌다 - 전종목 기준
// 신규 후보 발굴은 매일 자동 갱신(GitHub Actions, static-artifact/collect_and_build.py)이
// 담당한다.

const UA = { "User-Agent": "Mozilla/5.0 (compatible; decoupling-pairs-refresh/1.0)" };
const MAX_TICKERS = 250; // 화면에 나올 수 있는 최대 종목 수보다 넉넉히 - 남용 방지용 상한
const CONCURRENCY = 40;

function parseNum(s) {
  if (s == null) return null;
  const n = Number(String(s).replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

async function fetchBasic(url) {
  const r = await fetch(url, { headers: UA });
  if (!r.ok) return null;
  return r.json();
}

async function mapLimited(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      try {
        out[idx] = await fn(items[idx]);
      } catch {
        out[idx] = null;
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST 요청만 지원합니다." });
    return;
  }

  let tickers;
  try {
    const body = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
    tickers = Array.isArray(body && body.tickers) ? body.tickers.filter((t) => /^\d{6}$/.test(t)) : [];
  } catch {
    tickers = [];
  }
  tickers = Array.from(new Set(tickers)).slice(0, MAX_TICKERS);

  if (tickers.length === 0) {
    res.status(400).json({ error: "tickers가 비어있거나 올바르지 않습니다." });
    return;
  }

  const [stockResults, kospi, kosdaq] = await Promise.all([
    mapLimited(tickers, CONCURRENCY, (code) => fetchBasic(`https://m.stock.naver.com/api/stock/${code}/basic`)),
    fetchBasic("https://m.stock.naver.com/api/index/KOSPI/basic"),
    fetchBasic("https://m.stock.naver.com/api/index/KOSDAQ/basic"),
  ]);

  const prices = {};
  let asOf = null;
  let stillOpen = false;
  let openVotes = 0;
  let voteTotal = 0;

  tickers.forEach((code, i) => {
    const d = stockResults[i];
    if (!d) return;
    const close = parseNum(d.closePrice);
    if (close != null) prices[code] = close;
    if (d.localTradedAt) {
      const day = String(d.localTradedAt).slice(0, 10);
      if (!asOf || day > asOf) asOf = day;
    }
    if (d.marketStatus) {
      voteTotal++;
      if (d.marketStatus === "OPEN") openVotes++;
    }
  });
  if (voteTotal > 0) stillOpen = openVotes / voteTotal >= 0.5;
  if (!asOf) {
    // 개별 종목 응답을 하나도 못 받았으면 오늘 날짜(KST)로 대체
    asOf = new Date().toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10);
  }

  const index = {
    KOSPI: kospi ? parseNum(kospi.closePrice) : null,
    KOSDAQ: kosdaq ? parseNum(kosdaq.closePrice) : null,
  };

  const fetchedCount = Object.keys(prices).length;
  if (fetchedCount === 0) {
    res.status(502).json({ error: "Naver로부터 시세를 하나도 받아오지 못했습니다." });
    return;
  }

  res.setHeader("Cache-Control", "no-store");
  res.status(200).json({
    fetchedAt: new Date().toISOString(),
    asOf,
    stillOpen,
    requested: tickers.length,
    fetched: fetchedCount,
    prices,
    index,
  });
};
