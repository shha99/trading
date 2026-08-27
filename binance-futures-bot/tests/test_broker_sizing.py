"""리스크 기반 수량 계산 + stepSize 반올림 (순수 함수, 네트워크 없음)."""
from __future__ import annotations

import pytest

from app.broker import BrokerError, compute_quantity, round_step_size


def test_round_step_size_rounds_down_to_step():
    assert round_step_size(0.12345, 0.001) == 0.123
    assert round_step_size(1.0, 0.01) == 1.0
    assert round_step_size(0.0009, 0.001) == 0.0


def test_round_step_size_zero_step_is_noop():
    assert round_step_size(1.23456, 0) == 1.23456


def test_compute_quantity_basic():
    # risk 10 USDT, entry-stop 폭 100 -> raw qty 0.1, step 0.001 -> 0.1
    qty = compute_quantity(entry_price=30000, stop_price=29900, risk_usdt=10, step_size=0.001)
    assert qty == pytest.approx(0.1, rel=1e-6)


def test_compute_quantity_rounds_to_step():
    qty = compute_quantity(entry_price=30000, stop_price=29950, risk_usdt=10, step_size=0.01)
    # raw = 10/50 = 0.2 -> 이미 step에 맞음
    assert qty == pytest.approx(0.2, rel=1e-6)


def test_compute_quantity_zero_width_raises():
    with pytest.raises(BrokerError):
        compute_quantity(entry_price=100, stop_price=100, risk_usdt=10, step_size=0.001)


def test_compute_quantity_too_small_raises():
    # raw qty가 stepSize 미만이면 반올림 후 0이 되어 주문 불가 -> 에러
    # risk 1e-6 USDT / 폭 100 = raw qty 1e-8 -> step 0.001 미만이라 0으로 반올림됨
    with pytest.raises(BrokerError):
        compute_quantity(entry_price=30000, stop_price=29900, risk_usdt=1e-6, step_size=0.001)
