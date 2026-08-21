"""바이낸스 USDT-M 선물 클라이언트 팩토리.

데이터 조회(공개 klines)와 주문 실행(서명 필요) 양쪽이 이 모듈 하나만
참조하도록 해서 API 키 취급을 한 곳에 모은다. 키는 .env(환경변수)에서만
읽고, 로그/DB/API 응답 어디에도 남기지 않는다.
"""
from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

_client = None


def get_binance_client():
    """python-binance Client의 캐싱된 싱글턴을 반환한다.

    BINANCE_TESTNET=true(기본값)이면 선물 테스트넷(testnet.binancefuture.com)에
    연결된다. API 키가 없어도(공개 klines만 쓰는 경우) 인스턴스는 만들어지며,
    서명이 필요한 호출(주문 등)만 그때 가서 바이낸스가 거부한다.
    """
    global _client
    if _client is not None:
        return _client

    from binance.client import Client

    _client = Client(
        api_key=settings.binance_api_key or None,
        api_secret=settings.binance_api_secret or None,
        testnet=settings.binance_testnet,
    )
    logger.info(
        "바이낸스 클라이언트 초기화 (testnet=%s, api_key_set=%s)",
        settings.binance_testnet,
        bool(settings.binance_api_key),
    )
    return _client


def reset_client() -> None:
    """테스트/재설정용: 캐싱된 클라이언트를 초기화한다."""
    global _client
    _client = None
