"""전략에 필요한 최소 지표만 pandas로 직접 계산한다 (TA-Lib 불필요).

- ema: 추세 필터(200EMA)와 켈트너 채널의 중심선에 사용.
- atr: 변동성 측정, 켈트너 채널 폭 + 손절/익절 폭 계산에 사용.
- keltner_lower: 켈트너 채널 하단 (중심선 - ATR*배수).
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    # Wilder 방식 스무딩 (RSI와 동일한 관례 - 기존 저장소 RSIStrategy 참고)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def keltner_lower(
    df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0
) -> pd.Series:
    mid = ema(df["Close"], ema_period)
    width = atr(df, atr_period) * mult
    return mid - width


def keltner_channel(
    df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0
) -> dict[str, pd.Series]:
    """켈트너 채널 상단/중단/하단 (차트 대시보드의 커스텀 지표용).

    전략(strategy.py)은 하단만 필요해 keltner_lower를 그대로 쓰지만,
    지표 카탈로그(indicator_catalog.py)는 세 선을 다 그려야 해서 이 함수를
    쓴다 — 내부적으로 같은 mid/width 계산을 공유한다.
    """
    mid = ema(df["Close"], ema_period)
    width = atr(df, atr_period) * mult
    return {"upper": mid + width, "middle": mid, "lower": mid - width}


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def bollinger_bands(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> dict[str, pd.Series]:
    """볼린저 밴드 상/중/하단 (전략 실험실의 여러 볼린저 계열 전략에서 공용으로 씀)."""
    mid = sma(df["Close"], period)
    width = df["Close"].rolling(period).std(ddof=0) * num_std
    return {"upper": mid + width, "middle": mid, "lower": mid - width}


def donchian(df: pd.DataFrame, period: int = 20) -> dict[str, pd.Series]:
    """돈치안 채널 상/하단 (저항선/지지선으로 취급 - 전략 실험실 6/7번에 사용).

    직전 봉까지의 고점/저점만 봐야 "이번 봉이 그 저항/지지에 부딪혔는지"를
    판단할 수 있어서, shift(1)로 당겨 이번 봉 자신은 포함하지 않는다.
    """
    upper = df["High"].rolling(period).max().shift(1)
    lower = df["Low"].rolling(period).min().shift(1)
    return {"upper": upper, "lower": lower}
