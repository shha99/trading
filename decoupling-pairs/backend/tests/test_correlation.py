import numpy as np
import pandas as pd

from app import correlation


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
        }
    ).rename_axis("ticker")


def test_scan_pairs_ranks_most_negative_first():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    pairs, warn, days = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=3,
        exclude_managed=False, exclude_illiquid=False,
    )

    assert not warn
    assert days == len(prices)
    assert len(pairs) == 3
    # 가장 음의 상관관계인 A-C 페어가 최상단이어야 함
    top = pairs[0]
    assert {top.stock_a.ticker, top.stock_b.ticker} == {"A", "C"}
    assert top.correlation < 0
    # 오름차순 정렬 확인
    values = [p.correlation for p in pairs]
    assert values == sorted(values)


def test_scan_pairs_badges_reflect_sector_match():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    pairs, _, _ = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=10,
        exclude_managed=False, exclude_illiquid=False,
    )
    for p in pairs:
        expected = "cross_sector" if p.stock_a.sector != p.stock_b.sector else "same_sector_anomaly"
        assert p.badge == expected


def test_scan_pairs_reliability_warning_for_short_window():
    prices = _make_prices(n_days=10)
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    pairs, warn, days = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=3,
        exclude_managed=False, exclude_illiquid=False,
    )
    assert warn is True
    assert days == 10


def test_scan_pairs_excludes_incomplete_columns_in_window():
    prices = _make_prices()
    prices.loc[prices.index[5], "D"] = np.nan  # D에 결측 발생 -> 이 구간에서 제외되어야 함
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    pairs, _, _ = correlation.scan_pairs(
        prices, meta, prices.index.min(), prices.index.max(), top_n=10,
        exclude_managed=False, exclude_illiquid=False,
    )
    tickers_seen = {t for p in pairs for t in (p.stock_a.ticker, p.stock_b.ticker)}
    assert "D" not in tickers_seen


def test_search_correlations_full_period_handles_partial_history():
    prices = _make_prices(n_days=60)
    # C는 후반 30일만 상장(신규상장 종목 시뮬레이션)
    prices.loc[prices.index[:30], "C"] = np.nan
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])

    pairs, warn = correlation.search_correlations(
        "A", prices, meta, start=None, end=None, full_period=True, top_n=3,
        exclude_managed=False, exclude_illiquid=False,
    )
    by_ticker = {p.stock_b.ticker: p for p in pairs}
    assert "C" in by_ticker
    assert by_ticker["C"].overlap_days <= 30
    assert by_ticker["C"].reliability_warning is False or by_ticker["C"].overlap_days < 20


def test_search_correlations_excludes_illiquid_when_requested():
    prices = _make_prices()
    meta = _make_meta(["A", "B", "C", "D"], ["철강", "철강", "화학", "IT"])
    meta.loc["C", "avg_trading_value"] = 1  # 유동성 최하위로 설정

    pairs, _ = correlation.search_correlations(
        "A", prices, meta, start=prices.index.min(), end=prices.index.max(),
        full_period=False, top_n=3, exclude_managed=False, exclude_illiquid=True,
    )
    assert all(p.stock_b.ticker != "C" for p in pairs)


def test_eligible_universe_excludes_preferred_and_managed():
    meta = _make_meta(["A", "B"], ["철강", "화학"])
    meta.loc["B", "is_preferred"] = True
    universe = correlation.eligible_universe(meta, exclude_managed=True, exclude_illiquid=False)
    assert list(universe) == ["A"]
