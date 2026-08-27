"""실시간 모의투자(paper trading) 검증 — 합성 데이터, 바이낸스 실호출 없음.

`app.paper_trading.run_once()`가 청산된 거래만 잔고에 반영하고, 진행 중인
포지션은 기록하지 않으며, 같은 데이터로 재실행해도 중복 기록되지 않는지를
확인한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import app.paper_trading as pt
from app.db import PaperAccount, PaperTrade, SessionLocal


def random_walk_df(n=3000, seed=9, freq="15min"):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(1, 0.5, n))
    low = close - np.abs(rng.normal(1, 0.5, n))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    idx = pd.date_range("2023-01-01", periods=n, freq=freq)
    volume = np.abs(rng.normal(1000, 200, n))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def _seed_account(started_at: datetime) -> PaperAccount:
    """이미 예전부터 굴러온 계좌를 흉내낸다 - 합성 데이터 타임스탬프(2023년)가
    `started_at` 이후로 인식되게 하려는 용도(실제로는 `started_at`이 계좌 생성
    당시 "지금" - 즉 훨씬 나중 시각이라 합성 과거 데이터와는 안 맞기 때문)."""
    session = SessionLocal()
    try:
        account = PaperAccount(
            strategy_key=pt.PAPER_STRATEGY_KEY,
            symbol=pt.PAPER_SYMBOL,
            timeframe=pt.PAPER_TIMEFRAME,
            starting_balance=pt.PAPER_STARTING_BALANCE,
            balance=pt.PAPER_STARTING_BALANCE,
            started_at=started_at,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        return account
    finally:
        session.close()


@pytest.fixture
def patch_closed_candles(monkeypatch):
    def _apply(df):
        monkeypatch.setattr(pt, "fetch_klines", lambda *a, **k: df)
        monkeypatch.setattr(pt, "is_candle_closed", lambda *a, **k: True)
    return _apply


def test_insufficient_bars_does_not_create_account_or_crash(patch_closed_candles):
    tiny_df = random_walk_df(n=5)
    patch_closed_candles(tiny_df)
    pt.run_once()

    session = SessionLocal()
    try:
        assert session.query(PaperAccount).count() == 0
    finally:
        session.close()


def test_run_once_records_closed_trades_and_updates_balance(patch_closed_candles):
    _seed_account(started_at=datetime(2000, 1, 1))
    df = random_walk_df(n=3000, seed=9)
    patch_closed_candles(df)

    pt.run_once()

    session = SessionLocal()
    try:
        account = session.query(PaperAccount).filter_by(strategy_key=pt.PAPER_STRATEGY_KEY).first()
        assert account is not None
        recorded = (
            session.query(PaperTrade)
            .filter_by(account_id=account.id)
            .order_by(PaperTrade.entry_time.asc())
            .all()
        )
        assert len(recorded) > 0

        expected_balance = pt.PAPER_STARTING_BALANCE
        for t in recorded:
            expected_balance *= 1 + t.pct_return / 100.0
        assert account.balance == pytest.approx(expected_balance)
        assert account.balance != pt.PAPER_STARTING_BALANCE
    finally:
        session.close()


def test_run_once_is_idempotent_on_rerun_with_same_data(patch_closed_candles):
    _seed_account(started_at=datetime(2000, 1, 1))
    df = random_walk_df(n=3000, seed=9)
    patch_closed_candles(df)

    pt.run_once()
    session = SessionLocal()
    try:
        account = session.query(PaperAccount).filter_by(strategy_key=pt.PAPER_STRATEGY_KEY).first()
        balance_after_first = account.balance
        count_after_first = session.query(PaperTrade).filter_by(account_id=account.id).count()
    finally:
        session.close()

    pt.run_once()  # 같은 데이터로 재실행 - 새로 청산된 거래가 없어야 함
    session = SessionLocal()
    try:
        account = session.query(PaperAccount).filter_by(strategy_key=pt.PAPER_STRATEGY_KEY).first()
        assert account.balance == pytest.approx(balance_after_first)
        assert session.query(PaperTrade).filter_by(account_id=account.id).count() == count_after_first
    finally:
        session.close()


def test_open_position_not_recorded_as_trade_until_closed(patch_closed_candles):
    """마지막 거래가 "TIME"(데이터가 거기서 끝나 강제 종료)이면 아직 진행
    중인 포지션 - 거래로 기록하지 않고 `_last_open_position`에만 스냅샷을 남긴다."""
    _seed_account(started_at=datetime(2000, 1, 1))
    full_df = random_walk_df(n=3000, seed=9)

    from app.lab_backtest import simulate_lab
    from app.lab_strategies import BollingerWickBreakevenTrailStrategy

    all_trades = simulate_lab(full_df, BollingerWickBreakevenTrailStrategy())
    assert len(all_trades) > 1
    # 마지막 거래의 진입 시점보다 살짝 뒤에서 데이터를 끊어, 그 마지막 거래가
    # 아직 청산되지 않은 채로 데이터가 끝나도록 만든다.
    last_entry_time = pd.Timestamp(all_trades[-1]["entry_time"])
    entry_idx = full_df.index.get_indexer([last_entry_time])[0]
    # 진입 봉에서 데이터를 끊는다 - 그 뒤로 평가할 봉이 하나도 없으니 청산
    # 조건(SL/TRAIL)을 만날 기회 자체가 없어 반드시 "아직 진행 중"이 된다.
    truncated_df = full_df.iloc[: entry_idx + 1]
    patch_closed_candles(truncated_df)

    pt.run_once()

    session = SessionLocal()
    try:
        account = session.query(PaperAccount).filter_by(strategy_key=pt.PAPER_STRATEGY_KEY).first()
        recorded_entry_times = {
            pd.Timestamp(t.entry_time) for t in session.query(PaperTrade).filter_by(account_id=account.id).all()
        }
    finally:
        session.close()

    assert last_entry_time not in recorded_entry_times
    assert pt._last_open_position is not None
    assert pt._last_open_position["direction"] in ("LONG", "SHORT")


def test_get_status_reports_ready_false_before_first_run():
    status = pt.get_status()
    assert status == {"ready": False}


def test_get_status_reports_account_summary_after_run(patch_closed_candles):
    _seed_account(started_at=datetime(2000, 1, 1))
    df = random_walk_df(n=3000, seed=9)
    patch_closed_candles(df)
    pt.run_once()

    status = pt.get_status()
    assert status["ready"] is True
    assert status["symbol"] == pt.PAPER_SYMBOL
    assert status["timeframe"] == pt.PAPER_TIMEFRAME
    assert status["starting_balance"] == pt.PAPER_STARTING_BALANCE
    assert status["trade_count"] > 0
    assert 0 <= status["win_rate"] <= 100
    assert isinstance(status["recent_trades"], list)
    assert len(status["recent_trades"]) > 0
