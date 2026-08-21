"""SQLite(기본) 저장소: 시그널 이력, 매매 이력, 중복실행 방지용 스캔 상태.

기존 shha99/trading의 backend/app/database.py와 같은 스타일
(SQLAlchemy declarative + to_dict())을 따른다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

Base = declarative_base()


class SignalRecord(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    strategy = Column(String, nullable=False)
    signal_type = Column(String, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    time_stop_at = Column(DateTime, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    auto_traded = Column(String, default="NO")  # "YES"/"NO" — 화이트리스트로 실제 주문까지 갔는지
    details_json = Column(Text, default="{}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "signal_type": self.signal_type,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "time_stop_at": self.time_stop_at.isoformat() if self.time_stop_at else None,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "auto_traded": self.auto_traded,
            "details": json.loads(self.details_json or "{}"),
        }


class TradeRecord(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False)
    side = Column(String, nullable=False)  # BUY/SELL
    status = Column(String, nullable=False, default="OPEN", index=True)
    # OPEN / CLOSED_TP / CLOSED_SL / CLOSED_TIME / CLOSED_MANUAL / FAILED
    quantity = Column(Float, nullable=True)
    entry_order_id = Column(String, nullable=True)
    sl_order_id = Column(String, nullable=True)
    tp_order_id = Column(String, nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    realized_pnl_usdt = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)
    time_stop_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "status": self.status,
            "quantity": self.quantity,
            "entry_order_id": self.entry_order_id,
            "sl_order_id": self.sl_order_id,
            "tp_order_id": self.tp_order_id,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "realized_pnl_usdt": self.realized_pnl_usdt,
            "error_message": self.error_message,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "time_stop_at": self.time_stop_at.isoformat() if self.time_stop_at else None,
        }


class ScanState(Base):
    """(symbol, timeframe)별 마지막으로 처리한 완결 봉 시각 — 중복 시그널 방지."""

    __tablename__ = "scan_state"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", name="uq_scan_state_symbol_tf"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    last_processed_at = Column(DateTime, nullable=False)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_last_processed(symbol: str, timeframe: str) -> datetime | None:
    session = SessionLocal()
    try:
        row = (
            session.query(ScanState)
            .filter(ScanState.symbol == symbol, ScanState.timeframe == timeframe)
            .first()
        )
        return row.last_processed_at if row else None
    finally:
        session.close()


def set_last_processed(symbol: str, timeframe: str, ts: datetime) -> None:
    session = SessionLocal()
    try:
        row = (
            session.query(ScanState)
            .filter(ScanState.symbol == symbol, ScanState.timeframe == timeframe)
            .first()
        )
        if row is None:
            row = ScanState(symbol=symbol, timeframe=timeframe, last_processed_at=ts)
            session.add(row)
        else:
            row.last_processed_at = ts
        session.commit()
    finally:
        session.close()
