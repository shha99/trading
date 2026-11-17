"""Render 등 일부 클라우드 리전이 바이낸스 공개 API까지 지역 차단해서
`app/binance_account.get_live_status()`가 그 서버에서는 못 쓰게 되는 문제의
우회책.

**기본값은 꺼짐이다** (`STATUS_PUSH_TOKEN` 비어있음). 켜면(opt-in, 두 쪽
다 같은 토큰으로 설정):

- **송신측**(실제 매매가 도는, 차단 안 된 서버 - 보통 집 PC): 주기적으로
  (`run_trading_bot.py`의 스케줄러) `push_status_once()`가 자기가 직접
  조회한 `get_live_status()` 결과를 `REMOTE_CONTROL_URL`이 가리키는
  대시보드로 "보고"한다.
- **수신측**(대시보드, 예: Render - `server.py`의
  `POST /api/binance/status/push`): 받은 걸 `record_pushed_status()`로
  메모리에 캐싱해두면, `GET /api/binance/status`가 (대시보드 자신이 직접
  바이낸스를 호출하는 대신) `get_pushed_status()`로 그 캐시를 먼저 확인해서
  있으면 그대로 돌려준다 - 대시보드는 바이낸스를 전혀 호출할 필요가
  없어져서 지역 차단과 완전히 무관해진다. 캐시가 없거나 너무 오래됐으면
  (`STALE_AFTER_SECONDS`) 기존처럼 직접 조회로 폴백한다(송신측이 꺼져있는
  배포에는 전혀 영향 없음).

원격 킬스위치(`app/remote_control.py`)와 정반대 방향이지만 같은 설계
원칙(opt-in, 토큰 공유, 실패해도 조용히 다음 주기에 재시도)을 따른다.

메모리에만 캐싱하고 파일로 남기지 않는다 - Render 무료 플랜은 영구
디스크가 없어 재배포/재시작마다 파일도 어차피 사라지고, 송신측이 짧은
주기(기본 20초)로 계속 다시 채워주므로 파일로 남길 실익이 없다."""
from __future__ import annotations

import logging
import time

import requests

from .binance_account import get_live_status
from .broker import BinanceFuturesBroker
from .config import settings

logger = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 90  # 송신측이 몇 주기 연속 실패하면 "오래된 값"으로 보고 직접 조회로 폴백

_pushed_status: dict | None = None
_pushed_at: float = 0.0


def push_status_once(broker: BinanceFuturesBroker | None = None) -> bool:
    """송신측에서 주기적으로 호출 - 성공적으로 보고했으면 True."""
    if not settings.remote_control_url or not settings.status_push_token:
        return False

    broker = broker or BinanceFuturesBroker()
    try:
        status = get_live_status(broker)
    except Exception:
        logger.exception("바이낸스 상태 릴레이: 로컬 조회 실패 - 이번 주기는 건너뜀")
        return False

    if not status.get("ready"):
        logger.warning("바이낸스 상태 릴레이: 로컬 조회 자체가 준비 안 됨 - 보고 안 함 (%s)", status.get("reason"))
        return False

    try:
        res = requests.post(
            f"{settings.remote_control_url}/api/binance/status/push",
            json=status,
            headers={"X-Status-Push-Token": settings.status_push_token},
            timeout=10,
        )
        res.raise_for_status()
        return True
    except Exception:
        logger.warning("바이낸스 상태 릴레이 전송 실패 (다음 주기에 재시도)")
        return False


def record_pushed_status(status: dict) -> None:
    """수신측(`server.py`)에서 호출 - 방금 받은 상태를 캐싱한다."""
    global _pushed_status, _pushed_at
    _pushed_status = status
    _pushed_at = time.time()


def get_pushed_status() -> dict | None:
    """수신측에서 호출 - 캐시가 없거나 `STALE_AFTER_SECONDS`보다 오래됐으면
    None을 돌려줘서 호출부가 직접 조회로 폴백하게 한다."""
    if _pushed_status is None:
        return None
    if time.time() - _pushed_at > STALE_AFTER_SECONDS:
        return None
    return _pushed_status
