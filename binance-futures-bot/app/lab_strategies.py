"""전략 실험실(lab)의 후보 전략 11종.

`strategy.py`의 `KeltnerReclaimStrategy`(이미 검증돼 자동매매에 쓰이는 유일한
전략)와 달리, 여기 있는 건 전부 **비교/탐색용 후보**다. 자동매매
화이트리스트에는 절대 안 올라가고, `/lab` 페이지에서 심볼×시간대별 성적을
구경하는 용도로만 쓴다.

카드에 적힌 한 줄 설명만으로는 진입/청산의 정확한 숫자가 없어서, 아래
숫자는 전부 이 구현에서 합리적으로 채운 기본값이다(설명은 각 클래스
docstring에). 다른 값을 원하면 여기 상수만 바꾸면 된다.

각 전략은 `precompute(df)`(지표를 벡터로 한 번만 계산)와
`check_entry(k, ctx)`(봉 k에서 진입 신호 판정)만 구현하면 되고, 진입 이후
청산(고정 손절/익절, 동적 밴드 익절, 동적 손절선, ATR 트레일링 스탑,
시간손절)은 `lab_backtest.py`가 공통으로 처리한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import talib

from .custom_indicators import ichimoku
from .indicators import atr, bollinger_bands, donchian, ema, rsi


class LabStrategy:
    key: str = ""
    label: str = ""
    category: str = ""
    description: str = ""
    designed_timeframe: str = "4h"  # 이 규칙을 만들 때 기준으로 삼은 시간대 (경고 배너용)
    min_bars: int = 30

    def precompute(self, df: pd.DataFrame) -> dict:
        raise NotImplementedError

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        raise NotImplementedError


def _isnan_any(*values: float) -> bool:
    return any(np.isnan(v) for v in values)


class BigCandleBreakoutStrategy(LabStrategy):
    """큰 양봉 돌파 — 추세 추종.

    상승 추세(종가>50EMA)에서, 최근 20봉 평균 몸통 대비 2배 이상 큰 양봉이
    나오면 그 종가에 매수. 손절/고정 익절 없이 트레일링 스탑(고점 - 3×ATR)
    으로 추세를 끝까지 태운다.
    """

    key = "big_candle_breakout"
    label = "큰 양봉 돌파"
    category = "추세 추종"
    description = "상승 추세에서 평소보다 훨씬 큰 양봉이 나오면 따라 사고, 손절선을 올려가며 끌고 감"

    def __init__(self, trend_ema_period=50, body_avg_window=20, body_mult=2.0, atr_period=14, trail_mult=3.0):
        self.trend_ema_period = trend_ema_period
        self.body_avg_window = body_avg_window
        self.body_mult = body_mult
        self.atr_period = atr_period
        self.trail_mult = trail_mult
        self.min_bars = max(trend_ema_period, body_avg_window, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        body = (df["Close"] - df["Open"])
        avg_body = body.abs().rolling(self.body_avg_window).mean().shift(1)
        return {
            "ema_trend": ema(df["Close"], self.trend_ema_period).to_numpy(),
            "avg_body": avg_body.to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
            "body": body.to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        ema_trend, avg_body = ctx["ema_trend"][k], ctx["avg_body"][k]
        if _isnan_any(ema_trend, avg_body):
            return None
        body = ctx["body"][k]
        if ctx["close"][k] > ema_trend and body > 0 and body >= self.body_mult * avg_body:
            return {"direction": "LONG", "entry_price": ctx["close"][k], "trailing": True, "trail_mult": self.trail_mult}
        return None


class SharpDropBounceStrategy(LabStrategy):
    """급락 후 첫 반등 — 급락 매수(반등 노림).

    상승 추세(종가>200EMA) 유지 중, 직전 봉이 1.5×ATR 이상 급락한 뒤 처음
    나오는 양봉에서 매수. 손절 -2ATR / 익절 +3ATR / 2일 시간손절.
    """

    key = "sharp_drop_bounce"
    label = "급락 후 첫 반등"
    category = "급락 매수 (반등 노림)"
    description = "상승 추세가 유지되는 중에 짧게 급락한 뒤, 처음 나오는 양봉에서 매수"

    def __init__(self, trend_ema_period=200, atr_period=14, drop_mult=1.5, stop_mult=2.0, target_mult=3.0, time_stop_days=2):
        self.trend_ema_period = trend_ema_period
        self.atr_period = atr_period
        self.drop_mult = drop_mult
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.time_stop_days = time_stop_days
        self.min_bars = max(trend_ema_period, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        return {
            "ema_trend": ema(df["Close"], self.trend_ema_period).to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        ema_trend, atr_prev, atr_now = ctx["ema_trend"][k], ctx["atr"][k - 1], ctx["atr"][k]
        if _isnan_any(ema_trend, atr_prev, atr_now):
            return None
        close, open_ = ctx["close"], ctx["open_"]
        trend_ok = close[k] > ema_trend
        sharp_drop = (open_[k - 1] - close[k - 1]) >= self.drop_mult * atr_prev
        bullish_now = close[k] > open_[k]
        if trend_ok and sharp_drop and bullish_now:
            entry = float(close[k])
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "target_price": entry + self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        return None


class BollingerReversionStrategy(LabStrategy):
    """볼린저 하단 매수 → 상단 매도 — 밴드 되돌림(역추세).

    직전 종가가 볼린저 하단 이하였다가 이번 종가가 다시 하단 위로
    올라오면 매수, 상단에 닿으면 매도. 손절은 -2ATR, 3일 시간손절.
    """

    key = "bollinger_reversion"
    label = "볼린저 하단 매수 → 상단 매도"
    category = "밴드 되돌림 (역추세)"
    description = "볼린저 밴드 하단 아래로 밀렸다가 다시 올라오면 매수하고, 밴드 상단에 닿으면 매도"

    def __init__(self, bb_period=20, bb_std=2.0, atr_period=14, stop_mult=2.0, time_stop_days=3):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.time_stop_days = time_stop_days
        self.min_bars = max(bb_period, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        bb = bollinger_bands(df, self.bb_period, self.bb_std)
        return {
            "bb_lower": bb["lower"].to_numpy(),
            "bb_upper": bb["upper"].to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        lower_prev, lower_now, atr_now = ctx["bb_lower"][k - 1], ctx["bb_lower"][k], ctx["atr"][k]
        if _isnan_any(lower_prev, lower_now, atr_now):
            return None
        close = ctx["close"]
        if close[k - 1] <= lower_prev and close[k] > lower_now:
            entry = float(close[k])
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "dynamic_target_key": "bb_upper",
                "time_stop_days": self.time_stop_days,
            }
        return None


class BollingerBreakoutStrategy(LabStrategy):
    """볼린저 돌파 롱/숏 — 밴드 돌파(양방향).

    종가가 상단을 갓 뚫으면 롱, 하단을 갓 깨면 숏. 손절 1×ATR, 익절
    4×ATR(크게 먹고 짧게 끊는 비율).
    """

    key = "bollinger_breakout"
    label = "볼린저 돌파 롱/숏"
    category = "밴드 돌파 (양방향)"
    description = "볼린저 상단을 뚫으면 롱, 하단을 깨면 숏. 손절 1 : 익절 4 비율로 크게 먹고 짧게 끊는다"

    def __init__(self, bb_period=20, bb_std=2.0, atr_period=14, stop_mult=1.0, target_mult=4.0):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.min_bars = max(bb_period, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        bb = bollinger_bands(df, self.bb_period, self.bb_std)
        return {
            "bb_lower": bb["lower"].to_numpy(),
            "bb_upper": bb["upper"].to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        upper_prev, upper_now = ctx["bb_upper"][k - 1], ctx["bb_upper"][k]
        lower_prev, lower_now = ctx["bb_lower"][k - 1], ctx["bb_lower"][k]
        atr_now = ctx["atr"][k]
        if _isnan_any(upper_prev, upper_now, lower_prev, lower_now, atr_now):
            return None
        close = ctx["close"]
        entry = float(close[k])
        if close[k - 1] <= upper_prev and close[k] > upper_now:
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "target_price": entry + self.target_mult * atr_now,
            }
        if close[k - 1] >= lower_prev and close[k] < lower_now:
            return {
                "direction": "SHORT", "entry_price": entry,
                "stop_price": entry + self.stop_mult * atr_now,
                "target_price": entry - self.target_mult * atr_now,
            }
        return None


class ResistanceBreakFailStrategy(LabStrategy):
    """저항선 돌파/실패 — 저항 대응(양방향).

    최근 20봉 고점(직전까지)을 저항선으로 본다. 종가가 갓 뚫으면 롱(돌파),
    고가로 닿았지만 종가가 못 넘으면 숏(실패/거부). 손절 -2ATR, 익절
    +3ATR, 2일 시간손절.
    """

    key = "resistance_break_fail"
    label = "저항선 돌파/실패"
    category = "저항 대응 (양방향)"
    description = "평행·대각 저항선에 부딪힌 다음 봉이 종가로 뚫으면 롱, 못 뚫고 막히면 숏"

    def __init__(self, donchian_period=20, atr_period=14, stop_mult=2.0, target_mult=3.0, time_stop_days=2):
        self.donchian_period = donchian_period
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.time_stop_days = time_stop_days
        self.min_bars = max(donchian_period, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        d = donchian(df, self.donchian_period)
        return {"resistance": d["upper"].to_numpy(), "atr": atr(df, self.atr_period).to_numpy()}

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        res_prev, res_now, atr_now = ctx["resistance"][k - 1], ctx["resistance"][k], ctx["atr"][k]
        if _isnan_any(res_prev, res_now, atr_now):
            return None
        close, high = ctx["close"], ctx["high"]
        entry = float(close[k])
        if close[k - 1] <= res_prev and close[k] > res_now:  # 돌파(롱)
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "target_price": entry + self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        if high[k] >= res_now and close[k] < res_now:  # 저항 터치했지만 실패(숏)
            return {
                "direction": "SHORT", "entry_price": entry,
                "stop_price": entry + self.stop_mult * atr_now,
                "target_price": entry - self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        return None


class SupportHoldBreakStrategy(LabStrategy):
    """지지선 지지/이탈 — 지지 대응(양방향).

    최근 20봉 저점(직전까지)을 지지선으로 본다. 저가로 닿았지만 종가가
    지지 위에서 마감하면 롱(지지), 종가로 이탈하면 숏(붕괴). 손절 -2ATR,
    익절 +3ATR, 2일 시간손절.
    """

    key = "support_hold_break"
    label = "지지선 지지/이탈"
    category = "지지 대응 (양방향)"
    description = "평행·대각 지지선에 부딪힌 다음 봉이 지지되면 롱, 지지 못 하고 깨지면 숏"

    def __init__(self, donchian_period=20, atr_period=14, stop_mult=2.0, target_mult=3.0, time_stop_days=2):
        self.donchian_period = donchian_period
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.time_stop_days = time_stop_days
        self.min_bars = max(donchian_period, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        d = donchian(df, self.donchian_period)
        return {"support": d["lower"].to_numpy(), "atr": atr(df, self.atr_period).to_numpy()}

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        sup_prev, sup_now, atr_now = ctx["support"][k - 1], ctx["support"][k], ctx["atr"][k]
        if _isnan_any(sup_prev, sup_now, atr_now):
            return None
        close, low = ctx["close"], ctx["low"]
        entry = float(close[k])
        if low[k] <= sup_now and close[k] > sup_now:  # 지지(롱)
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "target_price": entry + self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        if close[k - 1] >= sup_prev and close[k] < sup_now:  # 이탈(숏)
            return {
                "direction": "SHORT", "entry_price": entry,
                "stop_price": entry + self.stop_mult * atr_now,
                "target_price": entry - self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        return None


class BollingerWickTouchStrategy(LabStrategy):
    """볼린저 꼬리 터치 롱/숏 — 밴드 터치(양방향).

    종가가 아니라 꼬리(고가·저가)가 볼린저 밴드에 닿기만 해도 진입 —
    하단 터치 롱, 상단 터치 숏. 손절 -2ATR, 익절 +3ATR, 2일 시간손절.
    """

    key = "bollinger_wick_touch"
    label = "볼린저 꼬리 터치 롱/숏"
    category = "밴드 터치 (양방향)"
    description = "종가가 아니라 꼬리(고가·저가)가 볼린저 밴드에 닿기만 해도 진입 — 상단 터치 롱, 하단 터치 숏"

    def __init__(self, bb_period=20, bb_std=2.0, atr_period=14, stop_mult=2.0, target_mult=3.0, time_stop_days=2):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.time_stop_days = time_stop_days
        self.min_bars = max(bb_period, atr_period) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        bb = bollinger_bands(df, self.bb_period, self.bb_std)
        return {
            "bb_lower": bb["lower"].to_numpy(),
            "bb_upper": bb["upper"].to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        lower_prev, lower_now = ctx["bb_lower"][k - 1], ctx["bb_lower"][k]
        upper_prev, upper_now = ctx["bb_upper"][k - 1], ctx["bb_upper"][k]
        atr_now = ctx["atr"][k]
        if _isnan_any(lower_prev, lower_now, upper_prev, upper_now, atr_now):
            return None
        close, high, low = ctx["close"], ctx["high"], ctx["low"]
        entry = float(close[k])
        # 직전 봉엔 안 닿았다가 이번 봉에 처음 닿은 경우만("신선한" 터치)
        if low[k] <= lower_now and low[k - 1] > lower_prev:
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "target_price": entry + self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        if high[k] >= upper_now and high[k - 1] < upper_prev:
            return {
                "direction": "SHORT", "entry_price": entry,
                "stop_price": entry + self.stop_mult * atr_now,
                "target_price": entry - self.target_mult * atr_now,
                "time_stop_days": self.time_stop_days,
            }
        return None


class IchimokuCloudBreakoutStrategy(LabStrategy):
    """이치모쿠 구름 돌파 — 추세 추종 (일목균형표).

    교과서적인 이치모쿠 매매법을 그대로 따른다 — 전통적으로 쓰이는 5가지
    확인 요소 중 핵심 3가지를 쓴다("미래 구름의 색"과 "구름 두께"는 판단이
    다소 주관적이라 여기선 뺐다):

    ① 종가가 구름(선행스팬A·B 중 더 높은/낮은 값)을 돌파
    ② 전환선(단기, 9봉)이 기준선(중기, 26봉) 위/아래에 있어 모멘텀 방향 확인
    ③ 후행스팬 확인 — 지금 종가가 26봉 전 종가보다 위/아래인지로 이중 확인

    청산은 손절/익절 가격을 고정하지 않고, **기준선을 종가가 반대 방향으로
    가로지르면** 청산하는 전통적인 "기준선 트레일" 방식을 쓴다 — 추세가
    살아있는 한 계속 들고 가고, 추세가 꺾이는 시점(기준선 이탈)에 나온다.
    """

    key = "ichimoku_cloud_breakout"
    label = "이치모쿠 구름 돌파"
    category = "추세 추종 (일목균형표)"
    description = "종가가 구름을 돌파 + 전환선/기준선 방향 확인 + 후행스팬 확인, 기준선을 종가가 반대로 가로지르면 청산"
    designed_timeframe = "1d"  # 이치모쿠는 원래 일봉 기준으로 설계된 지표

    def __init__(self, tenkan_period=9, kijun_period=26, senkou_b_period=52, displacement=26):
        self.tenkan_period = tenkan_period
        self.kijun_period = kijun_period
        self.senkou_b_period = senkou_b_period
        self.displacement = displacement
        self.min_bars = max(tenkan_period, kijun_period, senkou_b_period + displacement) + 5

    def precompute(self, df: pd.DataFrame) -> dict:
        ich = ichimoku(df, self.tenkan_period, self.kijun_period, self.senkou_b_period, self.displacement)
        senkou_a = ich["senkou_a"].to_numpy()
        senkou_b = ich["senkou_b"].to_numpy()
        return {
            "tenkan": ich["tenkan"].to_numpy(),
            "kijun": ich["kijun"].to_numpy(),
            "cloud_top": np.fmax(senkou_a, senkou_b),
            "cloud_bottom": np.fmin(senkou_a, senkou_b),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < self.displacement:
            return None
        tenkan, kijun = ctx["tenkan"][k], ctx["kijun"][k]
        cloud_top_prev, cloud_top_now = ctx["cloud_top"][k - 1], ctx["cloud_top"][k]
        cloud_bottom_prev, cloud_bottom_now = ctx["cloud_bottom"][k - 1], ctx["cloud_bottom"][k]
        if _isnan_any(tenkan, kijun, cloud_top_prev, cloud_top_now, cloud_bottom_prev, cloud_bottom_now):
            return None
        close = ctx["close"]
        entry = float(close[k])
        chikou_up = close[k] > close[k - self.displacement]
        chikou_down = close[k] < close[k - self.displacement]

        if close[k - 1] <= cloud_top_prev and close[k] > cloud_top_now and tenkan > kijun and chikou_up:
            return {"direction": "LONG", "entry_price": entry, "dynamic_stop_key": "kijun"}
        if close[k - 1] >= cloud_bottom_prev and close[k] < cloud_bottom_now and tenkan < kijun and chikou_down:
            return {"direction": "SHORT", "entry_price": entry, "dynamic_stop_key": "kijun"}
        return None


class RsiVolumeSpikeReversalStrategy(LabStrategy):
    """RSI+거래량 스파이크 되돌림 — 데이트레이딩 (RSI+거래량).

    1분~5분봉처럼 짧은 시간대의 스캘핑/데이트레이딩을 겨냥한 전략.
    RSI 과매도/과매수 되돌림만 보면 저유동성 구간의 잡음에도 계속
    걸리기 때문에, **거래량 스파이크**를 같이 요구해 "진짜 매수/매도세가
    붙은 되돌림"만 걸러낸다.

    진입(롱): RSI(14)가 30 밑으로 갔다가 이번 봉에 30 위로 다시 올라오고,
      동시에 이번 봉 거래량이 최근 20봉 평균 거래량의 1.5배 이상일 때.
    진입(숏): RSI가 70 위로 갔다가 이번 봉에 70 아래로 다시 내려오고,
      동시에 거래량 스파이크가 있을 때.
    청산: 손절 -1×ATR / 익절 +1.5×ATR / 데이트레이딩이라 오래 안 들고
      가도록 40봉 시간손절(1분봉 기준 40분, 5분봉 기준 약 3.3시간).
    """

    key = "rsi_volume_spike_reversal"
    label = "RSI+거래량 스파이크 되돌림"
    category = "데이트레이딩 (RSI+거래량)"
    description = "RSI 과매도/과매수 되돌림 + 거래량 스파이크(최근 평균의 1.5배 이상)로 진짜 되돌림만 필터링"
    designed_timeframe = "5m"  # 데이트레이딩/스캘핑용 - 1분~5분봉 겨냥

    def __init__(
        self, rsi_period=14, oversold=30.0, overbought=70.0,
        volume_avg_period=20, volume_mult=1.5,
        atr_period=14, stop_mult=1.0, target_mult=1.5, time_stop_bars=40,
    ):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.volume_avg_period = volume_avg_period
        self.volume_mult = volume_mult
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.target_mult = target_mult
        self.time_stop_bars = time_stop_bars
        self.min_bars = max(rsi_period, volume_avg_period, atr_period) + 50  # RSI 워밍업 여유

    def precompute(self, df: pd.DataFrame) -> dict:
        avg_volume = df["Volume"].rolling(self.volume_avg_period).mean().shift(1)
        return {
            "rsi": rsi(df["Close"], self.rsi_period).to_numpy(),
            "avg_volume": avg_volume.to_numpy(),
            "volume": df["Volume"].to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        rsi_prev, rsi_now = ctx["rsi"][k - 1], ctx["rsi"][k]
        avg_vol, atr_now = ctx["avg_volume"][k], ctx["atr"][k]
        if _isnan_any(rsi_prev, rsi_now, avg_vol, atr_now):
            return None
        volume_spike = ctx["volume"][k] >= self.volume_mult * avg_vol
        if not volume_spike:
            return None
        close = ctx["close"]
        entry = float(close[k])
        # 진입가 자체가 손절 폭 안에 들어오는 걸 막기 위해 최소한의 양의 ATR만 확인
        if atr_now <= 0:
            return None
        if rsi_prev < self.oversold and rsi_now >= self.oversold:
            return {
                "direction": "LONG", "entry_price": entry,
                "stop_price": entry - self.stop_mult * atr_now,
                "target_price": entry + self.target_mult * atr_now,
                "time_stop_bars": self.time_stop_bars,
            }
        if rsi_prev > self.overbought and rsi_now <= self.overbought:
            return {
                "direction": "SHORT", "entry_price": entry,
                "stop_price": entry + self.stop_mult * atr_now,
                "target_price": entry - self.target_mult * atr_now,
                "time_stop_bars": self.time_stop_bars,
            }
        return None


class BigCandleBollingerConfluenceStrategy(LabStrategy):
    """큰 양봉+볼린저 동시 돌파 — 추세 추종 (이중 확인 + 본전 이동 트레일링).

    실험실 전략끼리의 포지션(롱/숏/무포지션) 상관관계를 계산해보면 `큰 양봉 돌파`와
    `볼린저 돌파 롱/숏`이 가장 높다(0.32) — 둘 다 상승 모멘텀에 올라타는 추세추종
    계열이라 겹치는 게 당연하다. 이 둘을 "동시에 신호가 나올 때만 진입"하는 이중
    확인 필터로 묶으면 각자 단독으로 쓸 때보다 훨씬 선별적인 진입이 된다.

    **진입** (완결된 봉 기준, 둘 다 충족해야 함):
    ① 큰 양봉 돌파 조건 — 종가 > 50EMA고 몸통이 최근 20봉 평균의 2배 이상인 양봉
    ② 볼린저 돌파 조건 — 종가가 상단 밴드를 이번 봉에 갓 뚫음
    둘 다 **같은 봉에서 동시에** 롱 신호를 내야 진입한다 (한쪽만 신호 내면 무시).

    **청산** — 손절은 짧게, 이익은 길게 태우는 "본전 이동 트레일링":
    ① 손절 -2×ATR(진입 시점 ATR 기준, 고정)
    ② 가격이 +0.5×ATR 이익을 보면 손절선을 진입가(본전)로 올림 — 여기서부턴
       최악이어도 손실이 거의 없다
    ③ 본전 이동 이후로는 고점 - 0.5×ATR 트레일링 스탑으로 계속 따라가며 추적 —
       익절 상한 없이 추세가 이어지는 한 최대한 태운다

    처음엔 고정 익절(예: 손절 6×ATR/익절 1×ATR)로 실험했는데, 그러면 승률은
    높아도(검증 구간 81.5%) 손절 폭이 익절 폭보다 훨씬 넓어서 기대값이
    마이너스였다(검증 구간 -0.21%/거래) — 승률만으론 수익성을 보장 못 한다는
    걸 그대로 보여준 사례. 손절을 짧게(2×ATR) 하고 익절을 고정하지 않고 트레일링
    으로 바꾸니, 학습(2021~2025-02)과 검증(2025-02~) 양쪽 구간 모두에서 승률
    70%대 이상과 플러스 기대값을 동시에 만족했다(BTCUSDT 1시간봉 기준 - 학습
    75.3%/+0.84%, 검증 79.0%/+0.20%). 다만 ETHUSDT에서는 검증 구간이 마이너스라
    BTC 1시간봉 한정으로만 검증됐다는 점, 그리고 극단적으로 변동성이 튀는
    구간(예: 2022-05 LUNA 사태)에선 "2×ATR"이 실제로는 훨씬 큰 %손실로
    이어질 수 있다는 점은 감안해야 한다.
    """

    key = "big_candle_bollinger_confluence"
    label = "큰 양봉+볼린저 동시 돌파"
    category = "추세 추종 (이중 확인)"
    description = "큰 양봉 돌파와 볼린저 돌파가 같은 봉에서 동시에 롱 신호를 낼 때만 진입, 손절 후 본전 이동+트레일링으로 청산"
    designed_timeframe = "1h"  # BTCUSDT 1시간봉에서 학습/검증 양쪽 다 검증됨

    def __init__(self, stop_mult=2.0, breakeven_at_mult=0.5, trail_mult=0.5, atr_period=14):
        self._big = BigCandleBreakoutStrategy()
        self._boll = BollingerBreakoutStrategy()
        self.stop_mult = stop_mult
        self.breakeven_at_mult = breakeven_at_mult
        self.trail_mult = trail_mult
        self.atr_period = atr_period
        self.min_bars = max(self._big.min_bars, self._boll.min_bars)

    def precompute(self, df: pd.DataFrame) -> dict:
        close = df["Close"].to_numpy()
        high = df["High"].to_numpy()
        low = df["Low"].to_numpy()
        open_ = df["Open"].to_numpy()
        ohlc = {"close": close, "high": high, "low": low, "open_": open_}
        return {
            "big_ctx": {**self._big.precompute(df), **ohlc},
            "boll_ctx": {**self._boll.precompute(df), **ohlc},
            "atr": atr(df, self.atr_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e1 = self._big.check_entry(k, ctx["big_ctx"])
        e2 = self._boll.check_entry(k, ctx["boll_ctx"])
        if not (e1 and e2 and e1["direction"] == "LONG" and e2["direction"] == "LONG"):
            return None
        atr_now = ctx["atr"][k]
        if np.isnan(atr_now) or atr_now <= 0:
            return None
        entry_price = float(ctx["close"][k])
        return {
            "direction": "LONG",
            "entry_price": entry_price,
            "stop_price": entry_price - self.stop_mult * atr_now,
            "breakeven_trigger_price": entry_price + self.breakeven_at_mult * atr_now,
            "trail_mult": self.trail_mult,
            "breakeven_trail": True,
        }


class BollingerWickBreakevenTrailStrategy(LabStrategy):
    """볼린저 꼬리터치 되돌림 + 본전 이동 트레일링 — 데이트레이딩 (평균회귀, 15분·5분봉 전용).

    진입은 `BollingerWickTouchStrategy`의 꼬리 터치 조건(밴드에 "신선하게" 닿으면
    반대 방향 — 하단 터치 롱, 상단 터치 숏)에 **RSI 과매도/과매수 확인을 추가**한
    것이고, 청산은 `BigCandleBollingerConfluenceStrategy`가 쓰는 "본전 이동
    트레일링"(짧은 손절 → 소폭 이익에서 본전 이동 → ATR 트레일링, 익절 상한 없음)
    을 그대로 가져왔다.

    ⚠️ **파라미터와 진입 조건 둘 다 "실제 수수료 반영" 재검증을 거쳐 두 차례
    조정된 값이다.**

    **1차 조정(청산 파라미터)** — 처음 찾은 조합(stop=2×ATR / 본전이동=+0.3×ATR /
    트레일=0.3×ATR)은 승률·수익률이 화려했지만 **수수료 0원 가정** 백테스트였다.
    트레일이 타이트해서 대부분의 익절이 1% 미만의 작은 이익으로 끝났는데, 실제
    수수료(테이커 왕복 약 0.1%)만 반영해도 거래의 60~76%가 "이겼지만 수수료로
    손실 전환"되는 것으로 드러났다(BTC 5m: 승률 85.2%→10.7%). 손절을 넉넉히
    (2×ATR→3×ATR), 본전이동을 늦게(+0.3→+0.5×ATR) 잡아 자잘한 거래 자체를 줄이는
    쪽으로 재조정했다.

    **2차 조정(진입 조건에 RSI 필터 추가)** — 수수료는 거래소가 고정한 값이라
    더 낮출 수 없으므로, 대신 "밴드 꼬리 터치"만으로는 걸러지는 노이즈성
    되돌림(진짜 과매도/과매수가 아닌데 꼬리만 살짝 닿은 경우)을 줄이려고
    **같은 봉의 RSI(14)가 40 이하(롱)/60 이상(숏)일 때만 진입**하도록
    조건을 추가했다. 30/40/50대 여러 문턱값을 비교한 결과 40/60이 "품질과
    거래량"의 균형이 가장 좋았다(더 타이트한 30/70은 거래당 수익은 더 크지만
    표본이 5분의 1로 줄어 총 수익 총합은 오히려 작아짐). 밴드폭 확대(2.5σ),
    종가 밴드 재진입 확인, 200EMA 추세 필터는 전부 시도해봤지만 이 전략(평균
    회귀)에는 안 맞거나(추세 필터는 오히려 좋은 역추세 되돌림까지 걸러냄)
    개선 폭이 미미해 채택하지 않았다.

    **수수료 0.1%(바이낸스 선물 테이커 왕복) 반영, RSI 필터 포함, 5년 이상
    전체 히스토리 기준**(RSI 필터 적용 전 → 후, 학습/검증 둘 다 개선):

    - BTCUSDT 15분봉(4585건): 학습 88.0%→91.2%/+1.217%→+1.426%,
      검증 75.8%→79.4%/+0.190%→+0.233%
    - BTCUSDT 5분봉(13618건): 학습 77.5%→83.3%/+0.511%→+0.764%,
      검증 55.8%→64.7%/+0.091%→+0.101% (넷 중 마진은 여전히 가장 얇음)
    - ETHUSDT 15분봉(교차검증, 5053건): 학습 83.7%→89.2%/+1.084%→+1.367%,
      검증 78.3%→85.2%/+0.417%→+0.903% (검증 구간 거래당 수익률 2배 이상)
    - ETHUSDT 5분봉(교차검증, 14838건): 학습 71.9%→81.9%/+0.242%→+0.530%,
      검증 63.7%→71.4%/+0.109%→+0.375% (검증 구간 거래당 수익률 3배 이상)

    (표본이 줄어드는 대신 거래당 품질이 크게 좋아져서 네 조합 전부 승률·
    거래당 수익률 전부 개선됐다. 정확한 최신 수치는
    `data/validated_lab_stats.json`/`/api/lab/validated-stats` 또는 README
    "시간대별 운용 방침" 참고.)

    거래 표본이 수천~수만 건이라 "100% 몰빵 복리"로 계산하면 숫자가 천문학적으로
    부풀어(비현실적) 의미가 없다 — 실전에서는 매 거래마다 계좌 자본의 일부(예:
    1~5%)만 리스크에 거는 자금관리가 필수다. 손절폭이 넓어진 만큼(3×ATR)
    갭/슬리피지가 낀 최악의 단일 거래 손실폭도 구 파라미터보다 커질 수 있다는
    점은 감안해야 한다(스트레스 테스트 캐비어트는 README 참고). 또한 펀딩비
    (포지션을 8시간 정산 시점 너머로 들고 갈 때 붙는 별도 비용)는 이 전략의
    평균 보유시간이 워낙 짧아(중앙값 10~30분) 주된 위험 요인이 아님을 확인함.
    """

    key = "bollinger_wick_breakeven_trail"
    label = "볼린저 꼬리터치 되돌림 (본전 이동 트레일링)"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링)"
    description = (
        "볼린저 밴드에 꼬리가 신선하게 닿고 RSI(14)도 과매도(≤40)/과매수(≥60)일 때만 "
        "반대 방향 진입, 손절 3×ATR 후 소폭 이익(+0.5×ATR)에서 본전 이동 후 트레일링 청산 — "
        "실제 수수료(0.1%) 반영 재검증 완료, 15분·5분봉 전용"
    )
    designed_timeframe = "15m"  # 15분/5분봉 둘 다 검증됨 - 그리드서치 기준 시간대

    def __init__(
        self, stop_mult=3.0, breakeven_at_mult=0.5, trail_mult=0.3, atr_period=14,
        rsi_period=14, rsi_oversold=40.0, rsi_overbought=60.0,
    ):
        self._touch = BollingerWickTouchStrategy()
        self.stop_mult = stop_mult
        self.breakeven_at_mult = breakeven_at_mult
        self.trail_mult = trail_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.min_bars = max(self._touch.min_bars, atr_period + 5, rsi_period + 50)

    def precompute(self, df: pd.DataFrame) -> dict:
        close = df["Close"].to_numpy()
        high = df["High"].to_numpy()
        low = df["Low"].to_numpy()
        open_ = df["Open"].to_numpy()
        ohlc = {"close": close, "high": high, "low": low, "open_": open_}
        return {
            "touch_ctx": {**self._touch.precompute(df), **ohlc},
            "atr": atr(df, self.atr_period).to_numpy(),
            "rsi": rsi(df["Close"], self.rsi_period).to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = self._touch.check_entry(k, ctx["touch_ctx"])
        if e is None:
            return None
        atr_now = ctx["atr"][k]
        if np.isnan(atr_now) or atr_now <= 0:
            return None
        direction, entry_price = e["direction"], e["entry_price"]

        # RSI 확인 필터: 밴드 꼬리 터치만으로는 노이즈성 되돌림도 많이 걸려서
        # (자세한 근거는 클래스 docstring 참고) 같은 봉에서 RSI도 과매도/과매수
        # 쪽에 있어야만 진입한다 — 진짜 극단적 되돌림만 선별.
        rsi_now = ctx["rsi"][k]
        if np.isnan(rsi_now):
            return None
        if direction == "LONG" and rsi_now > self.rsi_oversold:
            return None
        if direction == "SHORT" and rsi_now < self.rsi_overbought:
            return None

        if direction == "LONG":
            stop = entry_price - self.stop_mult * atr_now
            trigger = entry_price + self.breakeven_at_mult * atr_now
        else:
            stop = entry_price + self.stop_mult * atr_now
            trigger = entry_price - self.breakeven_at_mult * atr_now
        return {
            "direction": direction,
            "entry_price": entry_price,
            "stop_price": stop,
            "breakeven_trigger_price": trigger,
            "trail_mult": self.trail_mult,
            "breakeven_trail": True,
        }


class BollingerWickExtremeMoveRelaxedStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy`(검증된 전략) + "그 봉이 평소보다
    훨씬 큰 캔들(과대낙폭/과대상승)이면 RSI 확인 문턱을 살짝 완화"하는 실험용
    변형. **아직 검증 안 됨** — `docstring`에 이미 적힌 BollingerWickBreakevenTrailStrategy
    자체는 손대지 않고(그 클래스를 상속만 해서 재사용), `check_entry()`만
    오버라이드해 "큰 캔들일 때만" 조건을 완화하는 추가 분기를 얹었다.

    완화 방식: 신호가 난 봉의 (고가-저가) 폭이 ATR의 `extreme_atr_mult`배
    이상이면 "과대낙폭/과대상승"으로 보고, RSI 문턱을
    `rsi_relax_amount`만큼 완화한다(롱은 oversold+amount, 숏은
    overbought-amount) — 평소엔 원래 전략과 완전히 동일하게 동작하고,
    드물게 캔들이 유난히 크게 튄 순간에만 조건이 느슨해져서 신호를 더
    잡는다는 아이디어.

    ⚠️ 이 변형은 백테스트로 비교 검증하기 전까지는 **실계좌 자동매매에
    연결하면 안 된다** — 다른 모든 검증된/후보 전략과 같은 원칙."""

    key = "bollinger_wick_extreme_move_relaxed"
    label = "볼린저 꼬리터치 (과대변동 시 RSI 완화) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + 신호 캔들이 "
        "ATR의 N배 이상 큰 '과대낙폭/과대상승'일 때만 RSI 문턱을 완화 - 아직 미검증"
    )

    def __init__(self, *args, extreme_atr_mult: float = 2.0, rsi_relax_amount: float = 5.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.extreme_atr_mult = extreme_atr_mult
        self.rsi_relax_amount = rsi_relax_amount

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = self._touch.check_entry(k, ctx["touch_ctx"])
        if e is None:
            return None
        atr_now = ctx["atr"][k]
        if np.isnan(atr_now) or atr_now <= 0:
            return None
        direction, entry_price = e["direction"], e["entry_price"]

        rsi_now = ctx["rsi"][k]
        if np.isnan(rsi_now):
            return None

        high_k, low_k = ctx["touch_ctx"]["high"][k], ctx["touch_ctx"]["low"][k]
        is_extreme_move = (high_k - low_k) >= self.extreme_atr_mult * atr_now
        oversold = self.rsi_oversold + self.rsi_relax_amount if is_extreme_move else self.rsi_oversold
        overbought = self.rsi_overbought - self.rsi_relax_amount if is_extreme_move else self.rsi_overbought

        if direction == "LONG" and rsi_now > oversold:
            return None
        if direction == "SHORT" and rsi_now < overbought:
            return None

        if direction == "LONG":
            stop = entry_price - self.stop_mult * atr_now
            trigger = entry_price + self.breakeven_at_mult * atr_now
        else:
            stop = entry_price + self.stop_mult * atr_now
            trigger = entry_price - self.breakeven_at_mult * atr_now
        return {
            "direction": direction,
            "entry_price": entry_price,
            "stop_price": stop,
            "breakeven_trigger_price": trigger,
            "trail_mult": self.trail_mult,
            "breakeven_trail": True,
        }


class BollingerWickVolumeConfirmStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy` + "터치 캔들의 거래량이 평소보다
    확실히 많을 때만 진입" 필터. 아이디어: 거래량이 평소 수준인 꼬리 터치는
    그냥 노이즈성 되돌림일 가능성이 높고, 거래량이 터진 터치는 실제
    투매/패닉성 매수세가 몰린 "진짜" 과매도/과매수일 가능성이 높다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_volume_confirm"
    label = "볼린저 꼬리터치 (거래량 확인) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + 터치 캔들 거래량이 "
        "최근 평균의 N배 이상일 때만 진입 - 아직 미검증"
    )

    def __init__(self, *args, volume_avg_period: int = 20, volume_mult: float = 1.2, **kwargs):
        super().__init__(*args, **kwargs)
        self.volume_avg_period = volume_avg_period
        self.volume_mult = volume_mult
        self.min_bars = max(self.min_bars, volume_avg_period + 5)

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        ctx["volume"] = df["Volume"].to_numpy()
        ctx["avg_volume"] = df["Volume"].rolling(self.volume_avg_period).mean().to_numpy()
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        avg_vol = ctx["avg_volume"][k]
        if np.isnan(avg_vol) or avg_vol <= 0:
            return None
        if ctx["volume"][k] < avg_vol * self.volume_mult:
            return None
        return e


class BollingerWickCandlePatternStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy` + "터치 캔들 자체가 실제 반전형
    캔들패턴일 때만 진입" 필터(TA-Lib 패턴 인식 사용). 롱은 망치형/역망치형/
    상승장악형/샛별형 중 하나, 숏은 유성형/교수형/하락장악형/저녁별형 중
    하나가 그 봉에서 떠야 통과 — 단순히 "꼬리가 밴드에 닿았다"보다 캔들
    자체의 반전 신호 품질까지 확인한다는 아이디어.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_candle_pattern"
    label = "볼린저 꼬리터치 (반전 캔들패턴 확인) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + 터치 캔들이 "
        "실제 반전형 캔들패턴(망치/장악형/별형 등)일 때만 진입 - 아직 미검증"
    )

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        o = df["Open"].to_numpy(dtype=float)
        h = df["High"].to_numpy(dtype=float)
        lo = df["Low"].to_numpy(dtype=float)
        c = df["Close"].to_numpy(dtype=float)
        bullish = (
            (talib.CDLHAMMER(o, h, lo, c) > 0)
            | (talib.CDLINVERTEDHAMMER(o, h, lo, c) > 0)
            | (talib.CDLENGULFING(o, h, lo, c) > 0)
            | (talib.CDLMORNINGSTAR(o, h, lo, c) > 0)
        )
        bearish = (
            (talib.CDLSHOOTINGSTAR(o, h, lo, c) < 0)
            | (talib.CDLHANGINGMAN(o, h, lo, c) < 0)
            | (talib.CDLENGULFING(o, h, lo, c) < 0)
            | (talib.CDLEVENINGSTAR(o, h, lo, c) < 0)
        )
        ctx["bullish_pattern"] = bullish
        ctx["bearish_pattern"] = bearish
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        if e["direction"] == "LONG" and not ctx["bullish_pattern"][k]:
            return None
        if e["direction"] == "SHORT" and not ctx["bearish_pattern"][k]:
            return None
        return e


class BollingerWickStochRsiConfirmStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy` + "StochRSI까지 같은 방향 극단일
    때만 진입" 이중 확인 필터. RSI(14)보다 StochRSI가 더 민감하게 움직이는
    지표라, 둘 다 극단이어야 통과시키면 RSI 단독보다 더 좁고 확실한 구간만
    남을 것이라는 아이디어.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_stochrsi_confirm"
    label = "볼린저 꼬리터치 (StochRSI 이중확인) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + StochRSI까지 "
        "같은 방향 과매도/과매수일 때만 진입 - 아직 미검증"
    )

    def __init__(
        self, *args, stochrsi_period: int = 14, stochrsi_fastk: int = 5, stochrsi_fastd: int = 3,
        stochrsi_oversold: float = 20.0, stochrsi_overbought: float = 80.0, **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.stochrsi_period = stochrsi_period
        self.stochrsi_fastk = stochrsi_fastk
        self.stochrsi_fastd = stochrsi_fastd
        self.stochrsi_oversold = stochrsi_oversold
        self.stochrsi_overbought = stochrsi_overbought

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        close = df["Close"].to_numpy(dtype=float)
        fastk, _ = talib.STOCHRSI(
            close, timeperiod=self.stochrsi_period,
            fastk_period=self.stochrsi_fastk, fastd_period=self.stochrsi_fastd,
        )
        ctx["stochrsi_k"] = fastk
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        k_val = ctx["stochrsi_k"][k]
        if np.isnan(k_val):
            return None
        if e["direction"] == "LONG" and k_val > self.stochrsi_oversold:
            return None
        if e["direction"] == "SHORT" and k_val < self.stochrsi_overbought:
            return None
        return e


class BollingerWickVolumeStochRsiConfirmStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickVolumeConfirmStrategy` + `BollingerWickStochRsiConfirmStrategy`를
    합친 조합 — 둘 다(터치 캔들 거래량 확인 **그리고** StochRSI 이중확인)를
    동시에 만족해야 진입한다.

    **2026-11, 파라미터 스윕 포함 검증 완료 → 실계좌 wick 엔진 기본 전략으로
    채택됨** (`app/wick_signal_engine.py`). 검증 과정(2026-03~2026-11, 4조합
    BTC/ETH×15m/5m, 학습 3~8월/검증 9~11월 분리):

    - 두 필터를 각각 따로 비교했을 때 둘 다 baseline보다 개선(검증구간 거래당
      평균: baseline +0.0233% → 거래량확인 단독 +0.0371%, StochRSI확인 단독
      +0.0290%), 상관관계는 0.90으로 겹치지만 완전히 같은 신호는 아님
    - 합쳤을 때(거래량 1.2배+StochRSI 20/80) 단독보다도 더 개선: 검증구간
      +0.0437%, 승률 64.0%(1,681건) - 같은 방식(4조합 신뢰도 비례 배분,
      조합당 배분액 전액 베팅)으로 9/1~현재 복기 시 baseline +18.18% →
      **+24.62%**
    - 파라미터 스윕(거래량배수 1.0~2.0×, StochRSI 문턱 15/85~30/70) 결과
      **StochRSI 문턱은 좁힐수록(15/85) 꾸준히 개선**되는 반면, 거래량배수는
      **1.3배를 넘기면 학습구간은 계속 좋아지는데 검증구간은 오히려
      나빠지는 과최적화 신호**가 나타나 1.2배 근처를 유지 - 최종
      거래량 1.2배 + StochRSI 15/85로 검증구간 +0.0449%, 같은 방식 복기 시
      **+24.96%**(주당 환산 약 +2.39%)

    ⚠️ 그래도 과거 데이터 기준 추정치라는 한계, 펀딩비/슬리피지 미반영 등은
    baseline과 동일하게 적용된다 - README "실계좌 자동매매" 섹션 참고."""

    key = "bollinger_wick_volume_stochrsi_confirm"
    label = "볼린저 꼬리터치 (거래량+StochRSI 동시확인) - 실계좌 기본 전략"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 진입조건 동일 + 터치 캔들 거래량 확인과 "
        "StochRSI 이중확인(15/85)을 동시에 만족할 때만 진입 - 2026-11 검증 완료, 실계좌 기본값"
    )

    def __init__(
        self, *args,
        volume_avg_period: int = 20, volume_mult: float = 1.2,
        stochrsi_period: int = 14, stochrsi_fastk: int = 5, stochrsi_fastd: int = 3,
        stochrsi_oversold: float = 15.0, stochrsi_overbought: float = 85.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.volume_avg_period = volume_avg_period
        self.volume_mult = volume_mult
        self.stochrsi_period = stochrsi_period
        self.stochrsi_fastk = stochrsi_fastk
        self.stochrsi_fastd = stochrsi_fastd
        self.stochrsi_oversold = stochrsi_oversold
        self.stochrsi_overbought = stochrsi_overbought
        self.min_bars = max(self.min_bars, volume_avg_period + 5)

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        ctx["volume"] = df["Volume"].to_numpy()
        ctx["avg_volume"] = df["Volume"].rolling(self.volume_avg_period).mean().to_numpy()
        close = df["Close"].to_numpy(dtype=float)
        fastk, _ = talib.STOCHRSI(
            close, timeperiod=self.stochrsi_period,
            fastk_period=self.stochrsi_fastk, fastd_period=self.stochrsi_fastd,
        )
        ctx["stochrsi_k"] = fastk
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        avg_vol = ctx["avg_volume"][k]
        if np.isnan(avg_vol) or avg_vol <= 0 or ctx["volume"][k] < avg_vol * self.volume_mult:
            return None
        k_val = ctx["stochrsi_k"][k]
        if np.isnan(k_val):
            return None
        if e["direction"] == "LONG" and k_val > self.stochrsi_oversold:
            return None
        if e["direction"] == "SHORT" and k_val < self.stochrsi_overbought:
            return None
        return e


class BollingerWickOverextensionStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy` + "밴드 중심선(SMA)에서부터 ATR
    기준으로 충분히 멀리 떨어진 터치만" 통과시키는 필터. 밴드가 좁아져 있을
    때는 중심선 바로 근처만 벗어나도 "터치"가 되는데, 이런 얕은 터치는
    진짜 과매도/과매수가 아닐 수 있다 — 중심선으로부터의 거리(ATR 배수)로
    "얼마나 멀리 밀렸는지"를 직접 확인한다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_overextension"
    label = "볼린저 꼬리터치 (중심선 이격도 확인) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + 진입가가 밴드 "
        "중심선(SMA)에서 ATR 기준 N배 이상 떨어져 있을 때만 진입 - 아직 미검증"
    )

    def __init__(self, *args, min_atr_distance: float = 1.5, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_atr_distance = min_atr_distance

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        bb = bollinger_bands(df, self._touch.bb_period, self._touch.bb_std)
        ctx["bb_middle"] = bb["middle"].to_numpy()
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        mid = ctx["bb_middle"][k]
        atr_now = ctx["atr"][k]
        if np.isnan(mid) or np.isnan(atr_now) or atr_now <= 0:
            return None
        if abs(e["entry_price"] - mid) / atr_now < self.min_atr_distance:
            return None
        return e


def _higher_timeframe_rsi(df: pd.DataFrame, group_size: int, rsi_period: int) -> np.ndarray:
    """`group_size`개 봉씩 묶어 상위 시간대 "proxy" 봉을 만들고 그 종가로
    RSI를 계산해 원래 df 인덱스 길이로 매핑한다.

    미래 데이터 유출 방지: 상위 봉 그룹 G가 "완결"되기 전까지는 그 그룹에
    속한 어떤 하위 봉도 그룹 G의 RSI를 알 수 없으므로, 각 하위 봉에는 항상
    "하나 이전(G-1)에 완결된" 상위 봉의 RSI만 매핑한다(`shift(1)`)."""
    n = len(df)
    group_id = np.arange(n) // group_size
    higher_close = df["Close"].groupby(group_id).last()
    higher_rsi = rsi(higher_close, rsi_period).shift(1)
    return higher_rsi.reindex(group_id).to_numpy()


class BollingerWickHigherTimeframeRsiStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy` + "상위 시간대(proxy) RSI도 같은
    방향으로 과매도/과매수일 때만 진입" 필터. 200EMA 추세 필터(이미 시도해봤고
    효과 없었음 - 역추세 되돌림 자체를 걸러내 버림)와는 다르게, 추세 방향은
    전혀 보지 않고 "더 큰 그림에서도 지금이 극단 구간인지"만 확인한다 - 예를
    들어 15분봉이 과매도인데 상위 시간대는 전혀 과열 안 된 상태면 그냥 그
    15분봉만의 잡음일 수 있다는 아이디어.

    `group_size`개 봉을 묶어 상위 시간대를 흉내낸다(15분봉 전략이면 기본값
    4 = 1시간봉에 해당, 5분봉 전략이면 4 = 20분 단위 - 정확히 표준
    시간대 이름에 맞아떨어지진 않지만 "몇 배 더 큰 그림"이라는 목적은 같다).

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_higher_tf_rsi"
    label = "볼린저 꼬리터치 (상위시간대 RSI 확인) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + 상위 시간대(proxy) "
        "RSI도 같은 방향 과매도/과매수일 때만 진입 - 아직 미검증"
    )

    def __init__(
        self, *args, higher_tf_group_size: int = 4,
        higher_tf_oversold: float = 40.0, higher_tf_overbought: float = 60.0, **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.higher_tf_group_size = higher_tf_group_size
        self.higher_tf_oversold = higher_tf_oversold
        self.higher_tf_overbought = higher_tf_overbought
        self.min_bars = max(self.min_bars, self.higher_tf_group_size * (self.rsi_period + 2))

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        ctx["higher_tf_rsi"] = _higher_timeframe_rsi(df, self.higher_tf_group_size, self.rsi_period)
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        higher_rsi = ctx["higher_tf_rsi"][k]
        if np.isnan(higher_rsi):
            return None
        if e["direction"] == "LONG" and higher_rsi > self.higher_tf_oversold:
            return None
        if e["direction"] == "SHORT" and higher_rsi < self.higher_tf_overbought:
            return None
        return e


class BollingerWickLowAdxFilterStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy` + "ADX(추세 강도)가 너무 높지
    않을 때만 진입" 필터. 평균회귀 전략은 박스권/횡보 구간에 잘 맞고, 강한
    추세 구간에서는 역추세 진입이 추세에 그대로 밀릴 위험이 커진다 - ADX가
    낮은(약한 추세/횡보) 구간으로만 진입을 제한한다는 아이디어.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_low_adx_filter"
    label = "볼린저 꼬리터치 (낮은 ADX 확인) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 완전히 동일 + ADX가 N 이하(약한 "
        "추세/횡보)일 때만 진입 - 아직 미검증"
    )

    def __init__(self, *args, adx_period: int = 14, max_adx: float = 25.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.adx_period = adx_period
        self.max_adx = max_adx
        self.min_bars = max(self.min_bars, adx_period * 2 + 5)

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        h = df["High"].to_numpy(dtype=float)
        lo = df["Low"].to_numpy(dtype=float)
        c = df["Close"].to_numpy(dtype=float)
        ctx["adx"] = talib.ADX(h, lo, c, timeperiod=self.adx_period)
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        adx_now = ctx["adx"][k]
        if np.isnan(adx_now) or adx_now > self.max_adx:
            return None
        return e


class BollingerWickPartialScaleOutStrategy(BollingerWickBreakevenTrailStrategy):
    """`BollingerWickBreakevenTrailStrategy`와 진입 조건은 완전히 동일하고,
    청산만 다르다: 본전 이동 트리거(+`breakeven_at_mult`×ATR)에 처음 닿는
    순간 포지션의 `scale_out_fraction`(기본 절반)을 그 가격에 바로 확정
    청산하고, 나머지는 원래처럼 트레일링을 계속 이어간다.

    아이디어: 지금 방식은 본전 이동 후 가격이 바로 반전해 트레일 스탑이
    거의 본전 근처에서 체결되면("왕복 손실") 그 거래가 사실상 수익 0에
    가깝게 끝난다. 절반을 트리거 가격에 먼저 챙겨두면, 나머지 절반이
    본전 근처에서 끝나더라도 전체 거래는 최소 "챙긴 절반만큼"은 플러스로
    남는다 - 반대로 가격이 계속 유리하게 가면 챙기지 않은 절반이 평소처럼
    더 크게 먹어준다.

    이 변형은 체결 로직 자체가 다르므로(분할 체결), `app.lab_backtest.simulate_lab`이
    아니라 전용 백테스트 함수 `simulate_partial_scaleout()`(이 파일 하단)으로
    돌려야 한다 - 공용 백테스트 엔진은 건드리지 않았다(다른 모든 전략에
    영향 없음을 보장하기 위함).

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_partial_scaleout"
    label = "볼린저 꼬리터치 (본전이동 시 절반 익절) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "검증된 bollinger_wick_breakeven_trail과 진입 조건은 동일 + 본전 이동 "
        "트리거에 닿으면 포지션 절반을 그 자리에서 확정 청산 - 아직 미검증. "
        "simulate_lab()이 아니라 simulate_partial_scaleout()으로 백테스트할 것"
    )

    def __init__(self, *args, scale_out_fraction: float = 0.5, **kwargs):
        super().__init__(*args, **kwargs)
        self.scale_out_fraction = scale_out_fraction


