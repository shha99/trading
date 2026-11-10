"""계좌 전체 긴급 서킷브레이커 (`EMERGENCY_STOP_PCT`, 기본 꺼짐) - 일일
손실 한도 킬스위치(app/risk.py)와는 다른 층이다.

차이점:
- 킬스위치는 "오늘 실현손익"만 보고(미실현 손익 제외) **신규 진입만**
  막는다 - 이미 열려있는 포지션은 그대로 둔다. 거래가 실제로 청산돼야만
  반영되므로, 진행 중인 포지션 하나가 크게 터지는 중이어도 그 순간에는
  개입하지 못한다.
- 이 서킷브레이커는 **미실현 손익까지 포함한 지금 이 순간의 실제 계좌
  가치**(`app/binance_account.py:get_account_snapshot`의
  `total_margin_balance`)를 보고, 오늘 시작 시점 대비 `EMERGENCY_STOP_PCT`%
  이상 빠지면 **열려있는 포지션을 전부 즉시 시장가로 강제 청산**한다 -
  플래시크래시/갭처럼 개별 손절 주문의 슬리피지가 감당 못 할 정도로 커지는
  상황(README "청산 로직 스트레스 테스트" 참고)에 대한 최후 방어선이다.

한 번 발동하면 일일 킬스위치처럼 자정에 자동으로 안 풀린다 -
`EMERGENCY_HALT_FILE`이 있는 동안은 두 엔진(켈트너/wick) 전부 신규 진입이
막힌다. 사람이 직접 상황을 확인하고 `clear_emergency_halt()`(또는 그냥
파일 삭제)로 재개해야 한다 - 이 정도 사건은 자동 재개가 아니라 사람이
봐야 한다는 판단.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .binance_account import get_account_snapshot
from .broker import BinanceFuturesBroker, BrokerError
from .config import DATA_DIR, settings
from .db import SessionLocal, TradeRecord
from .notify import send_message
from .position_manager import realized_pnl_usdt

logger = logging.getLogger(__name__)

EMERGENCY_HALT_FILE = DATA_DIR / "emergency_halt.json"
EMERGENCY_REFERENCE_FILE = DATA_DIR / "emergency_reference.json"


def is_emergency_halted() -> bool:
    return EMERGENCY_HALT_FILE.exists()


def clear_emergency_halt() -> None:
    """사람이 직접 상황을 확인한 뒤 재개할 때 호출(또는 파일을 직접 지워도 됨)."""
    EMERGENCY_HALT_FILE.unlink(missing_ok=True)
    logger.warning("긴급 정지 해제됨 - 신규 진입 재개")


def _todays_reference_balance(current_balance: float) -> float:
    """오늘(UTC) 처음 확인한 계좌 가치를 기준점으로 삼아 하루 동안 파일에
    고정해둔다(일일 손실 한도 킬스위치와 같은 "하루" 경계 - 매일 자정에
    다시 잡힘). 프로세스가 재시작돼도 오늘 안에는 최초 기준점을 그대로
    쓴다(재시작 시점 잔고로 기준이 미끄러지는 걸 방지)."""
    today = datetime.now(timezone.utc).date().isoformat()
    if EMERGENCY_REFERENCE_FILE.exists():
        try:
            payload = json.loads(EMERGENCY_REFERENCE_FILE.read_text(encoding="utf-8"))
            if payload.get("date") == today:
                return float(payload["balance"])
        except Exception:
            logger.exception("긴급 서킷브레이커 기준점 파일 읽기 실패 - 새로 기록")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EMERGENCY_REFERENCE_FILE.write_text(
        json.dumps({"date": today, "balance": current_balance}, indent=2), encoding="utf-8"
    )
    return current_balance


def check_emergency_stop(broker: BinanceFuturesBroker | None = None) -> bool:
    """`EMERGENCY_STOP_PCT`(0이면 비활성)를 넘는 계좌 손실이 감지되면 모든
    열린 포지션을 강제 청산하고 신규 진입을 막는다. 방금 발동시켰으면
    True, 그 외(비활성/이미 발동 중/정상/조회 실패)는 False를 반환한다."""
    if settings.emergency_stop_pct <= 0 or is_emergency_halted():
        return False

    broker = broker or BinanceFuturesBroker()
    try:
        snapshot = get_account_snapshot(broker)
    except BrokerError:
        logger.exception("긴급 서킷브레이커: 계좌 조회 실패 - 이번 주기는 건너뜀")
        return False

    current = snapshot["total_margin_balance"]
    reference = _todays_reference_balance(current)
    if reference <= 0:
        return False

    drawdown_pct = (reference - current) / reference * 100
    if drawdown_pct < settings.emergency_stop_pct:
        return False

    logger.critical(
        "🚨 긴급 서킷브레이커 발동: 오늘 시작(%.2f) 대비 %.2f%% 손실(기준 %.2f%%) - 전 포지션 강제 청산",
        reference, drawdown_pct, settings.emergency_stop_pct,
    )
    _close_all_open_positions(broker)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EMERGENCY_HALT_FILE.write_text(
        json.dumps({
            "halted_at": datetime.now(timezone.utc).isoformat(),
            "drawdown_pct": round(drawdown_pct, 2),
            "reference_balance": reference,
            "balance_at_halt": current,
        }, indent=2),
        encoding="utf-8",
    )
    send_message(
        f"🚨🚨🚨 긴급 서킷브레이커 발동 🚨🚨🚨\n"
        f"오늘 시작 대비 {drawdown_pct:.2f}% 손실 (기준 {settings.emergency_stop_pct}%)\n"
        f"전 포지션 강제 청산 완료. data/emergency_halt.json을 직접 지우기 전까지 "
        f"신규 진입이 전부 막혀있습니다."
    )
    return True


def _close_all_open_positions(broker: BinanceFuturesBroker) -> None:
    session = SessionLocal()
    try:
        open_trades = session.query(TradeRecord).filter(TradeRecord.status == "OPEN").all()
        for trade in open_trades:
            try:
                exit_side = "SELL" if trade.side == "BUY" else "BUY"
                result = broker.close_position_market(trade.symbol, trade.quantity, side=exit_side)
                trade.status = "CLOSED_MANUAL"
                trade.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                trade.exit_price = result.get("price")
                trade.realized_pnl_usdt = realized_pnl_usdt(trade, trade.exit_price)
                session.commit()
            except BrokerError:
                logger.exception("긴급 청산 실패 (수동 확인 필요): %s %s", trade.symbol, trade.timeframe)
                session.rollback()
    finally:
        session.close()
