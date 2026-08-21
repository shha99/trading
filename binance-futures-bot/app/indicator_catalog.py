"""TA-Lib 160종 + 커스텀 5종 = 165종 지표 카탈로그.

TA-Lib 함수를 하나하나 손으로 감싸지 않고, `talib.get_function_groups()` +
`talib.abstract.Function`의 리플렉션으로 카탈로그(이름/카테고리/파라미터
기본값/출력 이름)를 자동 생성한다. 오버레이(가격창)/보조창 분류와 캔들
패턴 여부도 TA-Lib이 함수마다 제공하는 `function_flags` 메타데이터로
프로그래밍적으로 판단한다 — 직접 확인:

  - 'Output scale same as input' 플래그가 있으면 가격과 같은 스케일이라
    overlay(가격창에 겹침). Overlap Studies 그룹(EMA/SMA/BBANDS/SAR 등)
    18개 전부가 이 플래그를 갖고 있다.
  - 'Output is a candlestick' 플래그가 있으면 캔들 패턴(-200~200 정수
    신호)이다. Pattern Recognition 그룹 61개 전부 이 플래그를 갖는다.
  - 그 외(Momentum/Volatility/Volume/Cycle/Statistic/Math)는 subpane
    (보조창)에 그린다.

`MAVP`(가변 기간 이동평균)는 기간을 배열로 받아야 해서 "기간 하나 입력"
파라미터 UI에 맞지 않아 카탈로그에서 제외한다 (친구분 원본도 165종 집계에
이 함수가 빠져 있는 것으로 보인다 — 나머지 개수가 정확히 일치함).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import talib
from talib import abstract

from .custom_indicators import CUSTOM_INDICATORS

EXCLUDED_TALIB_FUNCTIONS = {"MAVP"}

CATEGORY_LABELS_KO = {
    "Overlap Studies": "이동평균·추세선",
    "Momentum Indicators": "모멘텀",
    "Volatility Indicators": "변동성",
    "Volume Indicators": "거래량",
    "Cycle Indicators": "사이클",
    "Statistic Functions": "통계",
    "Price Transform": "가격 변환",
    "Pattern Recognition": "캔들 패턴",
    "Math Operators": "수학 함수",
    "Math Transform": "수학 함수",
}

_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _talib_entry(name: str) -> dict:
    fn = abstract.Function(name)
    info = fn.info
    flags = info.get("function_flags") or []
    is_pattern = "Output is a candlestick" in flags
    same_scale = "Output scale same as input" in flags
    pane = "overlay" if (same_scale and not is_pattern) else "subpane"
    return {
        "id": name,
        "label": info.get("display_name") or name,
        "group": info["group"],
        "category": CATEGORY_LABELS_KO.get(info["group"], info["group"]),
        "pane": pane,
        "is_pattern": is_pattern,
        "params": dict(info["parameters"]),
        "output_names": list(info["output_names"]),
        "source": "talib",
    }


def _custom_entry(cid: str, meta: dict) -> dict:
    return {
        "id": cid,
        "label": meta["label"],
        "group": "Custom",
        "category": "커스텀",
        "pane": "overlay",
        "is_pattern": False,
        "params": dict(meta["params"]),
        "output_names": list(meta["outputs"]),
        "source": "custom",
    }


_catalog_cache: list[dict] | None = None


def build_catalog() -> list[dict]:
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    entries: list[dict] = []
    for group_names in talib.get_function_groups().values():
        for name in group_names:
            if name in EXCLUDED_TALIB_FUNCTIONS:
                continue
            entries.append(_talib_entry(name))
    for cid, meta in CUSTOM_INDICATORS.items():
        entries.append(_custom_entry(cid, meta))

    entries.sort(key=lambda e: (e["category"], e["id"]))
    _catalog_cache = entries
    return entries


def get_indicator_meta(indicator_id: str) -> dict:
    for entry in build_catalog():
        if entry["id"] == indicator_id:
            return entry
    raise KeyError(f"알 수 없는 지표 id: {indicator_id}")


def _talib_inputs(df: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        "open": df["Open"].to_numpy(dtype=float),
        "high": df["High"].to_numpy(dtype=float),
        "low": df["Low"].to_numpy(dtype=float),
        "close": df["Close"].to_numpy(dtype=float),
        "volume": df["Volume"].to_numpy(dtype=float),
    }


def compute_indicator(indicator_id: str, df: pd.DataFrame, params: dict | None = None) -> dict[str, pd.Series]:
    """지표 하나를 계산해 {출력이름: Series(인덱스=df.index)}로 반환한다."""
    if not set(_REQUIRED_COLUMNS).issubset(df.columns):
        raise ValueError(f"df에 필요한 컬럼이 없습니다: {_REQUIRED_COLUMNS}")

    meta = get_indicator_meta(indicator_id)
    merged_params = dict(meta["params"])
    merged_params.update(params or {})

    if meta["source"] == "custom":
        result = CUSTOM_INDICATORS[indicator_id]["compute"](df, **merged_params)
        return {name: pd.Series(values, index=df.index) for name, values in result.items()}

    fn = abstract.Function(indicator_id)
    raw = fn(_talib_inputs(df), **merged_params)
    if isinstance(raw, (list, tuple)):
        outputs = raw
    else:
        outputs = [raw]
    return {name: pd.Series(values, index=df.index) for name, values in zip(meta["output_names"], outputs)}
