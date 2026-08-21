import numpy as np
import pandas as pd

from app import advanced_stats
from app.naver_client import adjust_for_corporate_actions


def test_benjamini_hochberg_rejects_all_when_none_significant():
    pvals = np.array([0.4, 0.5, 0.9, 0.3])
    passed, adj = advanced_stats.benjamini_hochberg(pvals, alpha=0.05)
    assert not passed.any()
    assert (adj >= pvals).all()


def test_benjamini_hochberg_accepts_strongly_significant():
    pvals = np.array([0.0001, 0.0002, 0.6, 0.7, 0.8])
    passed, adj = advanced_stats.benjamini_hochberg(pvals, alpha=0.05)
    assert passed[0] and passed[1]
    assert not passed[2]


def test_pearson_pvalues_small_for_near_perfect_correlation():
    corr = np.array([[1.0, 0.99], [0.99, 1.0]])
    p = advanced_stats.pearson_pvalues(corr, n_obs=100)
    assert p[0, 1] < 1e-6


def test_spearman_matrix_matches_pandas():
    rng = np.random.default_rng(1)
    df = pd.DataFrame(rng.normal(size=(50, 3)), columns=["x", "y", "z"])
    mat = advanced_stats.spearman_matrix(df)
    expected = df.corr(method="spearman").to_numpy()
    assert np.allclose(mat, expected, atol=1e-9)


def test_beta_residual_matrix_removes_shared_market_factor():
    rng = np.random.default_rng(2)
    n = 200
    market = rng.normal(0, 1, n)
    # a, b는 시장 베타만 다르고 고유 성분은 서로 무관 -> 원시 상관관계는 있지만 잔차는 거의 0
    a = 1.5 * market + rng.normal(0, 0.01, n)
    b = -1.0 * market + rng.normal(0, 0.01, n)
    returns = pd.DataFrame({"a": a, "b": b})
    market_s = pd.Series(market, index=returns.index)

    raw_corr = returns["a"].corr(returns["b"])
    resid = advanced_stats.beta_residual_matrix(returns, market_s)
    assert raw_corr < -0.9  # 시장 베타 차이로 인해 원시 상관관계는 강한 음수
    assert abs(resid[0, 1]) < 0.3  # 시장 요인 제거하면 사실상 무관계


def test_downturn_conditional_matrix_uses_only_downturn_days():
    rng = np.random.default_rng(3)
    n = 100
    market = rng.normal(0, 1, n)
    a = rng.normal(0, 1, n)
    b = -a + rng.normal(0, 0.01, n)  # 항상 강한 음의 상관관계
    returns = pd.DataFrame({"a": a, "b": b})
    market_s = pd.Series(market, index=returns.index)

    mat, n_days = advanced_stats.downturn_conditional_matrix(returns, market_s, quantile=0.2)
    assert n_days == 20
    assert mat[0, 1] < -0.8


def test_adjust_for_corporate_actions_removes_split_discontinuity():
    idx = pd.bdate_range("2026-01-01", periods=5)
    s = pd.Series([1000, 990, 980, 15000, 15200], index=idx, dtype=float)  # 1->4일차 15배 점프
    adj = adjust_for_corporate_actions(s)
    ratio = adj.iloc[2] / adj.iloc[1]
    assert 0.9 < ratio < 1.15  # 보정 후에는 정상적인 일간 변동 폭 안으로 들어와야 함


def test_adjust_for_corporate_actions_leaves_normal_moves_alone():
    idx = pd.bdate_range("2026-01-01", periods=3)
    s = pd.Series([100.0, 125.0, 90.0], index=idx)  # 25%, -28% 등 정상 범위 내 변동
    adj = adjust_for_corporate_actions(s)
    assert list(adj) == [100.0, 125.0, 90.0]
