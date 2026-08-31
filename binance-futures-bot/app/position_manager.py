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


def realized_pnl_usdt(trade: TradeRecord, exit_price: float) -> float | None:
    if trade.entry_price is None or trade.quantity is None or exit_price is None:
        return None
    direction = 1 if trade.side == "BUY" else -1  # 이 전략은 롱만 진입하지만 일반화해둠
    return round((exit_price - trade.entry_price) * trade.quantity * direction, 6)


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
                result = broker.close_position_market(trade.symbol, trade.quantity, side=exit_side)
                trade.status = "CLOSED_TIME"
                trade.closed_at = now
                trade.exit_price = result.get("price")
                trade.realized_pnl_usdt = realized_pnl_usdt(trade, trade.exit_price)
                session.commit()
                notify_trade_closed(trade.symbol, trade.timeframe, "시간손절", trade.realized_pnl_usdt)
                closed += 1
            except BrokerError:
                logger.exception("시간손절 청산 실패 (다음 주기에 재시도): %s", trade.symbol)
                session.rollback()
    finally:
        session.close()

    return closed


def reconcile_open_positions(broker: BinanceFuturesBroker | None = None) -> int:
    """열린 포지션의 SL/TP 주문이 거래소에서 체결됐는지 확인해 DB에 반영한다.

    STOP_MARKET/TAKE_PROFIT_MARKET은 가격 조건이 오면 거래소가 알아서
    체결하지만, 그 사실을 우리 DB(TradeRecord)에는 우리가 직접 반영해야
    한다 - 이게 안 되면 realized_pnl_usdt가 영영 채워지지 않아 일일 손실
    한도 킬스위치(risk.py)가 작동하지 않는다. 반영된 건수를 반환.
    """
    broker = broker or BinanceFuturesBroker()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    updated = 0

    session = SessionLocal()
    try:
        open_trades = session.query(TradeRecord).filter(TradeRecord.status == "OPEN").all()
        for trade in open_trades:
            try:
                filled_as, filled_price = None, None
                if trade.sl_order_id:
                    sl = broker.get_order_status(trade.symbol, trade.sl_order_id)
                    if sl and sl.get("status") == "FILLED":
                        filled_as, filled_price = "CLOSED_SL", float(sl.get("avgPrice") or 0.0) or None
                if filled_as is None and trade.tp_order_id:
                    tp = broker.get_order_status(trade.symbol, trade.tp_order_id)
                    if tp and tp.get("status") == "FILLED":
                        filled_as, filled_price = "CLOSED_TP", float(tp.get("avgPrice") or 0.0) or None

                if filled_as is None:
                    continue

                other_order_id = trade.tp_order_id if filled_as == "CLOSED_SL" else trade.sl_order_id
                if other_order_id:
                    broker.cancel_order(trade.symbol, other_order_id)

                trade.status = filled_as
                trade.closed_at = now
                trade.exit_price = filled_price
                trade.realized_pnl_usdt = realized_pnl_usdt(trade, filled_price)
                session.commit()
                notify_trade_closed(
                    trade.symbol, trade.timeframe,
                    "손절" if filled_as == "CLOSED_SL" else "익절",
                    trade.realized_pnl_usdt,
                )
                updated += 1
            except Exception:
                logger.exception("포지션 반영 실패 (다음 주기에 재시도): %s", trade.symbol)
                session.rollback()
    finally:
        session.close()

    return updated
