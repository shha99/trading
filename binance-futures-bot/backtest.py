"""구현한 전략(KeltnerReclaimStrategy) 단독 백테스트 — sanity check용.

친구분이 이미 114개 조합을 탐색해 이 규칙을 골랐으므로, 여기서는 그 탐색을
다시 하지 않는다. 대신 "우리가 구현한 코드가 그 규칙을 올바르게 재현하고
있는지"를 최근 과거 데이터로 빠르게 확인하는 용도다 (플러스 기대값이
나오는지 정도의 방향성 체크 — 원 저장소의 5년치 학습/검증 성적표를
대체하지 않는다).

사용법:
    python backtest.py --symbol BTCUSDT --timeframe 1h --bars 1500
"""
from __future__ import annotations

import argparse
from datetime import timedelta

import numpy as np
import pandas as pd

from app.history import fetch_extended_history
from app.indicators import atr, ema, keltner_lower
from app.strategy import KeltnerReclaimStrategy


def simulate(df: pd.DataFrame, strategy: KeltnerReclaimStrategy) -> list[dict]:
    """전략의 진입/청산 조건을 그대로 재현하되, 지표를 매 봉마다 처음부터
    다시 계산하지 않는다 (딱 한 번만 벡터 계산) — 5년치 데이터(수만 봉)에서
    이전 구현은 봉마다 지금까지의 전체 구간을 다시 계산해 사실상 O(n^2)이라
    build_stats.py가 실전에서 못 쓸 만큼 느렸다(실측: 1개 조합에도 여러 분).
    EMA/ATR/켈트너는 전부 과거 값만 보는(미래를 안 보는) 계산이라, 전체
    구간에 대해 한 번에 계산해도 봉 k 시점의 값은 "그 시점까지의 데이터로
    계산한 값"과 정확히 같다 - 즉 전략의 판정 로직(strategy.evaluate)과
    결과가 동일하다."""
    n = len(df)
    if n < strategy.min_bars:
        return []

    close = df["Close"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    ema_trend = ema(df["Close"], strategy.trend_ema_period).to_numpy()
    kelt_lower = keltner_lower(
        df, strategy.keltner_ema_period, strategy.keltner_atr_period, strategy.keltner_atr_mult
    ).to_numpy()
    atr_values = atr(df, strategy.keltner_atr_period).to_numpy()
    index = df.index

    trades: list[dict] = []
    k = strategy.min_bars - 1  # strategy.evaluate(df.iloc[:i])가 보던 "마지막 봉"의 0-based 인덱스 (i-1)

    while k < n:
        if (
            np.isnan(ema_trend[k]) or np.isnan(kelt_lower[k]) or np.isnan(kelt_lower[k - 1])
            or np.isnan(atr_values[k])
        ):
            k += 1
            continue

        trend_ok = close[k] > ema_trend[k]
        pullback = close[k - 1] <= kelt_lower[k - 1]
        reclaim = close[k] > kelt_lower[k]
        if not (trend_ok and pullback and reclaim):
            k += 1
            continue

        entry_price = float(close[k])
        atr_now = float(atr_values[k])
        stop_price = entry_price - strategy.stop_atr_mult * atr_now
        target_price = entry_price + strategy.target_atr_mult * atr_now
        entry_ts = index[k]
        entry_dt = entry_ts.to_pydatetime() if hasattr(entry_ts, "to_pydatetime") else entry_ts
        time_stop_at = entry_dt + timedelta(days=strategy.time_stop_days)

        exit_reason, exit_price, exit_idx = _walk_forward_exit(
            high, low, close, index, k, stop_price, target_price, time_stop_at, n
        )
        exit_time = index[exit_idx]

        r = (exit_price - entry_price) / (entry_price - stop_price)
        trades.append(
            {
                "entry_time": str(entry_ts),
                "exit_time": str(exit_time),
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "r_multiple": round(r, 3),
            }
        )
        k = max(exit_idx + 1, k + 1)  # 포지션 종료 이후부터 다음 진입 탐색 (중복 포지션 방지)

    return trades


def _walk_forward_exit(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, index: pd.Index,
    entry_idx: int, stop_price: float, target_price: float, time_stop_at, n: int,
) -> tuple[str, float, int]:
    for j in range(entry_idx + 1, n):
        if low[j] <= stop_price:
            return "SL", stop_price, j
        if high[j] >= target_price:
            return "TP", target_price, j
        ts = index[j]
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt >= time_stop_at:
            return "TIME", float(close[j]), j
    # 데이터 끝까지 못 빠져나온 경우 마지막 종가로 강제 정리
    return "TIME", float(close[-1]), n - 1


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r_values = [t["r_multiple"] for t in trades]
    wins = [r for r in r_values if r > 0]
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3),
        "total_r": round(sum(r_values), 3),
        "avg_r": round(sum(r_values) / len(trades), 3),
        "best_r": round(max(r_values), 3),
        "worst_r": round(min(r_values), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--bars", type=int, default=1500)
    args = parser.parse_args()

    df = fetch_extended_history(args.symbol, args.timeframe, args.bars)
    if df is None or df.empty:
        print("데이터를 가져오지 못했습니다.")
        return

    strategy = KeltnerReclaimStrategy()
    trades = simulate(df, strategy)
    summary = summarize(trades)

    print(f"{args.symbol} {args.timeframe} ({len(df)}봉, {df.index[0]} ~ {df.index[-1]})")
    print(summary)
    for t in trades[-10:]:
        print(t)


if __name__ == "__main__":
    main()
