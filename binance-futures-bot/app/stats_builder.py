"""심볼×시간대별 백테스트 성적을 계산해 data/strategy_stats.json에 저장한다.

전략/백테스트 로직 자체는 재구현하지 않고 backtest.py의
simulate()/summarize()를 그대로 재사용한다. 여기서는:
  1. 심볼×시간대 조합마다 필요한 만큼 과거 데이터를 받아오고,
  2. 학습/검증 구간, 연도별로 트레이드를 나눠 각각 집계하고,
  3. 그 결과를 JSON으로 저장해 /api/strategy/stats, 전략 페이지가 쓰게 한다.

시간대마다 "5년치" 봉 개수가 다르므로(15분봉은 1시간봉보다 4배 많음),
고정된 total_bars가 아니라 학습 시작일~검증 종료일 사이 기간을 봉 간격으로
나눠 필요한 개수를 매번 계산한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from app.config import DATA_DIR, settings
from app.history import interval_to_timedelta, fetch_extended_history
from app.strategy import KeltnerReclaimStrategy
from backtest import simulate, summarize

logger = logging.getLogger(__name__)

STATS_FILE = DATA_DIR / "strategy_stats.json"
MAX_BARS_PER_COMBO = 50_000  # 요청 과다 방지용 상한


def train_end_ts() -> pd.Timestamp:
    return pd.Timestamp(settings.backtest_train_end)


def validation_end_ts() -> pd.Timestamp:
    if settings.backtest_validation_end:
        return pd.Timestamp(settings.backtest_validation_end)
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def bars_needed_for_span(timeframe: str) -> int:
    start = pd.Timestamp(settings.backtest_train_start)
    end = validation_end_ts()
    span = end - start
    bar_span = interval_to_timedelta(timeframe)
    bars = int(span / bar_span) + 50  # 워밍업(200EMA 등) 여유분
    return min(bars, MAX_BARS_PER_COMBO)


def _split_trades(trades: list[dict]) -> dict:
    train_end = train_end_ts()
    train: list[dict] = []
    validation: list[dict] = []
    yearly: dict[int, list[dict]] = {}

    for t in trades:
        entry_time = pd.Timestamp(t["entry_time"])
        (train if entry_time < train_end else validation).append(t)
        yearly.setdefault(entry_time.year, []).append(t)

    return {
        "overall": summarize(trades),
        "train": summarize(train),
        "validation": summarize(validation),
        "yearly": {str(year): summarize(ts) for year, ts in sorted(yearly.items())},
        "recent_trades": trades[-50:],
    }


def build_symbol_timeframe(symbol: str, timeframe: str) -> dict:
    total_bars = bars_needed_for_span(timeframe)
    logger.info("백테스트 성적 계산 시작: %s %s (최대 %d봉)", symbol, timeframe, total_bars)
    df = fetch_extended_history(symbol, timeframe, total_bars)
    if df is None or df.empty:
        return {"error": "데이터를 가져오지 못했습니다"}

    strategy = KeltnerReclaimStrategy()
    trades = simulate(df, strategy)
    result = _split_trades(trades)
    result["bars"] = len(df)
    result["range"] = {"start": str(df.index[0]), "end": str(df.index[-1])}
    return result


def build_all(symbols: list[str] | None = None, timeframes: list[str] | None = None) -> dict:
    symbols = symbols or settings.symbols
    timeframes = timeframes or settings.dashboard_timeframes

    stats: dict = {}
    for symbol in symbols:
        stats[symbol] = {}
        for timeframe in timeframes:
            try:
                stats[symbol][timeframe] = build_symbol_timeframe(symbol, timeframe)
            except Exception:
                logger.exception("백테스트 성적 계산 실패: %s %s", symbol, timeframe)
                stats[symbol][timeframe] = {"error": "계산 실패 (서버 로그 확인)"}

    stats["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train_start": settings.backtest_train_start,
        "train_end": settings.backtest_train_end,
        "validation_end": str(validation_end_ts()),
        "strategy": KeltnerReclaimStrategy.key,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    logger.info("백테스트 성적 저장 완료: %s", STATS_FILE)
    return stats
