"""상관계수 계산 - 전부 numpy/pandas 벡터 연산이며, 명시적 for-loop로 페어를
순회하지 않는다(코스피+코스닥 전체 조합은 수백만 페어라 반복문은 비현실적).

정확도 보강 통계(다중검정 보정, 하락일 조건부 상관계수, 시장베타 잔차,
Spearman, 롤링 안정성 배지)는 advanced_stats.py에 있고, 이 모듈이 그걸
스캔/검색 파이프라인에 엮는다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from . import advanced_stats, config

Badge = str  # "cross_sector" | "same_sector_anomaly"


@dataclass
class StockInfo:
    ticker: str
    name: str
    market: str
    sector: str
    market_cap: float | None
    avg_trading_value: float | None
    index_corr: float | None = None
    index_reversal: bool = False
    volatility: float | None = None  # 연환산 변동성(일별 로그수익률 표준편차 × √252)


@dataclass
class PairResult:
    stock_a: StockInfo
    stock_b: StockInfo
    correlation: float
    badge: Badge
    overlap_days: int
    reliability_warning: bool
    # --- 정확도 보강 필드 (계산 불가하면 None) ---
    p_value: float | None = None
    adjusted_p_value: float | None = None
    fdr_passed: bool | None = None
    spearman_corr: float | None = None
    spearman_divergence_warning: bool = False
    downturn_corr: float | None = None
    downturn_days: int | None = None
    downturn_divergence_warning: bool = False
    residual_corr: float | None = None
    residual_illusion_warning: bool = False
    stability_badge: bool | None = None


def log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """로그수익률 = ln(P_t / P_t-1). 상장 전/결측 구간은 NaN 유지."""
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(prices).diff()
    return lr.iloc[1:]


def eligible_universe(
    meta: pd.DataFrame,
    exclude_managed: bool,
    exclude_illiquid: bool,
    exclude_index_reversal: bool = True,
    min_vol_percentile: float = 0.0,
    sector_scope: str | None = None,
) -> pd.Index:
    """관리/정지/우선주, 유동성 하위, 지수 역행, 저변동성 하위, 섹터 한정 필터를 적용한
    종목 코드 집합.

    min_vol_percentile: 연환산 변동성 하위 이 분위수(0~0.5)보다 낮은 종목 제외
    (기본 0.20 = 하위 20% 제외). 저변동성·경기방어 종목은 평소 거의 안 움직이다
    개별 이슈로 며칠만 튀는 패턴이 흔해, 그 소수의 날이 상관계수 계산을 지배해
    통계적으로 불안정한 강한 음의 상관관계로 오탐되기 쉽다.
    sector_scope: "all"이 아니면 해당 섹터 소속 종목으로만 후보군을 한정(섹터별 조회).
    """
    if meta.empty:
        return pd.Index([])
    mask = pd.Series(True, index=meta.index)
    if exclude_managed:
        mask &= ~meta["is_preferred"].fillna(False)
        mask &= ~meta["is_managed"].fillna(False)
        mask &= ~meta["is_halted"].fillna(False)
    if exclude_illiquid and "avg_trading_value" in meta.columns and meta["avg_trading_value"].notna().any():
        cutoff = meta["avg_trading_value"].quantile(config.DEFAULT_LIQUIDITY_EXCLUDE_PCTL)
        mask &= meta["avg_trading_value"].fillna(0) >= cutoff
    if exclude_index_reversal and "index_reversal" in meta.columns:
        mask &= ~meta["index_reversal"].fillna(False)
    if min_vol_percentile and min_vol_percentile > 0 and "volatility" in meta.columns and meta["volatility"].notna().any():
        cutoff = meta["volatility"].quantile(min_vol_percentile)
        mask &= meta["volatility"].fillna(-np.inf) >= cutoff
    if sector_scope and sector_scope != "all" and "sector" in meta.columns:
        mask &= meta["sector"].fillna("기타") == sector_scope
    return meta.index[mask]


def stock_info(ticker: str, meta: pd.DataFrame) -> StockInfo:
    if ticker in meta.index:
        row = meta.loc[ticker]
        return StockInfo(
            ticker=ticker,
            name=str(row.get("name", ticker)),
            market=str(row.get("market", "")),
            sector=str(row.get("sector", "기타")),
            market_cap=_safe_float(row.get("market_cap")),
            avg_trading_value=_safe_float(row.get("avg_trading_value")),
            index_corr=_safe_float(row.get("index_corr")),
            index_reversal=bool(row.get("index_reversal", False)),
            volatility=_safe_float(row.get("volatility")),
        )
    return StockInfo(ticker=ticker, name=ticker, market="", sector="기타", market_cap=None, avg_trading_value=None)


def _safe_float(v) -> float | None:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _badge(sector_a: str, sector_b: str, sector_scope_active: bool = False) -> Badge:
    if sector_a != sector_b:
        return "cross_sector"
    # 섹터별 조회로 후보군을 한 업종에 한정한 경우, 동일섹터인 게 당연하므로
    # "이례적"이 아니라 중립적인 "업종 내 디커플링"으로 구분한다.
    return "sector_scoped" if sector_scope_active else "same_sector_anomaly"


def _kospi_returns(index_prices: pd.DataFrame, window_index: pd.Index) -> pd.Series | None:
    if index_prices is None or index_prices.empty or "KOSPI" not in index_prices.columns:
        return None
    idx = index_prices["KOSPI"].reindex(window_index)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(idx).diff()


def _rolling_stability_for_pairs(prices: pd.DataFrame, pairs: list[PairResult]) -> None:
    """최종 후보로 추려진 소수 페어에 한해서만 60/120일 롤링 안정성 배지를 계산(저비용)."""
    for p in pairs:
        a, b = p.stock_a.ticker, p.stock_b.ticker
        if a not in prices.columns or b not in prices.columns:
            continue
        p.stability_badge = advanced_stats.rolling_stability_badge(prices[a], prices[b])


def scan_pairs(
    prices: pd.DataFrame,
    meta: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    top_n: int = config.DEFAULT_TOP_N,
    exclude_managed: bool = True,
    exclude_illiquid: bool = True,
    exclude_index_reversal: bool = True,
    sector_filter: str = "all",  # all | cross | same
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_trading_value: float | None = None,
    index_prices: pd.DataFrame | None = None,
    min_vol_percentile: float = config.DEFAULT_MIN_VOLATILITY_PCTL,
    sector_scope: str | None = None,  # "all" 또는 실제 섹터명 - 후보군을 그 섹터로 한정
) -> dict:
    """모드 A: 지정 기간 전체 스캔.

    Returns dict:
      pairs, low_significance_pairs, same_sector_highlights,
      reliability_warning, trading_days, universe_size, pool_size
    """
    sector_scope_active = bool(sector_scope and sector_scope != "all")
    window = prices.loc[(prices.index >= start) & (prices.index <= end)]
    trading_days = len(window)
    reliability_warning = trading_days < config.MIN_RELIABLE_TRADING_DAYS

    universe = eligible_universe(
        meta, exclude_managed, exclude_illiquid, exclude_index_reversal,
        min_vol_percentile=min_vol_percentile, sector_scope=sector_scope,
    )
    universe = universe.intersection(window.columns)
    if min_market_cap is not None:
        universe = universe[meta.loc[universe, "market_cap"].fillna(0) >= min_market_cap]
    if max_market_cap is not None:
        universe = universe[meta.loc[universe, "market_cap"].fillna(np.inf) <= max_market_cap]
    if min_trading_value is not None:
        universe = universe[meta.loc[universe, "avg_trading_value"].fillna(0) >= min_trading_value]

    window = window[universe]
    # 구간 내 결측(상장 전/장기 정지 등)이 하나라도 있는 종목은 이 구간 상관계수 계산에서 제외.
    complete_cols = window.columns[window.notna().all(axis=0)]
    window = window[complete_cols]

    empty = {
        "pairs": [], "low_significance_pairs": [], "same_sector_highlights": [],
        "reliability_warning": reliability_warning, "trading_days": trading_days,
        "universe_size": len(universe), "pool_size": 0,
    }
    if window.shape[1] < 2 or window.shape[0] < 2:
        return empty

    returns = log_returns(window)
    tickers = returns.columns.to_numpy()
    n_obs = len(returns)
    corr = np.corrcoef(returns.to_numpy(dtype=float).T)

    n = len(tickers)
    iu, ju = np.triu_indices(n, k=1)
    vals = corr[iu, ju]
    valid = ~np.isnan(vals)
    iu, ju, vals = iu[valid], ju[valid], vals[valid]

    if len(vals) == 0:
        return empty

    # --- 1-4a: 다중검정(BH-FDR) 보정 - 전체 후보 모집단 기준으로 계산 ---
    pvals_matrix = advanced_stats.pearson_pvalues(corr, n_obs)
    pvals = pvals_matrix[iu, ju]
    fdr_passed, adj_p = advanced_stats.benjamini_hochberg(pvals, alpha=config.FDR_ALPHA)

    # --- Spearman (전체, 같은 비용) ---
    spearman_full = advanced_stats.spearman_matrix(returns)
    spearman_vals = spearman_full[iu, ju]

    # --- 하락일 조건부 상관계수 / 시장베타 잔차 상관계수 (코스피 지수 기준) ---
    market_ret = _kospi_returns(index_prices, returns.index)
    if market_ret is not None:
        downturn_corr_full, downturn_n = advanced_stats.downturn_conditional_matrix(returns, market_ret)
        residual_corr_full = advanced_stats.beta_residual_matrix(returns, market_ret)
        downturn_vals = downturn_corr_full[iu, ju]
        residual_vals = residual_corr_full[iu, ju]
    else:
        downturn_n = 0
        downturn_vals = np.full(len(vals), np.nan)
        residual_vals = np.full(len(vals), np.nan)

    sectors = meta.reindex(tickers)["sector"].fillna("기타").to_numpy()
    is_same_sector = sectors[iu] == sectors[ju]

    def _make_result(k: int) -> PairResult:
        a, b = tickers[iu[k]], tickers[ju[k]]
        info_a, info_b = stock_info(a, meta), stock_info(b, meta)
        sp = float(spearman_vals[k]) if not np.isnan(spearman_vals[k]) else None
        dn = float(downturn_vals[k]) if not np.isnan(downturn_vals[k]) else None
        rs = float(residual_vals[k]) if not np.isnan(residual_vals[k]) else None
        raw = float(vals[k])
        return PairResult(
            stock_a=info_a,
            stock_b=info_b,
            correlation=raw,
            badge=_badge(info_a.sector, info_b.sector, sector_scope_active),
            overlap_days=trading_days,
            reliability_warning=reliability_warning,
            p_value=float(pvals[k]),
            adjusted_p_value=float(adj_p[k]),
            fdr_passed=bool(fdr_passed[k]),
            spearman_corr=sp,
            spearman_divergence_warning=(sp is not None and abs(raw - sp) >= advanced_stats.SPEARMAN_DIVERGENCE_THRESHOLD),
            downturn_corr=dn,
            downturn_days=downturn_n if dn is not None else None,
            downturn_divergence_warning=(dn is not None and (dn - raw) >= 0.3),
            residual_corr=rs,
            residual_illusion_warning=(rs is not None and rs > -0.2 and raw <= -0.3),
        )

    # --- 정렬: 오름차순(가장 음수부터), 사용자가 고른 섹터 필터 적용 ---
    order_all = np.argsort(vals)
    if sector_filter in ("cross", "same"):
        keep = (~is_same_sector) if sector_filter == "cross" else is_same_sector
        order_all = order_all[keep[order_all]]

    # 디커플링 스크리너이므로 "음의" 상관관계 후보만 대상으로 한다 - 양의 상관(동조)은
    # 통계적으로 유의해도 애초에 디커플링이 아니므로 상위/참고용 목록 어디에도 넣지 않는다.
    #
    # 1: 정렬 로직 = "하드 필터"가 아니라 "계층적 fallback". 1티어(FDR 통과)를 상관계수
    # 오름차순으로 우선 채우고, top_n에 못 미치면 2티어(미통과, 여전히 음의 상관)로 나머지
    # 자리를 채운다 - 후보군(pool)에 종목이 남아있는 한 "결과 없음"을 반환하지 않는다.
    negative_order = order_all[vals[order_all] < 0]
    tier1 = [k for k in negative_order if fdr_passed[k]]
    tier2 = [k for k in negative_order if not fdr_passed[k]]
    pool_size = len(tier1) + len(tier2)

    main_idx = tier1[:top_n]
    if len(main_idx) < top_n:
        main_idx = main_idx + tier2[: top_n - len(main_idx)]
    used_tier2 = max(0, len(main_idx) - len(tier1))
    low_sig_idx = tier2[used_tier2 : used_tier2 + top_n]

    pairs = [_make_result(k) for k in main_idx]
    low_significance_pairs = [_make_result(k) for k in low_sig_idx]

    # --- 2: 동일섹터 이례적 디커플링 하이라이트 - 사용자의 섹터 필터와 무관하게 항상 별도 추출.
    # 동일섹터인데 "음의" 상관관계인 경우만 이례적이다 - 양의 상관(동조)은 정상이므로 제외.
    # 3: 섹터별 조회로 후보군을 이미 한 업종으로 한정했다면 전부 동일섹터가 당연하므로 생략.
    if sector_scope_active:
        same_sector_highlights = []
    else:
        same_sector_order = [k for k in np.argsort(vals) if is_same_sector[k] and vals[k] < 0][:10]
        same_sector_highlights = [_make_result(k) for k in same_sector_order]

    _rolling_stability_for_pairs(prices, pairs + low_significance_pairs + same_sector_highlights)

    return {
        "pairs": pairs,
        "low_significance_pairs": low_significance_pairs,
        "same_sector_highlights": same_sector_highlights,
        "reliability_warning": reliability_warning,
        "trading_days": trading_days,
        "universe_size": len(universe),
        "pool_size": pool_size,
    }


def search_correlations(
    ticker: str,
    prices: pd.DataFrame,
    meta: pd.DataFrame,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    full_period: bool = False,
    top_n: int = config.DEFAULT_TOP_N,
    exclude_managed: bool = True,
    exclude_illiquid: bool = True,
    exclude_index_reversal: bool = True,
    sector_filter: str = "all",
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_trading_value: float | None = None,
    index_prices: pd.DataFrame | None = None,
    min_vol_percentile: float = config.DEFAULT_MIN_VOLATILITY_PCTL,
    sector_scope: str | None = None,  # "all" 또는 실제 섹터명 - 비교 후보군을 그 섹터로 한정
) -> dict:
    """모드 B: 기준 종목 하나 vs 나머지 전종목.

    full_period=True면 기준 종목의 상장 시점부터 현재까지 전체 데이터를 쓰고,
    상대 종목별로 실제 겹치는 거래일 수만큼만(pairwise) 상관계수를 계산한다.
    """
    sector_scope_active = bool(sector_scope and sector_scope != "all")
    empty = {
        "pairs": [], "low_significance_pairs": [], "same_sector_highlights": [],
        "any_short_sample": False, "pool_size": 0,
    }
    if ticker not in prices.columns:
        return empty

    base_series = prices[ticker].dropna()
    if full_period:
        window = prices.loc[base_series.index.min():]
    else:
        window = prices.loc[(prices.index >= start) & (prices.index <= end)]

    base_col = window[ticker].dropna()
    if len(base_col) < 2:
        return {**empty, "any_short_sample": True}

    universe = eligible_universe(
        meta, exclude_managed, exclude_illiquid, exclude_index_reversal,
        min_vol_percentile=min_vol_percentile, sector_scope=sector_scope,
    )
    universe = universe.intersection(window.columns).difference([ticker])
    if min_market_cap is not None:
        universe = universe[meta.loc[universe, "market_cap"].fillna(0) >= min_market_cap]
    if max_market_cap is not None:
        universe = universe[meta.loc[universe, "market_cap"].fillna(np.inf) <= max_market_cap]
    if min_trading_value is not None:
        universe = universe[meta.loc[universe, "avg_trading_value"].fillna(0) >= min_trading_value]

    candidates = window[universe]
    base_returns = np.log(window[ticker]).diff()
    cand_returns = np.log(candidates).diff()

    corr = cand_returns.corrwith(base_returns)
    overlap = cand_returns.notna().to_numpy() & base_returns.notna().to_numpy().reshape(-1, 1)
    overlap_counts = pd.Series(overlap.sum(axis=0), index=candidates.columns)
    corr = corr.dropna()
    if corr.empty:
        return empty

    # 종목마다 겹치는 거래일 수가 달라(특히 전체기간 모드) 표본수별로 p-value를 따로 계산.
    n_obs_series = overlap_counts.reindex(corr.index)
    dfree = (n_obs_series - 2).clip(lower=1)
    r_clip = corr.clip(-0.999999, 0.999999)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = r_clip * np.sqrt(dfree / (1 - r_clip**2))
    pvals = pd.Series(2 * scipy_stats.t.sf(np.abs(t_stat), df=dfree), index=corr.index)
    fdr_mask, adj_p = advanced_stats.benjamini_hochberg(pvals.to_numpy(), alpha=config.FDR_ALPHA)
    fdr_passed = pd.Series(fdr_mask, index=corr.index)
    adj_p_series = pd.Series(adj_p, index=corr.index)

    spearman = cand_returns.rank().corrwith(base_returns.rank())

    market_ret = _kospi_returns(index_prices, window.index)
    if market_ret is not None:
        m = market_ret.reindex(cand_returns.index)
        downturn_days_idx = m[m <= m.quantile(advanced_stats.DOWNTURN_QUANTILE)].dropna().index
        if len(downturn_days_idx) >= 5:
            downturn_corr = cand_returns.loc[downturn_days_idx].corrwith(base_returns.loc[downturn_days_idx])
        else:
            downturn_corr = pd.Series(dtype=float)
        downturn_n = len(downturn_days_idx)

        m_c = (m - m.mean()).fillna(0)
        var_m = float((m_c**2).sum()) or np.nan
        base_c = base_returns - base_returns.mean()
        beta_base = float((m_c * base_c).sum() / var_m) if var_m else np.nan
        base_resid = base_c - beta_base * m_c
        cand_c = cand_returns - cand_returns.mean()
        beta_cand = (m_c.to_numpy().reshape(-1, 1) * cand_c.to_numpy()).sum(axis=0) / var_m if var_m else np.full(cand_c.shape[1], np.nan)
        cand_resid = cand_c - m_c.to_numpy().reshape(-1, 1) * beta_cand
        residual_corr = cand_resid.corrwith(base_resid)
    else:
        downturn_corr = pd.Series(dtype=float)
        downturn_n = 0
        residual_corr = pd.Series(dtype=float)

    if sector_filter in ("cross", "same"):
        base_sector = stock_info(ticker, meta).sector
        sectors = meta.reindex(corr.index)["sector"].fillna("기타")
        is_cross = sectors != base_sector
        corr_ranked = corr[is_cross] if sector_filter == "cross" else corr[~is_cross]
    else:
        corr_ranked = corr

    base_info = stock_info(ticker, meta)

    def _make(other: str, value: float) -> PairResult:
        days = int(overlap_counts.get(other, 0))
        warn = days < config.MIN_RELIABLE_TRADING_DAYS
        other_info = stock_info(other, meta)
        sp = spearman.get(other)
        sp = float(sp) if sp is not None and not pd.isna(sp) else None
        dn = downturn_corr.get(other)
        dn = float(dn) if dn is not None and not pd.isna(dn) else None
        rs = residual_corr.get(other)
        rs = float(rs) if rs is not None and not pd.isna(rs) else None
        return PairResult(
            stock_a=base_info,
            stock_b=other_info,
            correlation=float(value),
            badge=_badge(base_info.sector, other_info.sector, sector_scope_active),
            overlap_days=days,
            reliability_warning=warn,
            p_value=_safe_float(pvals.get(other)),
            adjusted_p_value=_safe_float(adj_p_series.get(other)),
            fdr_passed=bool(fdr_passed.get(other)) if other in fdr_passed.index else None,
            spearman_corr=sp,
            spearman_divergence_warning=(sp is not None and abs(float(value) - sp) >= advanced_stats.SPEARMAN_DIVERGENCE_THRESHOLD),
            downturn_corr=dn,
            downturn_days=downturn_n if dn is not None else None,
            downturn_divergence_warning=(dn is not None and (dn - float(value)) >= 0.3),
            residual_corr=rs,
            residual_illusion_warning=(rs is not None and rs > -0.2 and float(value) <= -0.3),
        )

    # 디커플링 스크리너이므로 "음의" 상관관계 후보만 대상으로 한다 - 양의 상관(동조)은
    # 통계적으로 유의해도 애초에 디커플링이 아니므로 상위/참고용 목록 어디에도 넣지 않는다.
    #
    # 1: 정렬 로직 = "하드 필터"가 아니라 "계층적 fallback". 1티어(FDR 통과)를 상관계수
    # 오름차순으로 우선 채우고, top_n에 못 미치면 2티어(미통과, 여전히 음의 상관)로 나머지
    # 자리를 채운다 - 후보군(pool)에 종목이 남아있는 한 "결과 없음"을 반환하지 않는다.
    sorted_all = corr_ranked[corr_ranked < 0].sort_values(ascending=True)
    tier1_idx = [t for t in sorted_all.index if fdr_passed.get(t, False)]
    tier2_idx = [t for t in sorted_all.index if not fdr_passed.get(t, False)]
    pool_size = len(tier1_idx) + len(tier2_idx)

    main_idx = tier1_idx[:top_n]
    if len(main_idx) < top_n:
        main_idx = main_idx + tier2_idx[: top_n - len(main_idx)]
    used_tier2 = max(0, len(main_idx) - len(tier1_idx))
    low_sig_idx = tier2_idx[used_tier2 : used_tier2 + top_n]

    pairs = [_make(t, sorted_all[t]) for t in main_idx]
    low_significance_pairs = [_make(t, sorted_all[t]) for t in low_sig_idx]

    # 3: 섹터별 조회로 후보군을 이미 한 업종으로 한정했다면 전부 동일섹터가 당연하므로 생략.
    same_sector_idx = None
    if not sector_scope_active and "sector" in meta.columns:
        base_sector = base_info.sector
        sectors_all = meta.reindex(corr.index)["sector"].fillna("기타")
        # 동일섹터인데 "음의" 상관관계인 경우만 이례적이다 - 양의 상관(동조)은 정상이므로 제외.
        same_mask = (sectors_all == base_sector) & (corr < 0)
        same_sector_sorted = corr[same_mask].sort_values(ascending=True)
        same_sector_idx = list(same_sector_sorted.index[:10])
    same_sector_highlights = [_make(t, corr[t]) for t in (same_sector_idx or [])]

    any_short_sample = any(p.reliability_warning for p in pairs + low_significance_pairs)

    _rolling_stability_for_pairs(prices, pairs + low_significance_pairs + same_sector_highlights)

    return {
        "pairs": pairs,
        "low_significance_pairs": low_significance_pairs,
        "same_sector_highlights": same_sector_highlights,
        "any_short_sample": any_short_sample,
        "pool_size": pool_size,
    }
