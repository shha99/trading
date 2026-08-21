"""열린 포지션 조회 + 3일 시간손절 감시.

거래소에 걸어둔 STOP_MARKET/TAKE_PROFIT_MARKET은 가격 조건이 오면 알아서
체결되지만, "시간이 다 됐으니 청산"은 거래소가 대신 해주지 않으므로 봇이
주기적으로 확인해서 직접 시장가로 정리해야 한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .broker import BinanceFuturesBroker, BrokerError
from .db import SessionLocal, TradeRecord
from .notify import notify_trade_closed

logger = logging.getLogger(__name__)


def count_open_positions() -> int:
    session = SessionLocal()
    try:
        return session.query(TradeRecord).filter(TradeRecord.status == "OPEN").count()
    finally:
        session.close()


def has_open_position(symbol: str, timeframe: str) -> bool:
    session = SessionLocal()
    try:
        return (
            session.query(TradeRecord)
            .filter(TradeRecord.symbol == symbol, TradeRecord.timeframe == timeframe, TradeRecord.status == "OPEN")
            .first()
            is not None
        )
    finally:
        session.close()


def check_time_stops(broker: BinanceFuturesBroker | None = None) -> int:
    """시간손절 기한이 지난 열린 포지션을 전부 시장가 청산한다. 청산 건수를 반환."""
    broker = broker or BinanceFuturesBroker()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    closed = 0

    session = SessionLocal()
    try:
        due = (
            session.query(TradeRecord)
            .filter(TradeRecord.status == "OPEN", TradeRecord.time_stop_at.isnot(None))
            .filter(TradeRecord.time_stop_at <= now)
            .all()
        )
        for trade in due:
            try:
                exit_side = "SELL" if trade.side == "BUY" else "BUY"
                broker.close_position_market(trade.symbol, trade.quantity, side=exit_side)
                trade.status = "CLOSED_TIME"
                trade.closed_at = now
                session.commit()
                notify_trade_closed(trade.symbol, trade.timeframe, "시간손절", trade.realized_pnl_usdt)
                closed += 1
            except BrokerError:
                logger.exception("시간손절 청산 실패 (다음 주기에 재시도): %s", trade.symbol)
                session.rollback()
    finally:
        session.close()

    return closed
