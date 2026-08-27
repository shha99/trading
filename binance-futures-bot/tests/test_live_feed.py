"""LiveFeed의 메시지 파싱 로직 검증 (실제 WS 연결 없음)."""
from __future__ import annotations

import json

import pandas as pd

import app.live_feed as live_feed_module
from app.live_feed import LiveFeed


def _make_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=idx
    )


def test_handle_price_message_combined_stream_format():
    feed = LiveFeed(symbols=["BTCUSDT"])
    raw = json.dumps({
        "stream": "btcusdt@bookTicker",
        "data": {"s": "BTCUSDT", "b": "77000.10", "a": "77010.20"},
    })
    feed._handle_price_message(raw)
    price = feed.get_price("BTCUSDT")
    assert price is not None
    assert price["bid"] == 77000.10
    assert price["ask"] == 77010.20
    assert price["mid"] == (77000.10 + 77010.20) / 2


def test_handle_price_message_bare_format():
    feed = LiveFeed(symbols=["ETHUSDT"])
    raw = json.dumps({"s": "ETHUSDT", "b": "2400.0", "a": "2400.5"})
    feed._handle_price_message(raw)
    assert feed.get_price("ETHUSDT")["mid"] == 2400.25


def test_handle_price_message_malformed_is_ignored_not_raised():
    feed = LiveFeed(symbols=["BTCUSDT"])
    feed._handle_price_message("not json at all")
    assert feed.get_price("BTCUSDT") is None


def test_get_price_unknown_symbol_returns_none():
    feed = LiveFeed(symbols=["BTCUSDT"])
    assert feed.get_price("DOGEUSDT") is None
    assert feed.get_ticker24h("DOGEUSDT") is None


def test_get_candles_small_request_does_not_stomp_larger_cached_limit(monkeypatch):
    """limit=2로 도는 WS 루프가, limit=250을 요청하는 전략 페이지의 캐시를
    지워버리면 안 된다 (예전엔 캐시 키에 limit이 안 들어가 있어서 발생했던 버그)."""
    calls = []

    def fake_fetch_klines(symbol, timeframe, limit=500, end_time_ms=None):
        calls.append(limit)
        return _make_df(limit)

    monkeypatch.setattr(live_feed_module, "fetch_klines", fake_fetch_klines)
    feed = LiveFeed(symbols=["BTCUSDT"])

    small = feed.get_candles("BTCUSDT", "1h", limit=2)
    assert len(small) == 2
    assert calls == [2]

    big = feed.get_candles("BTCUSDT", "1h", limit=250)
    assert len(big) == 250
    assert calls == [2, 250]  # 부족해서 다시 받아옴

    small_again = feed.get_candles("BTCUSDT", "1h", limit=2)
    assert len(small_again) == 2
    assert calls == [2, 250]  # 캐시(250개)로 충분하니 재호출 없음
