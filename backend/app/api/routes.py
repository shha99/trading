"""시그널 대시보드 API 라우트."""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import desc

from ..database import SessionLocal, SignalRecord
from ..scheduler import run_once
from ..schemas import RefreshOut, SignalOut, StrategyOut
from ..signals.registry import default_strategies

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/strategies", response_model=list[StrategyOut])
def list_strategies() -> list[dict]:
    return [{"key": s.key, "label": s.label} for s in default_strategies()]


@router.get("/signals", response_model=list[SignalOut])
def list_signals(
    strategy: str | None = None,
    market: str | None = Query(default=None, description="KR 또는 US"),
    signal_type: str | None = Query(default=None, description="BUY 또는 SELL"),
    symbol: str | None = None,
    latest_only: bool = Query(default=True, description="종목x전략별 최신 시그널 1건만"),
    limit: int = Query(default=200, le=1000),
) -> list[dict]:
    session = SessionLocal()
    try:
        q = session.query(SignalRecord)
        if strategy:
            q = q.filter(SignalRecord.strategy == strategy)
        if market:
            q = q.filter(SignalRecord.market == market.upper())
        if signal_type:
            q = q.filter(SignalRecord.signal_type == signal_type.upper())
        if symbol:
            q = q.filter(SignalRecord.symbol == symbol)
        q = q.order_by(desc(SignalRecord.timestamp), desc(SignalRecord.created_at))
        # latest_only일 때 중복 제거 여유분을 두고 넉넉히 가져온다.
        fetch_limit = limit * 5 if latest_only else limit
        records = q.limit(fetch_limit).all()

        if latest_only:
            seen: set[tuple[str, str]] = set()
            deduped = []
            for r in records:
                key = (r.symbol, r.strategy)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(r)
                if len(deduped) >= limit:
                    break
            records = deduped
        else:
            records = records[:limit]

        return [r.to_dict() for r in records]
    finally:
        session.close()


@router.post("/refresh", response_model=RefreshOut)
def refresh() -> dict:
    """수동으로 시그널 재계산을 트리거한다 (개발/디버깅용)."""
    results = run_once()
    return {"detected": len(results)}
