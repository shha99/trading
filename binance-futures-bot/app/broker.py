"""바이낸스 USDT-M 선물 주문 실행.

진입은 리스크 기반으로 수량을 계산한 시장가 주문, 진입 직후 반대방향
STOP_MARKET/TAKE_PROFIT_MARKET(reduceOnly)을 걸어 손절/익절을 거래소가
직접 체결하도록 한다 (봇이 계속 틱을 감시할 필요가 없어 더 견고함).

클라이언트는 생성자로 주입 가능 — 테스트에서 실제 바이낸스 호출 없이
가짜(client) 객체로 전부 검증할 수 있게 하기 위함.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .binance_client import get_binance_client

logger = logging.getLogger(__name__)


class BrokerError(Exception):
    """주문 실행 중 발생한 오류. 호출부(order_manager)가 잡아서 FAILED로 기록한다."""


@dataclass
class EntryResult:
    quantity: float
    entry_order_id: str
    entry_price: float
    sl_order_id: str | None
    tp_order_id: str | None


def round_step_size(quantity: float, step_size: float) -> float:
    """LOT_SIZE stepSize에 맞춰 내림 반올림한다 (주문 거부 방지)."""
    if step_size <= 0:
        return quantity
    precision = max(0, round(-math.log10(step_size)))
    steps = math.floor(quantity / step_size)
    return round(steps * step_size, precision)


def compute_quantity(entry_price: float, stop_price: float, risk_usdt: float, step_size: float) -> float:
    """리스크 금액(risk_usdt)을 (entry-stop) 폭으로 나눠 수량을 정한다."""
    per_unit_risk = abs(entry_price - stop_price)
    if per_unit_risk <= 0:
        raise BrokerError("entry_price와 stop_price 폭이 0 이하입니다 (ATR 계산 확인 필요)")
    raw_qty = risk_usdt / per_unit_risk
    qty = round_step_size(raw_qty, step_size)
    if qty <= 0:
        raise BrokerError(f"계산된 수량이 0입니다 (risk_usdt={risk_usdt}, 폭={per_unit_risk}, step={step_size})")
    return qty


class BinanceFuturesBroker:
    def __init__(self, client=None):
        self._client = client
        self._step_size_cache: dict[str, float] = {}

    @property
    def client(self):
        if self._client is None:
            self._client = get_binance_client()
        return self._client

    def get_step_size(self, symbol: str) -> float:
        if symbol in self._step_size_cache:
            return self._step_size_cache[symbol]
        info = self.client.futures_exchange_info()
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        step = float(f["stepSize"])
                        self._step_size_cache[symbol] = step
                        return step
        raise BrokerError(f"{symbol}의 LOT_SIZE stepSize를 찾을 수 없습니다")

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"{symbol} 레버리지 설정 실패: {exc}") from exc

    def enter_long(
        self, symbol: str, entry_price_hint: float, stop_price: float, target_price: float,
        risk_usdt: float, leverage: int = 1,
    ) -> EntryResult:
        """롱 포지션 진입 + SL/TP 부착. 실패 시 BrokerError를 던진다."""
        step_size = self.get_step_size(symbol)
        quantity = compute_quantity(entry_price_hint, stop_price, risk_usdt, step_size)
        self.set_leverage(symbol, leverage)

        try:
            entry_order = self.client.futures_create_order(
                symbol=symbol, side="BUY", type="MARKET", quantity=quantity,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"{symbol} 진입 주문 실패: {exc}") from exc

        filled_price = float(entry_order.get("avgPrice") or entry_price_hint) or entry_price_hint

        sl_order_id = tp_order_id = None
        try:
            sl_order = self.client.futures_create_order(
                symbol=symbol, side="SELL", type="STOP_MARKET",
                stopPrice=round(stop_price, 4), quantity=quantity, reduceOnly=True,
            )
            sl_order_id = str(sl_order.get("orderId"))
        except Exception:
            logger.exception("%s 손절 주문 부착 실패 (진입은 이미 체결됨 — 수동 확인 필요)", symbol)

        try:
            tp_order = self.client.futures_create_order(
                symbol=symbol, side="SELL", type="TAKE_PROFIT_MARKET",
                stopPrice=round(target_price, 4), quantity=quantity, reduceOnly=True,
            )
            tp_order_id = str(tp_order.get("orderId"))
        except Exception:
            logger.exception("%s 익절 주문 부착 실패 (진입은 이미 체결됨 — 수동 확인 필요)", symbol)

        return EntryResult(
            quantity=quantity,
            entry_order_id=str(entry_order.get("orderId")),
            entry_price=filled_price,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

    def close_position_market(self, symbol: str, quantity: float, side: str = "SELL") -> str:
        """잔여 SL/TP를 취소하고 시장가로 포지션을 정리한다 (시간손절용)."""
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception:
            logger.exception("%s 잔여 주문 취소 실패 (계속 진행)", symbol)

        try:
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=quantity, reduceOnly=True,
            )
            return str(order.get("orderId"))
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"{symbol} 포지션 정리 실패: {exc}") from exc
