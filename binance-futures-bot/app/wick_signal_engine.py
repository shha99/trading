"""볼린저 꼬리터치+RSI 되돌림 전략(bollinger_wick_breakeven_trail) 전용 실계좌
자동매매 엔진 — 켈트너 엔진(signal_engine.py)과 완전히 분리돼 있다.

⚠️ **기본값은 꺼짐이다** (`WICK_AUTO_TRADE_ENABLED=false`,
`WICK_AUTO_TRADE_WHITELIST` 빈 값). 명시적으로 둘 다 설정해야 실제 주문이
나간다 — 검증된 전략이라도 자동매매 엔진에 조용히 얹지 않는다는 이 프로젝트
전체의 원칙 그대로다.

**기본 진입 전략은 2026-11부터 `BollingerWickVolumeStochRsiConfirmStrategy`다**
(기존 `BollingerWickBreakevenTrailStrategy`의 밴드터치+RSI 조건에, 터치 캔들
거래량 확인 + StochRSI(15/85) 이중확인을 추가한 검증된 상위 버전 - 상세 근거는
그 클래스 docstring, 백테스트 비교는 README "실계좌 자동매매" 섹션 참고).
청산(손절 → 본전 이동 → ATR 트레일링)은 그대로 상속해서 완전히 동일하다.

이 전략은 롱/숏 양방향이고 고정 익절이 없는 "본전 이동 트레일링" 청산이라
(app/lab_backtest.py의 로직과 완전히 같음), 진입 시 손절 주문 하나만 걸고
이후 app/wick_position_manager.py가 주기적으로 그 손절 주문을 취소·재등록
하며 따라간다.

**리스크 사이징**: `RISK_MODE=fixed`(기본)/`percent_balance`는
`risk.compute_risk_usdt()` × `combo_weights.get_combo_weight()` 배수로,
`RISK_MODE=combo_weighted`는 `combo_weights.compute_combo_weighted_risk_usdt()`
(가용잔고를 조합별 신뢰도 비중대로 나눠 그 몫 전액을 포지션 명목가치로 씀 -
README "실계좌 자동매매" 섹션의 백테스트 수치가 이 모드 기준)로 계산한다.
`combo_weighted` 모드에서 `WICK_CPPI_ENABLED=true`면 그 명목가치에 조합별
비대칭 CPPI 배율(`combo_weights.get_cppi_multiplier()`, 0.2~1.2배)이 추가로
곱해진다 - 평균 성과는 개선되지만 꼬리 리스크가 커지는 트레이드오프가
있다는 걸 반드시 README에서 확인할 것. 포지션이 청산될 때마다
`wick_position_manager.py`가 `combo_weights.update_cppi_state_after_trade()`
로 그 조합의 CPPI 상태를 갱신하고, 매월 1일 가중치 재계산과 같은 시점에
전 조합 리밸런싱(상태 리셋)된다.
"""
from __future__ import annotations

import logging

from . import db, risk
from .broker import BinanceFuturesBroker, BrokerError
from .combo_weights import compute_combo_weighted_risk_usdt, get_combo_weight
from .config import settings
from .db import SessionLocal, TradeRecord
from .emergency_stop import is_emergency_halted
from .history import fetch_klines, is_candle_closed
from .lab_strategies import BollingerWickBreakevenTrailStrategy, BollingerWickVolumeStochRsiConfirmStrategy
from .notify import notify_wick_entry
from .position_manager import count_open_positions
from .remote_control import is_remotely_enabled
from .wick_position_manager import WICK_STRATEGY_KEY, has_open_wick_position

logger = logging.getLogger(__name__)


