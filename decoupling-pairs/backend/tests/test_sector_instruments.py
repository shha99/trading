import numpy as np
import pandas as pd

from app import correlation, sector_instruments


def test_build_synthetic_sector_index_aggregates_by_sector_and_excludes_tiny_sectors():
    dates = pd.bdate_range("2024-01-02", periods=40)
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.01, 40).cumsum()
    noise = lambda: rng.normal(0, 0.001, 40).cumsum()  # noqa: E731

    # 철강 3종목: 전부 +base 방향, 화학 3종목: 전부 -base 방향 -> 두 섹터지수는 음의 상관이어야 함
    steel = {t: 100 * np.exp(base + noise()) for t in ("A", "B", "C")}
    chem = {t: 100 * np.exp(-base + noise()) for t in ("D", "E", "F")}
    # 기타(단일 종목 섹터) - 구성 종목 3개 미만이라 지수에서 제외돼야 함
    solo = {"G": 100 * np.exp(rng.normal(0, 0.01, 40).cumsum())}

    prices = pd.DataFrame({**steel, **chem, **solo}, index=dates)
    meta = pd.DataFrame(
        {
            "sector": {"A": "철강", "B": "철강", "C": "철강", "D": "화학", "E": "화학", "F": "화학", "G": "기타"},
            "market_cap": {"A": 100, "B": 200, "C": 150, "D": 100, "E": 100, "F": 100, "G": 500},
        }
    ).rename_axis("ticker")

    sector_prices, sector_meta = sector_instruments.build_synthetic_sector_index(prices, meta)

    assert set(sector_prices.columns) == {"철강", "화학"}  # "기타"는 3종목 미만이라 제외
    assert set(sector_meta.index) == {"철강", "화학"}
    assert len(sector_prices) == len(dates)

    returns = np.log(sector_prices).diff().dropna()
    corr = returns["철강"].corr(returns["화학"])
    assert corr < -0.5  # 구성상 명확한 음의 상관이 지수 단위에서도 재현돼야 함


def test_synthetic_sector_index_reusable_with_scan_pairs():
    """sector_instruments가 만든 (prices, meta)가 correlation.scan_pairs에 그대로
    들어가는지 - 새 상관계수 로직을 따로 만들 필요 없이 기존 파이프라인을 재사용한다."""
    dates = pd.bdate_range("2024-01-02", periods=40)
    rng = np.random.default_rng(5)
    base = rng.normal(0, 0.01, 40).cumsum()
    noise = lambda: rng.normal(0, 0.001, 40).cumsum()  # noqa: E731
    steel = {t: 100 * np.exp(base + noise()) for t in ("A", "B", "C")}
    chem = {t: 100 * np.exp(-base + noise()) for t in ("D", "E", "F")}
    prices = pd.DataFrame({**steel, **chem}, index=dates)
    meta = pd.DataFrame(
        {
            "sector": {"A": "철강", "B": "철강", "C": "철강", "D": "화학", "E": "화학", "F": "화학"},
            "market_cap": {t: 100 for t in "ABCDEF"},
        }
    ).rename_axis("ticker")

    sector_prices, sector_meta = sector_instruments.build_synthetic_sector_index(prices, meta)
    result = correlation.scan_pairs(
        sector_prices, sector_meta, sector_prices.index.min(), sector_prices.index.max(), top_n=5,
        exclude_managed=True, exclude_illiquid=False, exclude_index_reversal=False,
    )
    assert len(result["pairs"]) >= 1
    top = result["pairs"][0]
    assert {top.stock_a.ticker, top.stock_b.ticker} == {"철강", "화학"}
    assert top.correlation < 0
