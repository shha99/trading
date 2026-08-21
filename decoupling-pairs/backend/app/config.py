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
INDEX_PATH = DATA_DIR / "index.parquet"        # KOSPI/KOSDAQ 지수 종가 (index=date, columns=KOSPI,KOSDAQ)
SECTOR_ETF_PRICES_PATH = DATA_DIR / "sector_etf_prices.parquet"  # 섹터/ETF 비교 탭 - ETF 종가 패널
SECTOR_ETF_META_PATH = DATA_DIR / "sector_etf_meta.parquet"      # 섹터/ETF 비교 탭 - ETF 메타(테마명 등)
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

# 전종목 재수집(느림, 수 분)을 제한하는 하루 1회 배치성 새로고침
MANUAL_REFRESH_MIN_INTERVAL_HOURS = 20

# 화면에 지금 보이는 종목만 즉시 재조회하는 가벼운 "빠른 새로고침"의 최소 간격(초)
QUICK_REFRESH_MIN_INTERVAL_SECONDS = 60

# 스케줄러: 매 거래일 장마감 정산 데이터 안정화를 고려해 16:30 KST 실행
SCHEDULE_HOUR_KST = 16
SCHEDULE_MINUTE_KST = 30

# 지수 역행 종목 판정 기준 (개별 종목과 소속 시장지수의 상관계수가 이보다 낮으면 경고 태그)
INDEX_REVERSAL_CORR_THRESHOLD = -0.7
INDEX_CORR_LOOKBACK_DAYS = 250

# 저변동성(경기방어주) 오탐 필터 - 연환산 변동성 하위 N% 기본 제외(슬라이더로 조절 가능)
DEFAULT_MIN_VOLATILITY_PCTL = 0.20
# 4: 변동성 상한선 - 연환산 변동성 상위 5%도 기본 제외(급등락/이상치 종목)
DEFAULT_MAX_VOLATILITY_PCTL = 0.95
TRADING_DAYS_PER_YEAR = 252

# 4: 이상치/구조적 단절 종목 필터
# "장기 거래정지 이력" 판정: 최근 조회 구간 내 이 일수 이상 연속으로 거래량이 0/결측이면 이력으로 간주.
LONG_HALT_MIN_CONSECUTIVE_DAYS = 5
LONG_HALT_LOOKBACK_DAYS = 250
# 변동성 집중도: 절대값 기준 상위 K거래일이 전체 수익률 분산(제곱합)에서 차지하는 비중.
CONCENTRATION_TOP_K_DAYS = 5
CONCENTRATION_THRESHOLD = 0.50

# 다중검정(FDR) 보정 유의수준
FDR_ALPHA = 0.05
# FDR/보조 통계 계산 대상으로 삼는 확장 후보 풀 크기(최종 top_n보다 넉넉히 잡아서,
# FDR 통과 페어가 top_n보다 적게 남는 경우를 대비)
CANDIDATE_POOL_MULTIPLIER = 4
CANDIDATE_POOL_MAX = 300

CORS_ORIGINS = os.environ.get(
    "DECOUPLING_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
