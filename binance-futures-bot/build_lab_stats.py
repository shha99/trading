"""전략 실험실(켈트너 1 + 후보 7 = 8종)의 심볼×시간대별 성적을 다시
계산해 data/lab_stats.json에 저장하는 CLI. 계산 로직은
app/lab_stats_builder.py에 있다.

사용법:
    python build_lab_stats.py
    python build_lab_stats.py --symbols BTCUSDT --timeframes 1h,4h
"""
from __future__ import annotations

import argparse
import logging

from app.lab_stats_builder import build_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="콤마 구분, 기본값은 .env의 SYMBOLS")
    parser.add_argument("--timeframes", help="콤마 구분, 기본값은 .env의 DASHBOARD_TIMEFRAMES")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    timeframes = [t.strip() for t in args.timeframes.split(",")] if args.timeframes else None

    result = build_all(symbols=symbols, timeframes=timeframes)
    for symbol, by_tf in result["stats"].items():
        for timeframe, by_strategy in by_tf.items():
            bars = by_strategy.get("_bars", 0)
            print(f"\n{symbol} {timeframe} ({bars}봉)")
            for entry in result["catalog"]:
                s = by_strategy.get(entry["key"], {})
                if "error" in s:
                    print(f"  {entry['label']:24s} 실패 - {s['error']}")
                else:
                    print(
                        f"  {entry['label']:24s} {s.get('trades', 0):4d}건  "
                        f"거래당 {s.get('avg_pct_per_trade', 0):+.2f}%"
                    )


if __name__ == "__main__":
    main()
