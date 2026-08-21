"""테스트 전용 환경변수를 다른 모듈 import보다 먼저 세팅한다.

app.config.settings는 모듈 최초 import 시 한 번 계산되는 싱글턴이므로,
테스트용 값(특히 DATABASE_URL)은 conftest.py에서 가장 먼저 정해둬야 한다.
"""
from __future__ import annotations

import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="binance-futures-bot-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_dir}/test.db")
os.environ.setdefault("DATA_DIR", _tmp_dir)
os.environ.setdefault("AUTO_TRADE_ENABLED", "false")
os.environ.setdefault("BINANCE_TESTNET", "true")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")

import pytest  # noqa: E402

from app.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """각 테스트 전에 스키마를 보장하고, 테스트 간 데이터가 섞이지 않도록 정리한다."""
    init_db()
    from app.db import SessionLocal, ScanState, SignalRecord, TradeRecord

    session = SessionLocal()
    try:
        session.query(TradeRecord).delete()
        session.query(SignalRecord).delete()
        session.query(ScanState).delete()
        session.commit()
    finally:
        session.close()
    yield
