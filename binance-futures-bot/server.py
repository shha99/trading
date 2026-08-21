"""바이낸스 선물 시그널 엔진 + 테스트넷 자동매매 서버.

이번 단계(MVP)는 차트/165종 지표 대시보드 UI를 만들지 않는다 — 시그널
발생 → 텔레그램 알림 → (화이트리스트에 있으면) 테스트넷 자동매매까지가
목표이고, 결과 확인은 아래 읽기 전용 API와 텔레그램 알림으로 한다.
"""
from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc

from app.config import settings
from app.db import SessionLocal, SignalRecord, TradeRecord, init_db
from app.position_manager import check_time_stops
from app.risk import is_kill_switch_active, todays_realized_pnl_usdt
from app.signal_engine import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Binance Futures Signal & Testnet Auto-Trading Bot",
    description="200EMA + 켈트너 하단 눌림목 복귀 전략의 시그널 엔진 및 테스트넷 자동매매 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_scheduler: BackgroundScheduler | None = None


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    _start_scheduler()
    threading.Thread(target=run_once, daemon=True).start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_once, trigger="interval", seconds=settings.scan_interval_seconds,
        id="signal_scan", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        check_time_stops, trigger="interval", seconds=settings.position_watch_interval_seconds,
        id="position_time_stop_watch", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "스케줄러 시작: 시그널 스캔 %d초, 시간손절 감시 %d초 간격",
        settings.scan_interval_seconds, settings.position_watch_interval_seconds,
    )
    return _scheduler


@app.get("/")
def root() -> dict:
    return {"service": "binance-futures-signal-bot", "docs": "/docs"}


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "testnet": settings.binance_testnet,
        "auto_trade_enabled": settings.auto_trade_enabled,
        "auto_trade_whitelist": sorted(f"{s}:{tf}" for s, tf in settings.auto_trade_whitelist),
    }


@app.get("/api/signals")
def list_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(default=100, le=1000),
) -> list[dict]:
    session = SessionLocal()
    try:
        q = session.query(SignalRecord)
        if symbol:
            q = q.filter(SignalRecord.symbol == symbol.upper())
        if timeframe:
            q = q.filter(SignalRecord.timeframe == timeframe)
        q = q.order_by(desc(SignalRecord.timestamp)).limit(limit)
        return [r.to_dict() for r in q.all()]
    finally:
        session.close()


@app.get("/api/positions/open")
def list_open_positions() -> list[dict]:
    session = SessionLocal()
    try:
        rows = session.query(TradeRecord).filter(TradeRecord.status == "OPEN").all()
        return [r.to_dict() for r in rows]
    finally:
        session.close()


@app.get("/api/trades")
def list_trades(status: str | None = None, limit: int = Query(default=100, le=1000)) -> list[dict]:
    session = SessionLocal()
    try:
        q = session.query(TradeRecord)
        if status:
            q = q.filter(TradeRecord.status == status.upper())
        q = q.order_by(desc(TradeRecord.opened_at)).limit(limit)
        return [r.to_dict() for r in q.all()]
    finally:
        session.close()


@app.get("/api/risk/status")
def risk_status() -> dict:
    return {
        "daily_loss_limit_usdt": settings.daily_loss_limit_usdt,
        "todays_realized_pnl_usdt": todays_realized_pnl_usdt(),
        "kill_switch_active": is_kill_switch_active(),
    }


@app.post("/api/refresh")
def refresh() -> dict:
    """수동으로 시그널 스캔을 1회 트리거한다 (개발/디버깅용)."""
    detected = run_once()
    return {"detected": len(detected)}
