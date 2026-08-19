"""시그널 계산 오케스트레이터.

등록된 각 DataProvider의 유니버스를 순회하며 종목별 OHLCV를 가져오고,
등록된 각 Strategy를 실행해 새로 트리거된 시그널을 모아 DB에 저장한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .data.base import DataProvider
from .data.kr_provider import KRProvider
from .data.us_provider import USProvider
from .database import SessionLocal, SignalRecord, init_db
from .signals.base import Signal, Strategy
from .signals.registry import default_strategies

logger = logging.getLogger(__name__)

_MIN_BARS_BUFFER = 5


class SignalEngine:
    def __init__(
        self,
        providers: list[DataProvider] | None = None,
        strategies: list[Strategy] | None = None,
    ):
        self.providers = providers if providers is not None else [KRProvider(), USProvider()]
        self.strategies = strategies if strategies is not None else default_strategies()

    def run(self) -> list[Signal]:
        init_db()
        results: list[Signal] = []
        needed_bars = max((s.min_bars for s in self.strategies), default=30) + _MIN_BARS_BUFFER

        for provider in self.providers:
            try:
                universe = provider.get_universe()
            except Exception:
                logger.exception("유니버스 조회 실패: %s", provider.market)
                continue

            logger.info("[%s] %d개 종목 스캔 시작", provider.market, len(universe))
            for ticker in universe:
                try:
                    df = provider.get_ohlcv(ticker.symbol, lookback_days=needed_bars * 2)
                    if df is None or len(df) < needed_bars:
                        continue
                    for strategy in self.strategies:
                        signal = strategy.evaluate(ticker.symbol, ticker.name, provider.market, df)
                        if signal is not None:
                            results.append(signal)
                except Exception:
                    logger.exception("시그널 계산 실패: %s/%s", provider.market, ticker.symbol)

        logger.info("전체 스캔 완료: %d건 시그널 감지", len(results))
        if results:
            self._persist(results)
        return results

    @staticmethod
    def _persist(signals: list[Signal]) -> None:
        session = SessionLocal()
        try:
            for sig in signals:
                ts = sig.timestamp
                if isinstance(ts, datetime) and ts.tzinfo is not None:
                    ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                record = SignalRecord(
                    symbol=sig.symbol,
                    name=sig.name,
                    market=sig.market,
                    strategy=sig.strategy,
                    strategy_label=sig.strategy_label,
                    signal_type=sig.signal_type.value,
                    price=sig.price,
                    timestamp=ts,
                    details_json=_dumps(sig.details),
                )
                session.add(record)
            session.commit()
        finally:
            session.close()


def _dumps(details: dict) -> str:
    import json

    return json.dumps(details, ensure_ascii=False, default=str)