class WickSignalEngine:
    def __init__(self, strategy: BollingerWickBreakevenTrailStrategy | None = None, broker: BinanceFuturesBroker | None = None):
        self.strategy = strategy or BollingerWickVolumeStochRsiConfirmStrategy()
        self.broker = broker  # 지연 생성 (테스트에서는 fake broker 주입)

    def run_once(self) -> int:
        """새로 진입한 포지션 수를 반환한다 (테스트/수동 확인용)."""
        db.init_db()
        entered = 0
        for symbol in settings.symbols:
            for timeframe in settings.wick_timeframes:
                try:
                    if self._process_one(symbol, timeframe):
                        entered += 1
                except Exception:
                    logger.exception("wick 시그널 처리 실패: %s %s", symbol, timeframe)
        return entered

    def _process_one(self, symbol: str, timeframe: str) -> bool:
        limit = min(self.strategy.min_bars + 20, 1000)
        df = fetch_klines(symbol, timeframe, limit=limit)
        if df is None or df.empty:
            return False
        if not is_candle_closed(df, timeframe):
            df = df.iloc[:-1]  # 진행 중인 마지막 봉은 버리고 완결봉만 사용
        if len(df) < self.strategy.min_bars:
            return False

        latest_closed_ts = df.index[-1].to_pydatetime()
        last_processed = db.get_last_processed(symbol, timeframe, strategy=WICK_STRATEGY_KEY)
        if last_processed is not None and latest_closed_ts <= last_processed:
            return False  # 이 봉은 이미 평가했음
        db.set_last_processed(symbol, timeframe, latest_closed_ts, strategy=WICK_STRATEGY_KEY)

        ctx = self.strategy.precompute(df)
        k = len(df) - 1
        entry = self.strategy.check_entry(k, ctx)
        if entry is None:
            return False

        return self._maybe_execute(symbol, timeframe, entry, df.index[k].to_pydatetime())

    def _maybe_execute(self, symbol: str, timeframe: str, entry: dict, entry_time) -> bool:
        broker = self.broker or BinanceFuturesBroker()
        if not settings.is_wick_whitelisted(symbol, timeframe):
            return False
        if is_emergency_halted():
            logger.warning("긴급 서킷브레이커 발동 중 - wick 신규 진입 스킵: %s %s", symbol, timeframe)
            return False
        if not is_remotely_enabled("wick"):
            logger.info("원격 제어로 꺼짐 - wick 신규 진입 스킵: %s %s", symbol, timeframe)
            return False
        if risk.is_kill_switch_active(broker):
            logger.warning("일일 손실 한도 도달 - wick 신규 진입 스킵: %s %s", symbol, timeframe)
            return False
        if count_open_positions() >= settings.max_open_positions:
            logger.info("최대 동시 포지션 수 도달 - wick 진입 스킵: %s %s", symbol, timeframe)
            return False
        if has_open_wick_position(symbol, timeframe):
            logger.info("이미 열린 wick 포지션 존재 - 중복 진입 스킵: %s %s", symbol, timeframe)
            return False

        direction = entry["direction"]
        side = "BUY" if direction == "LONG" else "SELL"
        session = SessionLocal()
        try:
            if settings.risk_mode == "combo_weighted":
                risk_usdt = compute_combo_weighted_risk_usdt(
                    broker, symbol, timeframe, entry["entry_price"], entry["stop_price"],
                )
            else:
                risk_usdt = risk.compute_risk_usdt(broker) * get_combo_weight(symbol, timeframe)
            try:
                result = broker.enter_position(
                    direction=direction, symbol=symbol,
                    entry_price_hint=entry["entry_price"], stop_price=entry["stop_price"],
                    risk_usdt=risk_usdt, leverage=settings.leverage,
                )
            except BrokerError as exc:
                logger.error("wick 자동매매 진입 실패: %s %s - %s", symbol, timeframe, exc)
                session.add(
                    TradeRecord(
                        symbol=symbol, timeframe=timeframe, side=side, status="FAILED",
                        error_message=str(exc), strategy=WICK_STRATEGY_KEY,
                    )
                )
                session.commit()
                return False

            session.add(
                TradeRecord(
                    symbol=symbol, timeframe=timeframe, side=side, status="OPEN",
                    quantity=result.quantity, entry_order_id=result.entry_order_id,
                    sl_order_id=result.sl_order_id, tp_order_id=None,
                    entry_price=result.entry_price, opened_at=entry_time,
                    strategy=WICK_STRATEGY_KEY,
                    initial_stop_price=entry["stop_price"], current_stop_price=entry["stop_price"],
                    breakeven_trigger_price=entry["breakeven_trigger_price"], trail_mult=entry["trail_mult"],
                    atr_period=self.strategy.atr_period, moved_to_breakeven="NO",
                )
            )
            session.commit()
            notify_wick_entry(symbol, timeframe, direction, result.entry_price, entry["stop_price"])
            return True
        finally:
            session.close()


def run_once() -> int:
    return WickSignalEngine().run_once()
