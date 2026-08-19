"""FastAPI 응답 모델."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StrategyOut(BaseModel):
    key: str
    label: str


class SignalOut(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    strategy: str
    strategy_label: str
    signal_type: str
    price: float
    timestamp: str
    created_at: str | None = None
    details: dict[str, Any] = {}


class RefreshOut(BaseModel):
    detected: int
