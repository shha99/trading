"""전략 실험실(켈트너 1종 + 후보 8종 = 9종)의 심볼×시간대별 성적을
계산해 data/lab_stats.json에 저장한다.

기존 backtest.py(켈트너 전용, R-배수)와 lab_backtest.py(후보 8종, % 수익률)
양쪽을 그대로 재사용하고, 켈트너 트레이드만 "거래당 %수익률"로 환산해서
실험실 카드에 다른 8개와 같은 기준(%/거래)으로 나란히 보여준다.

`stats_builder.py`(전략 페이지의 켈트너 전용 학습/검증/연도별 성적)와는
별개의 파일이다 — 이쪽은 8개 전략을 한 화면에서 비교하는 게 목적이라
학습/검증 분리 없이 "요청한 기간 전체" 성적만 본다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.config import DATA_DIR, settings
from app.history import fetch_extended_history
from app.lab_backtest import simulate_lab, summarize_lab
from app.lab_strategies import lab_strategies
from app.stats_builder import bars_needed_for_span
from app.strategy import KeltnerReclaimStrategy
from backtest import simulate as simulate_keltner

logger = logging.getLogger(__name__)

LAB_STATS_FILE = DATA_DIR / "lab_stats.json"
RECENT_TRADES_KEEP = 20


def _keltner_catalog_entry() -> dict:
    return {
        "key": KeltnerReclaimStrategy.key,
        "label": KeltnerReclaimStrategy.label,
        "category": "눌림목 되돌림 (역추세)",
        "description": "상승 추세에서 켈트너 채널 하단까지 밀렸다가 다시 올라오는 순간에 매수 "
        "(검증된 전략 - 자동매매 화이트리스트에 올릴 수 있는 유일한 전략, /strategy 페이지 참고)",
        "designed_timeframe": "1h",
    }


def _keltner_trades_pct(df) -> list[dict]:
    """backtest.py의 트레이드(entry_price/exit_price)를 %수익률로 환산 -
    실험실의 다른 7개 전략과 같은 단위(거래당 %)로 비교하기 위함."""
    trades = simulate_keltner(df, KeltnerReclaimStrategy())
    out = []
    for t in trades:
        pct = (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100
        out.append({**t, "direction": "LONG", "pct_return": round(pct, 4)})
    return out


def catalog() -> list[dict]:
    entries = [_keltner_catalog_entry()]
    entries += [
        {
            "key": s.key, "label": s.label, "category": s.category,
            "description": s.description, "designed_timeframe": s.designed_timeframe,
        }
        for s in lab_strategies()
    ]
    return entries


def _simulate_fns() -> dict:
    fns = {KeltnerReclaimStrategy.key: _keltner_trades_pct}
    for strategy in lab_strategies():
        fns[strategy.key] = (lambda strat: lambda df: simulate_lab(df, strat))(strategy)
    return fns


def build_all(symbols: list[str] | None = None, timeframes: list[str] | None = None) -> dict:
    symbols = symbols or settings.symbols
    timeframes = timeframes or settings.dashboard_timeframes
    entries = catalog()
    simulate_fns = _simulate_fns()

    stats: dict = {}
    for symbol in symbols:
        stats[symbol] = {}
        for timeframe in timeframes:
            logger.info("실험실 성적 계산: %s %s (전략 %d개)", symbol, timeframe, len(entries))
            bars = bars_needed_for_span(timeframe)
            df = fetch_extended_history(symbol, timeframe, bars)

            by_strategy: dict = {}
            for entry in entries:
                key = entry["key"]
                if df is None or df.empty:
                    by_strategy[key] = {"error": "데이터를 가져오지 못했습니다"}
                    continue
                try:
                    trades = simulate_fns[key](df)
                    summary = summarize_lab(trades)
                    summary["recent_trades"] = trades[-RECENT_TRADES_KEEP:]
                    by_strategy[key] = summary
                except Exception:
                    logger.exception("실험실 성적 계산 실패: %s %s %s", symbol, timeframe, key)
                    by_strategy[key] = {"error": "계산 실패 (서버 로그 확인)"}

            by_strategy["_bars"] = len(df) if df is not None else 0
            by_strategy["_range"] = (
                {"start": str(df.index[0]), "end": str(df.index[-1])} if df is not None and not df.empty else None
            )
            stats[symbol][timeframe] = by_strategy

    result = {
        "catalog": entries,
        "stats": stats,
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": "켈트너 전략(#1)만 자동매매 화이트리스트 대상 - 나머지 7개는 비교용 후보임",
        },
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAB_STATS_FILE.write_text(json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    logger.info("실험실 성적 저장 완료: %s", LAB_STATS_FILE)
    return result
