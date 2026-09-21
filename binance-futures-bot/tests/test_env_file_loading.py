"""app/config.py가 .env 파일을 실제로 환경변수로 로드하는지 검증.

python-dotenv가 requirements.txt에는 있었지만 실제로 load_dotenv()를
호출하는 코드가 어디에도 없어서, .env 파일에 값을 채워도 실행 시
전혀 반영되지 않던 회귀 버그의 재발 방지용 테스트다(실사용자가 헤드리스
봇을 돌리다가 API 키가 하나도 안 읽히는 걸로 직접 발견함).

기존 테스트들처럼 이미 임포트된 `settings` 객체를 monkeypatch하는 방식으로는
"모듈을 새로 임포트할 때 .env가 실제로 로드되는지" 자체를 검증할 수 없다
(이미 프로세스에 떠 있는 객체를 만지는 거라 로딩 시점 버그를 못 잡음) -
그래서 별도 파이썬 프로세스를 띄워 진짜 기동 상황(작업 디렉터리에 .env가
있는 채로 처음부터 임포트)을 그대로 재현한다."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_in_clean_process(cwd, extra_env=None) -> list[str]:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO_ROOT)}
    if extra_env:
        env.update(extra_env)
    script = (
        "from app.config import settings\n"
        "print(repr(settings.binance_api_key))\n"
        "print(settings.wick_auto_trade_enabled)\n"
        "print(sorted(settings.wick_auto_trade_whitelist))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return result.stdout.strip().splitlines()


def test_env_file_in_cwd_is_loaded_at_startup(tmp_path):
    """실제 사용 시나리오 그대로: 작업 디렉터리에 .env를 두고 새 프로세스에서
    임포트하면 그 값이 반영돼야 한다 (지금까지는 반영 안 되던 버그)."""
    (tmp_path / ".env").write_text(
        "BINANCE_API_KEY=from_dotenv_test_key\n"
        "WICK_AUTO_TRADE_ENABLED=true\n"
        "WICK_AUTO_TRADE_WHITELIST=BTCUSDT:15m\n"
    )
    api_key, wick_enabled, wick_whitelist = _run_in_clean_process(tmp_path)
    assert api_key == "'from_dotenv_test_key'"
    assert wick_enabled == "True"
    assert wick_whitelist == "[('BTCUSDT', '15m')]"


def test_no_env_file_falls_back_to_safe_defaults(tmp_path):
    """.env가 아예 없는 디렉터리에서는 여전히 안전 기본값(꺼짐)이어야 한다 -
    load_dotenv() 추가가 기존 무설정 상태의 안전 기본값을 깨면 안 된다."""
    api_key, wick_enabled, wick_whitelist = _run_in_clean_process(tmp_path)
    assert api_key == "''"
    assert wick_enabled == "False"
    assert wick_whitelist == "[]"
