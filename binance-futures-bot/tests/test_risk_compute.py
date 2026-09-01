"""app/risk.py::compute_risk_usdt 검증 - RISK_MODE=fixed/percent_balance 전환,
잔고 조회 실패/0 이하 시 고정 리스크로 폴백, RISK_PERCENT_MAX_USDT 상한.
실제 바이낸스 호출 없음 - 가짜 broker 주입."""
from __future__ import annotations

import pytest

from app.config import settings
from app.risk import compute_risk_usdt


class FakeBroker:
    def __init__(self, balance=None, raises=False):
        self._balance = balance
        self._raises = raises

    def get_available_balance_usdt(self) -> float:
        if self._raises:
            raise RuntimeError("네트워크 오류")
        return self._balance


def test_fixed_mode_ignores_balance_and_returns_configured_amount(monkeypatch):
    monkeypatch.setattr(settings, "risk_mode", "fixed")
    monkeypatch.setattr(settings, "risk_per_trade_usdt", 10.0)

    broker = FakeBroker(balance=100_000.0)  # fixed 모드니까 이 값은 무시돼야 함
    assert compute_risk_usdt(broker) == pytest.approx(10.0)


def test_percent_balance_mode_computes_from_available_balance(monkeypatch):
    monkeypatch.setattr(settings, "risk_mode", "percent_balance")
    monkeypatch.setattr(settings, "risk_percent_of_balance", 2.0)
    monkeypatch.setattr(settings, "risk_percent_max_usdt", 0.0)
    monkeypatch.setattr(settings, "risk_per_trade_usdt", 10.0)

    broker = FakeBroker(balance=1000.0)
    assert compute_risk_usdt(broker) == pytest.approx(20.0)  # 1000 * 2%


def test_percent_balance_mode_applies_cap(monkeypatch):
    monkeypatch.setattr(settings, "risk_mode", "percent_balance")
    monkeypatch.setattr(settings, "risk_percent_of_balance", 5.0)
    monkeypatch.setattr(settings, "risk_percent_max_usdt", 30.0)
    monkeypatch.setattr(settings, "risk_per_trade_usdt", 10.0)

    broker = FakeBroker(balance=1000.0)  # 5% = 50, 상한 30에 잘려야 함
    assert compute_risk_usdt(broker) == pytest.approx(30.0)


def test_percent_balance_mode_falls_back_on_broker_exception(monkeypatch):
    monkeypatch.setattr(settings, "risk_mode", "percent_balance")
    monkeypatch.setattr(settings, "risk_percent_of_balance", 2.0)
    monkeypatch.setattr(settings, "risk_percent_max_usdt", 0.0)
    monkeypatch.setattr(settings, "risk_per_trade_usdt", 10.0)

    broker = FakeBroker(raises=True)
    assert compute_risk_usdt(broker) == pytest.approx(10.0)  # 고정값 폴백


def test_percent_balance_mode_falls_back_when_balance_zero_or_none(monkeypatch):
    monkeypatch.setattr(settings, "risk_mode", "percent_balance")
    monkeypatch.setattr(settings, "risk_percent_of_balance", 2.0)
    monkeypatch.setattr(settings, "risk_percent_max_usdt", 0.0)
    monkeypatch.setattr(settings, "risk_per_trade_usdt", 10.0)

    assert compute_risk_usdt(FakeBroker(balance=0.0)) == pytest.approx(10.0)
    assert compute_risk_usdt(FakeBroker(balance=None)) == pytest.approx(10.0)
    assert compute_risk_usdt(FakeBroker(balance=-5.0)) == pytest.approx(10.0)
