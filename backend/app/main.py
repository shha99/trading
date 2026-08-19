"""FastAPI 진입점."""
from __future__ import annotations

import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import settings
from .database import init_db
from .scheduler import run_once, start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trading Signal Dashboard API",
    description="전략별로 시그널링된 종목을 보여주는 대시보드의 백엔드 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    start_scheduler()
    # 서버 기동 직후에도 데이터가 비어있지 않도록 백그라운드에서 1회 즉시 실행.
    threading.Thread(target=run_once, daemon=True).start()


@app.get("/")
def root() -> dict:
    return {"service": "trading-signal-dashboard", "docs": "/docs"}
