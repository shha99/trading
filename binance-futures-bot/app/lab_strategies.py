"""전략 실험실(lab)의 후보 전략 9종.

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


def lab_strategies() -> list[LabStrategy]:
    """실험실에 올라가는 후보 9종 (검증된 켈트너 전략은 별도로 다룸)."""
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
    ]
