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
from app.lab_stats_builder import LAB_STATS_FILE
from app.lab_stats_builder import build_all as build_lab_stats
from app.lab_stats_builder import catalog as lab_catalog
from app.live_feed import get_live_feed
from app.multi_screen_backtest import MULTI_SCREEN_TRADES_FILE
from app.multi_screen_backtest import build_merged_trades as build_multi_screen_trades
from app.multi_screen_backtest import compute_return as multi_screen_compute_return
from app.paper_trading import get_status as paper_trading_status
from app.paper_trading import run_once as run_paper_trading_once
from app.position_manager import check_time_stops, reconcile_open_positions
from app.risk import is_kill_switch_active, todays_realized_pnl_usdt
from app.signal_engine import run_once
from app.signal_outcome_tracker import check_signal_outcomes
from app.stats_builder import STATS_FILE
from app.stats_builder import build_all as build_strategy_stats
from app.strategy import KeltnerReclaimStrategy
from app.validated_lab_stats_builder import VALIDATED_STATS_FILE
from app.validated_lab_stats_builder import build_all as build_validated_lab_stats
from app.wick_position_manager import manage_wick_positions
from app.wick_signal_engine import run_once as run_wick_signal_once

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
_stats_refresh_lock = threading.Lock()


def _build_missing_stats_in_background() -> None:
    """`/strategy`, `/lab` 페이지용 성적표가 아직 없으면 백그라운드에서 생성한다.

    로컬 개발에서는 `build_stats.py`/`build_lab_stats.py`를 미리 수동으로 돌려두지만,
    (Render 등에) 처음 배포한 서버는 이 파일들이 아예 없는 상태로 뜬다 — 매번 셸에 들어가
    스크립트를 돌리지 않아도 되도록, 서버가 뜰 때 파일이 없으면 알아서 한 번 만든다.
    몇 분 걸릴 수 있어 별도 스레드에서 실행하고, 끝나기 전까지 두 페이지는 "아직 계산되지
    않았다"는 안내만 보여준다(기존 API가 이미 그렇게 처리하고 있음).

    이건 "파일이 없을 때 한 번" 만이고, 계속 최신 상태로 유지하는 건
    `_refresh_all_stats_in_background()`(주기적 재계산, 아래)가 맡는다.
    """
    if not STATS_FILE.exists():
        threading.Thread(target=build_strategy_stats, daemon=True).start()
    if not LAB_STATS_FILE.exists():
        threading.Thread(target=build_lab_stats, daemon=True).start()
    if not VALIDATED_STATS_FILE.exists():
        # 15분/5분봉은 3년 이상 데이터를 받아와야 해서(특히 5분봉) 위 두 개보다
        # 오래 걸릴 수 있다 - 별도 스레드라 서버 기동 자체는 막지 않는다.
        threading.Thread(target=build_validated_lab_stats, daemon=True).start()
    if not MULTI_SCREEN_TRADES_FILE.exists():
        # 4개 조합을 각각 3년 이상 받아와야 해서 셋 중 가장 오래 걸릴 수 있다.
        threading.Thread(target=build_multi_screen_trades, daemon=True).start()


def _refresh_all_stats() -> None:
    """네 백테스트 성적표(켈트너/실험실 11종/검증된 2종/멀티스크리닝 원장)를 전부 다시 계산한다.

    브라우저를 열어두지 않아도 서버 프로세스가 켜져 있는 동안은 스케줄러가
    주기적으로(기본 24시간, `STATS_REFRESH_INTERVAL_HOURS`) 이 함수를 불러
    스스로 최신 데이터로 갱신한다 - 사람이 페이지를 띄워놓거나 스크립트를
    수동으로 돌릴 필요가 없다. 이전 갱신이 아직 끝나지 않았으면(특히 검증된
    전략의 5분봉 백테스트는 몇 분씩 걸림) 겹쳐 돌지 않도록 건너뛴다.
    """
    if not _stats_refresh_lock.acquire(blocking=False):
        logger.info("백테스트 성적표 정기 갱신 건너뜀 - 이전 갱신이 아직 진행 중")
        return
    try:
        logger.info("백테스트 성적표 정기 갱신 시작")
        build_strategy_stats()
        build_lab_stats()
        build_validated_lab_stats()
        build_multi_screen_trades()
        logger.info("백테스트 성적표 정기 갱신 완료")
    except Exception:
        logger.exception("백테스트 성적표 정기 갱신 실패 (다음 주기에 재시도)")
    finally:
        _stats_refresh_lock.release()


def _refresh_all_stats_in_background() -> None:
    threading.Thread(target=_refresh_all_stats, daemon=True).start()


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    _start_scheduler()
    threading.Thread(target=run_once, daemon=True).start()
    threading.Thread(target=run_wick_signal_once, daemon=True).start()
    threading.Thread(target=run_paper_trading_once, daemon=True).start()
    get_live_feed().start()
    _build_missing_stats_in_background()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    await get_live_feed().stop()


