"""실시간 모의투자(paper trading) — 검증된 "볼린저 꼬리터치 되돌림+RSI" 전략을
BTCUSDT 15분봉에서 100만원 가상 잔고로 지금 이 순간부터 실제로 계속 굴려본다.

⚠️ 100% 모의투자다 — 실제 주문은 절대 나가지 않는다. 실제 자동매매 엔진
(`signal_engine.py`/`TradeRecord`/`ScanState`/화이트리스트)과는 테이블도
로직도 완전히 분리돼 있어서, 이 모듈에 버그가 있어도 실거래에 영향을 줄 수 없다.

**동작 방식**: 매 스캔마다 최근 캔들(최근 약 10일치)을 다시 통째로
`app/lab_backtest.simulate_lab()`로 백테스트한다(전략 실험실/검증된 전략
페이지와 완전히 같은 엔진 — 청산 로직을 별도로 다시 구현하지 않아 로직이
둘로 갈라질 위험이 없다). 계좌 시작 시각(`started_at`) 이후에 진입한 거래
중 "이미 DB에 기록된 것"은 건너뛰고, 새로 청산된 거래만 잔고에 반영한다.
아직 청산되지 않고 진행 중인 포지션(마지막 거래의 청산 사유가 "TIME"으로
나오는 경우 — 이 전략은 시간손절이 없어서 데이터가 끝나 어쩔 수 없이
마지막 종가로 닫은 것 = 아직 안 끝났다는 뜻)은 기록하지 않고, 다음 스캔에서
다시 평가한다.

**포지션 크기**: 매 거래 잔고 전액(복리)을 건다. 거래 표본이 수천 건인
장기 백테스트(README/검증된 전략 페이지)에서는 이렇게 계산하면 숫자가
비현실적으로 부풀어 의미가 없다고 명시했지만, 여긴 다르다 — 소액(100만원)
계좌 1개가 항상 포지션을 최대 1개만 들고(다음 진입 전 반드시 청산) 순차적으로
매매하는 실제 운용 방식 그대로이므로 전액 배팅이 비현실적이지 않다.

⚠️ **Render 무료 플랜은 영구 디스크가 없어** 서버가 재시작/재배포될 때마다
이 모의투자 계좌도 DB와 함께 초기화된다(100만원부터 다시 시작) — 완전히
끊김 없는 트랙 레코드를 보장하지는 않는다. 이는 `data/*.json` 백테스트
성적표가 재배포마다 다시 계산되는 것과 같은 한계다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from .config import settings
from .db import PaperAccount, PaperTrade, SessionLocal, init_db
from .history import fetch_klines, is_candle_closed
from .lab_backtest import simulate_lab
from .lab_strategies import BollingerWickBreakevenTrailStrategy

logger = logging.getLogger(__name__)

PAPER_STRATEGY_KEY = BollingerWickBreakevenTrailStrategy.key
PAPER_SYMBOL = "BTCUSDT"
PAPER_TIMEFRAME = "15m"
PAPER_STARTING_BALANCE = 1_000_000.0
_FETCH_LIMIT = 1000  # 15분봉 1000개 ≈ 10.4일 - RSI/ATR 워밍업 + 진행 중 포지션 추적에 충분

# 진행 중(아직 청산 안 된) 포지션의 최신 스냅샷 - DB에 안 남기고(청산 전이라
# 아직 "거래"가 아님) API 상태 조회가 매번 바이낸스를 다시 부르지 않도록
# run_once()가 스캔할 때마다 이 모듈 전역에 캐시해둔다.
_last_open_position: dict | None = None
_last_scan_at: datetime | None = None


def _naive_utc(ts) -> datetime:
    dt = pd.Timestamp(ts).to_pydatetime()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _get_or_create_account(session) -> PaperAccount:
    account = (
        session.query(PaperAccount)
        .filter_by(strategy_key=PAPER_STRATEGY_KEY, symbol=PAPER_SYMBOL, timeframe=PAPER_TIMEFRAME)
        .first()
    )
    if account is None:
        account = PaperAccount(
            strategy_key=PAPER_STRATEGY_KEY,
            symbol=PAPER_SYMBOL,
            timeframe=PAPER_TIMEFRAME,
            starting_balance=PAPER_STARTING_BALANCE,
            balance=PAPER_STARTING_BALANCE,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        logger.info("모의투자 계좌 신규 생성: %s %s %s, 시작 잔고 %.0f원", PAPER_SYMBOL, PAPER_TIMEFRAME, PAPER_STRATEGY_KEY, PAPER_STARTING_BALANCE)
    return account


def _record_trade_if_new(session, account: PaperAccount, trade: dict) -> None:
    entry_time = _naive_utc(trade["entry_time"])
    exists = (
        session.query(PaperTrade)
        .filter_by(account_id=account.id, entry_time=entry_time)
        .first()
    )
    if exists is not None:
        return

    balance_before = account.balance
    balance_after = balance_before * (1 + trade["pct_return"] / 100.0)
    session.add(
        PaperTrade(
            account_id=account.id,
            direction=trade["direction"],
            entry_time=entry_time,
            exit_time=_naive_utc(trade["exit_time"]),
            entry_price=trade["entry_price"],
            exit_price=trade["exit_price"],
            exit_reason=trade["exit_reason"],
            pct_return=trade["pct_return"],
            balance_before=balance_before,
            balance_after=balance_after,
        )
    )
    account.balance = balance_after
    account.updated_at = datetime.now(timezone.utc)
    session.commit()
    logger.info(
        "모의투자 거래 청산 기록: %s %s %.2f%% (사유 %s) 잔고 %.0f -> %.0f원",
        trade["direction"], trade["entry_time"], trade["pct_return"], trade["exit_reason"],
        balance_before, balance_after,
    )


def run_once() -> None:
    """스케줄러가 주기적으로 부른다: 최근 캔들을 다시 백테스트해 새로 청산된
    거래만 계좌에 반영하고, 진행 중인 포지션 스냅샷을 갱신한다."""
    global _last_open_position, _last_scan_at
    init_db()

    df = fetch_klines(PAPER_SYMBOL, PAPER_TIMEFRAME, limit=_FETCH_LIMIT)
    if df is None or df.empty:
        return
    if not is_candle_closed(df, PAPER_TIMEFRAME):
        df = df.iloc[:-1]  # 진행 중인 마지막 봉은 버리고 완결봉만 사용

    strategy = BollingerWickBreakevenTrailStrategy()
    if len(df) < strategy.min_bars:
        return

    session = SessionLocal()
    try:
        account = _get_or_create_account(session)
        started_at = pd.Timestamp(account.started_at)
        trades = simulate_lab(df, strategy, fee_pct=settings.taker_fee_pct_roundtrip)

        open_snapshot = None
        for i, trade in enumerate(trades):
            entry_time = pd.Timestamp(trade["entry_time"])
            if entry_time < started_at:
                continue  # 계좌 시작 이전에 진입한 거래는 무시

            is_last = i == len(trades) - 1
            if is_last and trade["exit_reason"] == "TIME":
                # 이 전략엔 시간손절이 없다 - "TIME"으로 닫혔다는 건 데이터가
                # 거기서 끝나 어쩔 수 없이 마지막 종가로 강제 종료했다는 뜻,
                # 즉 아직 청산 안 되고 진행 중인 포지션이다. 기록하지 않고
                # 다음 스캔에서 다시 평가한다.
                open_snapshot = {
                    "direction": trade["direction"],
                    "entry_time": trade["entry_time"],
                    "entry_price": trade["entry_price"],
                    "last_price": trade["exit_price"],
                    "unrealized_pct_return": trade["pct_return"],
                }
                continue

            _record_trade_if_new(session, account, trade)

        _last_open_position = open_snapshot
        _last_scan_at = datetime.now(timezone.utc)
    finally:
        session.close()


def get_status() -> dict:
    """`/api/paper-trading/status`가 그대로 반환할 상태 딕셔너리."""
    init_db()
    session = SessionLocal()
    try:
        account = (
            session.query(PaperAccount)
            .filter_by(strategy_key=PAPER_STRATEGY_KEY, symbol=PAPER_SYMBOL, timeframe=PAPER_TIMEFRAME)
            .first()
        )
        if account is None:
            return {"ready": False}

        trades_q = session.query(PaperTrade).filter_by(account_id=account.id)
        total = trades_q.count()
        wins = trades_q.filter(PaperTrade.pct_return > 0).count()
        recent = trades_q.order_by(PaperTrade.exit_time.desc()).limit(50).all()

        return {
            "ready": True,
            "strategy_key": account.strategy_key,
            "symbol": account.symbol,
            "timeframe": account.timeframe,
            "starting_balance": account.starting_balance,
            "balance": account.balance,
            "return_pct": round((account.balance / account.starting_balance - 1) * 100, 4),
            "started_at": account.started_at.isoformat() if account.started_at else None,
            "updated_at": account.updated_at.isoformat() if account.updated_at else None,
            "last_scan_at": _last_scan_at.isoformat() if _last_scan_at else None,
            "trade_count": total,
            "win_rate": round(wins / total * 100, 2) if total else None,
            "recent_trades": [t.to_dict() for t in recent],
            "open_position": _last_open_position,
        }
    finally:
        session.close()
