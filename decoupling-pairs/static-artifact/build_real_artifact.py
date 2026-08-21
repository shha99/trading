"""naver_cache/*.json (실데이터) -> decoupling-demo.html 의 __REAL_DATA_JSON__ 치환.

시가총액 상위 TOP_N 종목만 골라 baked-in한다(브라우저에서 O(n^2) 전체 스캔을
돌려야 하므로 유니버스를 너무 키우면 버벅임 - 실제 전종목(~3900개)은
decoupling-pairs/backend 쪽 parquet 캐시에 별도로 들어간다).
"""
import json
import math
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRATCH = Path(__file__).parent
CACHE_DIR = SCRATCH / "naver_cache"
HIST_DIR = CACHE_DIR / "history"
TEMPLATE = SCRATCH / "decoupling-demo.template.html"
OUT = SCRATCH / "decoupling-demo.html"

TOP_N = 600
MIN_HISTORY_DAYS = 150  # 이보다 데이터가 적은 종목은 신규상장 등으로 보고 제외하지 않되, 정렬 우선순위만 낮춤
INVERSE_LEVERAGE_RE = re.compile(r"인버스|레버리지|곱버스", re.IGNORECASE)
INDEX_REVERSAL_THRESHOLD = -0.7


def _market_still_open() -> tuple[bool, str]:
    """장이 아직 안 끝났으면 당일 데이터는 확정 종가가 아니라 장중 체결가일 수 있으므로,
    그런 경우 오늘 날짜는 아예 제외한다(대표 종목 하나로 실시간 상태 확인).
    Returns: (오늘자 데이터를 신뢰할 수 없으면 True, 오늘 날짜 YYYY-MM-DD(KST))
    """
    req = urllib.request.Request(
        "https://m.stock.naver.com/api/stock/005930/basic",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read())
    traded_at = d.get("localTradedAt", "")  # e.g. "2026-08-21T10:00:22+09:00"
    today_kst = traded_at[:10] if traded_at else datetime.now().strftime("%Y-%m-%d")
    still_open = d.get("marketStatus") == "OPEN"
    return still_open, today_kst