def _position_watch_tick() -> None:
    """열린 포지션 관련 백그라운드 점검: 시간손절 청산 + SL/TP 체결 반영 +
    (화이트리스트 밖이라 실제 주문이 안 나간) 시그널의 가상 체결 결과 갱신 +
    wick 엔진의 트레일링 스탑 갱신/청산 반영."""
    check_time_stops()
    reconcile_open_positions()
    check_signal_outcomes()
    manage_wick_positions()


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
        run_wick_signal_once, trigger="interval", seconds=settings.scan_interval_seconds,
        id="wick_signal_scan", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _position_watch_tick, trigger="interval", seconds=settings.position_watch_interval_seconds,
        id="position_watch", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        _refresh_all_stats_in_background, trigger="interval", hours=settings.stats_refresh_interval_hours,
        id="stats_refresh", max_instances=1, coalesce=True,
    )
    _scheduler.add_job(
        run_paper_trading_once, trigger="interval", seconds=settings.scan_interval_seconds,
        id="paper_trading_scan", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "스케줄러 시작: 시그널 스캔 %d초(켈트너+wick), 포지션 점검 %d초, "
        "백테스트 성적표 갱신 %.1f시간 간격, 모의투자 스캔 %d초 - "
        "wick 자동매매 %s (화이트리스트 %s)",
        settings.scan_interval_seconds, settings.position_watch_interval_seconds,
        settings.stats_refresh_interval_hours, settings.scan_interval_seconds,
        "ON" if settings.wick_auto_trade_enabled else "off",
        sorted(f"{s}:{tf}" for s, tf in settings.wick_auto_trade_whitelist) or "없음",
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


@app.get("/vf")
def validated_strategies_page() -> FileResponse:
    """검증된 전략(Validated - 켈트너/콘플루언스/볼린저 꼬리터치+RSI) 전용 페이지.
    /strategy와 완전히 같은 JS(static/strategy_page.js)를 재사용하고,
    기본 탭만 최신 최적화 전략(wick)으로 연다(vf.html의 인라인 스크립트)."""
    return FileResponse(STATIC_DIR / "vf.html")


@app.get("/trading")
def trading_page() -> FileResponse:
    """실제 투자 가능한 형태로 구성한 매매 현황판 - 두 자동매매 엔진(켈트너/wick)의
    켜짐 여부·화이트리스트, 오늘의 리스크 현황, 열린 포지션·최근 매매 기록
    (전부 기존 /api/positions/open, /api/trades, /api/risk/status, /api/health를
    그대로 재사용 - 새 백엔드 엔드포인트 없음), 실시간 모의투자 현황까지 한
    화면에 모아 보여준다."""
    return FileResponse(STATIC_DIR / "trading.html")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    """PWA 서비스워커 - 루트 경로("/sw.js")로 내려줘야 스코프가 사이트 전체가 된다
    (스코프는 기본적으로 스크립트 위치 디렉터리로 제한되므로 /static/sw.js로
    두면 /static/ 아래만 제어하게 됨)."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


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
        "wick_auto_trade_enabled": settings.wick_auto_trade_enabled,
        "wick_auto_trade_whitelist": sorted(f"{s}:{tf}" for s, tf in settings.wick_auto_trade_whitelist),
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


@app.get("/api/lab/validated-stats")
def validated_lab_stats() -> dict:
    """켈트너 전략과 동급(학습/검증/연도별 분리)으로 검증된 lab 후보들의 성적
    (big_candle_bollinger_confluence, bollinger_wick_breakeven_trail) - 여전히
    비교/참고용이고 자동매매 엔진에는 연결돼 있지 않다."""
    if not VALIDATED_STATS_FILE.exists():
        return {}
    return json.loads(VALIDATED_STATS_FILE.read_text(encoding="utf-8"))


@app.get("/api/lab/multi-screen-backtest")
def multi_screen_backtest(
    bet_fraction_pct: float = Query(default=20.0, ge=0.1, le=300.0),
    start: str | None = Query(default=None, description='예: "2023-01-01", "2026-01-01" - 생략하면 전체 히스토리'),
) -> dict:
    """4종목(BTC/ETH×15m/5m) 동시 스크리닝 - 계좌 1개, 포지션 1개, "먼저 뜨는
    신호"에 배팅비율(%)만큼 진입하는 방식을 배팅비율/시작일을 바꿔가며
    즉석에서 재계산해 보여준다. `data/multi_screen_trades.json`(4개 조합을
    각각 백테스트해 합친 원장)만 읽으므로 네트워크 호출 없이 빠르게 응답한다."""
    if start is not None:
        try:
            pd.Timestamp(start)
        except (ValueError, TypeError):
            return {"ready": False, "error": f"잘못된 날짜 형식: {start}"}
    return multi_screen_compute_return(bet_fraction_pct, start)


# --------------------------------------------------------------------------
# 실시간 모의투자 (paper trading) API
# --------------------------------------------------------------------------

@app.get("/api/paper-trading/status")
def paper_trading_status_endpoint() -> dict:
    """검증된 볼린저 꼬리터치+RSI 전략(BTCUSDT 15분봉)을 100만원 가상 잔고로
    지금부터 실시간 굴리는 모의투자 계좌의 현재 상태. 실제 주문은 전혀 나가지
    않는다 - 백그라운드 스케줄러(`app/paper_trading.run_once`)가 주기적으로
    최근 캔들을 다시 백테스트해서 새로 청산된 거래만 반영한다."""
    return paper_trading_status()


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
