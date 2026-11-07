"""일일 손실 한도 킬스위치 + 거래당 리스크 금액(USDT) 계산.

킬스위치: 당일(UTC) 청산된 거래의 실현손익 합계가 일일 한도를 넘는
손실이면, 신규 진입만 차단한다 (이미 걸려있는 SL/TP 주문은 거래소에
그대로 남아 계속 보호 역할을 하므로 건드리지 않는다). 다음날(UTC 자정)이
되면 자동으로 다시 풀린다.

한도는 두 방식 중 하나다: `DAILY_LOSS_LIMIT_USDT`(기본, 고정 금액) 또는
`DAILY_LOSS_LIMIT_PCT`(정률제, 0보다 크면 우선 적용 - 신규 진입 시도마다
가용잔고를 다시 조회해 "잔고의 N%"를 그 순간의 한도로 쓴다).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func

from .config import settings
from .db import SessionLocal, TradeRecord

logger = logging.getLogger(__name__)

_CLOSED_STATUSES = ("CLOSED_TP", "CLOSED_SL", "CLOSED_TIME", "CLOSED_MANUAL")


def todays_realized_pnl_usdt() -> float:
    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day = start_of_day.replace(tzinfo=None)  # DB는 naive UTC로 저장

    session = SessionLocal()
    try:
        total = (
            session.query(func.coalesce(func.sum(TradeRecord.realized_pnl_usdt), 0.0))
            .filter(TradeRecord.status.in_(_CLOSED_STATUSES))
            .filter(TradeRecord.closed_at >= start_of_day)
            .scalar()
        )
        return float(total or 0.0)
    finally:
        session.close()


def _resolve_daily_loss_limit_usdt(broker) -> float:
    """정률제(`DAILY_LOSS_LIMIT_PCT`>0)면 "지금 가용잔고의 N%"를 매번
    새로 계산해서 돌려주고, 아니면 고정값(`DAILY_LOSS_LIMIT_USDT`)을
    그대로 돌려준다. 정률제인데 broker가 없거나 잔고 조회가 실패/0 이하면
    안전하게 고정값으로 폴백한다(RISK_MODE=percent_balance와 동일한 철학)."""
    if settings.daily_loss_limit_pct <= 0:
        return settings.daily_loss_limit_usdt

    if broker is None:
        logger.warning("정률제 일일 손실 한도인데 broker가 없음 - 고정 한도(DAILY_LOSS_LIMIT_USDT)로 폴백")
        return settings.daily_loss_limit_usdt

    try:
        balance = broker.get_available_balance_usdt()
    except Exception:
        logger.exception("계좌 잔고 조회 실패 - 고정 일일 손실 한도로 폴백")
        return settings.daily_loss_limit_usdt

    if not balance or balance <= 0:
        logger.warning("계좌 가용 잔고가 0 이하(%s) - 고정 일일 손실 한도로 폴백", balance)
        return settings.daily_loss_limit_usdt

    return balance * settings.daily_loss_limit_pct / 100.0


def is_kill_switch_active(broker=None) -> bool:
    """오늘 실현손실이 한도를 넘었으면 True (신규 진입 차단 신호).

    `broker`는 정률제(`DAILY_LOSS_LIMIT_PCT`) 한도를 계산할 때만 쓰인다 -
    고정 금액제(기본값)에서는 안 넘겨도 된다."""
    limit = _resolve_daily_loss_limit_usdt(broker)
    if limit <= 0:
        return False  # 0 이하로 설정하면 킬스위치 비활성 취급
    return todays_realized_pnl_usdt() <= -abs(limit)


def compute_risk_usdt(broker) -> float:
    """이번 거래에 걸 리스크 금액(USDT)을 정한다.

    RISK_MODE=fixed(기본값): RISK_PER_TRADE_USDT 고정 금액을 그대로 쓴다.

    RISK_MODE=percent_balance: 매 거래 직전 선물 지갑의 가용 잔고
    (broker.get_available_balance_usdt())를 다시 조회해 그
    RISK_PERCENT_OF_BALANCE(%)를 리스크로 쓴다 - 잔고가 늘면 다음 거래
    리스크도 같이 커지는 복리형 사이징이다(모의투자 계좌가 "잔고 전액
    배팅"으로 복리 성장하는 것과 같은 원리를, 실계좌에서는 안전한 비율만
    떼어 쓰는 것). 잔고 조회가 실패하거나 0 이하로 나오면 안전하게 고정
    금액(RISK_PER_TRADE_USDT)으로 폴백한다 - 잔고 API 오류 때문에 거래
    자체가 막히거나 이상값이 계산되는 걸 막기 위함.

    RISK_PERCENT_MAX_USDT(>0)이 설정돼 있으면 계산된 리스크를 그 값 이하로
    자른다 - 잔고 조회가 이상값을 반환하는 등의 버그로 한 거래에 과도한
    리스크가 걸리는 걸 막는 마지막 안전장치.
    """
    if settings.risk_mode != "percent_balance":
        return settings.risk_per_trade_usdt

    try:
        balance = broker.get_available_balance_usdt()
    except Exception:
        logger.exception("계좌 잔고 조회 실패 - 고정 리스크(RISK_PER_TRADE_USDT)로 폴백")
        return settings.risk_per_trade_usdt

    if not balance or balance <= 0:
        logger.warning("계좌 가용 잔고가 0 이하(%s) - 고정 리스크로 폴백", balance)
        return settings.risk_per_trade_usdt

    risk_usdt = balance * settings.risk_percent_of_balance / 100.0
    if settings.risk_percent_max_usdt > 0:
        risk_usdt = min(risk_usdt, settings.risk_percent_max_usdt)
    return risk_usdt
