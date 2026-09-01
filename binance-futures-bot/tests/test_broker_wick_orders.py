"""BinanceFuturesBroker.enter_position()/replace_stop_order() 검증 (wick 엔진용 -
롱/숏 양방향 + 익절 주문 없음). 실제 바이낸스 호출 없음 - 가짜 client 주입."""
from __future__ import annotations

import pytest

from app.broker import BinanceFuturesBroker, BrokerError


class FakeBinanceClient:
    def __init__(self, step_size: float = 0.001):
        self.step_size = step_size
        self.created_orders: list[dict] = []
        self.leverage_calls: list[tuple] = []
        self.cancelled: list[tuple] = []
        self._next_order_id = 1

    def futures_exchange_info(self):
        return {"symbols": [{"symbol": "BTCUSDT", "filters": [{"filterType": "LOT_SIZE", "stepSize": str(self.step_size)}]}]}

    def futures_change_leverage(self, symbol, leverage):
        self.leverage_calls.append((symbol, leverage))

    def futures_create_order(self, **kwargs):
        order_id = self._next_order_id
        self._next_order_id += 1
        self.created_orders.append(kwargs)
        avg_price = kwargs.get("stopPrice") or 30000.0
        return {"orderId": order_id, "avgPrice": str(avg_price)}

    def futures_cancel_order(self, symbol, orderId):
        self.cancelled.append((symbol, orderId))


def test_enter_position_long_places_market_entry_and_stop_only():
    client = FakeBinanceClient()
    broker = BinanceFuturesBroker(client=client)

    result = broker.enter_position(
        direction="LONG", symbol="BTCUSDT", entry_price_hint=30000, stop_price=29900,
        risk_usdt=10, leverage=1,
    )

    assert len(client.created_orders) == 2  # 진입 + 손절, 익절 없음
    entry_order, sl_order = client.created_orders
    assert entry_order["side"] == "BUY" and entry_order["type"] == "MARKET"
    assert sl_order["side"] == "SELL" and sl_order["type"] == "STOP_MARKET"
    assert sl_order["stopPrice"] == 29900
    assert sl_order["reduceOnly"] is True
    assert result.tp_order_id is None
    assert result.quantity == pytest.approx(10 / 100, rel=1e-6)  # risk/폭


def test_enter_position_short_reverses_sides():
    client = FakeBinanceClient()
    broker = BinanceFuturesBroker(client=client)

    broker.enter_position(
        direction="SHORT", symbol="BTCUSDT", entry_price_hint=30000, stop_price=30100,
        risk_usdt=10, leverage=1,
    )

    entry_order, sl_order = client.created_orders
    assert entry_order["side"] == "SELL" and entry_order["type"] == "MARKET"
    assert sl_order["side"] == "BUY" and sl_order["type"] == "STOP_MARKET"
    assert sl_order["stopPrice"] == 30100


def test_enter_position_sl_attach_failure_does_not_raise():
    class FlakyClient(FakeBinanceClient):
        def futures_create_order(self, **kwargs):
            if kwargs.get("type") == "STOP_MARKET":
                raise RuntimeError("network blip")
            return super().futures_create_order(**kwargs)

    broker = BinanceFuturesBroker(client=FlakyClient())
    result = broker.enter_position(
        direction="LONG", symbol="BTCUSDT", entry_price_hint=30000, stop_price=29900, risk_usdt=10,
    )
    assert result.sl_order_id is None  # 손절 부착 실패해도 진입 결과 자체는 반환됨(로그만 남김)


def test_replace_stop_order_cancels_old_and_places_new():
    client = FakeBinanceClient()
    broker = BinanceFuturesBroker(client=client)

    new_id = broker.replace_stop_order("BTCUSDT", "LONG", quantity=0.1, new_stop_price=30050, old_order_id="old-1")

    assert client.cancelled == [("BTCUSDT", "old-1")]
    assert len(client.created_orders) == 1
    order = client.created_orders[0]
    assert order["side"] == "SELL" and order["type"] == "STOP_MARKET" and order["stopPrice"] == 30050
    assert new_id == "1"


def test_replace_stop_order_short_uses_buy_side():
    client = FakeBinanceClient()
    broker = BinanceFuturesBroker(client=client)

    broker.replace_stop_order("BTCUSDT", "SHORT", quantity=0.1, new_stop_price=29950, old_order_id=None)

    assert client.cancelled == []  # old_order_id가 없으면 취소할 것도 없음
    assert client.created_orders[0]["side"] == "BUY"


def test_replace_stop_order_raises_broker_error_on_failure():
    class FailingClient(FakeBinanceClient):
        def futures_create_order(self, **kwargs):
            raise RuntimeError("rejected")

    broker = BinanceFuturesBroker(client=FailingClient())
    with pytest.raises(BrokerError):
        broker.replace_stop_order("BTCUSDT", "LONG", quantity=0.1, new_stop_price=30000, old_order_id=None)


# ---------------------------------------------------------------------------
# get_available_balance_usdt - RISK_MODE=percent_balance의 기준값 조회
# ---------------------------------------------------------------------------

class FakeBinanceClientWithBalance(FakeBinanceClient):
    def __init__(self, balances):
        super().__init__()
        self._balances = balances

    def futures_account_balance(self):
        return self._balances


def test_get_available_balance_usdt_finds_usdt_entry():
    client = FakeBinanceClientWithBalance([
        {"asset": "BNB", "availableBalance": "1.5"},
        {"asset": "USDT", "availableBalance": "1234.56"},
    ])
    broker = BinanceFuturesBroker(client=client)
    assert broker.get_available_balance_usdt() == pytest.approx(1234.56)


def test_get_available_balance_usdt_raises_when_usdt_missing():
    client = FakeBinanceClientWithBalance([{"asset": "BNB", "availableBalance": "1.5"}])
    broker = BinanceFuturesBroker(client=client)
    with pytest.raises(BrokerError):
        broker.get_available_balance_usdt()
