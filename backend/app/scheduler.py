"""주기적으로 SignalEngine을 실행하는 백그라운드 스케줄러."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import settings
from .engine import SignalEngine
from .signals.base import Signal

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def run_once() -> list[Signal]:
    logger.info("시그널 엔진 실행 시작")
    results = SignalEngine().run()
    logger.info("시그널 엔진 실행 완료: %d건 감지", len(results))
    return results


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.enable_scheduler:
        logger.info("스케줄러가 비활성화되어 있습니다 (ENABLE_SCHEDULER=false)")
        return None
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_once,
        trigger="interval",
        minutes=settings.refresh_interval_minutes,
        id="signal_refresh",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("스케줄러 시작: %d분 간격으로 재계산", settings.refresh_interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
