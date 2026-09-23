"""실계좌(또는 테스트넷) 바이낸스 선물 계좌의 실시간 상태를 거래소에 직접
물어본다 - 로컬 DB(TradeRecord)와 완전히 무관하다.

**왜 필요한가**: 실제 매매(`run_trading_bot.py`)는 사용자의 PC에서, 조회용
대시보드는 Render 같은 별도 서버에서 돌아갈 수 있는데, 이 둘은 서로 다른
로컬 DB 파일을 쓰기 때문에 조회 서버가 실제 매매 기록을 알 방법이 없다.
하지만 **어느 프로세스가 주문을 냈든, 그 결과는 결국 같은 바이낸스 계좌에
남는다** - 그러니 로컬 DB 대신 바이낸스 API에 직접 물어보면, 어디서
매매하고 있는지와 무관하게 항상 "지금 이 계좌의 실제 상태"를 정확히 볼 수
있다.

전부 **읽기 전용** API만 호출한다 - 주문 실행/취소 관련 함수는 이 모듈에
전혀 없다. `BINANCE_API_KEY`/`SECRET`이 비어있으면 호출부(server.py)가
그 사실을 먼저 확인해서 이 모듈 자체를 호출하지 않는다.
"""
from __future__ import annotations

import logging

from .broker import BinanceFuturesBroker, BrokerError
from .config import settings

logger = logging.getLogger(__name__)


def get_account_snapshot(broker: BinanceFuturesBroker | None = None) -> dict:
    """지갑 잔고/미실현손익 스냅샷 (futures_account() 중 필요한 필드만)."""
    broker = broker or BinanceFuturesBroker()
    try:
        account = broker.client.futures_account()
    except Exception as exc:  # noqa: BLE001
        raise BrokerError(f"계좌 조회 실패: {exc}") from exc
    return {
        "total_wallet_balance": float(account.get("totalWalletBalance", 0.0)),
        "total_unrealized_profit": float(account.get("totalUnrealizedProfit", 0.0)),
        "total_margin_balance": float(account.get("totalMarginBalance", 0.0)),
        "available_balance": float(account.get("availableBalance", 0.0)),
    }


def get_open_positions(broker: BinanceFuturesBroker | None = None) -> list[dict]:
    """포지션 수량이 0이 아닌 것만 추린 실제 열린 포지션 목록.

    누가(어느 프로세스가) 이 포지션을 열었는지는 이 API 응답만으로는 알 수
    없다 - 그게 이 함수의 목적과 정확히 일치한다(실제 계좌 상태 그 자체)."""
    broker = broker or BinanceFuturesBroker()
    try:
        positions = broker.client.futures_position_information()
    except Exception as exc:  # noqa: BLE001
        raise BrokerError(f"포지션 조회 실패: {exc}") from exc

    open_positions = []
    for p in positions:
        amt = float(p.get("positionAmt", 0.0))
        if amt == 0:
            continue
        open_positions.append({
            "symbol": p.get("symbol"),
            "side": "LONG" if amt > 0 else "SHORT",
            "quantity": abs(amt),
            "entry_price": float(p.get("entryPrice", 0.0)),
            "mark_price": float(p.get("markPrice", 0.0)),
            "unrealized_pnl": float(p.get("unRealizedProfit", 0.0)),
            "leverage": int(p.get("leverage", 1)),
        })
    return open_positions


def get_recent_trades(
    symbols: list[str] | None = None, limit_per_symbol: int = 20,
    broker: BinanceFuturesBroker | None = None,
) -> list[dict]:
    """설정된 심볼들의 최근 체결 내역을 합쳐서 최신순으로 반환한다.

    바이낸스 선물 체결 내역 조회 API는 심볼별로만 호출 가능해서(한 번에
    "전체 계좌"로는 못 물어봄), `settings.symbols`(기본 BTCUSDT/ETHUSDT) 각각
    조회 후 합친다. 한 심볼 조회가 실패해도(네트워크 등) 나머지는 계속
    반환한다 - 부분적으로라도 보여주는 게 전체를 못 보여주는 것보다 낫다."""
    broker = broker or BinanceFuturesBroker()
    symbols = symbols or settings.symbols
    trades: list[dict] = []
    for symbol in symbols:
        try:
            raw = broker.client.futures_account_trades(symbol=symbol, limit=limit_per_symbol)
        except Exception:
            logger.exception("%s 체결 내역 조회 실패 - 건너뜀", symbol)
            continue
        for t in raw:
            trades.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "price": float(t.get("price", 0.0)),
                "quantity": float(t.get("qty", 0.0)),
                "realized_pnl": float(t.get("realizedPnl", 0.0)),
                "commission": float(t.get("commission", 0.0)),
                "commission_asset": t.get("commissionAsset"),
                "time": int(t.get("time", 0)),
            })
    trades.sort(key=lambda t: t["time"], reverse=True)
    return trades


def get_live_status(broker: BinanceFuturesBroker | None = None) -> dict:
    """`/api/binance/status`가 그대로 반환하는 통합 스냅샷.

    `BINANCE_API_KEY`/`SECRET`이 비어있으면 API를 아예 호출하지 않고
    `ready=False`로 즉시 반환한다 - 설정 안 된 상태에서 굳이 에러를 내지
    않기 위함(Render에 이 기능을 아직 안 켠 배포와 호환)."""
    if not settings.binance_api_key or not settings.binance_api_secret:
        return {"ready": False, "reason": "BINANCE_API_KEY/SECRET이 설정되지 않음"}

    broker = broker or BinanceFuturesBroker()
    try:
        snapshot = get_account_snapshot(broker)
        positions = get_open_positions(broker)
        trades = get_recent_trades(broker=broker)[:30]
    except BrokerError as exc:
        return {"ready": False, "reason": str(exc)}

    return {
        "ready": True,
        "testnet": settings.binance_testnet,
        "account": snapshot,
        "open_positions": positions,
        "recent_trades": trades,
    }
