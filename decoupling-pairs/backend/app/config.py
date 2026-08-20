"""전역 설정 값 모음.

경로, 캐시 파일명, 통계/필터 관련 기본값을 한곳에 모아둔다.
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DECOUPLING_DATA_DIR", BACKEND_DIR / "data" / "cache"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

PRICES_PATH = DATA_DIR / "prices.parquet"      # 일별 종가 패널 (index=date, columns=ticker)
VALUE_PATH = DATA_DIR / "trading_value.parquet"  # 일별 거래대금 패널 (index=date, columns=ticker)
META_PATH = DATA_DIR / "meta.parquet"          # 종목 메타 스냅샷 (index=ticker)
STATE_PATH = DATA_DIR / "state.json"           # 마지막 갱신일, 수동 새로고침 이력 등

# ---------------------------------------------------------------------------
# 통계/필터 기본값
# ---------------------------------------------------------------------------
MIN_RELIABLE_TRADING_DAYS = 20  # 이보다 표본이 짧으면 신뢰도 경고
DEFAULT_TOP_N = 20
MAX_TOP_N = 50
MIN_TOP_N = 5

# 유동성 하위 컷 (최근 거래대금 기준 하위 20% 기본 제외)
DEFAULT_LIQUIDITY_EXCLUDE_PCTL = 0.20
LIQUIDITY_LOOKBACK_DAYS = 20  # "최근 거래대금" 산정에 사용할 최근 거래일 수

# 백필 시 기본으로 확보해둘 과거 데이터 기간(연 단위) - 최초 캐시 구축 시 사용
DEFAULT_BACKFILL_YEARS = 3

# 하루 1회로 제한하는 수동 새로고침
MANUAL_REFRESH_MIN_INTERVAL_HOURS = 20

# 스케줄러: 매 거래일 장마감 정산 데이터 안정화를 고려해 16:30 KST 실행
SCHEDULE_HOUR_KST = 16
SCHEDULE_MINUTE_KST = 30

CORS_ORIGINS = os.environ.get(
    "DECOUPLING_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
