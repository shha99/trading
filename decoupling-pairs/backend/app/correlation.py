"""상관계수 계산 - 전부 numpy/pandas 벡터 연산이며, 명시적 for-loop로 페어를
순회하지 않는다(코스피+코스닥 전체 조합은 수백만 페어라 반복문은 비현실적).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config

Badge = str  # "cross_sector" | "same_sector_anomaly"


@dataclass
class StockInfo:
    ticker: str
    name: str
    market: str
    sector: str
    market_cap: float | None
    avg_trading_value: float | None


@dataclass
class PairResult:
    stock_a: StockInfo
    stock_b: StockInfo
    correlation: float
    badge: Badge
    overlap_days: int
    reliability_warning: bool


def log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """로그수익률 = ln(P_t / P_t-1). 상장 전/결측 구간은 NaN 유지."""
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(prices).diff()
    return lr.iloc[1:]


def eligible_universe(meta: pd.DataFrame, exclude_managed: bool, exclude_illiquid: bool) -> pd.Index:
    """관리/정지/우선주, 유동성 하위 종목 필터를 적용한 종목 코드 집합."""
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
        )
    return StockInfo(ticker=ticker, name=ticker, market="", sector="기타", market_cap=None, avg_trading_value=None)


def _safe_float(v) -> float | None:
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _badge(sector_a: str, sector_b: str) -> Badge:
    return "cross_sector" if sector_a != sector_b else "same_sector_anomaly"


def scan_pairs(
    prices: pd.DataFrame,
    meta: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    top_n: int = config.DEFAULT_TOP_N,
    exclude_managed: bool = True,
    exclude_illiquid: bool = True,
    sector_filter: str = "all",  # all | cross | same
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_trading_value: float | None = None,
) -> tuple[list[PairResult], bool, int]:
    """모드 A: 지정 기간 전체 스캔.

    Returns: (페어 리스트, 표본기간 짧음 경고, 사용된 거래일 수)
    """
    window = prices.loc[(prices.index >= start) & (prices.index <= end)]
    trading_days = len(window)
    reliability_warning = trading_days < config.MIN_RELIABLE_TRADING_DAYS

    universe = eligible_universe(meta, exclude_managed, exclude_illiquid)
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

    if window.shape[1] < 2 or window.shape[0] < 2:
        return [], reliability_warning, trading_days

    returns = log_returns(window)
    tickers = returns.columns.to_numpy()
    corr = np.corrcoef(returns.to_numpy(dtype=float).T)

    n = len(tickers)
    iu, ju = np.triu_indices(n, k=1)
    vals = corr[iu, ju]
    valid = ~np.isnan(vals)
    iu, ju, vals = iu[valid], ju[valid], vals[valid]

    if sector_filter in ("cross", "same"):
        sectors = meta.reindex(tickers)["sector"].fillna("기타").to_numpy()
        is_cross = sectors[iu] != sectors[ju]
        keep = is_cross if sector_filter == "cross" else ~is_cross
        iu, ju, vals = iu[keep], ju[keep], vals[keep]

    order = np.argsort(vals)  # 오름차순 = 가장 음수부터
    order = order[: max(top_n, 0)]

    results: list[PairResult] = []
    for k in order:
        a, b = tickers[iu[k]], tickers[ju[k]]
        info_a, info_b = stock_info(a, meta), stock_info(b, meta)
        results.append(
            PairResult(
                stock_a=info_a,
                stock_b=info_b,
                correlation=float(vals[k]),
                badge=_badge(info_a.sector, info_b.sector),
                overlap_days=trading_days,
                reliability_warning=reliability_warning,
            )
        )
    return results, reliability_warning, trading_days


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
    sector_filter: str = "all",
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_trading_value: float | None = None,
) -> tuple[list[PairResult], bool]:
    """모드 B: 기준 종목 하나 vs 나머지 전종목.

    full_period=True면 기준 종목의 상장 시점부터 현재까지 전체 데이터를 쓰고,
    상대 종목별로 실제 겹치는 거래일 수만큼만(pairwise) 상관계수를 계산한다.
    """
    if ticker not in prices.columns:
        return [], False

    base_series = prices[ticker]
    base_series = base_series.dropna()
    if full_period:
        window = prices.loc[base_series.index.min():]
    else:
        window = prices.loc[(prices.index >= start) & (prices.index <= end)]

    base_col = window[ticker].dropna()
    if len(base_col) < 2:
        return [], True

    universe = eligible_universe(meta, exclude_managed, exclude_illiquid)
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

    corr = cand_returns.corrwith(base_returns)  # pairwise, NaN 자동 정렬 처리(내장 pandas 연산)
    overlap = cand_returns.notna().to_numpy() & base_returns.notna().to_numpy().reshape(-1, 1)
    overlap_counts = pd.Series(overlap.sum(axis=0), index=candidates.columns)

    corr = corr.dropna()
    if sector_filter in ("cross", "same"):
        base_sector = stock_info(ticker, meta).sector
        sectors = meta.reindex(corr.index)["sector"].fillna("기타")
        is_cross = sectors != base_sector
        corr = corr[is_cross] if sector_filter == "cross" else corr[~is_cross]

    corr = corr.sort_values(ascending=True).head(max(top_n, 0))

    base_info = stock_info(ticker, meta)
    results: list[PairResult] = []
    any_short_sample = False
    for other, value in corr.items():
        days = int(overlap_counts.get(other, 0))
        warn = days < config.MIN_RELIABLE_TRADING_DAYS
        any_short_sample = any_short_sample or warn
        other_info = stock_info(other, meta)
        results.append(
            PairResult(
                stock_a=base_info,
                stock_b=other_info,
                correlation=float(value),
                badge=_badge(base_info.sector, other_info.sector),
                overlap_days=days,
                reliability_warning=warn,
            )
        )
    return results, any_short_sample
