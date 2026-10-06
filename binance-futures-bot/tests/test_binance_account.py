"""app/binance_account.py 검증 - 로컬 DB가 아니라 바이낸스 API를 직접
조회해 실계좌 상태를 보여주는 읽기 전용 모듈. 실제 바이낸스 호출 없음 -
가짜 client 주입."""
from __future__ import annotations

import pytest

from app.binance_account import (
    get_account_snapshot,
    get_live_status,
    get_open_positions,
    get_recent_round_trips,
    get_recent_trades,
)
from app.broker import BinanceFuturesBroker, BrokerError
from app.config import settings


class FakeBinanceClient:
    def __init__(self, account=None, positions=None, trades_by_symbol=None, raise_on=None):
        self._account = account or {}
        self._positions = positions or []
        self._trades_by_symbol = trades_by_symbol or {}
        self._raise_on = raise_on or set()  # {"account", "positions", "trades"}

    def futures_account(self):
        if "account" in self._raise_on:
            raise RuntimeError("네트워크 오류")
        return self._account

    def futures_position_information(self):
        if "positions" in self._raise_on:
            raise RuntimeError("네트워크 오류")
        return self._positions

    def futures_account_trades(self, symbol, limit):
        if "trades" in self._raise_on:
            raise RuntimeError("네트워크 오류")
        return self._trades_by_symbol.get(symbol, [])


def test_get_account_snapshot_extracts_expected_fields(monkeypatch):
    monkeypatch.setattr(settings, "real_account_starting_balance_usdt", 0.0)
    client = FakeBinanceClient(account={
        "totalWalletBalance": "1000.5", "totalUnrealizedProfit": "-12.3",
        "totalMarginBalance": "988.2", "availableBalance": "900.0",
    })
    broker = BinanceFuturesBroker(client=client)

    snapshot = get_account_snapshot(broker)

    assert snapshot == {
        "total_wallet_balance": pytest.approx(1000.5),
        "total_unrealized_profit": pytest.approx(-12.3),
        "total_margin_balance": pytest.approx(988.2),
        "available_balance": pytest.approx(900.0),
        "starting_balance_usdt": None,
        "cumulative_return_pct": None,
    }


def test_get_account_snapshot_computes_cumulative_return_when_starting_balance_set(monkeypatch):
    monkeypatch.setattr(settings, "real_account_starting_balance_usdt", 1000.0)
    client = FakeBinanceClient(account={"totalWalletBalance": "1100.0"})
    broker = BinanceFuturesBroker(client=client)

    snapshot = get_account_snapshot(broker)

    assert snapshot["starting_balance_usdt"] == pytest.approx(1000.0)
    assert snapshot["cumulative_return_pct"] == pytest.approx(10.0)  # (1100-1000)/1000*100


def test_get_account_snapshot_wraps_exception_as_broker_error():
    broker = BinanceFuturesBroker(client=FakeBinanceClient(raise_on={"account"}))
    with pytest.raises(BrokerError):
        get_account_snapshot(broker)


def test_get_open_positions_filters_out_zero_amount():
    client = FakeBinanceClient(positions=[
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "60000", "markPrice": "60500",
         "unRealizedProfit": "250", "leverage": "3"},
        {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0", "markPrice": "3000",
         "unRealizedProfit": "0", "leverage": "1"},
        {"symbol": "SOLUSDT", "positionAmt": "-2.0", "entryPrice": "150", "markPrice": "148",
         "unRealizedProfit": "4", "leverage": "2"},
    ])
    broker = BinanceFuturesBroker(client=client)

    positions = get_open_positions(broker)

    assert len(positions) == 2  # ETHUSDT(수량 0)는 빠짐
    btc = next(p for p in positions if p["symbol"] == "BTCUSDT")
    assert btc["side"] == "LONG" and btc["quantity"] == pytest.approx(0.5)
    sol = next(p for p in positions if p["symbol"] == "SOLUSDT")
    assert sol["side"] == "SHORT" and sol["quantity"] == pytest.approx(2.0)


