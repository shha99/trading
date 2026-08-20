from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import batch_update, cache_store, config
from .api.routes import router
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _seed_cache_if_empty():
    """캐시가 비어 있으면(최초 배포 직후 등) 백그라운드 스레드에서 1회 자동 채움.

    스케줄러는 평일 16:30 KST에만 도니, 방금 띄운 빈 서버가 그때까지
    "데이터 없음" 상태로 남아있지 않도록 시작 시 한 번 즉시 채워준다.
    수 분 걸릴 수 있어 앱 시작을 막지 않게 별도 스레드에서 실행한다.
    """
    if not cache_store.load_prices().empty:
        return

    def _run():
        logger.info("캐시가 비어 있어 초기 데이터 수집을 백그라운드에서 시작합니다 (수 분 소요될 수 있음)")
        try:
            result = batch_update.refresh_via_naver()
            logger.info("초기 데이터 수집 완료: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("초기 데이터 수집 실패 - 수동 새로고침 또는 다음 스케줄에서 재시도하세요")

    threading.Thread(target=_run, daemon=True, name="initial-seed").start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    _seed_cache_if_empty()
    yield
    stop_scheduler()


app = FastAPI(
    title="KOSPI/KOSDAQ 디커플링 페어 검색 API",
    description="코스피·코스닥 전종목 대상 음의 상관관계(디커플링) 페어 검색",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in config.CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