def _walk_forward_partial_scaleout(
    ctx: dict, entry_idx: int, entry: dict, n: int,
) -> tuple[str, float, int, bool, float | None]:
    """`app.lab_backtest._walk_forward_breakeven_trail()`과 완전히 같은 본전
    이동+트레일링 로직이되, 본전 이동 트리거에 처음 닿는 순간을 별도로
    기록해서 "그 가격에 절반을 먼저 확정 청산했다"고 취급할 수 있게 한다.

    반환: (exit_reason, final_exit_price, exit_idx, scaled_out_여부, 트리거체결가)
    공용 엔진(app/lab_backtest.py)은 전혀 건드리지 않고 이 실험 하나만을
    위해 별도로 복제해둔 함수다."""
    high, low, close, atr_values = ctx["high"], ctx["low"], ctx["close"], ctx["atr"]
    direction = entry["direction"]
    entry_price = entry["entry_price"]
    stop_price = entry["stop_price"]
    trigger_price = entry["breakeven_trigger_price"]
    trail_mult = entry["trail_mult"]
    moved_to_breakeven = False
    scaled_out = False
    scale_out_price: float | None = None
    extreme = high[entry_idx] if direction == "LONG" else low[entry_idx]

    for j in range(entry_idx + 1, n):
        if direction == "LONG":
            extreme = max(extreme, high[j])
            if not moved_to_breakeven and high[j] >= trigger_price:
                moved_to_breakeven = True
                scaled_out = True
                scale_out_price = trigger_price
                stop_price = entry_price
            if moved_to_breakeven:
                stop_price = max(stop_price, extreme - trail_mult * atr_values[j])
            if low[j] <= stop_price:
                return ("TRAIL" if moved_to_breakeven else "SL"), stop_price, j, scaled_out, scale_out_price
        else:
            extreme = min(extreme, low[j])
            if not moved_to_breakeven and low[j] <= trigger_price:
                moved_to_breakeven = True
                scaled_out = True
                scale_out_price = trigger_price
                stop_price = entry_price
            if moved_to_breakeven:
                stop_price = min(stop_price, extreme + trail_mult * atr_values[j])
            if high[j] >= stop_price:
                return ("TRAIL" if moved_to_breakeven else "SL"), stop_price, j, scaled_out, scale_out_price

    return "TIME", float(close[-1]), n - 1, scaled_out, scale_out_price