def test_get_recent_trades_merges_symbols_sorted_by_time_desc():
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [{"symbol": "BTCUSDT", "side": "BUY", "price": "60000", "qty": "0.01",
                     "realizedPnl": "5", "commission": "0.1", "commissionAsset": "USDT", "time": 100}],
        "ETHUSDT": [{"symbol": "ETHUSDT", "side": "SELL", "price": "3000", "qty": "0.1",
                     "realizedPnl": "-2", "commission": "0.05", "commissionAsset": "USDT", "time": 200}],
    })
    broker = BinanceFuturesBroker(client=client)

    trades = get_recent_trades(symbols=["BTCUSDT", "ETHUSDT"], broker=broker)

    assert [t["symbol"] for t in trades] == ["ETHUSDT", "BTCUSDT"]  # 최신순(200 > 100)


def test_get_recent_trades_skips_symbol_on_failure_but_keeps_others():
    client = FakeBinanceClient(
        trades_by_symbol={"ETHUSDT": [{"symbol": "ETHUSDT", "side": "SELL", "price": "3000", "qty": "0.1",
                                       "realizedPnl": "-2", "commission": "0.05", "commissionAsset": "USDT", "time": 200}]},
    )

    class PartiallyFailingClient(FakeBinanceClient):
        def futures_account_trades(self, symbol, limit):
            if symbol == "BTCUSDT":
                raise RuntimeError("이 심볼만 실패")
            return super().futures_account_trades(symbol, limit)

    broker = BinanceFuturesBroker(client=PartiallyFailingClient(
        trades_by_symbol=client._trades_by_symbol,
    ))

    trades = get_recent_trades(symbols=["BTCUSDT", "ETHUSDT"], broker=broker)

    assert len(trades) == 1 and trades[0]["symbol"] == "ETHUSDT"


def test_get_recent_round_trips_pairs_long_entry_with_exit():
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [
            {"symbol": "BTCUSDT", "side": "BUY", "price": "60000", "qty": "0.01",
             "realizedPnl": "0", "time": 100},  # 진입
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60600", "qty": "0.01",
             "realizedPnl": "6", "time": 200},  # 청산(트레일링 스탑 체결)
        ],
    })
    broker = BinanceFuturesBroker(client=client)

    trips = get_recent_round_trips(symbols=["BTCUSDT"], broker=broker)

    assert len(trips) == 1
    t = trips[0]
    assert t["symbol"] == "BTCUSDT" and t["side"] == "LONG"
    assert t["quantity"] == pytest.approx(0.01)
    assert t["entry_price"] == pytest.approx(60000)
    assert t["exit_price"] == pytest.approx(60600)
    assert t["realized_pnl"] == pytest.approx(6.0)
    assert t["opened_at"] == 100 and t["closed_at"] == 200


def test_get_recent_round_trips_pairs_short_entry_with_exit():
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60000", "qty": "0.01",
             "realizedPnl": "0", "time": 100},
            {"symbol": "BTCUSDT", "side": "BUY", "price": "59400", "qty": "0.01",
             "realizedPnl": "6", "time": 200},
        ],
    })
    broker = BinanceFuturesBroker(client=client)

    trips = get_recent_round_trips(symbols=["BTCUSDT"], broker=broker)

    assert len(trips) == 1 and trips[0]["side"] == "SHORT"


