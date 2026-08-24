"""바이낸스 선물 시그널 엔진 + 테스트넷 자동매매 + 실시간 차트 대시보드 서버.

- `/` : 실시간 차트 대시보드 (165종 지표 + 61종 캔들패턴)
- `/strategy` : 켈트너 하단 복귀 전략 전용 페이지 (백테스트 성적표 등)
- 그 외는 위 두 페이지가 쓰는 REST/WS API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import timedelta
from pathlib import Path

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc

from app.config import settings
from app.db import SessionLocal, SignalRecord, TradeRecord, init_db
from app.history import is_candle_closed
from app.indicator_catalog import build_catalog, compute_indicator
from app.lab_stats_builder import LAB_STATS_FILE, catalog as lab_catalog
from app.live_feed import get_live_feed
from app.position_manager import check_time_stops, reconcile_open_positions
from app.risk import is_kill_switch_active, todays_realized_pnl_usdt
from app.signal_engine import run_once
from app.strategy import KeltnerReclaimStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"

app = FastAPI(
    title="Binance Futures Signal & Testnet Auto-Trading Bot",
    description="실시간 차트 대시보드 + 200EMA/켈트너 하단 복귀 전략 시그널 엔진 및 테스트넷 자동매매 API",
    version="0.2.0",
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
async def on_startup() -> None:
    init_db()
    _start_scheduler()
    threading.Thread(target=run_once, daemon=True).start()
    get_live_feed().start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    await get_live_feed().stop()


def _position_watch_tick() -> None:
    """열린 포지션 관련 백그라운드 점검: 시간손절 청산 + SL/TP 체결 반영."""
    check_time_stops()
    reconcile_open_positions()


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
        _position_watch_tick, trigger="interval", seconds=settings.position_watch_interval_seconds,
        id="position_watch", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "스케줄러 시작: 시그널 스캔 %d초, 포지션 점검 %d초 간격",
        settings.scan_interval_seconds, settings.position_watch_interval_seconds,
    )
    return _scheduler


# --------------------------------------------------------------------------
# 페이지 (정적 파일)
# --------------------------------------------------------------------------

@app.get("/")
def dashboard_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/strategy")
def strategy_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "strategy.html")


@app.get("/lab")
def lab_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "lab.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --------------------------------------------------------------------------
# 헬스/시그널/매매 이력 (기존 MVP API)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# 차트 대시보드 API (캔들 / 지표 / 실시간 시세)
# --------------------------------------------------------------------------

def _candles_to_json(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "time": int(ts.timestamp()),
            "open": float(row.Open), "high": float(row.High),
            "low": float(row.Low), "close": float(row.Close),
            "volume": float(row.Volume),
        }
        for ts, row in df.iterrows()
    ]


@app.get("/api/indicators")
def list_indicators() -> list[dict]:
    return build_catalog()


@app.get("/api/candles")
def get_candles(symbol: str, timeframe: str, limit: int = Query(default=300, le=1500)) -> list[dict]:
    df = get_live_feed().get_candles(symbol.upper(), timeframe, limit=limit)
    return _candles_to_json(df) if df is not None else []


@app.get("/api/indicator-values")
def get_indicator_values(
    symbol: str, timeframe: str, id: str,
    limit: int = Query(default=300, le=1500),
    params: str | None = Query(default=None, description='JSON, 예: {"timeperiod":20}'),
) -> dict:
    df = get_live_feed().get_candles(symbol.upper(), timeframe, limit=limit)
    if df is None or df.empty:
        return {}
    try:
        parsed_params = json.loads(params) if params else {}
        result = compute_indicator(id, df, parsed_params)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}

    times = [int(ts.timestamp()) for ts in df.index]
    return {
        name: [
            {"time": t, "value": None if pd.isna(v) else float(v)}
            for t, v in zip(times, series.tolist())
        ]
        for name, series in result.items()
    }


@app.get("/api/ticker24h")
def get_ticker24h(symbol: str) -> dict:
    return get_live_feed().get_ticker24h(symbol.upper()) or {}


@app.get("/api/price")
def get_price(symbol: str) -> dict:
    return get_live_feed().get_price(symbol.upper()) or {}


# --------------------------------------------------------------------------
# 전략 페이지 API
# --------------------------------------------------------------------------

@app.get("/api/strategy/live-status")
def strategy_live_status(symbol: str = "BTCUSDT", timeframe: str = "1h") -> dict:
    df = get_live_feed().get_candles(symbol.upper(), timeframe, limit=250)
    if df is None or df.empty:
        return {"ready": False, "reason": "no_data"}
    if not is_candle_closed(df, timeframe):
        df = df.iloc[:-1]
    return KeltnerReclaimStrategy().condition_status(df)


@app.get("/api/strategy/stats")
def strategy_stats() -> dict:
    path = DATA_DIR / "strategy_stats.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/strategy/signals/recent")
def strategy_recent_signals(limit: int = Query(default=50, le=500)) -> list[dict]:
    session = SessionLocal()
    try:
        signals = (
            session.query(SignalRecord)
            .filter(SignalRecord.strategy == KeltnerReclaimStrategy.key)
            .order_by(desc(SignalRecord.timestamp))
            .limit(limit)
            .all()
        )
        out = []
        for sig in signals:
            entry = sig.to_dict()
            trade = (
                session.query(TradeRecord)
                .filter(TradeRecord.symbol == sig.symbol, TradeRecord.timeframe == sig.timeframe)
                .filter(TradeRecord.opened_at >= sig.timestamp - timedelta(minutes=5))
                .filter(TradeRecord.opened_at <= sig.timestamp + timedelta(minutes=5))
                .first()
            )
            entry["trade"] = trade.to_dict() if trade else None
            out.append(entry)
        return out
    finally:
        session.close()


# --------------------------------------------------------------------------
# 전략 실험실 (켈트너 1 + 후보 7 = 8종 비교) API
# --------------------------------------------------------------------------

@app.get("/api/lab/strategies")
def lab_strategies_catalog() -> list[dict]:
    return lab_catalog()


@app.get("/api/lab/stats")
def lab_stats() -> dict:
    if not LAB_STATS_FILE.exists():
        return {}
    return json.loads(LAB_STATS_FILE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 실시간 WebSocket (현재가 / 24h 통계 / 캔들)
# --------------------------------------------------------------------------

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    feed = get_live_feed()
    symbols = settings.symbols
    timeframes = settings.dashboard_timeframes

    try:
        while True:
            candles: dict[str, dict[str, list[dict]]] = {}
            for symbol in symbols:
                candles[symbol] = {}
                for timeframe in timeframes:
                    df = feed.get_candles(symbol, timeframe, limit=2)
                    if df is not None:
                        candles[symbol][timeframe] = _candles_to_json(df)

            await websocket.send_json({
                "type": "tick",
                "prices": {s: feed.get_price(s) for s in symbols},
                "ticker24h": {s: feed.get_ticker24h(s) for s in symbols},
                "candles": candles,
            })
            await asyncio.sleep(settings.live_poll_interval_seconds)
    except WebSocketDisconnect:
        logger.info("실시간 WS 클라이언트 연결 종료")
    except Exception:
        logger.exception("실시간 WS 처리 중 오류")
