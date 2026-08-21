"""통계 정확도 보강 - 전부 벡터 연산(N×N 행렬 단위)으로 계산한다.

- 다중검정(Benjamini-Hochberg FDR) 보정
- 하락일 조건부 상관계수 (KOSPI 급락일만 골라 그날들만의 상관계수)
- 시장베타 제거 후 잔차 상관계수
- Spearman 순위상관
- 롤링 윈도우(60일/120일) 부호 안정성

전부 correlation.py의 scan_pairs/search_correlations에서 호출된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

DOWNTURN_QUANTILE = 0.10  # 코스피 지수 일간수익률 하위 10%를 "급락일"로 정의
SPEARMAN_DIVERGENCE_THRESHOLD = 0.20
ROLLING_WINDOWS = (60, 120)


def pearson_pvalues(corr: np.ndarray, n_obs: int) -> np.ndarray:
    """상관계수 행렬 + 표본수로 t검정 양측 p-value 행렬을 닫힌 형태로 계산.

    별도로 각 페어를 다시 상관 계산할 필요 없이, 이미 구한 상관계수 행렬에서
    바로 유도한다: t = r * sqrt((n-2)/(1-r^2)), 자유도 n-2.
    """
    df = max(n_obs - 2, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.clip(corr, -0.999999, 0.999999)
        t = r * np.sqrt(df / (1 - r**2))
        p = 2 * stats.t.sf(np.abs(t), df=df)
    return p


def benjamini_hochberg(pvalues: np.ndarray, alpha: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """BH-FDR 보정. 전체 pvalues 배열(=테스트한 전체 페어 모집단) 기준으로 계산해야
    다중검정 보정의 의미가 있다(상위 몇 개만 잘라서 보정하면 안 됨).

    Returns: (passed: bool 배열, adjusted_p: 배열) - 입력과 같은 순서.
    """
    pvalues = np.asarray(pvalues, dtype=float)
    m = len(pvalues)
    if m == 0:
        return np.array([], dtype=bool), np.array([])
    order = np.argsort(pvalues)
    ranked = pvalues[order]
    ranks = np.arange(1, m + 1)
    adjusted_ranked = np.clip(np.minimum.accumulate((ranked * m / ranks)[::-1])[::-1], 0, 1)
    passed_ranked = ranked <= (ranks / m) * alpha

    adjusted = np.empty(m)
    passed = np.empty(m, dtype=bool)
    adjusted[order] = adjusted_ranked
    passed[order] = passed_ranked
    return passed, adjusted


def spearman_matrix(returns: pd.DataFrame) -> np.ndarray:
    """Spearman = 순위로 변환한 값의 Pearson이므로, rank 변환 후 그대로 corrcoef 재사용
    (페어별로 다시 도는 것보다 훨씬 빠름)."""
    ranks = returns.rank(axis=0).to_numpy(dtype=float)
    return np.corrcoef(ranks.T)


def beta_residual_matrix(returns: pd.DataFrame, market_returns: pd.Series) -> np.ndarray:
    """각 종목 수익률을 시장(코스피) 수익률에 단순회귀(베타 추정)한 뒤,
    잔차(고유 수익률)끼리의 상관계수 행렬을 반환한다.
    """
    m = market_returns.reindex(returns.index).to_numpy(dtype=float)
    r = returns.to_numpy(dtype=float)
    m_centered = m - m.mean()
    var_m = np.sum(m_centered**2)
    if var_m == 0:
        return np.full((r.shape[1], r.shape[1]), np.nan)
    r_centered = r - r.mean(axis=0, keepdims=True)
    beta = (m_centered @ r_centered) / var_m  # (N,)
    residuals = r_centered - np.outer(m_centered, beta)
    return np.corrcoef(residuals.T)


def downturn_conditional_matrix(returns: pd.DataFrame, market_returns: pd.Series, quantile: float = DOWNTURN_QUANTILE) -> tuple[np.ndarray, int]:
    """코스피 지수 일간수익률 하위 quantile 구간(급락일)만 골라 그 날들만의 상관계수 행렬.

    Returns: (상관계수 행렬, 급락일로 판정된 거래일 수)
    """
    m = market_returns.reindex(returns.index)
    valid = m.notna()
    m = m[valid]
    if len(m) < 10:
        return np.full((returns.shape[1], returns.shape[1]), np.nan), 0
    cutoff = m.quantile(quantile)
    downturn_days = m[m <= cutoff].index
    sub = returns.loc[returns.index.intersection(downturn_days)]
    if len(sub) < 5 or sub.shape[1] < 2:
        return np.full((returns.shape[1], returns.shape[1]), np.nan), len(sub)
    return np.corrcoef(sub.to_numpy(dtype=float).T), len(sub)


def rolling_stability_badge(price_a: pd.Series, price_b: pd.Series, windows: tuple[int, int] = ROLLING_WINDOWS) -> bool | None:
    """최근 windows[0]일/windows[1]일 두 구간에서 상관계수 부호가 일치하면 "안정적 디커플링".

    두 구간 모두 계산 가능하고 부호가 일치할 때만 True. 데이터 부족하면 None(뱃지 미표시).
    """
    both = pd.concat([price_a, price_b], axis=1).dropna()
    if len(both) < max(windows) + 1:
        return None
    signs = []
    for w in windows:
        tail = both.tail(w + 1)
        r = np.log(tail).diff().dropna()
        if len(r) < 5:
            return None
        c = r.iloc[:, 0].corr(r.iloc[:, 1])
        if c is None or np.isnan(c):
            return None
        signs.append(np.sign(c))
    return bool(signs[0] == signs[1] and signs[0] != 0)
