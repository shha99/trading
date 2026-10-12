"""DART/KIND 공시 포털 서버.

- `/` : 실시간 공시 포털 (전체 공시 + 관심종목)
- `/api/disclosures` : 날짜별 공시 목록 (오늘은 after_seq로 증분 조회)
- `/api/status` : 폴링 상태
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.dart import DartClient, DartError
from app.store import DisclosureStore, today_kst

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

store = DisclosureStore(DartClient(settings.dart_api_key))


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = None
    if settings.enable_scheduler:
        threading.Thread(target=store.poll_today, daemon=True).start()
        scheduler = BackgroundScheduler(timezone="Asia/Seoul")
        scheduler.add_job(store.poll_today, "interval", seconds=settings.poll_seconds, max_instances=1, coalesce=True)
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="공시 포털", version="0.1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """PORTAL_PASSWORD가 있으면 HTTP Basic 인증으로 막는다(/healthz는 예외)."""
    if not settings.portal_password or request.url.path == "/healthz":
        return await call_next(request)
    try:
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        _, _, password = base64.b64decode(encoded).decode().partition(":")
        ok = scheme.lower() == "basic" and secrets.compare_digest(password, settings.portal_password)
    except (binascii.Error, UnicodeDecodeError):
        ok = False
    if ok:
        return await call_next(request)
    return PlainTextResponse(
        "비밀번호가 필요합니다.", status_code=401, headers={"WWW-Authenticate": 'Basic realm="disclosure-portal", charset="UTF-8"'}
    )


def _check_date(date: str) -> None:
    if not re.fullmatch(r"\d{8}", date):
        raise HTTPException(400, "date는 YYYYMMDD 형식이어야 합니다.")
    if date > today_kst():
        raise HTTPException(400, "미래 날짜는 조회할 수 없습니다.")


@app.get("/api/disclosures")
def disclosures(
    date: str | None = Query(None, description="YYYYMMDD, 생략하면 오늘(KST)"),
    after_seq: int = Query(0, ge=0, description="오늘 목록에서 이 seq 이후 것만"),
):
    if date is None or date == today_kst():
        return store.today(after_seq)
    _check_date(date)
    try:
        return store.for_date(date)
    except DartError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/refresh")
def refresh(date: str | None = Query(None, description="YYYYMMDD, 생략하면 오늘(KST)")):
    """수동 새로고침: DART에서 그 날짜 전체를 다시 받아 지금 목록과 대사한다."""
    date = date or today_kst()
    _check_date(date)
    try:
        return store.reconcile(date)
    except DartError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/status")
def status():
    return {"today": today_kst(), "poll_seconds": settings.poll_seconds, **store.status()}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