def test_get_recent_round_trips_merges_partial_fills_on_both_legs():
    """진입이 체결 2개로 쪼개지고, 청산도 체결 2개로 쪼개진 경우도
    수량 가중평균으로 올바르게 묶여야 한다."""
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [
            {"symbol": "BTCUSDT", "side": "BUY", "price": "60000", "qty": "0.005", "realizedPnl": "0", "time": 100},
            {"symbol": "BTCUSDT", "side": "BUY", "price": "60100", "qty": "0.005", "realizedPnl": "0", "time": 101},
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60500", "qty": "0.006", "realizedPnl": "3", "time": 200},
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60600", "qty": "0.004", "realizedPnl": "2", "time": 201},
        ],
    })
    broker = BinanceFuturesBroker(client=client)

    trips = get_recent_round_trips(symbols=["BTCUSDT"], broker=broker)

    assert len(trips) == 1
    t = trips[0]
    assert t["quantity"] == pytest.approx(0.01)
    assert t["entry_price"] == pytest.approx(60050)  # (60000*0.005+60100*0.005)/0.01
    assert t["realized_pnl"] == pytest.approx(5.0)


def test_get_recent_round_trips_handles_multiple_sequential_trades():
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [
            {"symbol": "BTCUSDT", "side": "BUY", "price": "60000", "qty": "0.01", "realizedPnl": "0", "time": 100},
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60600", "qty": "0.01", "realizedPnl": "6", "time": 200},
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60000", "qty": "0.01", "realizedPnl": "0", "time": 300},
            {"symbol": "BTCUSDT", "side": "BUY", "price": "59700", "qty": "0.01", "realizedPnl": "3", "time": 400},
        ],
    })
    broker = BinanceFuturesBroker(client=client)

    trips = get_recent_round_trips(symbols=["BTCUSDT"], broker=broker)

    assert len(trips) == 2
    assert trips[0]["closed_at"] == 400  # 최신순 정렬
    assert trips[1]["closed_at"] == 200


def test_get_recent_round_trips_discards_exit_with_no_matching_entry():
    """조회 구간 시작 전에 이미 열려 있던 포지션의 청산(짝 없음)은 버려야 한다."""
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [
            {"symbol": "BTCUSDT", "side": "SELL", "price": "60600", "qty": "0.01", "realizedPnl": "6", "time": 100},
        ],
    })
    broker = BinanceFuturesBroker(client=client)

    trips = get_recent_round_trips(symbols=["BTCUSDT"], broker=broker)

    assert trips == []


def test_get_recent_round_trips_ignores_still_open_entry():
    """아직 청산 안 된 진입은 미완결 상태라 결과에 포함하면 안 된다
    (get_open_positions()가 따로 보여주는 영역)."""
    client = FakeBinanceClient(trades_by_symbol={
        "BTCUSDT": [
            {"symbol": "BTCUSDT", "side": "BUY", "price": "60000", "qty": "0.01", "realizedPnl": "0", "time": 100},
        ],
    })
    broker = BinanceFuturesBroker(client=client)

    trips = get_recent_round_trips(symbols=["BTCUSDT"], broker=broker)

    assert trips == []


def test_get_live_status_reports_not_ready_when_no_api_key(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")

    status = get_live_status()

    assert status == {"ready": False, "reason": "BINANCE_API_KEY/SECRET이 설정되지 않음"}


def test_get_live_status_returns_full_snapshot_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "dummy")
    monkeypatch.setattr(settings, "binance_api_secret", "dummy")

    client = FakeBinanceClient(
        account={"totalWalletBalance": "100", "totalUnrealizedProfit": "0",
                 "totalMarginBalance": "100", "availableBalance": "100"},
        positions=[],
        trades_by_symbol={},
    )
    broker = BinanceFuturesBroker(client=client)

    status = get_live_status(broker)

    assert status["ready"] is True
    assert status["account"]["total_wallet_balance"] == pytest.approx(100.0)
    assert status["open_positions"] == []
    assert status["recent_trades"] == []


def test_get_live_status_reports_not_ready_on_broker_error(monkeypatch):
    monkeypatch.setattr(settings, "binance_api_key", "dummy")
    monkeypatch.setattr(settings, "binance_api_secret", "dummy")

    broker = BinanceFuturesBroker(client=FakeBinanceClient(raise_on={"account"}))

    status = get_live_status(broker)

    assert status["ready"] is False
    assert "reason" in status
