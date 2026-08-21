"""TA-Lib에 없는 커스텀 지표 5종: VWAP · Supertrend · 일목균형표 · 돈치안 채널
· 켈트너 채널. 전부 가격창에 겹쳐 그리는(overlay) 지표다.

각 compute 함수는 df(Open/High/Low/Close/Volume, 오래된->최신 순)와
파라미터를 받아 {출력이름: pd.Series} 딕셔너리를 반환한다 — indicator_catalog.py의
TA-Lib 지표와 같은 모양이라 프론트가 두 종류를 구분 없이 다룰 수 있다.
"""
from __future__ import annotations

import pandas as pd

from .indicators import atr, keltner_channel


def vwap(df: pd.DataFrame, **_params) -> dict[str, pd.Series]:
    """세션(UTC 하루) 기준 VWAP — 하루가 바뀌면 누적을 초기화한다."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    tpv = typical * df["Volume"]
    day = df.index.normalize()
    cum_tpv = tpv.groupby(day).cumsum()
    cum_vol = df["Volume"].groupby(day).cumsum()
    return {"vwap": cum_tpv / cum_vol.replace(0, pd.NA)}


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> dict[str, pd.Series]:
    """ATR 기반 추세추종 밴드. supertrend 값 + 추세방향(1=상승/-1=하락)을 반환."""
    hl2 = (df["High"] + df["Low"]) / 2.0
    band_width = atr(df, period) * multiplier
    basic_upper = hl2 + band_width
    basic_lower = hl2 - band_width
    close = df["Close"]

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = pd.Series(1, index=df.index, dtype=int)

    for i in range(1, len(df)):
        prev_upper, prev_lower = final_upper.iloc[i - 1], final_lower.iloc[i - 1]

        # ATR 워밍업 중(prev가 아직 NaN)이면 그냥 이번 봉의 basic 값을 채택한다.
        # (NaN과의 비교는 항상 False라서, 그대로 두면 NaN이 영원히 이어짐)
        if pd.isna(prev_upper) or (basic_upper.iloc[i] < prev_upper or close.iloc[i - 1] > prev_upper):
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = prev_upper

        if pd.isna(prev_lower) or (basic_lower.iloc[i] > prev_lower or close.iloc[i - 1] < prev_lower):
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = prev_lower

        if pd.isna(prev_upper) and pd.isna(prev_lower):
            trend.iloc[i] = trend.iloc[i - 1]
        elif close.iloc[i] > (prev_upper if not pd.isna(prev_upper) else final_upper.iloc[i]):
            trend.iloc[i] = 1
        elif close.iloc[i] < (prev_lower if not pd.isna(prev_lower) else final_lower.iloc[i]):
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i - 1]

    st = final_lower.where(trend == 1, final_upper)
    return {"supertrend": st, "trend": trend.astype(float)}


def ichimoku(
    df: pd.DataFrame, tenkan_period: int = 9, kijun_period: int = 26,
    senkou_b_period: int = 52, displacement: int = 26,
) -> dict[str, pd.Series]:
    """일목균형표: 전환선/기준선/선행스팬A·B/후행스팬."""
    high, low, close = df["High"], df["Low"], df["Close"]

    def donchian_mid(period: int) -> pd.Series:
        return (high.rolling(period).max() + low.rolling(period).min()) / 2.0

    tenkan = donchian_mid(tenkan_period)
    kijun = donchian_mid(kijun_period)
    senkou_a = ((tenkan + kijun) / 2.0).shift(displacement)
    senkou_b = donchian_mid(senkou_b_period).shift(displacement)
    chikou = close.shift(-displacement)

    return {
        "tenkan": tenkan, "kijun": kijun,
        "senkou_a": senkou_a, "senkou_b": senkou_b, "chikou": chikou,
    }


def donchian_channel(df: pd.DataFrame, period: int = 20) -> dict[str, pd.Series]:
    upper = df["High"].rolling(period).max()
    lower = df["Low"].rolling(period).min()
    return {"upper": upper, "lower": lower, "middle": (upper + lower) / 2.0}


def keltner(
    df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0
) -> dict[str, pd.Series]:
    return keltner_channel(df, ema_period=ema_period, atr_period=atr_period, mult=mult)


# id -> (라벨, 계산함수, 기본 파라미터, 출력 이름들)
CUSTOM_INDICATORS: dict[str, dict] = {
    "VWAP": {"label": "VWAP", "compute": vwap, "params": {}, "outputs": ["vwap"]},
    "SUPERTREND": {
        "label": "Supertrend", "compute": supertrend,
        "params": {"period": 10, "multiplier": 3.0}, "outputs": ["supertrend", "trend"],
    },
    "ICHIMOKU": {
        "label": "Ichimoku (일목균형표)", "compute": ichimoku,
        "params": {"tenkan_period": 9, "kijun_period": 26, "senkou_b_period": 52, "displacement": 26},
        "outputs": ["tenkan", "kijun", "senkou_a", "senkou_b", "chikou"],
    },
    "DONCHIAN": {
        "label": "Donchian Channel (돈치안)", "compute": donchian_channel,
        "params": {"period": 20}, "outputs": ["upper", "lower", "middle"],
    },
    "KELTNER": {
        "label": "Keltner Channel (켈트너)", "compute": keltner,
        "params": {"ema_period": 20, "atr_period": 10, "mult": 2.0}, "outputs": ["upper", "middle", "lower"],
    },
}
