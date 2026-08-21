"""Naver 데이터 수집 + 정적 아티팩트 빌드를 한 번에 실행하는 스크립트.

GitHub Actions의 매일 스케줄 워크플로우(.github/workflows/refresh-data.yml)가
이 스크립트 하나만 실행하면 naver_cache/*.json을 새로 받아 vercel-static/index.html까지
갱신한다. backend/app/naver_client.py의 검증된 수집/보정 로직(ETF·인버스·레버리지
제외, ±30% 상하한 기준 액면분할/감자 역보정 등)을 그대로 재사용하므로, 백엔드와
정적 아티팩트가 서로 다른 로직으로 갈라지지 않는다.

사용법:
    cd static-artifact
    python3 collect_and_build.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent
REPO_ROOT = STATIC_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app import naver_client  # noqa: E402

CACHE_DIR = STATIC_DIR / "naver_cache"
HIST_DIR = CACHE_DIR / "history"
HISTORY_DAYS = 400  # 400일 확보해두면 "전체 기간(상장일~현재)" 옵션도 대부분 커버됨


def _clean(v):
    """NaN/None을 JSON에 안전한 None으로."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    return v


def main() -> None:
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    end = datetime.now()
    start = end - timedelta(days=HISTORY_DAYS)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    print("[1/5] 업종/유니버스 수집 중 (ETF·ETN·인버스·레버리지 제외)...")
    universe_df = naver_client.build_universe()
    print(f"  -> {len(universe_df)}종목")

    universe_out = {
        ticker: {
            "name": row["name"],
            "market": row["market"],
            "sector": row["sector"],
            "market_cap": _clean(row["market_cap"]),
        }
        for ticker, row in universe_df.iterrows()
    }
    (CACHE_DIR / "universe.json").write_text(
        json.dumps(universe_out, ensure_ascii=False), encoding="utf-8"
    )

    tickers = list(universe_out.keys())
    print(f"[2/5] {len(tickers)}종목 일별 시세 수집 중 (최근 {HISTORY_DAYS}일, 액면분할/감자 자동 보정 포함)...")
    histories = naver_client.fetch_histories(tickers, start_s, end_s)
    for ticker, df in histories.items():
        rows = [
            {"date": idx.strftime("%Y-%m-%d"), "close": _clean(float(c)), "volume": _clean(float(v) if v is not None else 0.0)}
            for idx, c, v in zip(df.index, df["close"], df["volume"])
        ]
        (HIST_DIR / f"{ticker}.json").write_text(json.dumps(rows), encoding="utf-8")
    failed = len(tickers) - len(histories)
    print(f"  -> 완료 (수집 실패/데이터 없음 {failed}개)")

    print("[3/5] 지수(KOSPI/KOSDAQ) 시세 수집 중...")
    index_out: dict[str, list] = {}
    for idx_name in ("KOSPI", "KOSDAQ"):
        s = naver_client.fetch_index_history(idx_name, start_s, end_s)
        index_out[idx_name] = [[d.strftime("%Y%m%d"), float(c)] for d, c in s.items()]
    (CACHE_DIR / "index_history.json").write_text(
        json.dumps(index_out, ensure_ascii=False), encoding="utf-8"
    )

    print("[4/5] 정적 아티팩트 빌드 중 (decoupling-demo.html)...")
    sys.path.insert(0, str(STATIC_DIR))
    import build_real_artifact  # noqa: E402

    build_real_artifact.main()

    print("[5/5] vercel-static/index.html 갱신 중...")
    built_html = (STATIC_DIR / "decoupling-demo.html").read_text(encoding="utf-8")
    (REPO_ROOT / "vercel-static" / "index.html").write_text(built_html, encoding="utf-8")
    print("완료.")


if __name__ == "__main__":
    main()
