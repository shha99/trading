"""원격 킬스위치 - 휴대폰 등 어디서든 대시보드(`/trading`)의 버튼으로 두
자동매매 엔진의 **신규 진입**만 끄고 켤 수 있게 한다. 이미 열린 포지션의
SL/TP 체결 반영·트레일링 스탑 갱신에는 전혀 영향 없다 - 기존 일일 손실
한도 킬스위치(`app/risk.py::is_kill_switch_active()`)와 정확히 같은
범위·같은 설계 원칙이다.

**기본값은 완전히 꺼짐(기존 동작에 영향 없음)**: `REMOTE_CONTROL_URL`을
설정하지 않으면 `is_remotely_enabled()`는 항상 `True`를 반환한다 - opt-in
기능. 설정했을 때만(보통 Oracle 등 실제 매매가 도는 서버에서, 대시보드가
떠 있는 주소를 가리키게 설정) 매 진입 시도마다 그 주소의
`/api/control/status`에 물어본다.

**네트워크 문제 시 동작**: 조회 실패 시 조용히 끄거나 켜지 않고, **마지막
으로 성공했을 때의 값을 그대로 유지**한다(한 번도 성공한 적 없으면
`True`로 시작). 즉:
- 정전/네트워크 단절로 한동안 못 물어봤다고 꺼져있던 게 조용히 다시
  켜지는 일도 없고,
- 반대로 끈 적이 없는데 네트워크 문제만으로 계속 멈춰있게 되는 일도 없다.

마지막 값은 프로세스 메모리뿐 아니라 로컬 파일(`data/remote_control_cache.json`)
에도 저장해서, 프로세스가 재시작돼도(예: systemd 재시작) 직전 상태를
그대로 이어간다."""
from __future__ import annotations

import json
import logging

import requests

from .config import DATA_DIR, settings

logger = logging.getLogger(__name__)

_CACHE_FILE = DATA_DIR / "remote_control_cache.json"
_cache: dict[str, bool] | None = None


def _load_cache() -> dict[str, bool]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(_CACHE_FILE.read_text())
    except Exception:
        _cache = {}
    return _cache


def _save_cache(data: dict[str, bool]) -> None:
    global _cache
    _cache = data
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data))
    except Exception:
        logger.exception("원격 제어 캐시 저장 실패 (계속 진행 - 다음 조회 시 재시도)")


def is_remotely_enabled(engine: str) -> bool:
    """engine: "wick" 또는 "keltner". `REMOTE_CONTROL_URL` 미설정 시 항상 True."""
    if not settings.remote_control_url:
        return True

    cache = _load_cache()
    try:
        res = requests.get(f"{settings.remote_control_url}/api/control/status", timeout=5)
        res.raise_for_status()
        data = res.json()
        cache = {
            "wick": bool(data.get("wick_enabled", True)),
            "keltner": bool(data.get("keltner_enabled", True)),
        }
        _save_cache(cache)
    except Exception:
        logger.warning("원격 제어 상태 조회 실패 - 마지막 값 유지(%s): %s", cache, engine)

    return cache.get(engine, True)
