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

import pandas as pd

from app.history import fetch_extended_history
from app.strategy import KeltnerReclaimStrategy, Signal


def simulate(df: pd.DataFrame, strategy: KeltnerReclaimStrategy) -> list[dict]:
    trades: list[dict] = []
    i = strategy.min_bars
    n = len(df)

    while i <= n:
        window = df.iloc[:i]
        signal: Signal | None = strategy.evaluate("BT", "BT", window)
        if signal is None:
            i += 1
            continue

        entry_idx = i - 1  # window의 마지막 봉 = 시그널이 발생한 봉
        exit_reason, exit_price, exit_idx = _walk_forward_exit(df, entry_idx, signal)
        entry_time = df.index[entry_idx]
        exit_time = df.index[exit_idx] if exit_idx < n else df.index[-1]

        r = (exit_price - signal.entry_price) / (signal.entry_price - signal.stop_price)
        trades.append(
            {
                "entry_time": str(entry_time),
                "exit_time": str(exit_time),
                "entry_price": signal.entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "r_multiple": round(r, 3),
            }
        )
        i = max(exit_idx + 1, i + 1)  # 포지션 종료 이후부터 다음 진입 탐색 (중복 포지션 방지)

    return trades


def _walk_forward_exit(df: pd.DataFrame, entry_idx: int, signal: Signal) -> tuple[str, float, int]:
    for j in range(entry_idx + 1, len(df)):
        bar = df.iloc[j]
        ts = df.index[j]
        if bar["Low"] <= signal.stop_price:
            return "SL", signal.stop_price, j
        if bar["High"] >= signal.target_price:
            return "TP", signal.target_price, j
        if ts.to_pydatetime() >= signal.time_stop_at:
            return "TIME", float(bar["Close"]), j
    # 데이터 끝까지 못 빠져나온 경우 마지막 종가로 강제 정리
    return "TIME", float(df.iloc[-1]["Close"]), len(df) - 1


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
