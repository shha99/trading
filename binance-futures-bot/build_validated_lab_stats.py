"""켈트너 전략과 동급(학습/검증/연도별 분리)으로 검증된 lab 후보들의
백테스트를 다시 계산해 data/validated_lab_stats.json에 저장하는 CLI.
계산 로직은 app/validated_lab_stats_builder.py에 있다.

지금 대상: big_candle_bollinger_confluence(BTC/ETH 1h),
bollinger_wick_breakeven_trail(BTC/ETH 15m·5m). 15분/5분봉도 3년 이상
데이터를 받아오기 때문에(특히 5분봉) 시간이 좀 걸릴 수 있다.

사용법:
    python build_validated_lab_stats.py
"""
from __future__ import annotations

import logging

from app.validated_lab_stats_builder import build_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    result = build_all()
    for key, by_symbol in result.items():
        if key == "_meta":
            continue
        for symbol, by_tf in by_symbol.items():
            for timeframe, r in by_tf.items():
                if "error" in r:
                    print(f"{key} {symbol} {timeframe}: 실패 - {r['error']}")
                    continue
                o, tr, va = r["overall"], r["train"], r["validation"]
                print(
                    f"{key} {symbol} {timeframe} ({r['bars']}봉, {r['range']['start']}~{r['range']['end']})\n"
                    f"  전체: {o.get('trades', 0)}건 승률 {o.get('win_rate', 0)*100:.1f}% 거래당 {o.get('avg_pct_per_trade', 0):+.3f}%\n"
                    f"  학습: {tr.get('trades', 0)}건 승률 {tr.get('win_rate', 0)*100:.1f}% 거래당 {tr.get('avg_pct_per_trade', 0):+.3f}%\n"
                    f"  검증: {va.get('trades', 0)}건 승률 {va.get('win_rate', 0)*100:.1f}% 거래당 {va.get('avg_pct_per_trade', 0):+.3f}%"
                )


if __name__ == "__main__":
    main()
