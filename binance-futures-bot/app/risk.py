"""일일 손실 한도 킬스위치.

당일(UTC) 청산된 거래의 실현손익 합계가 DAILY_LOSS_LIMIT_USDT를 넘는
손실이면, 신규 진입만 차단한다 (이미 걸려있는 SL/TP 주문은 거래소에 그대로
남아 계속 보호 역할을 하므로 건드리지 않는다). 다음날(UTC 자정)이 되면
자동으로 다시 풀린다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func

from .config import settings
from .db import SessionLocal, TradeRecord

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


def is_kill_switch_active() -> bool:
    """오늘 실현손실이 한도를 넘었으면 True (신규 진입 차단 신호)."""
    if settings.daily_loss_limit_usdt <= 0:
        return False  # 0 이하로 설정하면 킬스위치 비활성 취급
    return todays_realized_pnl_usdt() <= -abs(settings.daily_loss_limit_usdt)
