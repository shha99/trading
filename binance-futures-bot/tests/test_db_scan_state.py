"""ScanState가 (symbol, timeframe)뿐 아니라 strategy까지 키로 삼는지 검증 -
켈트너/wick 두 엔진이 같은 심볼×시간대를 봐도 서로의 처리 상태를 밟지
않아야 한다(안 그러면 한쪽이 처리한 봉을 다른 쪽이 건너뛰는 실제 버그)."""
from __future__ import annotations

from datetime import datetime

from app.db import get_last_processed, set_last_processed


def test_different_strategies_track_independent_state_for_same_combo():
    ts_keltner = datetime(2026, 1, 1, 0, 0, 0)
    ts_wick = datetime(2026, 1, 1, 0, 15, 0)

    set_last_processed("BTCUSDT", "15m", ts_keltner, strategy="keltner_reclaim_200ema")
    set_last_processed("BTCUSDT", "15m", ts_wick, strategy="bollinger_wick_breakeven_trail")

    assert get_last_processed("BTCUSDT", "15m", strategy="keltner_reclaim_200ema") == ts_keltner
    assert get_last_processed("BTCUSDT", "15m", strategy="bollinger_wick_breakeven_trail") == ts_wick


def test_unset_combo_returns_none():
    assert get_last_processed("ETHUSDT", "5m", strategy="bollinger_wick_breakeven_trail") is None


def test_default_strategy_matches_keltner_key():
    """strategy 인자를 생략하면 기존 켈트너 엔진 호출부와 호환되는 기본값이어야 한다."""
    ts = datetime(2026, 2, 1, 0, 0, 0)
    set_last_processed("BTCUSDT", "1h", ts)  # strategy 생략
    assert get_last_processed("BTCUSDT", "1h", strategy="keltner_reclaim_200ema") == ts
    assert get_last_processed("BTCUSDT", "1h") == ts  # 생략 시에도 동일 키 조회


def test_set_last_processed_updates_existing_row_for_same_key():
    set_last_processed("BTCUSDT", "15m", datetime(2026, 1, 1), strategy="bollinger_wick_breakeven_trail")
    set_last_processed("BTCUSDT", "15m", datetime(2026, 1, 2), strategy="bollinger_wick_breakeven_trail")

    assert get_last_processed("BTCUSDT", "15m", strategy="bollinger_wick_breakeven_trail") == datetime(2026, 1, 2)
