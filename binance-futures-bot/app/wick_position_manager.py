"""볼린저 꼬리터치+RSI 되돌림 전략(bollinger_wick_breakeven_trail)이 연 실계좌
포지션의 "본전 이동 트레일링" 손절을 계속 갱신하고, 청산됐는지 확인한다.

이 전략은 고정 익절이 없다 — 손절 주문 하나만 걸어두고, 가격이 유리하게
움직이면 그 손절 주문을 취소·재등록하며 계속 끌어올린다(내려간다, 숏이면).
켈트너 엔진(position_manager.py, 고정 SL+TP 두 개 걸어두고 거래소가 알아서
체결)과는 완전히 다른 방식이라 별도 모듈로 뗀다 — 서로의 포지션(TradeRecord
.strategy로 구분)을 절대 건드리지 않는다.

**스탑 재계산은 매번 진입 시점부터 무상태로(stateless) 다시 재현한다** -
`app/paper_trading.py`가 최근 캔들을 매번 다시 백테스트하는 것과 같은
설계 원칙(중간 상태를 신뢰하지 않고 항상 원본 데이터로 재도출) - DB에 저장된
중간 상태가 어떤 이유로든 틀어져도 다음 틱에 저절로 바로잡힌다.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import pandas as pd

from .broker import BinanceFuturesBroker, BrokerError
from .db import SessionLocal, TradeRecord
from .history import fetch_klines, is_candle_closed
from .indicators import atr as atr_series
from .lab_strategies import BollingerWickBreakevenTrailStrategy
from .notify import notify_trade_closed
from .position_manager import realized_pnl_usdt

logger = logging.getLogger(__name__)

WICK_STRATEGY_KEY = BollingerWickBreakevenTrailStrategy.key
FETCH_LIMIT = 1000  # paper_trading.py와 동일한 근거 - 15분봉 기준 ~10일, 워밍업+보유기간 충분


def has_open_wick_position(symbol: str, timeframe: str) -> bool:
    session = SessionLocal()
    try:
        return (
            session.query(TradeRecord)
            .filter(
                TradeRecord.symbol == symbol, TradeRecord.timeframe == timeframe,
                TradeRecord.strategy == WICK_STRATEGY_KEY, TradeRecord.status == "OPEN",
            )
            .first()
            is not None
        )
    finally:
        session.close()


def compute_trailing_state(
    direction: str, entry_price: float, initial_stop: float, trigger_price: float,
    trail_mult: float, highs, lows, atrs,
) -> dict:
    """진입 이후 완결봉들의 (high, low, atr)를 순서대로 재생해, 지금 이 순간의
    이론적 손절가와 본전 이동 여부를 계산한다.

    `app/lab_backtest.py::_walk_forward_breakeven_trail()`과 상태 전이 로직이
    완전히 같다 — 다만 저 함수는 "언제 청산되는지"까지 판정하지만, 여기서는
    청산 판정을 하지 않는다(그건 거래소에 걸린 실제 STOP_MARKET 주문이 담당 -
    이 함수는 "지금 스탑이 얼마여야 하는가"만 계산해서 필요하면 그 주문을
    갱신하는 데 쓴다).
    """
    stop_price = initial_stop
    moved_to_breakeven = False
    extreme = entry_price

    for high, low, atr_now in zip(highs, lows, atrs):
        if direction == "LONG":
            extreme = max(extreme, high)
            if not moved_to_breakeven and high >= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if moved_to_breakeven and atr_now is not None and not math.isnan(atr_now):
                stop_price = max(stop_price, extreme - trail_mult * atr_now)
        else:
            extreme = min(extreme, low)
            if not moved_to_breakeven and low <= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if moved_to_breakeven and atr_now is not None and not math.isnan(atr_now):
                stop_price = min(stop_price, extreme + trail_mult * atr_now)

    return {"stop_price": stop_price, "moved_to_breakeven": moved_to_breakeven, "extreme_price": extreme}


def manage_wick_positions(broker: BinanceFuturesBroker | None = None) -> int:
    """열린 wick 포지션 전부를 점검한다: ① 손절 주문이 이미 체결됐으면 DB에
    반영, ② 아직 열려있으면 트레일링 스탑을 최신 상태로 갱신. 갱신/청산 처리된
    건수를 반환."""
    broker = broker or BinanceFuturesBroker()
    updated = 0

    session = SessionLocal()
    try:
        open_trades = (
            session.query(TradeRecord)
            .filter(TradeRecord.status == "OPEN", TradeRecord.strategy == WICK_STRATEGY_KEY)
            .all()
        )
        for trade in open_trades:
            try:
                if _reconcile_if_stopped_out(session, broker, trade):
                    updated += 1
                    continue
                if _update_trailing_stop(session, broker, trade):
                    updated += 1
            except Exception:
                logger.exception("wick 포지션 관리 실패 (다음 주기에 재시도): %s %s", trade.symbol, trade.timeframe)
                session.rollback()
    finally:
        session.close()

    return updated


def _reconcile_if_stopped_out(session, broker: BinanceFuturesBroker, trade: TradeRecord) -> bool:
    if not trade.sl_order_id:
        return False
    order = broker.get_order_status(trade.symbol, trade.sl_order_id)
    if not order or order.get("status") != "FILLED":
        return False

    exit_price = float(order.get("avgPrice") or 0.0) or None
    status = "CLOSED_TRAIL" if trade.moved_to_breakeven == "YES" else "CLOSED_SL"
    trade.status = status
    trade.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    trade.exit_price = exit_price
    trade.realized_pnl_usdt = realized_pnl_usdt(trade, exit_price)
    session.commit()
    notify_trade_closed(
        trade.symbol, trade.timeframe,
        "트레일링 청산" if status == "CLOSED_TRAIL" else "손절",
        trade.realized_pnl_usdt,
    )
    return True


def _update_trailing_stop(session, broker: BinanceFuturesBroker, trade: TradeRecord) -> bool:
    df = fetch_klines(trade.symbol, trade.timeframe, limit=FETCH_LIMIT)
    if df is None or df.empty:
        return False
    if not is_candle_closed(df, trade.timeframe):
        df = df.iloc[:-1]

    opened_at = pd.Timestamp(trade.opened_at)
    since_entry = df[df.index > opened_at]
    if since_entry.empty:
        return False  # 아직 진입봉 다음 완결봉이 없음 - 다음 틱에 다시 확인

    atr_period = trade.atr_period or 14
    atr_full = atr_series(df, int(atr_period))
    atrs = atr_full.reindex(since_entry.index).to_numpy()
    highs = since_entry["High"].to_numpy()
    lows = since_entry["Low"].to_numpy()

    direction = "LONG" if trade.side == "BUY" else "SHORT"
    state = compute_trailing_state(
        direction, trade.entry_price, trade.initial_stop_price, trade.breakeven_trigger_price,
        trade.trail_mult, highs, lows, atrs,
    )
    new_stop = round(float(state["stop_price"]), 6)
    moved = state["moved_to_breakeven"]

    changed = False
    if trade.current_stop_price is None or not math.isclose(new_stop, trade.current_stop_price, rel_tol=1e-9):
        try:
            new_order_id = broker.replace_stop_order(
                trade.symbol, direction, trade.quantity, new_stop, trade.sl_order_id,
            )
        except BrokerError:
            logger.exception("%s 트레일링 스탑 갱신 실패 (다음 주기에 재시도)", trade.symbol)
            return False
        trade.sl_order_id = new_order_id
        trade.current_stop_price = new_stop
        changed = True
    if moved and trade.moved_to_breakeven != "YES":
        trade.moved_to_breakeven = "YES"
        changed = True

    if changed:
        session.commit()
    return changed
