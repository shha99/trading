import numpy as np
import pandas as pd

from app import config, correlation


def _make_prices(n_days=60, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days)

    base = rng.normal(0, 0.01, n_days).cumsum()
    # A/B: 강한 양의 상관관계, C: A와 강한 음의 상관관계(디커플링), D: 무관계
    a = 100 * np.exp(base)
    b = 100 * np.exp(base + rng.normal(0, 0.002, n_days).cumsum())
    c = 100 * np.exp(-base + rng.normal(0, 0.002, n_days).cumsum())
    d = 100 * np.exp(rng.normal(0, 0.01, n_days).cumsum())

    prices = pd.DataFrame({"A": a, "B": b, "C": c, "D": d}, index=dates)
    return prices


def _make_meta(tickers, sectors):
    return pd.DataFrame(
        {
            "name": {t: t for t in tickers},
            "market": {t: "KOSPI" for t in tickers},
            "sector": dict(zip(tickers, sectors)),
            "market_cap": {t: 1_000_000_000 for t in tickers},
            "avg_trading_value": {t: 1_000_000 for t in tickers},
            "is_preferred": {t: False for t in tickers},
            "is_managed": {t: False for t in tickers},
            "is_halted": {t: False for t in tickers},
            "listed_shares": {t: 1000 for t in tickers},
            "index_corr": {t: 0.0 for t in tickers},
            "index_reversal": {t: False for t in tickers},
        }
    ).rename_axis("ticker")


def _all_pairs(result: dict) -> list:
    return result["pairs"] + result["low_significance_pairs"]


def test_scan_pairs_ranks_most_negative_first():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=3,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
    )

    assert not result["reliability_warning"]
    assert result["trading_days"] == len(prices)
    pairs = _all_pairs(result)
    assert len(pairs) >= 1
    # 가장 음의 상관관계인 A-C 페어가 최상단이어야 함 (FDR 통과 리스트 기준)
    top = result["pairs"][0]
    assert {top.stock_a.ticker, top.stock_b.ticker} == {"A", "C"}
    assert top.correlation < 0
    assert top.fdr_passed is True
    # 오름차순 정렬 확인
    values = [p.correlation for p in result["pairs"]]
    assert values == sorted(values)


def test_scan_pairs_badges_reflect_sector_match():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=10,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
    )
    for p in _all_pairs(result):
        expected = "cross_sector" if p.stock_a.sector != p.stock_b.sector else "same_sector_anomaly"
        assert p.badge == expected


def test_scan_pairs_reliability_warning_for_short_window():
    prices = _make_prices(n_days=10)
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=3,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
    )
    assert result["reliability_warning"] is True
    assert result["trading_days"] == 10


def test_scan_pairs_excludes_incomplete_columns_in_window():
    prices = _make_prices()
    prices.loc[prices.index[5], "D"] = np.nan  # D에 결측 발생 -> 이 구간에서 제외되어야 함
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=10,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
    )
    tickers_seen = {t for p in _all_pairs(result) for t in (p.stock_a.ticker, p.stock_b.ticker)}
    assert "D" not in tickers_seen


def test_scan_pairs_excludes_index_reversal_by_default():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    meta.loc["C", "index_reversal"] = True

    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=10,
        exclude_managed=False, exclude_illiquid=False,  # exclude_index_reversal 기본값 True
    )
    tickers_seen = {t for p in _all_pairs(result) for t in (p.stock_a.ticker, p.stock_b.ticker)}
    assert "C" not in tickers_seen


def test_search_correlations_full_period_handles_partial_history():
    prices = _make_prices(n_days=60)
    # C는 후반 30일만 상장(신규상장 종목 시뮬레이션)
    prices.loc[prices.index[:30], "C"] = np.nan
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    result = correlation.search_correlations(
        "A", prices, meta, start=None, end=None, full_period=True, top_n=3,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
    )
    pairs = _all_pairs(result)
    by_ticker = {p.stock_b.ticker: p for p in pairs}
    assert "C" in by_ticker
    assert by_ticker["C"].overlap_days <= 30
    assert by_ticker["C"].reliability_warning is False or by_ticker["C"].overlap_days < 20


