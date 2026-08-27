"""app/history.py의 캔들 스파이크 보정(sanitize_klines) 테스트.

바이낸스 테스트넷 과거 K라인에서 실제로 관측된 두 가지 패턴을 재현해 검증한다:
1. 같은 캔들 안에서 고가/저가만 나머지 세 값 대비 비정상적으로 벌어진 경우
   (예: ETHUSDT 1일봉 고가만 104,454.9).
2. 시가/고가/저가/종가가 통째로 몇 분간 요동치는 경우(예: BTCUSDT 1분봉
   2025-04-24 11:50~12:10 구간) - 캔들 자기 자신은 내부적으로 "일관"돼
   보여서 1번 검사로는 못 잡는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.history import sanitize_klines


def _df(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2021-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=idx)


def test_sanitize_klines_fixes_spike_high():
    df = _df([
        {"Open": 2537.20, "High": 2600.00, "Low": 2470.01, "Close": 2498.06, "Volume": 10},
        {"Open": 2537.20, "High": 104454.90, "Low": 2470.01, "Close": 2498.06, "Volume": 10},
    ])
    out = sanitize_klines(df)
    assert out.iloc[0]["High"] == 2600.00  # 정상 캔들은 그대로
    assert out.iloc[1]["High"] == 2537.20  # max(Open, Low, Close)로 보정
    assert out.iloc[1]["Low"] == 2470.01
    assert out.iloc[1]["Close"] == 2498.06


def test_sanitize_klines_fixes_spike_low():
    df = _df([
        {"Open": 56811.0, "High": 56900.0, "Low": 56000.0, "Close": 56500.0, "Volume": 10},
        {"Open": 56811.0, "High": 1000000.0, "Low": 150.0, "Close": 47070.0, "Volume": 10},
    ])
    out = sanitize_klines(df)
    assert out.iloc[1]["High"] == 56811.0  # max(Open, Low(원본), Close)
    assert out.iloc[1]["Low"] == 47070.0  # min(Open, High(원본), Close)


def test_sanitize_klines_leaves_normal_volatility_untouched():
    # 하루에 20% 넘게 움직인 정상 캔들 - 시가/종가 대비 고가/저가 차이가 커도
    # _WICK_FACTOR(1.5배)에는 한참 못 미치므로 그대로 유지되어야 한다.
    df = _df([
        {"Open": 1799.35, "High": 2149.99, "Low": 1733.93, "Close": 2143.66, "Volume": 10},
    ])
    out = sanitize_klines(df)
    pd.testing.assert_frame_equal(out, df)


def test_sanitize_klines_empty_df_returns_as_is():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    out = sanitize_klines(df)
    assert out.empty


def _minute_df(n=120, seed=1, base=92000.0):
    """실제 1분봉과 비슷한 규모의 변동(봉마다 ~0.05~0.2%)을 가진 합성 데이터."""
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(0, base * 0.001, n))
    rows = [{"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000.0} for c in close]
    idx = pd.date_range("2025-01-01", periods=n, freq="min")
    return pd.DataFrame(rows, index=idx)


def test_sanitize_klines_catches_self_consistent_neighbor_outlier_cluster():
    # 실제 관측된 패턴 재현: 캔들 자기 자신의 O/H/L/C끼리는 1.5배 안쪽이라
    # 같은 캔들 검사(_WICK_FACTOR)로는 못 잡지만, 주변 봉(~92,000대) 대비로는
    # 명백히 깨진 값들(47,000~138,000대)이 몇 분간 이어지는 상황.
    df = _minute_df(n=120, seed=2)
    bad_idx = df.index[60:70]
    df.loc[bad_idx[0], ["Open", "High", "Low", "Close"]] = [93374.0, 97273.7, 80000.0, 97000.0]
    df.loc[bad_idx[1], ["Open", "High", "Low", "Close"]] = [97000.0, 99999.0, 75601.3, 91996.8]
    df.loc[bad_idx[2], ["Open", "High", "Low", "Close"]] = [70000.0, 110000.0, 60999.0, 92535.9]
    df.loc[bad_idx[3], ["Open", "High", "Low", "Close"]] = [92590.0, 138000.0, 47000.0, 47000.0]
    df.loc[bad_idx[4], ["Open", "High", "Low", "Close"]] = [47000.0, 138023.7, 47000.0, 92336.3]

    out = sanitize_klines(df)
    fixed = out.loc[bad_idx[:5]]
    # 전부 주변 정상가(~92,000대) 근처로 눌려야 하고, 원래의 극단값이 남아있으면 안 된다
    assert (fixed["High"] < 95000).all() and (fixed["Low"] > 88000).all()
    assert fixed["High"].max() < 100000
    # 오염 구간 바깥의 정상 캔들은 그대로여야 한다
    untouched = df.index.difference(bad_idx[:5])
    pd.testing.assert_frame_equal(out.loc[untouched], df.loc[untouched])


def test_sanitize_klines_leaves_realistic_minute_volatility_untouched():
    df = _minute_df(n=200, seed=3)
    out = sanitize_klines(df)
    pd.testing.assert_frame_equal(out, df)


def test_sanitize_klines_leaves_genuine_large_single_bar_move_untouched():
    # 평소 조용하다가 어느 한 봉에서 실제로 크게(그 시간대 평소 변동폭의
    # 10배 정도로, _NEIGHBOR_FACTOR=20배에는 못 미치게) 움직인 경우는
    # "이상치"로 보정하면 안 된다 - 실제 관측된 오염(수십~수백 배)과는
    # 규모 자체가 다르다.
    df = _minute_df(n=120, seed=4)
    jump_idx = df.index[80]
    prev_close = df.loc[df.index[79], "Close"]
    jumped = prev_close * 1.01  # 이 합성 데이터의 평소 변동폭(~0.1%) 대비 10배 정도의 급등
    df.loc[jump_idx, ["Open", "High", "Low", "Close"]] = [prev_close, jumped, prev_close, jumped]
    out = sanitize_klines(df)
    pd.testing.assert_frame_equal(out, df)


def test_sanitize_klines_leaves_quiet_period_normal_wicks_untouched():
    # 실제 관측된 오탐 패턴: 종가끼리는 거의 안 움직이는 조용한(횡보) 구간에서도,
    # 정상적인 캔들 하나의 고가/저가 꼬리는 종가 변동보다 훨씬 크게 벌어지는 게
    # 흔하다 - "평소 변동폭"을 종가 차이로만 재면 이런 정상 꼬리조차 이상치로
    # 오인한다. true range 기준으로 바꾼 뒤에는 그대로 통과해야 한다.
    # (_NEIGHBOR_WINDOW=31이라 검사 자체가 돌아가려면 최소 31봉이 필요 - 실제
    # 관측된 구간(17봉) 앞뒤를 같은 패턴의 조용한 봉으로 채워 32봉을 만든다.)
    quiet = {"Open": 108000.0, "High": 108020.0, "Low": 108000.0, "Close": 108005.0}
    real_rows = [
        {"Open": 108006.3, "High": 108065.5, "Low": 108000.0, "Close": 108025.8},
        {"Open": 108000.1, "High": 108087.6, "Low": 108000.0, "Close": 108001.0},
        {"Open": 108001.0, "High": 108032.8, "Low": 108000.0, "Close": 108000.1},
        {"Open": 108000.1, "High": 108024.6, "Low": 108000.0, "Close": 108000.0},
        {"Open": 108000.0, "High": 108097.6, "Low": 108000.0, "Close": 108034.6},
        {"Open": 108034.5, "High": 108231.0, "Low": 108018.3, "Close": 108069.1},
        {"Open": 108068.7, "High": 108118.8, "Low": 108064.8, "Close": 108098.9},
        {"Open": 108098.9, "High": 108247.5, "Low": 108000.0, "Close": 108091.8},
        {"Open": 108086.4, "High": 108239.6, "Low": 108083.7, "Close": 108093.5},
        {"Open": 108093.6, "High": 108135.4, "Low": 108084.7, "Close": 108085.6},
        {"Open": 108084.8, "High": 108435.5, "Low": 108000.0, "Close": 108007.8},  # 가장 큰 꼬리
        {"Open": 108007.7, "High": 108019.2, "Low": 108000.0, "Close": 108002.2},
        {"Open": 108000.1, "High": 108127.0, "Low": 108000.0, "Close": 108003.7},
        {"Open": 108003.7, "High": 108054.7, "Low": 108000.0, "Close": 108000.1},
        {"Open": 108000.1, "High": 108203.2, "Low": 108000.0, "Close": 108000.2},
        {"Open": 108010.7, "High": 108021.6, "Low": 108000.1, "Close": 108009.9},
        {"Open": 108009.9, "High": 108043.9, "Low": 108000.1, "Close": 108007.1},
    ]
    rows = [dict(quiet) for _ in range(8)] + real_rows + [dict(quiet) for _ in range(7)]
    for r in rows:
        r["Volume"] = 100.0
    idx = pd.date_range("2025-07-06 04:00:00", periods=len(rows), freq="15min")
    df = pd.DataFrame(rows, index=idx)
    assert len(df) >= 31
    out = sanitize_klines(df)
    pd.testing.assert_frame_equal(out, df)


def test_sanitize_klines_short_series_skips_neighbor_check():
    # 10봉 미만이면 중앙값 기준 자체가 불안정하므로 이웃 봉 검사를 건너뛴다.
    df = _minute_df(n=5, seed=5)
    df.loc[df.index[2], ["Open", "High", "Low", "Close"]] = [92000.0, 92050.0, 91950.0, 92000.0]
    out = sanitize_klines(df)
    pd.testing.assert_frame_equal(out, df)
