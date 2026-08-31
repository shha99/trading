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

    # 화이트리스트에 없어(auto_traded=NO) 실제 주문이 안 나간 시그널도, "이 로직대로
    # 진짜 체결됐다면 어떻게 됐을지"를 그 신호 자체의 stop_price/target_price/
    # time_stop_at 기준으로 계속 추적한다 (app/signal_outcome_tracker.py).
    # OPEN(아직 진행 중) / SL / TP / TIME 중 하나.
    virtual_status = Column(String, default="OPEN", index=True)
    virtual_exit_price = Column(Float, nullable=True)
    virtual_exit_at = Column(DateTime, nullable=True)
    virtual_pct_return = Column(Float, nullable=True)
    virtual_r_multiple = Column(Float, nullable=True)

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
            "virtual_status": self.virtual_status,
            "virtual_exit_price": self.virtual_exit_price,
            "virtual_exit_at": self.virtual_exit_at.isoformat() if self.virtual_exit_at else None,
            "virtual_pct_return": self.virtual_pct_return,
            "virtual_r_multiple": self.virtual_r_multiple,
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

    # 어느 엔진이 연 포지션인지 - NULL/미지정이면 켈트너 엔진(signal_engine.py,
    # 고정 SL/TP)이 관리한다. "bollinger_wick_breakeven_trail"이면 별도 엔진
    # (wick_signal_engine.py/wick_position_manager.py)이 본전 이동 트레일링
    # 스탑을 계속 갱신하며 관리한다 - 두 엔진이 서로의 포지션을 절대 건드리지
    # 않도록 이 필드로 소유권을 명확히 구분한다.
    strategy = Column(String, nullable=True, index=True)
    # 아래 5개는 wick 엔진 전용 - 진입 시점에 고정해두고 이후 트레일링 스탑을
    # 매번 진입 시점부터 다시 재현(stateless replay)하는 데 쓴다.
    initial_stop_price = Column(Float, nullable=True)
    current_stop_price = Column(Float, nullable=True)  # 지금 거래소에 걸려있는 손절가
    breakeven_trigger_price = Column(Float, nullable=True)
    trail_mult = Column(Float, nullable=True)
    atr_period = Column(Integer, nullable=True)
    moved_to_breakeven = Column(String, default="NO")  # "YES"/"NO"

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
            "strategy": self.strategy,
            "initial_stop_price": self.initial_stop_price,
            "current_stop_price": self.current_stop_price,
            "breakeven_trigger_price": self.breakeven_trigger_price,
            "trail_mult": self.trail_mult,
            "atr_period": self.atr_period,
            "moved_to_breakeven": self.moved_to_breakeven,
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


_DEFAULT_SCAN_STRATEGY = "keltner_reclaim_200ema"


class ScanState(Base):
    """(symbol, timeframe, strategy)별 마지막으로 처리한 완결 봉 시각 — 중복
    시그널 방지.

    `strategy`를 키에 포함하는 이유: 켈트너 엔진과 wick 엔진(둘 다 BTCUSDT:15m
    같은 같은 심볼/시간대를 스캔할 수 있음)이 이 키를 공유하면, 한쪽이 어떤
    봉을 "처리함"으로 찍으면 다른 쪽은 그 봉을 아예 평가해보지도 않고
    건너뛰게 되는 실제 버그가 생긴다 — 두 엔진은 서로 독립된 전략이라 같은
    봉이라도 각자 반드시 자기 로직으로 평가해야 한다.
    """

    __tablename__ = "scan_state"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "strategy", name="uq_scan_state_symbol_tf_strategy"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    strategy = Column(String, nullable=False, default=_DEFAULT_SCAN_STRATEGY)
    last_processed_at = Column(DateTime, nullable=False)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_last_processed(
    symbol: str, timeframe: str, strategy: str = _DEFAULT_SCAN_STRATEGY
) -> datetime | None:
    session = SessionLocal()
    try:
        row = (
            session.query(ScanState)
            .filter(ScanState.symbol == symbol, ScanState.timeframe == timeframe, ScanState.strategy == strategy)
            .first()
        )
        return row.last_processed_at if row else None
    finally:
        session.close()


def set_last_processed(
    symbol: str, timeframe: str, ts: datetime, strategy: str = _DEFAULT_SCAN_STRATEGY
) -> None:
    session = SessionLocal()
    try:
        row = (
            session.query(ScanState)
            .filter(ScanState.symbol == symbol, ScanState.timeframe == timeframe, ScanState.strategy == strategy)
            .first()
        )
        if row is None:
            row = ScanState(symbol=symbol, timeframe=timeframe, strategy=strategy, last_processed_at=ts)
            session.add(row)
        else:
            row.last_processed_at = ts
        session.commit()
    finally:
        session.close()
