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


class PaperAccount(Base):
    """모의투자(paper trading) 가상 계좌 — 전략 1개(`strategy_key`+심볼+시간대)당 하나.

    실제 주문을 절대 내지 않는다 — `run_once()`가 매 스캔마다 최근 캔들을 다시
    백테스트해서(`app/lab_backtest.simulate_lab`) 새로 청산된 거래만 여기 잔고에
    반영한다. 실제 자동매매 엔진(`TradeRecord`/`ScanState`)과는 완전히 분리된
    테이블이라 화이트리스트/포지션 카운트 등 실거래 로직과 절대 섞이지 않는다.
    """

    __tablename__ = "paper_account"
    __table_args__ = (
        UniqueConstraint("strategy_key", "symbol", "timeframe", name="uq_paper_account_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_key = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    starting_balance = Column(Float, nullable=False)
    balance = Column(Float, nullable=False)
    started_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "strategy_key": self.strategy_key,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "starting_balance": self.starting_balance,
            "balance": self.balance,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PaperTrade(Base):
    """모의투자 계좌에 실제로 청산 반영된 거래 1건."""

    __tablename__ = "paper_trades"
    __table_args__ = (
        UniqueConstraint("account_id", "entry_time", name="uq_paper_trade_account_entry"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, index=True)
    direction = Column(String, nullable=False)  # LONG/SHORT
    entry_time = Column(DateTime, nullable=False)
    exit_time = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    exit_reason = Column(String, nullable=False)  # SL/TRAIL/TIME
    pct_return = Column(Float, nullable=False)  # 수수료 반영 순수익률(%)
    balance_before = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "direction": self.direction,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pct_return": self.pct_return,
            "balance_before": self.balance_before,
            "balance_after": self.balance_after,
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
