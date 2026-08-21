"""시그널 엔진: 대상 (symbol, timeframe)마다 새로 완결된 봉이 생기면
전략을 평가하고, 시그널 기록 → 텔레그램 알림 → (화이트리스트+안전조건을
모두 통과하면) 자동매매 실행까지 이어간다.

한 (symbol, timeframe)의 처리 실패가 다른 조합 처리를 막지 않도록 각
조합을 독립적으로 try/except한다.
"""
from __future__ import annotations

import logging

import pandas as pd

from . import db, risk
from .broker import BinanceFuturesBroker, BrokerError
from .config import settings
from .db import SessionLocal, SignalRecord, TradeRecord
from .history import fetch_klines, is_candle_closed
from .notify import notify_signal
from .position_manager import count_open_positions, has_open_position
from .strategy import KeltnerReclaimStrategy, Signal

logger = logging.getLogger(__name__)


class SignalEngine:
    def __init__(self, strategy: KeltnerReclaimStrategy | None = None, broker: BinanceFuturesBroker | None = None):
        self.strategy = strategy or KeltnerReclaimStrategy()
        self.broker = broker  # 지연 생성 (테스트에서는 fake broker 주입)

    def run_once(self) -> list[Signal]:
        db.init_db()
        detected: list[Signal] = []
        for symbol in settings.symbols:
            for timeframe in settings.timeframes:
                try:
                    signal = self._process_one(symbol, timeframe)
                    if signal is not None:
                        detected.append(signal)
                except Exception:
                    logger.exception("시그널 처리 실패: %s %s", symbol, timeframe)
        return detected

    def _process_one(self, symbol: str, timeframe: str) -> Signal | None:
        limit = min(self.strategy.min_bars + 10, 1000)
        df = fetch_klines(symbol, timeframe, limit=limit)
        if df is None or df.empty:
            return None

        if not is_candle_closed(df, timeframe):
            df = df.iloc[:-1]  # 진행 중인 마지막 봉은 버리고 완결봉만 사용

        if len(df) < self.strategy.min_bars:
            return None

        latest_closed_ts = df.index[-1].to_pydatetime()
        last_processed = db.get_last_processed(symbol, timeframe)
        if last_processed is not None and latest_closed_ts <= last_processed:
            return None  # 이 봉은 이미 평가했음 (중복 시그널/중복 매매 방지)

        signal = self.strategy.evaluate(symbol, timeframe, df)
        db.set_last_processed(symbol, timeframe, latest_closed_ts)  # 성공/실패와 무관하게 항상 전진

        if signal is None:
            return None

        auto_traded = self._maybe_execute(signal)
        self._persist_signal(signal, auto_traded)
        notify_signal(signal, auto_traded)
        return signal

    def _maybe_execute(self, signal: Signal) -> bool:
        symbol, timeframe = signal.symbol, signal.timeframe

        if not settings.is_whitelisted(symbol, timeframe):
            return False
        if risk.is_kill_switch_active():
            logger.warning("일일 손실 한도 도달 - 신규 진입 스킵: %s %s", symbol, timeframe)
            return False
        if count_open_positions() >= settings.max_open_positions:
            logger.info("최대 동시 포지션 수 도달 - 진입 스킵: %s %s", symbol, timeframe)
            return False
        if has_open_position(symbol, timeframe):
            logger.info("이미 열린 포지션 존재 - 중복 진입 스킵: %s %s", symbol, timeframe)
            return False

        broker = self.broker or BinanceFuturesBroker()
        session = SessionLocal()
        try:
            try:
                result = broker.enter_long(
                    symbol=symbol,
                    entry_price_hint=signal.entry_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    risk_usdt=settings.risk_per_trade_usdt,
                    leverage=settings.leverage,
                )
            except BrokerError as exc:
                logger.error("자동매매 진입 실패: %s %s - %s", symbol, timeframe, exc)
                session.add(
                    TradeRecord(
                        symbol=symbol, timeframe=timeframe, side="BUY", status="FAILED",
                        error_message=str(exc),
                    )
                )
                session.commit()
                return False

            session.add(
                TradeRecord(
                    symbol=symbol, timeframe=timeframe, side="BUY", status="OPEN",
                    quantity=result.quantity, entry_order_id=result.entry_order_id,
                    sl_order_id=result.sl_order_id, tp_order_id=result.tp_order_id,
                    entry_price=result.entry_price, time_stop_at=signal.time_stop_at,
                )
            )
            session.commit()
            return True
        finally:
            session.close()

    @staticmethod
    def _persist_signal(signal: Signal, auto_traded: bool) -> None:
        session = SessionLocal()
        try:
            ts = signal.timestamp
            if isinstance(ts, pd.Timestamp):
                ts = ts.to_pydatetime()
            session.add(
                SignalRecord(
                    symbol=signal.symbol,
                    timeframe=signal.timeframe,
                    strategy=KeltnerReclaimStrategy.key,
                    signal_type=signal.signal_type.value,
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    time_stop_at=signal.time_stop_at,
                    timestamp=ts,
                    auto_traded="YES" if auto_traded else "NO",
                    details_json=_dumps(signal.details),
                )
            )
            session.commit()
        finally:
            session.close()


def _dumps(details: dict) -> str:
    import json

    return json.dumps(details, ensure_ascii=False, default=str)


def run_once() -> list[Signal]:
    return SignalEngine().run_once()
