"""심볼×시간대별 백테스트 성적을 다시 계산해 data/strategy_stats.json에
저장하는 CLI. 실제 계산 로직은 app/stats_builder.py에 있다.

주의: 시간대(15m/1h/4h/1d) x 심볼(BTC/ETH) 조합마다 몇 년치 데이터를
1500봉씩 나눠 받아오기 때문에 시간이 좀 걸린다(대략 1~5분, 레이트리밋
대기 포함). 전략 페이지가 참조하는 파일을 새로 만드는 것뿐이라, 서버가
켜져 있는 동안 실행해도 안전하다(다 끝난 뒤 파일이 갱신됨).

사용법:
    python build_stats.py
    python build_stats.py --symbols BTCUSDT --timeframes 1h,4h
"""
from __future__ import annotations

import argparse
import logging

from app.stats_builder import build_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", help="콤마 구분, 기본값은 .env의 SYMBOLS")
    parser.add_argument("--timeframes", help="콤마 구분, 기본값은 .env의 DASHBOARD_TIMEFRAMES")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    timeframes = [t.strip() for t in args.timeframes.split(",")] if args.timeframes else None

    stats = build_all(symbols=symbols, timeframes=timeframes)
    for symbol, by_tf in stats.items():
        if symbol == "_meta":
            continue
        for timeframe, result in by_tf.items():
            if "error" in result:
                print(f"{symbol} {timeframe}: 실패 - {result['error']}")
                continue
            overall = result["overall"]
            print(f"{symbol} {timeframe}: {result['bars']}봉, 전체 {overall.get('trades', 0)}건 "
                  f"(검증구간 {result['validation'].get('trades', 0)}건, "
                  f"검증 total_r={result['validation'].get('total_r', 'n/a')})")


if __name__ == "__main__":
    main()
