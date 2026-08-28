"""4종목(BTC/ETH × 15분/5분봉) 동시 스크리닝 백테스트용 거래 원장을 다시
계산해 data/multi_screen_trades.json에 저장하는 CLI. 계산 로직은
app/multi_screen_backtest.py에 있다.

4개 조합 각각 3년 이상 데이터를 페이지네이션으로 받아오기 때문에 시간이
좀 걸릴 수 있다 (특히 5분봉).

사용법:
    python build_multi_screen_trades.py
"""
from __future__ import annotations

import logging

from app.multi_screen_backtest import build_merged_trades

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    result = build_merged_trades()
    meta = result["_meta"]
    print(f"거래 원장 생성 완료: {len(result['trades'])}건 (수수료 {meta['taker_fee_pct_roundtrip']}%)")
    for combo, info in meta["per_combo"].items():
        if "error" in info:
            print(f"  {combo}: 실패 - {info['error']}")
            continue
        print(f"  {combo}: {info['trades']}건 ({info['bars']}봉, {info['range']['start']}~{info['range']['end']})")


if __name__ == "__main__":
    main()
