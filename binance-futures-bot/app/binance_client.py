"""바이낸스 USDT-M 선물 클라이언트 팩토리.

데이터 조회(공개 klines)와 주문 실행(서명 필요) 양쪽이 이 모듈 하나만
참조하도록 해서 API 키 취급을 한 곳에 모은다. 키는 .env(환경변수)에서만
읽고, 로그/DB/API 응답 어디에도 남기지 않는다.
"""
from __future__ import annotations

import logging

# 모듈 최상단에서 미리 import해둔다 - 함수 안에서 지연 import하면, 서버
# 기동 시 여러 스레드/asyncio 태스크(시그널 스캔 스레드, 실시간 피드 태스크,
# uvicorn 메인 스레드)가 거의 동시에 binance 패키지를 "처음" import하려다
# 파이썬의 import 락 경합으로 "partially initialized module" 순환 임포트
# 오류가 나는 걸 직접 겪었다. 여기서 한 번만 로드해두면 이후 import는 전부
# sys.modules 캐시를 읽는 것이라 안전하다.
from binance.client import Client

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

    _client = Client(
        api_key=settings.binance_api_key or None,
        api_secret=settings.binance_api_secret or None,
        testnet=settings.binance_testnet,
        # Client()가 생성자에서 기본으로 self.ping()을 호출하는데, 이게
        # (환경에 따라) 선물 testnet=True를 줘도 막혀있는 현물 엔드포인트를
        # 때리는 경우가 있어 여기서 끈다 - 실제 연결 가능 여부는 우리가
        # 호출하는 futures_klines/futures_ticker 등에서 각자 에러 처리한다.
        ping=False,
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