def simulate_partial_scaleout(
    df: pd.DataFrame, strategy: BollingerWickPartialScaleOutStrategy, fee_pct: float = 0.0,
) -> list[dict]:
    """`BollingerWickPartialScaleOutStrategy` 전용 백테스트 - `app.lab_backtest.simulate_lab()`과
    같은 진입 탐색 루프를 쓰되, 청산만 위 분할체결 로직으로 대체한다."""
    n = len(df)
    if n < strategy.min_bars:
        return []

    pre = strategy.precompute(df)
    ctx = {
        **pre,
        "close": df["Close"].to_numpy(), "high": df["High"].to_numpy(),
        "low": df["Low"].to_numpy(), "open_": df["Open"].to_numpy(),
    }
    index = df.index
    fraction = strategy.scale_out_fraction

    trades: list[dict] = []
    k = strategy.min_bars - 1
    while k < n:
        entry = strategy.check_entry(k, ctx)
        if entry is None:
            k += 1
            continue

        exit_reason, exit_price, exit_idx, scaled_out, scale_out_price = _walk_forward_partial_scaleout(
            ctx, k, entry, n
        )
        entry_price = entry["entry_price"]
        direction = entry["direction"]

        def _pct(price: float) -> float:
            if direction == "LONG":
                return (price - entry_price) / entry_price * 100
            return (entry_price - price) / entry_price * 100

        if scaled_out and scale_out_price is not None:
            pct = fraction * _pct(scale_out_price) + (1 - fraction) * _pct(exit_price)
        else:
            pct = _pct(exit_price)

        trades.append({
            "entry_time": str(index[k]),
            "exit_time": str(index[exit_idx]),
            "direction": direction,
            "entry_price": round(float(entry_price), 6),
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason,
            "scaled_out": scaled_out,
            "pct_return": round(pct - fee_pct, 4),
            "gross_pct_return": round(pct, 4),
        })
        k = max(exit_idx + 1, k + 1)

    return trades


