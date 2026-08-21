"""실시간 현재가(bookTicker WS) + 24시간 통계(REST 폴링) 피드.

캔들 자체는 여기서 다루지 않는다 — 바이낸스 klines REST 응답이 진행 중인
마지막 봉을 이미 실시간으로 갱신해서 주기 때문에, `/api/candles`가
`app/history.py`의 `fetch_klines`를 그때그때 다시 부르기만 하면
"마지막 캔들이 실시간으로 움직이는" 효과가 그대로 난다.

`python-binance`의 `ThreadedWebsocketManager`는 testnet=True를 줘도 내부
클라이언트 부트스트랩 과정에서 (환경에 따라) 막혀있는 엔드포인트를 호출해
실패할 수 있어, `websockets`로 스트림에 직접 붙는다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import pandas as pd
import websockets

from .binance_client import get_binance_client
from .config import settings
from .history import fetch_klines

logger = logging.getLogger(__name__)

_RECONNECT_BACKOFF_SECONDS = (1, 2, 5, 10, 20, 30)


class LiveFeed:
    def __init__(self, symbols: list[str] | None = None):
        self.symbols = [s.upper() for s in (symbols or settings.symbols)]
        self.prices: dict[str, dict] = {}
        self.ticker24h: dict[str, dict] = {}
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._candle_cache: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}

    def start(self) -> None:
        """실행 중인 이벤트 루프에 백그라운드 태스크로 등록한다 (FastAPI startup에서 호출)."""
        self._stopping = False
        self._tasks = [
            asyncio.create_task(self._run_price_stream(), name="live_feed_price_ws"),
            asyncio.create_task(self._run_ticker24h_poll(), name="live_feed_ticker24h"),
        ]

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        self._tasks = []

    def get_price(self, symbol: str) -> dict | None:
        return self.prices.get(symbol.upper())

    def get_ticker24h(self, symbol: str) -> dict | None:
        return self.ticker24h.get(symbol.upper())

    def get_candles(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame | None:
        """캔들을 REST로 다시 받아온다 - `live_poll_interval_seconds` 동안은
        캐시를 재사용해서, 여러 WS 클라이언트/API 호출이 동시에 봐도 바이낸스
        호출이 중복되지 않게 한다.

        캐시 키에 limit을 넣지 않는다 - 대신 "지금까지 받아둔 것 중 가장 큰
        limit"으로 캐시하고, 더 작은 limit 요청은 꼬리만 잘라서 재사용한다.
        (limit까지 키에 넣으면 limit=2로 도는 WS 루프가 limit=250을 요청하는
        전략 페이지의 캐시를 매번 갈아치우는 문제가 있었음.)
        """
        key = (symbol.upper(), timeframe)
        cached = self._candle_cache.get(key)
        now = time.time()
        if cached is not None:
            cached_at, cached_df = cached
            fresh = now - cached_at < settings.live_poll_interval_seconds
            if fresh and len(cached_df) >= limit:
                return cached_df.tail(limit)

        fetch_limit = max(limit, cached[1].shape[0] if cached is not None else 0)
        df = fetch_klines(symbol.upper(), timeframe, limit=fetch_limit)
        if df is not None:
            self._candle_cache[key] = (now, df)
            return df.tail(limit)
        return None

    async def _run_price_stream(self) -> None:
        streams = "/".join(f"{s.lower()}@bookTicker" for s in self.symbols)
        url = f"{settings.futures_ws_base_url}/stream?streams={streams}"
        attempt = 0

        while not self._stopping:
            try:
                async with websockets.connect(url, open_timeout=10, ping_interval=15) as ws:
                    logger.info("실시간 현재가 WS 연결됨: %s", url)
                    attempt = 0
                    async for raw in ws:
                        self._handle_price_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = _RECONNECT_BACKOFF_SECONDS[min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)]
                logger.warning("현재가 WS 끊김 - %d초 후 재연결 시도", delay, exc_info=True)
                attempt += 1
                await asyncio.sleep(delay)

    def _handle_price_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            symbol = data.get("s")
            bid, ask = float(data["b"]), float(data["a"])
            if symbol:
                self.prices[symbol] = {
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2.0,
                    "updated_at": time.time(),
                }
        except Exception:
            logger.exception("현재가 WS 메시지 파싱 실패")

    async def _run_ticker24h_poll(self) -> None:
        while not self._stopping:
            for symbol in self.symbols:
                try:
                    data = await asyncio.to_thread(get_binance_client().futures_ticker, symbol=symbol)
                    self.ticker24h[symbol] = data
                except Exception:
                    logger.exception("24시간 통계 조회 실패: %s", symbol)
            await asyncio.sleep(settings.ticker24h_poll_interval_seconds)


_feed: LiveFeed | None = None


def get_live_feed() -> LiveFeed:
    global _feed
    if _feed is None:
        _feed = LiveFeed()
    return _feed