def main():
    universe = json.loads((CACHE_DIR / "universe.json").read_text(encoding="utf-8"))
    print(f"raw universe: {len(universe)}")

    still_open, today_kst = _market_still_open()
    print(f"market status check: still_open={still_open}, today_kst={today_kst}")

    # 히스토리 로드 + 시총 상위 정렬 (인버스/레버리지/곱버스는 이중 방어로 한 번 더 제외)
    candidates = []
    excluded_by_name = 0
    for ticker, info in universe.items():
        if INVERSE_LEVERAGE_RE.search(info.get("name", "")):
            excluded_by_name += 1
            continue
        p = HIST_DIR / f"{ticker}.json"
        if not p.exists():
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        if still_open:
            rows = [r for r in rows if r["date"] != today_kst]  # 장중 체결가는 확정 종가가 아니므로 제외
        if len(rows) < 20:
            continue
        candidates.append((ticker, info, rows))
    print(f"인버스/레버리지 명칭으로 추가 제외: {excluded_by_name}개")

    candidates.sort(key=lambda x: -(x[1].get("market_cap") or 0))
    chosen = candidates[:TOP_N]
    print(f"chosen (top {TOP_N} by market cap): {len(chosen)}")

    # 공유 날짜 축 = 선택된 종목들의 날짜 합집합
    all_dates = set()
    for _, _, rows in chosen:
        all_dates.update(r["date"] for r in rows)
    dates = sorted(all_dates)
    date_idx = {d: i for i, d in enumerate(dates)}
    T = len(dates)
    print(f"date axis length: {T} ({dates[0]} ~ {dates[-1]})")

    # 지수(KOSPI/KOSDAQ) 시계열도 같은 날짜축에 정렬 - 하락일 조건부 상관계수/시장베타
    # 잔차/지수 역행 종목 판정에 JS 쪽에서 그대로 재사용한다.
    index_raw = json.loads((CACHE_DIR / "index_history.json").read_text(encoding="utf-8"))
    index_out: dict[str, list] = {}
    index_by_date: dict[str, dict] = {"KOSPI": {}, "KOSDAQ": {}}
    for idx_name, rows in index_raw.items():
        for d, c in rows:
            dd = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
            if still_open and dd == today_kst:
                continue
            index_by_date[idx_name][dd] = c
        index_out[idx_name] = [index_by_date[idx_name].get(d) for d in dates]

    def _index_returns(idx_name: str) -> list[float | None]:
        series = index_out[idx_name]
        out = [None] * len(series)
        for i in range(1, len(series)):
            a, b = series[i - 1], series[i]
            if a and b and a > 0:
                out[i] = math.log(b / a)
        return out

    index_returns = {k: _index_returns(k) for k in index_out}

    def _index_corr(row_close: list, market: str) -> float | None:
        idx_ret = index_returns.get(market)
        if idx_ret is None:
            return None
        xs, ys = [], []
        for i in range(1, len(row_close)):
            p0, p1 = row_close[i - 1], row_close[i]
            r_idx = idx_ret[i]
            if p0 and p1 and p0 > 0 and r_idx is not None:
                xs.append(math.log(p1 / p0))
                ys.append(r_idx)
        n = len(xs)
        if n < 30:
            return None
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        if sxx == 0 or syy == 0:
            return None
        return sxy / math.sqrt(sxx * syy)

    # 2: 저변동성(경기방어주) 오탐 필터용 - 연환산 변동성(일별 로그수익률 표준편차 × √252).
    # 보유한 전체 히스토리를 사용한다(특정 조회 구간이 아니라 종목 자체의 일반적 변동성 특성).
    _TRADING_DAYS_PER_YEAR = 252

    def _annualized_volatility(row_close: list) -> float | None:
        rets = []
        for i in range(1, len(row_close)):
            p0, p1 = row_close[i - 1], row_close[i]
            if p0 and p1 and p0 > 0:
                rets.append(math.log(p1 / p0))
        n = len(rets)
        if n < 30:
            return None
        m = sum(rets) / n
        var = sum((r - m) ** 2 for r in rets) / (n - 1)
        return math.sqrt(var) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    universe_out = []
    prices_out = []
    n_reversal = 0
    for ticker, info, rows in chosen:
        row_close = [None] * T
        row_vol = [0.0] * T
        for r in rows:
            i = date_idx.get(r["date"])
            if i is None:
                continue
            row_close[i] = r["close"]
            row_vol[i] = r.get("volume") or 0.0
        prices_out.append(row_close)

        # 최근 20거래일 평균 거래대금(근사: 종가*거래량)
        value_series = [c * v if c is not None else 0.0 for c, v in zip(row_close, row_vol)]
        nonzero_tail = [v for v in value_series[-20:] if v]
        avg_value = sum(nonzero_tail) / len(nonzero_tail) if nonzero_tail else 0.0

        name = info["name"]
        is_preferred = (len(ticker) == 6 and ticker[-1] != "0") or name.endswith("우") or "우B" in name or "우C" in name
        last_vol = next((v for v in reversed(row_vol) if v), 0)
        is_halted = last_vol <= 0

        idx_corr = _index_corr(row_close, info["market"])
        is_reversal = idx_corr is not None and idx_corr <= INDEX_REVERSAL_THRESHOLD
        if is_reversal:
            n_reversal += 1
        volatility = _annualized_volatility(row_close)

        universe_out.append(
            {
                "c": ticker,
                "n": name,
                "m": info["market"],
                "s": info["sector"],
                "mc": info.get("market_cap"),
                "av": round(avg_value),
                "fl": bool(is_preferred or is_halted),
                "ic": round(idx_corr, 4) if idx_corr is not None else None,
                "ir": is_reversal,
                "vt": round(volatility, 4) if volatility is not None else None,
            }
        )
    print(f"지수 역행 종목(상위 {TOP_N} 중): {n_reversal}개")

    real_data = {
        "asOf": dates[-1],
        "dates": dates,
        "universe": universe_out,
        "prices": prices_out,
        "index": index_out,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceNote": "Naver Finance 공개 API (finance.naver.com, m.stock.naver.com, api.stock.naver.com)",
    }

    payload = json.dumps(real_data, ensure_ascii=False, separators=(",", ":"))
    print(f"payload size: {len(payload) / 1e6:.2f} MB")

    html = TEMPLATE.read_text(encoding="utf-8")
    if "__REAL_DATA_JSON__" not in html:
        raise SystemExit("템플릿에서 __REAL_DATA_JSON__ 마커를 찾을 수 없습니다 (이미 치환됐을 수 있음)")
    html = html.replace("__REAL_DATA_JSON__", payload)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
