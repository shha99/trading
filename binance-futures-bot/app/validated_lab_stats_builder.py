"""`/lab` 후보 중 켈트너 전략과 동급으로 "검증됨"으로 승격된 전략들의
학습/검증/연도별 성적을 stats_builder.py(켈트너 전용)와 똑같은 방식으로
계산해 data/validated_lab_stats.json에 저장한다.

`lab_stats_builder.py`는 11개 후보 전부를 "요청 기간 전체 한 줄 요약"으로만
비교하는 게 목적이라 학습/검증 분리가 없다. 여기서는 그 중 실제로 켈트너급
검증(학습/검증 분리 + 연도별 + 3년 이상 데이터)을 통과해 "이제부터 검증된
전략으로 취급하기로" 정한 후보만 별도로 다룬다 - 지금은 2종:

- `big_candle_bollinger_confluence` — BTCUSDT 1시간봉 전용으로 검증됨
- `bollinger_wick_breakeven_trail` — BTCUSDT/ETHUSDT 15분·5분봉에서 검증됨

**주의**: "검증됨"은 어디까지나 백테스트 성적 기준이고, 자동매매 엔진
(`app/signal_engine.py`)에는 아직 연결돼 있지 않다 - 화이트리스트는 여전히
`AUTO_TRADE_WHITELIST` 기본값(빈 값)이 그대로고, 이 전략들의 "본전 이동
트레일링" 청산은 진입 후에도 손절 주문을 계속 옮겨줘야 해서 지금의 브로커
코드(진입 시 고정 SL/TP만 검)로는 실행할 수 없다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from app.config import DATA_DIR, settings
from app.history import fetch_extended_history, interval_to_timedelta
from app.lab_backtest import simulate_lab, summarize_lab
from app.lab_strategies import BigCandleBollingerConfluenceStrategy, BollingerWickBreakevenTrailStrategy, LabStrategy
from app.stats_builder import train_end_ts, validation_end_ts

logger = logging.getLogger(__name__)

VALIDATED_STATS_FILE = DATA_DIR / "validated_lab_stats.json"

# stats_builder.py의 5만봉 상한은 15분/5분봉으로는 3년치도 못 채운다(15분봉
# 5만봉 = 1.4년, 5분봉은 그 1/3). 검증된 전략은 "최소 3년 이상"이 전제라
# 상한을 훨씬 크게 잡는다 - 5분봉으로 2021-07~현재 전체(~54만봉)도 커버 가능.
MAX_BARS_PER_COMBO = 700_000


def bars_needed_for_span(timeframe: str) -> int:
    start = pd.Timestamp(settings.backtest_train_start)
    end = validation_end_ts()
    span = end - start
    bar_span = interval_to_timedelta(timeframe)
    bars = int(span / bar_span) + 50  # 워밍업 여유분
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
        "overall": summarize_lab(trades),
        "train": summarize_lab(train),
        "validation": summarize_lab(validation),
        "yearly": {str(year): summarize_lab(ts) for year, ts in sorted(yearly.items())},
        "recent_trades": trades[-50:],
    }


def build_symbol_timeframe(strategy: LabStrategy, symbol: str, timeframe: str, fee_pct: float = 0.0) -> dict:
    total_bars = bars_needed_for_span(timeframe)
    logger.info(
        "검증 전략 백테스트: %s %s %s (최대 %d봉, 수수료 %.3f%%)",
        strategy.key, symbol, timeframe, total_bars, fee_pct,
    )
    df = fetch_extended_history(symbol, timeframe, total_bars)
    if df is None or df.empty:
        return {"error": "데이터를 가져오지 못했습니다"}

    trades = simulate_lab(df, strategy, fee_pct=fee_pct)
    result = _split_trades(trades)
    result["bars"] = len(df)
    result["range"] = {"start": str(df.index[0]), "end": str(df.index[-1])}
    return result


def _default_specs() -> list[dict]:
    # fee_pct: 실제 바이낸스 선물 왕복 수수료(settings.taker_fee_pct_roundtrip)를
    # 반영한다 - "검증됨" 등급은 수수료 0원 가정으로는 실전 판단이 왜곡될 수
    # 있기 때문(잦은 매매 전략일수록 영향이 크다 - README 참고).
    fee_pct = settings.taker_fee_pct_roundtrip
    return [
        {
            "strategy": BigCandleBollingerConfluenceStrategy(),
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframes": ["1h"],
            "fee_pct": fee_pct,
        },
        {
            "strategy": BollingerWickBreakevenTrailStrategy(),
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframes": ["15m", "5m"],
            "fee_pct": fee_pct,
        },
    ]


def build_all(specs: list[dict] | None = None) -> dict:
    specs = specs if specs is not None else _default_specs()

    stats: dict = {}
    for spec in specs:
        strategy = spec["strategy"]
        fee_pct = spec.get("fee_pct", 0.0)
        stats[strategy.key] = {}
        for symbol in spec["symbols"]:
            stats[strategy.key][symbol] = {}
            for timeframe in spec["timeframes"]:
                try:
                    stats[strategy.key][symbol][timeframe] = build_symbol_timeframe(
                        strategy, symbol, timeframe, fee_pct=fee_pct
                    )
                except Exception:
                    logger.exception("검증 전략 백테스트 실패: %s %s %s", strategy.key, symbol, timeframe)
                    stats[strategy.key][symbol][timeframe] = {"error": "계산 실패 (서버 로그 확인)"}

    stats["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "켈트너 전략(#1 - /strategy 페이지)과 동급으로 학습/검증/연도별 분리 "
            "검증을 통과한 lab 후보만 포함. 거래 1건당 왕복 수수료(taker_fee_pct_roundtrip)를 "
            "반영한 순수익률이다. 여전히 자동매매 엔진에는 연결 안 됨 "
            "(문서/정책 수준 - AUTO_TRADE_WHITELIST 기본값 그대로)."
        ),
        "taker_fee_pct_roundtrip": settings.taker_fee_pct_roundtrip,
        "train_start": settings.backtest_train_start,
        "train_end": settings.backtest_train_end,
        "validation_end": str(validation_end_ts()),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATED_STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    logger.info("검증 전략 백테스트 저장 완료: %s", VALIDATED_STATS_FILE)
    return stats
