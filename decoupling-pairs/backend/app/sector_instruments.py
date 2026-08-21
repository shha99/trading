"""신규 탭 "섹터/ETF 비교" - 개별 종목이 아니라 섹터 단위로 디커플링을 조회한다.

기준가격 소스 두 가지:
  1. 자체 계산 시가총액가중 섹터지수(synthetic) - 이미 보유한 전종목 시세+시총
     스냅샷으로 직접 구성한 참고용 지수. **공식 KRX/WICS 업종지수가 아니다** -
     data.krx.co.kr(pykrx가 감싸는 바로 그 서버)가 이 환경에서 국가 차단(403)돼
     있어(README "실행 환경에 대한 참고" 절 참고) 공식 지수를 가져올 방법이 없다.
     대신 같은 원리(시총가중 평균)로 직접 구성해 근사치를 제공한다.
  2. 섹터 추종 ETF - 실제 상장된 종목 코드라 naver_client.fetch_history를 그대로
     재사용해 받아온다. 실전 헤지에는 이쪽이 더 유용하다(실제 매매 가능).

두 소스 모두 correlation.scan_pairs/search_correlations가 기대하는 것과 동일한 모양의
(prices: DataFrame[date, instrument_code], meta: DataFrame[instrument_code, ...]) 쌍을
만들어내므로, 상관계수/FDR/계층적 fallback 계산 로직은 전혀 새로 만들 필요가 없다 -
이미 검증된 correlation.py를 그대로 재사용한다.
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

from . import naver_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. 섹터 추종 ETF 큐레이션 목록
# ---------------------------------------------------------------------------
# 국내 상장·비레버리지/비인버스·비합성(물리적 복제)·국내(해외 익스포저 아님) 섹터
# 추종 ETF만 손으로 검증해 선정했다(Naver의 전체 ETF 목록 ~1,160개를 업종 키워드로
# 자동 매칭하면 레버리지/인버스/합성/해외지수/채권형 상품이 대량으로 섞여 들어와
# 오히려 신뢰도가 떨어짐 - 각 티커는 실제 Naver API 응답으로 존재/이름을 확인함).
SECTOR_ETF_THEMES: dict[str, str] = {
    "091160": "반도체",
    "305720": "2차전지",
    "244580": "바이오",
    "143860": "헬스케어",
    "091180": "자동차",
    "091170": "은행",
    "102970": "증권",
    "140700": "보험",
    "228790": "화장품",
    "365000": "인터넷",
    "300950": "게임",
    "228810": "미디어콘텐츠",
    "117700": "건설",
    "117680": "철강",
    "117460": "에너지화학",
    "228800": "여행레저",
    "266410": "필수소비재",
    "266390": "경기소비재",
    "266370": "IT",
    "102960": "기계장비",
    "441540": "조선해운",
    "449450": "방산",
}


def build_etf_universe(max_workers: int = naver_client.DEFAULT_WORKERS) -> pd.DataFrame:
    """SECTOR_ETF_THEMES의 각 ETF 현재가/시총 스냅샷을 조회해 meta 형태로 반환."""
    tickers = list(SECTOR_ETF_THEMES.keys())
    # ETF는 marketValue 리스팅에서 stockEndType!="stock"으로 걸러지므로(build_universe와
    # 동일 필터) 개별 basic 엔드포인트로 이름/현재가만 확인한다 - 시총은 이 엔드포인트에
    # 없어 market_cap은 채우지 않는다(상관계수 계산에는 불필요, 정렬 참고용일 뿐).
    rows = []
    for t in tickers:
        try:
            info = naver_client._get_json(f"https://m.stock.naver.com/api/stock/{t}/basic")  # noqa: SLF001
        except naver_client.NaverUnavailableError:
            logger.warning("섹터 ETF %s 정보 조회 실패 - 건너뜀", t)
            continue
        rows.append(
            {
                "ticker": t,
                "name": info.get("stockName", t),
                "theme": SECTOR_ETF_THEMES[t],
                # 위 build_synthetic_sector_index와 같은 이유로 sector=theme(고유값)을 채운다.
                "sector": SECTOR_ETF_THEMES[t],
                "market_cap": None,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["name", "theme", "sector", "market_cap"]).rename_axis("ticker")
    return pd.DataFrame(rows).set_index("ticker")


def fetch_etf_prices(start: str, end: str, max_workers: int = naver_client.DEFAULT_WORKERS) -> dict[str, pd.DataFrame]:
    """섹터 ETF들의 일별 종가를 병렬 수집(개별 종목과 동일한 fetch_histories 재사용)."""
    return naver_client.fetch_histories(list(SECTOR_ETF_THEMES.keys()), start, end, max_workers=max_workers)


# ---------------------------------------------------------------------------
# 2. 자체 계산 시가총액가중 섹터지수(synthetic)
# ---------------------------------------------------------------------------
def build_synthetic_sector_index(prices: pd.DataFrame, meta: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """전종목 시세(prices)+메타(meta, market_cap 포함)로 섹터별 시총가중 지수를 구성한다.

    한계: 점별(point-in-time) 일별 시가총액 이력이 없어(수집 시점 스냅샷 시총만 보유),
    "가중치는 수집 시점 시총으로 고정, 일별 수익률만 가중평균" 방식으로 근사한다
    (표준 시총가중지수 구성과 원리는 같지만 가중치가 날짜별로 갱신되지 않는 단순화).
    지수 자체가 아니라 지수 "수익률"을 누적하는 방식이라 절대 레벨은 임의값(기준 100)이다.

    Returns: (sector_prices: DataFrame[date, sector_name], sector_meta: DataFrame[sector_name, ...])
    """
    if prices.empty or meta.empty or "sector" not in meta.columns:
        return pd.DataFrame(), pd.DataFrame(columns=["name", "market_cap"]).rename_axis("ticker")

    returns = np.log(prices).diff()
    weights = meta["market_cap"].reindex(prices.columns).fillna(0)

    sector_returns: dict[str, pd.Series] = {}
    sector_market_cap: dict[str, float] = {}
    for sector, tickers in meta.groupby("sector").groups.items():
        cols = [t for t in tickers if t in returns.columns]
        if len(cols) < 3:  # 구성 종목이 너무 적으면(3개 미만) 지수로서 의미가 약해 제외
            continue
        w = weights.reindex(cols).fillna(0)
        if w.sum() <= 0:
            w = pd.Series(1.0, index=cols)  # 시총 정보가 없으면 동일가중으로 대체
        r = returns[cols]
        # 그날 값이 있는 종목만으로 가중평균(결측 종목은 그날 가중치에서 제외)
        valid = r.notna()
        w_matrix = valid.mul(w, axis=1)
        weighted_sum = (r.fillna(0) * w_matrix).sum(axis=1)
        w_total = w_matrix.sum(axis=1)
        sector_ret = (weighted_sum / w_total.replace(0, np.nan))
        sector_returns[sector] = sector_ret
        sector_market_cap[sector] = float(w.sum())

    if not sector_returns:
        return pd.DataFrame(), pd.DataFrame(columns=["name", "market_cap"]).rename_axis("ticker")

    ret_df = pd.DataFrame(sector_returns)
    # 수익률을 기준 100에서 누적해 "지수" 레벨로 변환(첫 유효값부터 시작)
    level_df = np.exp(ret_df.fillna(0).cumsum()) * 100

    sector_meta = pd.DataFrame(
        {
            "name": {s: f"{s} 섹터지수(자체계산)" for s in sector_returns},
            "theme": {s: s for s in sector_returns},
            # correlation.py의 배지(cross/same sector) 계산이 "sector" 컬럼을 요구하는데,
            # 섹터 지수끼리 비교할 때는 그 개념 자체가 무의미하다(자기 자신과만 같음) -
            # 각자 고유값을 넣어 항상 cross_sector가 나오게만 하고 UI에서는 표시하지 않는다.
            "sector": {s: s for s in sector_returns},
            "market_cap": sector_market_cap,
        }
    ).rename_axis("ticker")
    level_df.columns.name = None
    return level_df, sector_meta