class BollingerWickVolatilityAdaptiveBreakevenStrategy(BollingerWickVolumeStochRsiConfirmStrategy):
    """실계좌 기본 전략(`BollingerWickVolumeStochRsiConfirmStrategy`)과 진입
    조건은 완전히 동일하고, "본전 이동 트리거를 언제 당길지"만 그 순간의
    변동성에 맞춰 조정한다.

    아이디어: 지금은 `breakeven_at_mult`(기본 0.5×ATR)가 고정값이라, 변동성이
    낮을 때(ATR이 작을 때)는 상대적으로 "빠르게" 본전으로 묶이고 변동성이
    높을 때도 똑같이 빠르게 묶인다. 변동성이 평소보다 높은 상황(추세가 막
    터지는 구간일 가능성)에서는 트리거를 조금 늦춰서(본전 이동을 미뤄서)
    트레일링을 더 오래 타게 하면, 손실 거래의 다운사이드는 그대로 작게
    유지하면서 승리 거래의 상단만 더 열어줄 수 있다는 가설.

    구현: 진입 시점 ATR을 최근 `vol_lookback`봉 평균 ATR로 나눈 비율
    (`vol_ratio`, [`vol_adapt_min`, `vol_adapt_max`]로 클리핑)을
    `breakeven_at_mult`에 곱해서 트리거 가격만 다시 계산한다. 평소(ATR이
    평균 근처)면 원래 전략과 거의 동일하게 동작하고, ATR이 평균보다 2배
    높으면(vol_adapt_max=2.0 기준) 트리거까지의 거리도 2배로 늘어난다.
    손절선(`stop_price`)·트레일 배수(`trail_mult`)는 그대로 둬서 "손실은
    짧게, 이익 거래의 본전 이동만 변동성에 맞춰 늦춘다"는 범위로 한정했다.
    청산 로직 자체(본전 이동 후 ATR 트레일링)는 바뀌지 않으므로 기존
    `app.lab_backtest.simulate_lab()`을 그대로 쓸 수 있다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_volatility_adaptive_breakeven"
    label = "볼린저 꼬리터치 (변동성 적응형 본전이동) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "실계좌 기본 전략과 진입 조건 동일 + 본전 이동 트리거 거리를 "
        "진입 시점 ATR이 최근 평균보다 높을수록 늘려서(변동성 적응) 유리한 거래를 더 오래 타게 함 - 아직 미검증"
    )

    def __init__(self, *args, vol_lookback: int = 50, vol_adapt_min: float = 0.5, vol_adapt_max: float = 2.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.vol_lookback = vol_lookback
        self.vol_adapt_min = vol_adapt_min
        self.vol_adapt_max = vol_adapt_max
        self.min_bars = max(self.min_bars, vol_lookback + 5)

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        ctx["atr_rolling_mean"] = pd.Series(ctx["atr"]).rolling(self.vol_lookback).mean().to_numpy()
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        atr_now = ctx["atr"][k]
        atr_avg = ctx["atr_rolling_mean"][k]
        if np.isnan(atr_avg) or atr_avg <= 0:
            return e  # 평균 ATR을 아직 못 구하면(워밍업 구간) 원래 전략 그대로
        vol_ratio = np.clip(atr_now / atr_avg, self.vol_adapt_min, self.vol_adapt_max)
        adjusted_mult = self.breakeven_at_mult * vol_ratio
        entry_price = e["entry_price"]
        if e["direction"] == "LONG":
            e["breakeven_trigger_price"] = entry_price + adjusted_mult * atr_now
        else:
            e["breakeven_trigger_price"] = entry_price - adjusted_mult * atr_now
        return e


class BollingerWickSignalExitStrategy(BollingerWickVolumeStochRsiConfirmStrategy):
    """실계좌 기본 전략과 진입 조건은 완전히 동일하고, 본전 이동 이후의
    청산 방식만 다르다: ATR 거리 기준 트레일링 대신, StochRSI가 반대쪽
    과열 지점까지 되돌아올 때까지는 끊지 않고 계속 들고 있는다.

    아이디어: ATR 트레일은 "가격이 얼마나 밀렸는지"만 보고 기계적으로
    끊는데, 추세가 계속 유리한 방향으로 가는 중이어도 일시적으로 ATR
    트레일 거리만큼 되돌리면 끊겨버린다. 진입 조건으로도 쓰는 StochRSI가
    반대쪽 극단(예: 롱인데 과매수 90 이상)에 도달해야만 "추세가 꺾였다"고
    보고 끊으면, 추세가 더 이어지는 동안은 안 털리고 계속 탈 수 있다는
    가설.

    구체적인 상태 전이:
    ① 진입 직후엔 원래와 같은 고정 손절선(`stop_price`)만 있음 - 손실은
       여전히 짧게 자른다.
    ② 본전 이동 트리거(`breakeven_trigger_price`)에 처음 닿으면 손절선을
       본전(진입가)으로 올린다 - 여기부터는 최악이어도 손실이 0에 가깝다.
    ③ 본전 이동 이후로는 ATR 트레일 없이 그대로 들고 있다가, StochRSI가
       반대쪽 과열 문턱(`exit_stochrsi_overbought`/`exit_stochrsi_oversold`,
       기본 90/10)에 도달하는 그 봉의 종가로 청산한다. 그 전에 가격이 다시
       본전까지 내려오면 본전(무승부 근처)으로 청산된다.

    ⚠️ 트레일링이 없어서, 큰 수익을 내다가 반전신호가 늦게 뜨면 ATR
    트레일 방식보다 더 많이 반납할 수 있다는 리스크가 있다 - 이게 바로
    검증해야 할 트레이드오프다. 체결 로직이 다르므로(ATR 트레일 대신
    StochRSI 신호 대기) `app.lab_backtest.simulate_lab`이 아니라 전용
    백테스트 함수 `simulate_signal_exit()`(이 파일 하단)으로 돌려야 한다 -
    공용 백테스트 엔진은 건드리지 않았다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_signal_exit"
    label = "볼린저 꼬리터치 (StochRSI 반대신호 대기 청산) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "실계좌 기본 전략과 진입 조건 동일 + 본전 이동 후 ATR 트레일 대신 StochRSI가 "
        "반대쪽 과열 문턱에 도달할 때까지 계속 들고 있음 - 아직 미검증. "
        "simulate_lab()이 아니라 simulate_signal_exit()으로 백테스트할 것"
    )

    def __init__(self, *args, exit_stochrsi_overbought: float = 90.0, exit_stochrsi_oversold: float = 10.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.exit_stochrsi_overbought = exit_stochrsi_overbought
        self.exit_stochrsi_oversold = exit_stochrsi_oversold


def _walk_forward_signal_exit(ctx: dict, entry_idx: int, entry: dict, strategy: BollingerWickSignalExitStrategy, n: int) -> tuple[str, float, int]:
    """`app.lab_backtest._walk_forward_breakeven_trail()`과 ①②는 같고(고정
    손절 -> 본전 이동), ③만 다르다: ATR 트레일 대신 StochRSI 반대쪽 과열
    문턱을 기다린다. 공용 엔진은 건드리지 않고 이 실험 전용으로 복제함."""
    high, low, close, stochrsi_k = ctx["high"], ctx["low"], ctx["close"], ctx["stochrsi_k"]
    direction = entry["direction"]
    entry_price = entry["entry_price"]
    stop_price = entry["stop_price"]
    trigger_price = entry["breakeven_trigger_price"]
    moved_to_breakeven = False

    for j in range(entry_idx + 1, n):
        if direction == "LONG":
            if not moved_to_breakeven and high[j] >= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if low[j] <= stop_price:
                return ("BE" if moved_to_breakeven else "SL"), stop_price, j
            if moved_to_breakeven:
                k_val = stochrsi_k[j]
                if not np.isnan(k_val) and k_val >= strategy.exit_stochrsi_overbought:
                    return "SIGNAL", float(close[j]), j
        else:
            if not moved_to_breakeven and low[j] <= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if high[j] >= stop_price:
                return ("BE" if moved_to_breakeven else "SL"), stop_price, j
            if moved_to_breakeven:
                k_val = stochrsi_k[j]
                if not np.isnan(k_val) and k_val <= strategy.exit_stochrsi_oversold:
                    return "SIGNAL", float(close[j]), j

    return "TIME", float(close[-1]), n - 1


def simulate_signal_exit(
    df: pd.DataFrame, strategy: BollingerWickSignalExitStrategy, fee_pct: float = 0.0,
) -> list[dict]:
    """`BollingerWickSignalExitStrategy` 전용 백테스트 - `app.lab_backtest.simulate_lab()`과
    같은 진입 탐색 루프를 쓰되, 청산만 위 StochRSI 반대신호 대기 로직으로 대체한다."""
    n = len(df)
    if n < strategy.min_bars:
        return []

    pre = strategy.precompute(df)
    ctx = {
        **pre,
        "close": df["Close"].to_numpy(), "high": df["High"].to_numpy(),
        "low": df["Low"].to_numpy(), "open_": df["Open"].to_numpy(),
    }
    index = df.index

    trades: list[dict] = []
    k = strategy.min_bars - 1
    while k < n:
        entry = strategy.check_entry(k, ctx)
        if entry is None:
            k += 1
            continue

        exit_reason, exit_price, exit_idx = _walk_forward_signal_exit(ctx, k, entry, strategy, n)
        entry_price = entry["entry_price"]
        direction = entry["direction"]
        if direction == "LONG":
            pct = (exit_price - entry_price) / entry_price * 100
        else:
            pct = (entry_price - exit_price) / entry_price * 100

        trades.append({
            "entry_time": str(index[k]),
            "exit_time": str(index[exit_idx]),
            "direction": direction,
            "entry_price": round(float(entry_price), 6),
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason,
            "pct_return": round(pct - fee_pct, 4),
            "gross_pct_return": round(pct, 4),
        })
        k = max(exit_idx + 1, k + 1)

    return trades


class BollingerWickBandwidthAdaptiveTrailStrategy(BollingerWickVolumeStochRsiConfirmStrategy):
    """실계좌 기본 전략과 진입 조건은 완전히 동일하고, 트레일 배수
    (`trail_mult`)만 "그 순간 밴드폭이 평소보다 좁은지/넓은지"에 맞춰
    조정한다. `BollingerWickVolatilityAdaptiveBreakevenStrategy`(ATR 기준,
    본전 이동 트리거 조정)와는 다른 신호(밴드폭 변화율)로 다른 파라미터
    (트레일 배수)를 조정하는 별개의 변형이다.

    아이디어: 밴드가 수축 중(스퀴즈)이면 진짜 평균회귀 구간일 가능성이
    높으니 트레일을 타이트하게 유지해 빨리 챙기고, 밴드가 평소보다
    넓게 벌어져 있으면(변동성 폭발 이후) 추세가 더 이어질 수 있으니
    트레일을 느슨하게 줘서 더 태운다는 가설.

    구현: 진입 시점 밴드폭(상단-하단)을 최근 `bw_lookback`봉 평균 밴드폭으로
    나눈 비율(`bw_ratio`, [`bw_adapt_min`, `bw_adapt_max`]로 클리핑)을
    `trail_mult`에 곱한다. 본전 이동 트리거(`breakeven_at_mult`)는 그대로
    둬서 "손실은 그대로 짧게, 트레일 폭만 밴드 상태에 맞춰 조정"으로
    한정했다. 청산 로직 자체(본전 이동 후 ATR 트레일링)는 바뀌지 않으므로
    기존 `app.lab_backtest.simulate_lab()`을 그대로 쓸 수 있다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_bandwidth_adaptive_trail"
    label = "볼린저 꼬리터치 (밴드폭 적응형 트레일) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "실계좌 기본 전략과 진입 조건 동일 + 트레일 배수를 진입 시점 밴드폭이 "
        "최근 평균보다 좁은지/넓은지에 맞춰 조정(스퀴즈면 타이트, 확장이면 느슨) - 아직 미검증"
    )

    def __init__(self, *args, bw_lookback: int = 50, bw_adapt_min: float = 0.5, bw_adapt_max: float = 2.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.bw_lookback = bw_lookback
        self.bw_adapt_min = bw_adapt_min
        self.bw_adapt_max = bw_adapt_max
        self.min_bars = max(self.min_bars, bw_lookback + 5)

    def precompute(self, df: pd.DataFrame) -> dict:
        ctx = super().precompute(df)
        bb_width = ctx["touch_ctx"]["bb_upper"] - ctx["touch_ctx"]["bb_lower"]
        ctx["bb_width"] = bb_width
        ctx["bb_width_avg"] = pd.Series(bb_width).rolling(self.bw_lookback).mean().to_numpy()
        return ctx

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        e = super().check_entry(k, ctx)
        if e is None:
            return None
        width_now = ctx["bb_width"][k]
        width_avg = ctx["bb_width_avg"][k]
        if np.isnan(width_avg) or width_avg <= 0:
            return e  # 평균 밴드폭을 아직 못 구하면(워밍업 구간) 원래 전략 그대로
        bw_ratio = np.clip(width_now / width_avg, self.bw_adapt_min, self.bw_adapt_max)
        e["trail_mult"] = self.trail_mult * bw_ratio
        return e


class BollingerWickPyramidStrategy(BollingerWickVolumeStochRsiConfirmStrategy):
    """실계좌 기본 전략과 진입 조건·청산 조건(본전 이동 후 ATR 트레일링)은
    완전히 동일하고, 포지션이 열려있는 동안 **같은 방향으로 또 진입 신호가
    뜨면 추가 진입(피라미딩)**한다는 점만 다르다.

    ⚠️ **2026-11 레그별 비중 최적화 중 발견: 이 전략은 실계좌 반영 보류
    상태다.** 2026-01~11월 데이터로 레그별 수익률을 분리해보니, 추가진입이
    실제로 발생한 324건은 1호 진입 평균이 **-0.2846%(손절률 55.6%)**로
    추가진입이 없었던 거래(평균 +0.1813%, 손절률 12.7%)보다 훨씬 나빴다 -
    즉 "같은 방향 신호가 다시 뜬다"는 이 평균회귀 전략에서 추세 지속
    신호가 아니라 "1호 진입이 이미 틀어지고 있다(가격이 불리하게 되돌아가
    밴드에 또 닿았다)"는 신호였다. 레그 수로 사후에 균등 재가중한 최초
    백테스트(+10.6%/+18.3% 개선처럼 보였던 수치)는 체결 전엔 알 수 없는
    정보(추가 레그가 더 나은 가격일지)로 사후 재가중한 통계적 착시였고,
    1호 레그는 항상 베이스라인과 동일 비중으로 고정하고 추가 레그를 순수
    추가자본으로 보는 선견지향 회계로 다시 계산하면 추가 비중을 키울수록
    오히려 전체 수익이 나빠진다(add_weight 0→0.25→0.5→1.0에서 559.3%→
    554.7%→550.0%→540.7%). 이 평균회귀 전략에는 "추세 확인 시 추가진입"이
    아니라 사실상 "물타기"로 작동하므로 **이 클래스는 백테스트 비교 외
    목적으로 쓰면 안 된다.**

    아이디어: 지금까지 테스트한 변형들은 전부 "하나 들어가서 얼마나 오래/
    어떻게 들고 있을지"만 건드렸는데, 이건 "추세가 계속 확인되는 동안
    사이즈를 더 키운다"는 다른 종류의 접근이다 - 승리 거래의 상한을
    실제로 더 키우려는 시도.

    구현: 최초 진입(1호 레그) 이후 포지션이 열려있는 동안, 매 봉마다 같은
    전략으로 진입 조건을 다시 확인해서 같은 방향이면 추가 레그를 연다
    (`max_adds`까지). 손절/본전 이동 트리거/트레일은 전부 **1호 레그
    기준으로 통일**해서 전체 스택을 하나의 포지션처럼 관리한다(레그별로
    따로 손절을 두면 관리가 과도하게 복잡해지고, 실제로도 포지션 전체를
    한 손절선으로 관리하는 게 더 현실적이다). 청산되면 각 레그의 개별
    진입가 → 공통 청산가 수익률을 레그 수만큼 동일 비중으로 평균낸다.

    ⚠️ 이 평균은 "레그당 동일 비중"을 가정한다 - 실제로 추가 진입할 때마다
    그 순간의 가용 잔고/리스크 예산을 다시 나눠야 하므로, 이 숫자는
    실제 자금관리를 반영한 정확한 수익률이 아니라 "레그 단위 평균
    수익률"로의 근사치다(baseline은 항상 레그 1개라 이 근사가 필요 없다 -
    그래서 둘을 total_pct로 직접 비교할 때는 이 비대칭을 감안해야 함).

    체결 로직이 다르므로(멀티레그) `app.lab_backtest.simulate_lab`이 아니라
    전용 백테스트 함수 `simulate_pyramid()`(이 파일 하단)으로 돌려야 한다 -
    공용 백테스트 엔진은 건드리지 않았다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_pyramid"
    label = "볼린저 꼬리터치 (추세 지속 시 추가진입/피라미딩) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "실계좌 기본 전략과 진입/청산 조건 동일 + 포지션이 열려있는 동안 같은 방향 신호가 "
        "다시 뜨면 추가 진입(최대 max_adds회) - 아직 미검증. "
        "simulate_lab()이 아니라 simulate_pyramid()으로 백테스트할 것"
    )

    def __init__(self, *args, max_adds: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_adds = max_adds


def simulate_pyramid(
    df: pd.DataFrame, strategy: BollingerWickPyramidStrategy, fee_pct: float = 0.0,
) -> list[dict]:
    """`BollingerWickPyramidStrategy` 전용 백테스트 - 1호 레그 기준 손절/본전
    이동/트레일은 `app.lab_backtest._walk_forward_breakeven_trail()`과
    완전히 같고, 매 봉마다 추가 진입 기회를 같이 확인한다는 점만 다르다."""
    n = len(df)
    if n < strategy.min_bars:
        return []

    pre = strategy.precompute(df)
    ctx = {
        **pre,
        "close": df["Close"].to_numpy(), "high": df["High"].to_numpy(),
        "low": df["Low"].to_numpy(), "open_": df["Open"].to_numpy(),
    }
    high, low, close, atr_values = ctx["high"], ctx["low"], ctx["close"], ctx["atr"]
    index = df.index

    def _pct(direction: str, entry_price: float, price: float) -> float:
        if direction == "LONG":
            return (price - entry_price) / entry_price * 100
        return (entry_price - price) / entry_price * 100

    trades: list[dict] = []
    k = strategy.min_bars - 1
    while k < n:
        entry = strategy.check_entry(k, ctx)
        if entry is None:
            k += 1
            continue

        direction = entry["direction"]
        leg_entries = [entry["entry_price"]]
        stop_price = entry["stop_price"]
        trigger_price = entry["breakeven_trigger_price"]
        trail_mult = entry["trail_mult"]
        moved_to_breakeven = False
        extreme = high[k] if direction == "LONG" else low[k]

        exit_reason, exit_price, exit_idx = "TIME", float(close[-1]), n - 1
        j = k + 1
        while j < n:
            if direction == "LONG":
                extreme = max(extreme, high[j])
                if not moved_to_breakeven and high[j] >= trigger_price:
                    moved_to_breakeven = True
                    stop_price = entry["entry_price"]
                if moved_to_breakeven:
                    stop_price = max(stop_price, extreme - trail_mult * atr_values[j])
                if low[j] <= stop_price:
                    exit_reason = "TRAIL" if moved_to_breakeven else "SL"
                    exit_price, exit_idx = stop_price, j
                    break
            else:
                extreme = min(extreme, low[j])
                if not moved_to_breakeven and low[j] <= trigger_price:
                    moved_to_breakeven = True
                    stop_price = entry["entry_price"]
                if moved_to_breakeven:
                    stop_price = min(stop_price, extreme + trail_mult * atr_values[j])
                if high[j] >= stop_price:
                    exit_reason = "TRAIL" if moved_to_breakeven else "SL"
                    exit_price, exit_idx = stop_price, j
                    break

            if len(leg_entries) - 1 < strategy.max_adds:
                add = strategy.check_entry(j, ctx)
                if add is not None and add["direction"] == direction:
                    leg_entries.append(add["entry_price"])
            j += 1

        leg_pcts = [_pct(direction, e, exit_price) for e in leg_entries]
        pct = sum(leg_pcts) / len(leg_pcts)

        trades.append({
            "entry_time": str(index[k]),
            "exit_time": str(index[exit_idx]),
            "direction": direction,
            "entry_price": round(float(leg_entries[0]), 6),
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason,
            "legs": len(leg_entries),
            "leg_entry_prices": [round(float(p), 6) for p in leg_entries],
            "pct_return": round(pct - fee_pct, 4),
            "gross_pct_return": round(pct, 4),
        })
        k = max(exit_idx + 1, k + 1)

    return trades


class BollingerWickCooldownStrategy(BollingerWickVolumeStochRsiConfirmStrategy):
    """실계좌 기본 전략과 진입/청산 조건은 완전히 동일하고, "연속으로
    손실 거래가 몇 번 나오면 다음 신호 몇 번은 건너뛴다"는 쿨다운 필터만
    추가한다.

    아이디어: 평균회귀 전략은 "진짜 추세로 전환되는 시점"에 되돌림 실패가
    몰려서 나오는 경향이 있다(밴드를 또 터치해도 되돌아오지 못하고 계속
    밀림). 같은 조합에서 손실이 연속으로 나오면 그 구간은 일단 쉬어가는
    게 "더 먹기"는 아니지만 "나쁜 구간에서 덜 잃기"로 순수익에는 도움이
    될 수 있다는 가설.

    구현: `cooldown_after_losses`번 연속 손실(수익률<=0, 수수료 반영 후
    기준) 직후 뜨는 신호부터 `cooldown_skip_signals`번은 진입하지 않고
    건너뛴다(건너뛴 신호는 승패에 반영되지 않음 - 그냥 쉬는 것). 쿨다운이
    끝나면 연속 손실 카운터를 0으로 리셋하고 평소처럼 복귀한다. 청산
    로직 자체는 바뀌지 않지만, "신호를 건너뛸지"를 매 신호마다 상태
    기반으로 결정해야 해서 전용 백테스트 함수 `simulate_cooldown()`(이
    파일 하단)으로 돌려야 한다 - 공용 백테스트 엔진은 건드리지 않았다.

    ⚠️ 백테스트로 비교 검증하기 전까지는 실계좌 자동매매에 연결하면 안 됨."""

    key = "bollinger_wick_cooldown"
    label = "볼린저 꼬리터치 (연속손실 쿨다운) - 실험용"
    category = "데이트레이딩 (평균회귀 + 본전 이동 트레일링, 변형)"
    description = (
        "실계좌 기본 전략과 진입/청산 조건 동일 + 연속 손실 N번 직후 다음 신호 M번은 "
        "건너뛰는 쿨다운 필터 추가 - 아직 미검증. simulate_lab()이 아니라 simulate_cooldown()으로 백테스트할 것"
    )

    def __init__(self, *args, cooldown_after_losses: int = 2, cooldown_skip_signals: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self.cooldown_after_losses = cooldown_after_losses
        self.cooldown_skip_signals = cooldown_skip_signals


def simulate_cooldown(
    df: pd.DataFrame, strategy: BollingerWickCooldownStrategy, fee_pct: float = 0.0,
) -> list[dict]:
    """`BollingerWickCooldownStrategy` 전용 백테스트 - 청산 로직은
    `app.lab_backtest._walk_forward_breakeven_trail()`과 완전히 같고,
    연속 손실 이후 신호를 건너뛰는 상태만 추가로 추적한다."""
    n = len(df)
    if n < strategy.min_bars:
        return []

    pre = strategy.precompute(df)
    ctx = {
        **pre,
        "close": df["Close"].to_numpy(), "high": df["High"].to_numpy(),
        "low": df["Low"].to_numpy(), "open_": df["Open"].to_numpy(),
    }
    high, low, close, atr_values = ctx["high"], ctx["low"], ctx["close"], ctx["atr"]
    index = df.index

    trades: list[dict] = []
    consecutive_losses = 0
    skip_remaining = 0
    k = strategy.min_bars - 1
    while k < n:
        entry = strategy.check_entry(k, ctx)
        if entry is None:
            k += 1
            continue

        if skip_remaining > 0:
            skip_remaining -= 1
            k += 1
            continue

        direction = entry["direction"]
        entry_price = entry["entry_price"]
        stop_price = entry["stop_price"]
        trigger_price = entry["breakeven_trigger_price"]
        trail_mult = entry["trail_mult"]
        moved_to_breakeven = False
        extreme = high[k] if direction == "LONG" else low[k]

        exit_reason, exit_price, exit_idx = "TIME", float(close[-1]), n - 1
        for j in range(k + 1, n):
            if direction == "LONG":
                extreme = max(extreme, high[j])
                if not moved_to_breakeven and high[j] >= trigger_price:
                    moved_to_breakeven = True
                    stop_price = entry_price
                if moved_to_breakeven:
                    stop_price = max(stop_price, extreme - trail_mult * atr_values[j])
                if low[j] <= stop_price:
                    exit_reason = "TRAIL" if moved_to_breakeven else "SL"
                    exit_price, exit_idx = stop_price, j
                    break
            else:
                extreme = min(extreme, low[j])
                if not moved_to_breakeven and low[j] <= trigger_price:
                    moved_to_breakeven = True
                    stop_price = entry_price
                if moved_to_breakeven:
                    stop_price = min(stop_price, extreme + trail_mult * atr_values[j])
                if high[j] >= stop_price:
                    exit_reason = "TRAIL" if moved_to_breakeven else "SL"
                    exit_price, exit_idx = stop_price, j
                    break

        if direction == "LONG":
            pct = (exit_price - entry_price) / entry_price * 100
        else:
            pct = (entry_price - exit_price) / entry_price * 100
        pct = round(pct - fee_pct, 4)

        trades.append({
            "entry_time": str(index[k]),
            "exit_time": str(index[exit_idx]),
            "direction": direction,
            "entry_price": round(float(entry_price), 6),
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason,
            "pct_return": pct,
            "gross_pct_return": round(pct + fee_pct, 4),
        })

        if pct <= 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0
        if consecutive_losses >= strategy.cooldown_after_losses:
            skip_remaining = strategy.cooldown_skip_signals
            consecutive_losses = 0

        k = max(exit_idx + 1, k + 1)

    return trades


def lab_strategies() -> list[LabStrategy]:
    """실험실에 올라가는 후보 11종 (검증된 켈트너 전략은 별도로 다룸)."""
    return [
        BigCandleBreakoutStrategy(),
        SharpDropBounceStrategy(),
        BollingerReversionStrategy(),
        BollingerBreakoutStrategy(),
        ResistanceBreakFailStrategy(),
        SupportHoldBreakStrategy(),
        BollingerWickTouchStrategy(),
        IchimokuCloudBreakoutStrategy(),
        RsiVolumeSpikeReversalStrategy(),
        BigCandleBollingerConfluenceStrategy(),
        BollingerWickBreakevenTrailStrategy(),
    ]