def test_search_correlations_excludes_illiquid_when_requested():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    meta.loc["C", "avg_trading_value"] = 1  # 유동성 최하위로 설정

    result = correlation.search_correlations(
        "A", prices, meta, start=prices.index.min(), end=prices.index.max(),
        full_period=False, top_n=3, exclude_managed=False, exclude_illiquid=True,
        exclude_index_reversal=False,
    )
    assert all(p.stock_b.ticker != "C" for p in _all_pairs(result))


def test_eligible_universe_excludes_preferred_and_managed():
    meta = _make_meta(["A", "B"], ["철강", "화학"])
    meta.loc["B", "is_preferred"] = True
    universe = correlation.eligible_universe(meta, exclude_managed=True, exclude_illiquid=False, exclude_index_reversal=False)
    assert list(universe) == ["A"]


def test_eligible_universe_excludes_index_reversal_by_default():
    meta = _make_meta(["A", "B"], ["철강", "화학"])
    meta.loc["B", "index_reversal"] = True
    universe = correlation.eligible_universe(meta, exclude_managed=False, exclude_illiquid=False)
    assert list(universe) == ["A"]


def test_eligible_universe_excludes_low_volatility_when_requested():
    meta = _make_meta(["A", "B", "C", "D", "E"], ["철강"] * 5)
    meta["volatility"] = [0.05, 0.06, 0.30, 0.32, 0.35]  # A, B가 저변동성

    # eligible_universe 자체의 기본값(0.0)으로는 필터링 안 됨
    universe_unfiltered = correlation.eligible_universe(
        meta, exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False
    )
    assert "A" in universe_unfiltered

    # scan_pairs/search_correlations가 실제로 넘기는 기본 컷오프(하위 20%)를 명시하면 제외됨
    universe_filtered = correlation.eligible_universe(
        meta, exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
        min_vol_percentile=config.DEFAULT_MIN_VOLATILITY_PCTL,
    )
    assert "A" not in universe_filtered
    assert "E" in universe_filtered


def test_eligible_universe_sector_scope_restricts_to_one_sector():
    meta = _make_meta(["A", "B", "C"], ["철강", "화학", "철강"])
    universe = correlation.eligible_universe(
        meta, exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
        sector_scope="철강",
    )
    assert set(universe) == {"A", "C"}


def test_scan_pairs_tier2_fallback_never_empty_with_candidates(monkeypatch):
    """1: 정렬 로직은 하드 필터가 아니라 계층적 fallback - 1티어(FDR 통과)가 하나도
    없어도 후보(음의 상관)가 남아있으면 2티어로 top_n을 채워야 한다."""
    monkeypatch.setattr(
        correlation.advanced_stats,
        "benjamini_hochberg",
        lambda pvals, alpha: (np.zeros(len(pvals), dtype=bool), pvals),
    )
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=3,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
    )
    # 하드 필터였다면 FDR 전부 미통과라 pairs가 텅 비었을 것 - 이제는 2티어로 채워져야 한다.
    assert len(result["pairs"]) > 0
    assert all(p.fdr_passed is False for p in result["pairs"])
    assert result["pool_size"] == len(result["pairs"]) + len(result["low_significance_pairs"])


def test_scan_pairs_sector_scope_restricts_candidates_and_badge():
    prices = _make_prices()
    # A-C가 구성상 강한 음의 상관 - 둘을 같은 섹터에 둬서 섹터 한정 조회를 검증한다.
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "화학", "철강", "IT"])

    result = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=10,
        exclude_managed=False, exclude_illiquid=False, exclude_index_reversal=False,
        sector_scope="철강",
    )
    pairs = _all_pairs(result)
    assert len(pairs) >= 1
    tickers_seen = {t for p in pairs for t in (p.stock_a.ticker, p.stock_b.ticker)}
    assert tickers_seen == {"A", "C"}
    assert all(p.badge == "sector_scoped" for p in pairs)
    # 섹터 한정 조회에서는 "동일섹터 이례적" 하이라이트가 의미 없으므로 생략된다.
    assert result["same_sector_highlights"] == []


def test_search_correlations_sector_scope_restricts_candidates():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "화학", "화학", "IT"])

    result = correlation.search_correlations(
        "A", prices, meta, start=prices.index.min(), end=prices.index.max(),
        full_period=False, top_n=10, exclude_managed=False, exclude_illiquid=False,
        exclude_index_reversal=False, sector_scope="화학",
    )
    pairs = _all_pairs(result)
    assert all(p.stock_b.ticker in ("B", "C") for p in pairs)
    assert result["same_sector_highlights"] == []
