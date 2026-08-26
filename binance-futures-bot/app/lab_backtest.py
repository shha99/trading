"""전략 실험실 후보들을 위한 범용 백테스트 엔진.

`backtest.py`(켈트너 전략 전용, R-배수 기준)와는 별개로 둔다 — 실험실은
- 방향이 롱/숏 둘 다 있고,
- 청산 방식이 전략마다 다르고(고정 손절+익절 / 동적 밴드 익절 / 트레일링 스탑),
- 카드에 보여줄 지표가 R-배수가 아니라 "거래당 평균 수익률(%)"이라
`backtest.py`의 로직을 그대로 재사용하기 어렵다. 대신 `app/lab_strategies.py`의
각 전략이 `precompute()`/`check_entry()`만 구현하면, 지표를 한 번만 벡터로
계산해두고(성능 - backtest.py에서 겪었던 O(n^2) 문제를 처음부터 피함) 이
모듈이 진입~청산~집계를 공통으로 처리한다.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from .lab_strategies import LabStrategy


def simulate_lab(df: pd.DataFrame, strategy: LabStrategy) -> list[dict]:
    n = len(df)
    if n < strategy.min_bars:
        return []

    pre = strategy.precompute(df)
    ctx = {
        **pre,
        "close": df["Close"].to_numpy(),
        "high": df["High"].to_numpy(),
        "low": df["Low"].to_numpy(),
        "open_": df["Open"].to_numpy(),
    }
    index = df.index

    trades: list[dict] = []
    k = strategy.min_bars - 1
    while k < n:
        entry = strategy.check_entry(k, ctx)
        if entry is None:
            k += 1
            continue

        exit_reason, exit_price, exit_idx = _walk_forward_exit(ctx, index, k, entry, n)
        entry_price = entry["entry_price"]
        direction = entry["direction"]
        if direction == "LONG":
            pct = (exit_price - entry_price) / entry_price * 100
        else:
            pct = (entry_price - exit_price) / entry_price * 100

        trades.append({
            "entry_time": str(index[k]),
            "exit_time": str(index[exit_idx]),
            "direction": direction,
            "entry_price": round(float(entry_price), 6),
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason,
            "pct_return": round(pct, 4),
        })
        k = max(exit_idx + 1, k + 1)  # 포지션 종료 이후부터 다음 진입 탐색 (중복 포지션 방지)

    return trades


def _walk_forward_exit(ctx: dict, index: pd.Index, entry_idx: int, entry: dict, n: int) -> tuple[str, float, int]:
    high, low, close = ctx["high"], ctx["low"], ctx["close"]
    direction = entry["direction"]

    time_stop_at = None
    if entry.get("time_stop_days") is not None:
        entry_ts = index[entry_idx]
        entry_dt = entry_ts.to_pydatetime() if hasattr(entry_ts, "to_pydatetime") else entry_ts
        time_stop_at = entry_dt + timedelta(days=entry["time_stop_days"])

    # 날짜(day) 대신 "봉 개수" 기준 시간손절 - 데이트레이딩 전략처럼 시간대가
    # 1분/5분 등으로 짧을 때 "N일"보다 "N봉"이 더 자연스러운 경우에 씀.
    time_stop_bar_idx = None
    if entry.get("time_stop_bars") is not None:
        time_stop_bar_idx = entry_idx + entry["time_stop_bars"]

    if entry.get("trailing"):
        return _walk_forward_trailing(ctx, entry_idx, direction, entry["trail_mult"], n)

    if entry.get("breakeven_trail"):
        return _walk_forward_breakeven_trail(ctx, entry_idx, entry, n)

    dynamic_stop_key = entry.get("dynamic_stop_key")
    stop_price = entry.get("stop_price")
    dynamic_target_key = entry.get("dynamic_target_key")
    fixed_target = entry.get("target_price")

    for j in range(entry_idx + 1, n):
        # 이동하는 선(예: 기준선)을 "종가"가 반대로 가로지르면 청산하는 방식
        # (일목균형표 기준선 트레일 등) — 저가/고가로 찌르는 고정 손절과 달리
        # 종가 기준이라 그 봉의 종가 자체를 청산가로 쓴다.
        if dynamic_stop_key is not None:
            stop_now = ctx[dynamic_stop_key][j]
            if stop_now is not None and not np.isnan(stop_now):
                if direction == "LONG" and close[j] < stop_now:
                    return "SL", float(close[j]), j
                if direction == "SHORT" and close[j] > stop_now:
                    return "SL", float(close[j]), j
        elif stop_price is not None:
            if direction == "LONG" and low[j] <= stop_price:
                return "SL", stop_price, j
            if direction == "SHORT" and high[j] >= stop_price:
                return "SL", stop_price, j

        target_now = ctx[dynamic_target_key][j] if dynamic_target_key else fixed_target
        target_valid = target_now is not None and not np.isnan(target_now)
        if direction == "LONG" and target_valid and high[j] >= target_now:
            return "TP", float(target_now), j
        if direction == "SHORT" and target_valid and low[j] <= target_now:
            return "TP", float(target_now), j

        if time_stop_at is not None:
            ts = index[j]
            ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if ts_dt >= time_stop_at:
                return "TIME", float(close[j]), j
        if time_stop_bar_idx is not None and j >= time_stop_bar_idx:
            return "TIME", float(close[j]), j

    return "TIME", float(close[-1]), n - 1


def _walk_forward_trailing(ctx: dict, entry_idx: int, direction: str, trail_mult: float, n: int) -> tuple[str, float, int]:
    high, low, close, atr_values = ctx["high"], ctx["low"], ctx["close"], ctx["atr"]
    extreme = high[entry_idx] if direction == "LONG" else low[entry_idx]

    for j in range(entry_idx + 1, n):
        if direction == "LONG":
            extreme = max(extreme, high[j])
            trail_level = extreme - trail_mult * atr_values[j]
            if low[j] <= trail_level:
                return "TRAIL", trail_level, j
        else:
            extreme = min(extreme, low[j])
            trail_level = extreme + trail_mult * atr_values[j]
            if high[j] >= trail_level:
                return "TRAIL", trail_level, j

    return "TRAIL", float(close[-1]), n - 1


def _walk_forward_breakeven_trail(ctx: dict, entry_idx: int, entry: dict, n: int) -> tuple[str, float, int]:
    """짧은 손절 + "본전 이동 후 트레일링" 청산 — 손실은 짧게 자르고 이익은 길게 태운다.

    ① 진입 직후엔 고정 손절선(`stop_price`)만 있음.
    ② 가격이 `breakeven_trigger_price`에 처음 닿으면 손절선을 진입가(본전)로 올린다
       — 여기서부터는 최악이어도 손실이 0에 가깝다.
    ③ 본전 이동 이후로는 고점(LONG)/저점(SHORT) - `trail_mult`×ATR 트레일링 스탑으로
       계속 따라가며 추적 — 익절 상한을 두지 않고 추세가 이어지는 한 최대한 태운다.

    (전략 실험실 조합 실험 - "큰 양봉 돌파"+"볼린저 돌파"가 같은 봉에서 동시에 롱
    신호를 낼 때만 진입하는 `BigCandleBollingerConfluenceStrategy`가 이 청산 방식을 씀.
    고정 익절을 쓰면 승률이 높아도 손절:익절 비율 때문에 기대값이 마이너스가 되는
    문제가 있었는데, 이 방식으로 바꾸니 학습/검증 구간 양쪽에서 승률 70%대 이상과
    플러스 기대값을 동시에 만족했다.)
    """
    high, low, close, atr_values = ctx["high"], ctx["low"], ctx["close"], ctx["atr"]
    direction = entry["direction"]
    entry_price = entry["entry_price"]
    stop_price = entry["stop_price"]
    trigger_price = entry["breakeven_trigger_price"]
    trail_mult = entry["trail_mult"]
    moved_to_breakeven = False
    extreme = high[entry_idx] if direction == "LONG" else low[entry_idx]

    for j in range(entry_idx + 1, n):
        if direction == "LONG":
            extreme = max(extreme, high[j])
            if not moved_to_breakeven and high[j] >= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if moved_to_breakeven:
                stop_price = max(stop_price, extreme - trail_mult * atr_values[j])
            if low[j] <= stop_price:
                return ("TRAIL" if moved_to_breakeven else "SL"), stop_price, j
        else:
            extreme = min(extreme, low[j])
            if not moved_to_breakeven and low[j] <= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if moved_to_breakeven:
                stop_price = min(stop_price, extreme + trail_mult * atr_values[j])
            if high[j] >= stop_price:
                return ("TRAIL" if moved_to_breakeven else "SL"), stop_price, j

    return "TIME", float(close[-1]), n - 1


def summarize_lab(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    pct = [t["pct_return"] for t in trades]
    wins = [p for p in pct if p > 0]
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3),
        "avg_pct_per_trade": round(sum(pct) / len(trades), 4),
        "total_pct": round(sum(pct), 4),
        "best_pct": round(max(pct), 4),
        "worst_pct": round(min(pct), 4),
    }
