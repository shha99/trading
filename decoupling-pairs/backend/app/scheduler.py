"""매 거래일 장마감 후 자동 갱신 스케줄러.

pykrx는 KRX 종가(EOD) 데이터만 제공하므로 장중 실시간 갱신은 불가능하다.
정산 데이터 안정화 시간을 고려해 평일 16:30 KST에 실행하고, 실패 시 로그만
남기고 다음날 스케줄에서 자연히 재시도된다(캐시가 밀린 날짜부터 이어서
받아오므로 별도 복구 로직이 필요 없다).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import batch_update, config

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_job():
    # Naver 경로를 기본으로 쓴다 - KRX 직접 접속(update_latest)은 한국 리전에서만
    # 동작하므로, 어디서나 동작하는 이 경로를 기본값으로 둔다.
    # NOTE: 현재는 매 실행마다 업종/유니버스까지 함께 재조회한다. 트래픽을
    # 더 아끼고 싶다면 업종 매핑은 주 1회만 갱신하도록 분리하는 것을 권장한다.
    try:
        result = batch_update.refresh_via_naver()
        logger.info("스케줄 갱신 완료: %s", result)
    except Exception:  # noqa: BLE001 - 배치 실패는 로그만 남기고 다음날 재시도
        logger.exception("스케줄 갱신 실패 - 다음 거래일에 재시도됩니다")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone=config.KST)
    _scheduler.add_job(
        _run_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=config.SCHEDULE_HOUR_KST,
            minute=config.SCHEDULE_MINUTE_KST,
            timezone=config.KST,
        ),
        id="daily_krx_update",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "일일 갱신 스케줄러 시작 (매 평일 %02d:%02d KST)",
        config.SCHEDULE_HOUR_KST,
        config.SCHEDULE_MINUTE_KST,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
