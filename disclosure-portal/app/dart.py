"""OpenDART 공시검색(list.json) 클라이언트 + 응답 정규화.

KIND(kind.krx.co.kr)는 공개 API가 없고 해외 서버 IP를 막아서(403) 직접 긁지 않는다.
대신 거래소 시장본부가 낸 공시(조회공시, 시장조치, 투자주의 등 KIND 공시)도 DART
목록에 함께 올라오고, 비고(rm) 필드의 "유"/"코"/"넥" 표시로 구분된다 — 이걸
`is_exchange`로 노출해 KIND 공시를 따로 볼 수 있게 한다.
"""
from __future__ import annotations

from typing import Any

import requests

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
PAGE_COUNT = 100  # OpenDART 최대값

MARKET_BY_CLS = {"Y": "유가", "K": "코스닥", "N": "코넥스", "E": "기타"}

# 비고(rm) 필드의 한 글자 표시 — OpenDART 개발가이드 기준
RM_FLAGS = {
    "유": "유가증권시장본부",
    "코": "코스닥시장본부",
    "넥": "코넥스시장본부",
    "채": "채권상장법인",
    "공": "공정위",
    "연": "연결부분 포함",
    "정": "정정 제출 예정",
    "철": "철회",
}
EXCHANGE_FLAGS = {"유", "코", "넥"}

# 제목 키워드 → 태그. 순서대로 매칭, 한 공시에 여러 태그가 붙을 수 있다.
KEYWORD_TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("계약", ("단일판매", "공급계약")),
    ("증자", ("유상증자", "무상증자")),
    ("감자", ("감자결정",)),
    ("자사주", ("자기주식",)),
    ("메자닌", ("전환사채", "신주인수권부사채", "교환사채")),
    ("최대주주", ("최대주주변경", "최대주주 변경")),
    ("합병·분할", ("합병", "분할")),
    ("실적", ("영업(잠정)실적", "매출액또는손익구조")),
    ("시장조치", ("거래정지", "매매거래정지", "관리종목", "상장폐지", "불성실공시", "투자주의", "투자경고", "투자위험")),
    ("조회공시", ("조회공시",)),
    ("횡령·배임", ("횡령", "배임")),
]
# 위 태그 중 "중요 공시"로 강조할 것
IMPORTANT_TAGS = {"계약", "증자", "감자", "자사주", "메자닌", "최대주주", "합병·분할", "시장조치", "횡령·배임"}


class DartError(RuntimeError):
    pass


def tags_for(report_nm: str) -> list[str]:
    title = report_nm.replace(" ", "")
    tags = []
    for tag, words in KEYWORD_TAGS:
        if any(w.replace(" ", "") in title for w in words):
            tags.append(tag)
    return tags


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    rm = (raw.get("rm") or "").strip()
    flags = [c for c in rm if c in RM_FLAGS]
    report_nm = " ".join((raw.get("report_nm") or "").split())  # DART 제목엔 공백이 길게 끼어 있다
    tags = tags_for(report_nm)
    rcept_no = raw["rcept_no"]
    return {
        "rcept_no": rcept_no,
        "rcept_dt": raw.get("rcept_dt", rcept_no[:8]),
        "corp_code": raw.get("corp_code", ""),
        "corp_name": (raw.get("corp_name") or "").strip(),
        "stock_code": (raw.get("stock_code") or "").strip(),
        "market": MARKET_BY_CLS.get(raw.get("corp_cls", ""), "기타"),
        "report_nm": report_nm,
        "flr_nm": (raw.get("flr_nm") or "").strip(),
        "rm": rm,
        "flags": [RM_FLAGS[f] for f in flags],
        "is_exchange": any(f in EXCHANGE_FLAGS for f in flags),
        "is_correction": report_nm.startswith("[") and "정정" in report_nm.split("]")[0],
        "tags": tags,
        "important": any(t in IMPORTANT_TAGS for t in tags),
        "url": DART_VIEWER_URL.format(rcept_no=rcept_no),
    }


class DartClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, timeout: float = 15.0) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_page(self, date: str, page_no: int) -> tuple[list[dict[str, Any]], int]:
        """date(YYYYMMDD)의 공시 목록 한 페이지와 전체 페이지 수를 돌려준다(최신순)."""
        if not self.api_key:
            raise DartError("DART_API_KEY가 설정되지 않았습니다.")
        params = {
            "crtfc_key": self.api_key,
            "bgn_de": date,
            "end_de": date,
            "page_no": page_no,
            "page_count": PAGE_COUNT,
            "sort": "date",
            "sort_mth": "desc",
        }
        try:
            resp = self.session.get(DART_LIST_URL, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise DartError(f"DART 요청 실패: {exc}") from exc

        status = data.get("status")
        if status == "013":  # 조회된 데이터 없음
            return [], 0
        if status != "000":
            raise DartError(f"DART 오류 {status}: {data.get('message', '')}")
        items = [normalize(r) for r in data.get("list", [])]
        return items, int(data.get("total_page") or 1)
