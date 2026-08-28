"""화이트리스트에 없어(auto_traded=NO) 실제 주문이 안 나간 시그널도, "그
로직대로 진짜 체결됐다면 어떻게 됐을지"를 시그널 자체의 stop_price/
target_price/time_stop_at 기준으로 계속 추적한다 - 실제 주문은 절대 내지
않는다(순수 사후 판정, 시그널 스캔/자동매매 게이팅과 완전히 분리된 로직).

`backtest.py::_walk_forward_exit()`(켈트너 전략의 고정 손절/익절/시간손절
walk-forward 로직)를 그대로 재사용한다 - 청산 판정 로직을 두 번 구현하지
않아 백테스트와 이 실시간 추적이 절대 어긋나지 않는다.
"""
from __future__ import annotations

import logging

import pandas as pd

from backtest import _walk_forward_exit
from .db import SessionLocal, SignalRecord
from .history import fetch_klines

logger = logging.getLogger(__name__)

# 최대 시간손절(3일)보다 넉넉히 - 가장 촘촘한 시간대(15m 기준 3일=288봉)도
# 여유있게 커버한다.
LOOKBACK_LIMIT = 1500


def check_signal_outcomes() -> int:
    """virtual_status가 아직 OPEN인 시그널을 전부 검사해, SL/TP/TIME 중
    하나로 확정되면 갱신한다. 갱신된 시그널 수를 반환한다."""
    session = SessionLocal()
    updated = 0
    try:
        open_signals = session.query(SignalRecord).filter(SignalRecord.virtual_status == "OPEN").all()
        for signal in open_signals:
            try:
                if _resolve_one(session, signal):
                    updated += 1
            except Exception:
                logger.exception(
                    "가상 체결 결과 추적 실패: %s %s (id=%s)", signal.symbol, signal.timeframe, signal.id
                )
        return updated
    finally:
        session.close()


def _resolve_one(session, signal: SignalRecord) -> bool:
    df = fetch_klines(signal.symbol, signal.timeframe, limit=LOOKBACK_LIMIT)
    if df is None or df.empty:
        return False

    signal_ts = pd.Timestamp(signal.timestamp)
    if signal_ts not in df.index:
        # 시그널이 난 봉이 조회 범위 밖으로 밀려남(오래전 시그널) - 더 판정 못함.
        # OPEN인 채로 둔다(이미 지나간 일이라 결과가 궁금하면 별도 조회 필요).
        return False

    entry_idx = df.index.get_loc(signal_ts)
    n = len(df)
    if entry_idx >= n - 1:
        return False  # 진입봉이 최신봉 - 아직 그 다음 봉이 없어 판정할 데이터 없음

    high, low, close, index = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy(), df.index

    exit_reason, exit_price, exit_idx = _walk_forward_exit(
        high, low, close, index, entry_idx, signal.stop_price, signal.target_price, signal.time_stop_at, n,
    )

    if exit_reason == "TIME" and exit_idx == n - 1 and index[exit_idx] < pd.Timestamp(signal.time_stop_at):
        # _walk_forward_exit는 "데이터 끝까지 못 빠져나오면"도 TIME으로 반환한다
        # (강제 정리) - 이 경우는 진짜 시간손절이 아니라 "아직 최신 봉까지밖에
        # 못 봤다"는 뜻이므로 확정하지 않고 다음 스캔에서 다시 본다.
        return False

    pct = (exit_price - signal.entry_price) / signal.entry_price * 100
    r_multiple = (exit_price - signal.entry_price) / (signal.entry_price - signal.stop_price)

    signal.virtual_status = exit_reason
    signal.virtual_exit_price = round(float(exit_price), 6)
    signal.virtual_exit_at = index[exit_idx].to_pydatetime()
    signal.virtual_pct_return = round(pct, 4)
    signal.virtual_r_multiple = round(r_multiple, 3)
    session.commit()
    logger.info(
        "가상 체결 결과 확정: %s %s (id=%s) -> %s %.4f%% (R=%.3f)",
        signal.symbol, signal.timeframe, signal.id, exit_reason, pct, r_multiple,
    )
    return True
