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

    # 4: 이상치/구조적 단절 종목 필터 - collect_and_build.py가 남겨둔 감자/액면병합
    # 구조적 불연속 감지 신호(수집 스크립트가 없거나 오래된 캐시면 빈 dict로 대체).
    structural_flags_path = CACHE_DIR / "structural_flags.json"
    structural_flags: dict[str, bool] = (
        json.loads(structural_flags_path.read_text(encoding="utf-8")) if structural_flags_path.exists() else {}
    )

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

    _LONG_HALT_MIN_CONSECUTIVE_DAYS = 5

    def _long_halt_flag(row_vol: list) -> bool:
        """4: "장기 거래정지 이력" 프록시 - 거래량 0/결측이 이 일수 이상 연속되면 이력으로 간주."""
        run = 0
        best = 0
        for v in row_vol:
            if not v:
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best >= _LONG_HALT_MIN_CONSECUTIVE_DAYS

    _CONCENTRATION_TOP_K_DAYS = 5

    def _concentration_ratio(row_close: list) -> float | None:
        """4: 변동성 집중도 - 절대값 기준 상위 K거래일이 전체 수익률 "에너지"(제곱합)에서
        차지하는 비중. 높을수록 소수 이벤트일이 상관계수 계산을 지배한다는 뜻."""
        sq = []
        for i in range(1, len(row_close)):
            p0, p1 = row_close[i - 1], row_close[i]
            if p0 and p1 and p0 > 0:
                sq.append(math.log(p1 / p0) ** 2)
        if len(sq) < 30:
            return None
        total = sum(sq)
        if total <= 0:
            return None
        top_k = sum(sorted(sq, reverse=True)[:_CONCENTRATION_TOP_K_DAYS])
        return min(1.0, top_k / total)

    # 신규 탭 "섹터/ETF 비교" - 자체 계산 시가총액가중 섹터지수.
    # top-600(chosen)이 아니라 histories 로드에 성공한 전종목(candidates)을 기준으로
    # 구성해야 소형 섹터도 제대로 대표된다. 날짜축은 chosen 기준(dates/date_idx)을
    # 그대로 쓴다 - 상위 600종목의 거래일이 사실상 전체 거래일을 다 덮는다.
    def _build_synthetic_sector_index():
        sector_weighted_sum: dict[str, list[float]] = {}
        sector_weight_total: dict[str, list[float]] = {}
        sector_members: dict[str, set[str]] = {}
        for ticker, info, rows in candidates:
            sector = info.get("sector")
            weight = info.get("market_cap") or 0
            if not sector or weight <= 0:
                continue
            row_close = [None] * T
            for r in rows:
                i = date_idx.get(r["date"])
                if i is not None:
                    row_close[i] = r["close"]
            ws = sector_weighted_sum.setdefault(sector, [0.0] * T)
            wt = sector_weight_total.setdefault(sector, [0.0] * T)
            has_any = False
            for t in range(1, T):
                p0, p1 = row_close[t - 1], row_close[t]
                if p0 and p1 and p0 > 0:
                    r_t = math.log(p1 / p0)
                    ws[t] += weight * r_t
                    wt[t] += weight
                    has_any = True
            if has_any:
                sector_members.setdefault(sector, set()).add(ticker)

        sector_out, sector_prices_out = [], []
        for sector, members in sector_members.items():
            if len(members) < 3:  # 구성 종목이 너무 적으면 지수로서 의미가 약해 제외
                continue
            ws, wt = sector_weighted_sum[sector], sector_weight_total[sector]
            level = [100.0] * T
            for t in range(1, T):
                r_t = (ws[t] / wt[t]) if wt[t] > 0 else 0.0
                level[t] = level[t - 1] * math.exp(r_t)
            sector_out.append({"c": sector, "n": f"{sector} 섹터지수(자체계산)", "s": sector, "mcnt": len(members)})
            sector_prices_out.append([round(v, 4) for v in level])
        return sector_out, sector_prices_out

    sector_index_out, sector_index_prices_out = _build_synthetic_sector_index()
    print(f"자체 계산 섹터지수: {len(sector_index_out)}개 섹터 (전종목 {len(candidates)}개 기준)")

    # 신규 탭 "섹터/ETF 비교" - 섹터 추종 ETF (collect_and_build.py가 받아둔 것을 로드).
    sector_etf_out, sector_etf_prices_out = [], []
    etf_meta_path = CACHE_DIR / "sector_etf_meta.json"
    if etf_meta_path.exists():
        etf_meta = json.loads(etf_meta_path.read_text(encoding="utf-8"))
        for ticker, info in etf_meta.items():
            p = CACHE_DIR / "sector_etf" / f"{ticker}.json"
            if not p.exists():
                continue
            rows = json.loads(p.read_text(encoding="utf-8"))
            row_close = [None] * T
            for r in rows:
                i = date_idx.get(r["date"])
                if i is not None:
                    row_close[i] = r["close"]
            sector_etf_out.append({"c": ticker, "n": info.get("name", ticker), "s": info.get("theme", ticker)})
            sector_etf_prices_out.append(row_close)
    print(f"섹터 ETF: {len(sector_etf_out)}개")

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
        structural_break = bool(structural_flags.get(ticker, False))
        long_halt = _long_halt_flag(row_vol)
        concentration = _concentration_ratio(row_close)

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
                "sb": structural_break,
                "lh": long_halt,
                "cr": round(concentration, 4) if concentration is not None else None,
            }
        )
    print(f"지수 역행 종목(상위 {TOP_N} 중): {n_reversal}개")
    n_structural = sum(1 for u in universe_out if u["sb"] or u["lh"])
    n_concentrated = sum(1 for u in universe_out if u["cr"] is not None and u["cr"] >= 0.50)
    print(f"이상치/구조적 단절 이력 종목(상위 {TOP_N} 중): {n_structural}개, 소수 이벤트 주도 종목: {n_concentrated}개")

    real_data = {
        "asOf": dates[-1],
        "dates": dates,
        "universe": universe_out,
        "prices": prices_out,
        "index": index_out,
        "sectorIndex": sector_index_out,
        "sectorIndexPrices": sector_index_prices_out,
        "sectorEtf": sector_etf_out,
        "sectorEtfPrices": sector_etf_prices_out,
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
