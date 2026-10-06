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
    """지갑 잔고/미실현손익 스냅샷 (futures_account() 중 필요한 필드만) +
    (설정했다면) 시작 잔고 대비 누적 수익률."""
    broker = broker or BinanceFuturesBroker()
    try:
        account = broker.client.futures_account()
    except Exception as exc:  # noqa: BLE001
        raise BrokerError(f"계좌 조회 실패: {exc}") from exc

    total_wallet_balance = float(account.get("totalWalletBalance", 0.0))
    starting = settings.real_account_starting_balance_usdt
    cumulative_return_pct = (
        round((total_wallet_balance - starting) / starting * 100, 4) if starting > 0 else None
    )
    return {
        "total_wallet_balance": total_wallet_balance,
        "total_unrealized_profit": float(account.get("totalUnrealizedProfit", 0.0)),
        "total_margin_balance": float(account.get("totalMarginBalance", 0.0)),
        "available_balance": float(account.get("availableBalance", 0.0)),
        "starting_balance_usdt": starting if starting > 0 else None,
        "cumulative_return_pct": cumulative_return_pct,
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


def get_recent_round_trips(
    symbols: list[str] | None = None, limit_per_symbol: int = 50,
    broker: BinanceFuturesBroker | None = None,
) -> list[dict]:
    """최근 체결 내역을 "진입→청산" 한 쌍짜리 거래 기록으로 묶어서 반환한다
    (로컬 DB의 TradeRecord와 같은 모양 - 심볼/방향/진입가/청산가/수량/
    실현손익/진입·청산시각). `get_recent_trades()`가 주는 낱개 체결 목록은
    사람이 읽기엔 불편해서("어느 체결이 어느 체결의 청산인지" 알 수 없음)
    이 함수가 그걸 묶어준다.

    묶는 규칙: 이 봇의 주문 생명주기 특성상(한 번에 전량 진입, 트레일링
    스탑이 전량 청산) `realizedPnl == 0`인 체결은 "진입"(또는 같은 방향
    추가 진입), `!= 0`인 체결은 "청산"으로 본다. 진입 수량만큼 청산
    수량이 채워지면 그 묶음을 하나의 완결된 거래로 확정한다. 조회
    구간 시작 전에 이미 열려 있던 포지션의 청산(짝 없는 청산 체결)은
    버린다 - 진입가를 알 수 없어 잘못된 숫자를 보여주는 것보다 안전함.
    조회 구간 끝에 아직 안 닫힌 진입은(미확정 상태) 결과에 포함하지 않는다
    - 그건 `get_open_positions()`가 이미 보여준다."""
    broker = broker or BinanceFuturesBroker()
    symbols = symbols or settings.symbols
    round_trips: list[dict] = []

    for symbol in symbols:
        try:
            raw = broker.client.futures_account_trades(symbol=symbol, limit=limit_per_symbol)
        except Exception:
            logger.exception("%s 체결 내역 조회 실패 - 건너뜀", symbol)
            continue

        raw = sorted(raw, key=lambda t: int(t.get("time", 0)))
        open_group: dict | None = None
        for t in raw:
            realized = float(t.get("realizedPnl", 0.0))
            qty = float(t.get("qty", 0.0))
            price = float(t.get("price", 0.0))
            time_ms = int(t.get("time", 0))

            if realized == 0.0:
                if open_group is None:
                    open_group = {
                        "symbol": symbol,
                        "side": "LONG" if t.get("side") == "BUY" else "SHORT",
                        "entry_notional": 0.0, "entry_qty": 0.0,
                        "exit_notional": 0.0, "exit_qty": 0.0,
                        "realized_pnl": 0.0, "opened_at": time_ms, "closed_at": None,
                    }
                open_group["entry_notional"] += price * qty
                open_group["entry_qty"] += qty
            else:
                if open_group is None:
                    continue  # 조회 구간 밖에서 열린 포지션의 청산 - 짝이 없어 버림
                open_group["exit_notional"] += price * qty
                open_group["exit_qty"] += qty
                open_group["realized_pnl"] += realized
                open_group["closed_at"] = time_ms
                if open_group["exit_qty"] >= open_group["entry_qty"] - 1e-9:
                    round_trips.append({
                        "symbol": open_group["symbol"],
                        "side": open_group["side"],
                        "quantity": open_group["entry_qty"],
                        "entry_price": open_group["entry_notional"] / open_group["entry_qty"],
                        "exit_price": open_group["exit_notional"] / open_group["exit_qty"],
                        "realized_pnl": open_group["realized_pnl"],
                        "opened_at": open_group["opened_at"],
                        "closed_at": open_group["closed_at"],
                    })
                    open_group = None

    round_trips.sort(key=lambda r: r["closed_at"], reverse=True)
    return round_trips


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
        round_trips = get_recent_round_trips(broker=broker)[:30]
    except BrokerError as exc:
        return {"ready": False, "reason": str(exc)}

    return {
        "ready": True,
        "testnet": settings.binance_testnet,
        "account": snapshot,
        "open_positions": positions,
        "recent_trades": round_trips,
    }
