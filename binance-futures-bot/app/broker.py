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

    def get_available_balance_usdt(self) -> float:
        """선물 지갑의 가용 USDT 잔고(availableBalance - 이미 열린 포지션의
        증거금은 제외된, 신규 진입에 실제로 쓸 수 있는 금액)를 조회한다.

        RISK_MODE=percent_balance(잔고 비례 리스크 사이징)의 기준값으로 쓴다
        (app/risk.py:compute_risk_usdt). 실패 시 예외를 그대로 던진다 -
        호출부가 고정 리스크로 폴백할지 판단한다.
        """
        balances = self.client.futures_account_balance()
        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0.0))
        raise BrokerError("선물 계좌에서 USDT 잔고를 찾을 수 없습니다")

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

    def enter_position(
        self, direction: str, symbol: str, entry_price_hint: float, stop_price: float,
        risk_usdt: float, leverage: int = 1,
    ) -> EntryResult:
        """롱/숏 포지션 진입 + 손절만 부착 (익절 주문 없음).

        `enter_long()`과 달리 고정 익절이 없는 "본전 이동 트레일링" 전략용이다
        (예: 볼린저 꼬리터치+RSI) - 청산은 손절 주문 하나를 계속 취소·재등록
        하며 따라가는 방식으로 처리한다(app/wick_position_manager.py 참고).
        """
        step_size = self.get_step_size(symbol)
        quantity = compute_quantity(entry_price_hint, stop_price, risk_usdt, step_size)
        self.set_leverage(symbol, leverage)

        entry_side = "BUY" if direction == "LONG" else "SELL"
        exit_side = "SELL" if direction == "LONG" else "BUY"

        try:
            entry_order = self.client.futures_create_order(
                symbol=symbol, side=entry_side, type="MARKET", quantity=quantity,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"{symbol} 진입 주문 실패: {exc}") from exc

        filled_price = float(entry_order.get("avgPrice") or entry_price_hint) or entry_price_hint

        sl_order_id = None
        try:
            sl_order = self.client.futures_create_order(
                symbol=symbol, side=exit_side, type="STOP_MARKET",
                stopPrice=round(stop_price, 4), quantity=quantity, reduceOnly=True,
            )
            sl_order_id = str(sl_order.get("orderId"))
        except Exception:
            logger.exception("%s 손절 주문 부착 실패 (진입은 이미 체결됨 — 수동 확인 필요)", symbol)

        return EntryResult(
            quantity=quantity,
            entry_order_id=str(entry_order.get("orderId")),
            entry_price=filled_price,
            sl_order_id=sl_order_id,
            tp_order_id=None,
        )

    def replace_stop_order(
        self, symbol: str, direction: str, quantity: float, new_stop_price: float, old_order_id: str | None,
    ) -> str:
        """트레일링 스탑 갱신 - 기존 손절 주문을 취소하고 새 가격으로 다시 건다.

        먼저 취소 후 등록하는 순서라, 그 찰나에 손절 보호가 잠깐 비는 구간이
        생긴다(거래소 특성상 "취소+새 주문"을 원자적으로 묶을 수 없음) - 대신
        새 가격은 항상 기존보다 유리한 방향으로만 이동하므로(app/lab_backtest.py
        의 본전 이동 트레일링 로직과 동일), 그 짧은 순간의 리스크는 감내할
        만한 수준으로 설계돼 있다.
        """
        if old_order_id:
            self.cancel_order(symbol, old_order_id)
        exit_side = "SELL" if direction == "LONG" else "BUY"
        try:
            order = self.client.futures_create_order(
                symbol=symbol, side=exit_side, type="STOP_MARKET",
                stopPrice=round(new_stop_price, 4), quantity=quantity, reduceOnly=True,
            )
            return str(order.get("orderId"))
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"{symbol} 트레일링 스탑 갱신 실패: {exc}") from exc

    def close_position_market(self, symbol: str, quantity: float, side: str = "SELL") -> dict:
        """잔여 SL/TP를 취소하고 시장가로 포지션을 정리한다 (시간손절용).

        {"order_id":..., "price":...} 를 반환 - price는 실현손익 계산에 쓴다.
        """
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception:
            logger.exception("%s 잔여 주문 취소 실패 (계속 진행)", symbol)

        try:
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=quantity, reduceOnly=True,
            )
            return {"order_id": str(order.get("orderId")), "price": float(order.get("avgPrice") or 0.0) or None}
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"{symbol} 포지션 정리 실패: {exc}") from exc

    def get_order_status(self, symbol: str, order_id: str) -> dict | None:
        """SL/TP 주문이 체결됐는지 확인할 때 쓴다 (position_manager.reconcile_open_positions).

        조회 실패(네트워크 등)는 None을 반환 - 호출부가 "다음 주기에 다시
        확인"하도록 한다.
        """
        try:
            return self.client.futures_get_order(symbol=symbol, orderId=order_id)
        except Exception:
            logger.exception("%s 주문(%s) 상태 조회 실패", symbol, order_id)
            return None

    def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
        except Exception:
            logger.exception("%s 주문(%s) 취소 실패 (이미 체결/취소됐을 수 있음)", symbol, order_id)
