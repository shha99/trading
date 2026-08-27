#!/usr/bin/env python3
""""12번 전략"(전략 실험실 카드 순번 기준 — 볼린저 꼬리터치+RSI 확인 + 본전 이동
트레일링, `app/lab_strategies.py::BollingerWickBreakevenTrailStrategy`와 완전히
동일한 로직/파라미터)을 이 저장소 없이도 그대로 돌릴 수 있게 뽑아낸 단일 파일.

⚠️ **이 파일은 실제 주문을 내지 않는다.** 백테스트와 "신호가 떴는지 확인/감시"까지만
한다 — 이 프로젝트 전체에 걸친 정책(검증된 전략이라도 명시적 결정 없이는 자동매매에
안 올린다)과 동일하다. 실거래에 연결하려면 별도로 주문 실행 코드를 붙여야 한다.

의존성은 pandas/numpy/requests 셋뿐이다(원본 저장소의 FastAPI/SQLAlchemy/
python-binance 등에 의존하지 않음) — 그래서 이 파일 하나만 다른 서버/환경에
복사해도 바로 동작한다.

    pip install pandas numpy requests

사용 예:
    # 최근 3년 백테스트 (테스트넷 데이터, 수수료 0.1% 반영)
    python wick_strategy_standalone.py backtest --symbol BTCUSDT --timeframe 15m --years 3

    # 지금 이 순간 신호가 떠 있는지 1회 확인
    python wick_strategy_standalone.py signal --symbol BTCUSDT --timeframe 15m

    # 배포용 - 새 봉이 완결될 때마다 신호를 콘솔에 계속 출력 (주문은 내지 않음)
    python wick_strategy_standalone.py watch --symbol BTCUSDT --timeframe 15m --poll-seconds 60

원본과 100% 같은 결과를 내려면(테스트넷 히스토리 스파이크 보정 포함) 아래
`sanitize_klines()`까지 그대로 거쳐야 한다 — 원본 저장소 `app/history.py`와 동일.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

# ===========================================================================
# 1) 지표 (원본 app/indicators.py 중 이 전략에 필요한 것만 그대로 가져옴)
# ===========================================================================

def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def bollinger_bands(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> dict[str, pd.Series]:
    mid = df["Close"].rolling(period).mean()
    width = df["Close"].rolling(period).std(ddof=0) * num_std
    return {"upper": mid + width, "middle": mid, "lower": mid - width}


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    out = 100 - (100 / (1 + rs))
    return out.where(avg_loss != 0, 100.0)


# ===========================================================================
# 2) 데이터 수집 (python-binance 없이 requests로 바이낸스 선물 공개 REST 직접 호출 -
#    klines 조회는 공개 API라 API 키가 필요 없다)
# ===========================================================================

_BASE_URL = {True: "https://testnet.binancefuture.com", False: "https://fapi.binance.com"}
_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_klines(
    symbol: str, interval: str, limit: int = 500, end_time_ms: int | None = None, testnet: bool = True,
) -> pd.DataFrame | None:
    """가장 최근 `limit`개의 완결/진행 캔들을 오래된→최신 순으로 반환. 실패 시 None."""
    base = _BASE_URL[testnet]
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time_ms is not None:
        params["endTime"] = end_time_ms

    for attempt in range(5):
        try:
            resp = requests.get(f"{base}/fapi/v1/klines", params=params, timeout=15)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if resp.status_code in (418, 429):
            wait_s = float(resp.headers.get("Retry-After", 30))
            print(f"레이트리밋({resp.status_code}) - {wait_s}초 대기 후 재시도", file=sys.stderr)
            time.sleep(wait_s)
            continue
        if resp.status_code != 200:
            print(f"klines 조회 실패: HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr)
            return None
        raw = resp.json()
        if not raw:
            return None
        return _to_dataframe(raw)
    return None


def _to_dataframe(raw: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(
        raw,
        columns=[
            "OpenTime", "Open", "High", "Low", "Close", "Volume",
            "CloseTime", "QuoteVolume", "Trades", "TakerBuyBase", "TakerBuyQuote", "Ignore",
        ],
    )
    df["OpenTime"] = pd.to_datetime(df["OpenTime"], unit="ms")
    df = df.set_index("OpenTime")
    for col in _REQUIRED_COLUMNS:
        df[col] = df[col].astype(float)
    return sanitize_klines(df[_REQUIRED_COLUMNS].sort_index())


_WICK_FACTOR = 1.5
_NEIGHBOR_FACTOR = 20
_NEIGHBOR_WINDOW = 31


def sanitize_klines(df: pd.DataFrame) -> pd.DataFrame:
    """테스트넷 과거 K라인에 간헐적으로 섞여 나오는 명백히 깨진 고가/저가/캔들을 보정한다
    (원본 app/history.py와 동일 로직 - 백테스트 신뢰성을 위해 그대로 유지)."""
    if df.empty:
        return df
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    safe_high = pd.concat([o, l, c], axis=1).max(axis=1)
    safe_low = pd.concat([o, h, c], axis=1).min(axis=1)
    bad_high = h > safe_high * _WICK_FACTOR
    bad_low = safe_low > l * _WICK_FACTOR

    out = df
    if bad_high.any() or bad_low.any():
        out = df.copy()
        if bad_high.any():
            out.loc[bad_high, "High"] = safe_high[bad_high]
        if bad_low.any():
            out.loc[bad_low, "Low"] = safe_low[bad_low]

    if len(out) < _NEIGHBOR_WINDOW:
        return out
    o, h, l, c = out["Open"], out["High"], out["Low"], out["Close"]
    half = _NEIGHBOR_WINDOW // 2
    ref = c.rolling(_NEIGHBOR_WINDOW, center=True, min_periods=half + 1).median()
    prev_close = c.shift(1)
    true_range = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    typical_move = true_range.rolling(_NEIGHBOR_WINDOW, center=True, min_periods=half + 1).median()
    typical_move = typical_move.where(typical_move > 0)
    max_dev = pd.concat([(o - ref).abs(), (h - ref).abs(), (l - ref).abs(), (c - ref).abs()], axis=1).max(axis=1)
    bad_candle = ref.notna() & typical_move.notna() & (max_dev > typical_move * _NEIGHBOR_FACTOR)
    if not bad_candle.any():
        return out
    out = out.copy()
    flat = ref[bad_candle]
    out.loc[bad_candle, ["Open", "High", "Low", "Close"]] = flat.values.reshape(-1, 1)
    return out


def fetch_extended_history(symbol: str, interval: str, total_bars: int, testnet: bool = True) -> pd.DataFrame | None:
    """1500봉 제한을 넘는 과거 데이터를 여러 번 나눠 호출해 이어붙인다 (백테스트용)."""
    chunks: list[pd.DataFrame] = []
    end_time_ms: int | None = None
    remaining = total_bars
    while remaining > 0:
        batch = min(remaining, 1500)
        df = fetch_klines(symbol, interval, limit=batch, end_time_ms=end_time_ms, testnet=testnet)
        if df is None or df.empty:
            break
        chunks.append(df)
        remaining -= len(df)
        end_time_ms = int(df.index[0].timestamp() * 1000) - 1
        if len(df) < batch:
            break
        time.sleep(0.3)  # 레이트리밋 여유
    if not chunks:
        return None
    merged = pd.concat(chunks[::-1])
    return merged[~merged.index.duplicated(keep="first")].sort_index()


def interval_to_timedelta(interval: str) -> pd.Timedelta:
    unit = interval[-1]
    value = int(interval[:-1])
    unit_map = {"m": "min", "h": "h", "d": "D", "w": "W"}
    return pd.Timedelta(value, unit=unit_map.get(unit, "min"))


def is_candle_closed(df: pd.DataFrame, interval: str) -> bool:
    if df is None or df.empty:
        return False
    now = pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    return now >= df.index[-1] + interval_to_timedelta(interval)


# ===========================================================================
# 3) 전략: 볼린저 꼬리터치 되돌림 + RSI 확인 + 본전 이동 트레일링
#    (app/lab_strategies.py::BollingerWickBreakevenTrailStrategy와 파라미터/로직 100% 동일 -
#    검증 완료 기본값: 손절 3×ATR, +0.5×ATR에서 본전 이동, 이후 0.3×ATR 트레일링,
#    RSI(14) ≤40(롱)/≥60(숏)일 때만 진입)
# ===========================================================================

class WickBreakevenTrailStrategy:
    key = "bollinger_wick_breakeven_trail"
    label = "볼린저 꼬리터치 되돌림 (RSI 확인 + 본전 이동 트레일링)"

    def __init__(
        self, bb_period: int = 20, bb_std: float = 2.0, atr_period: int = 14,
        stop_mult: float = 3.0, breakeven_at_mult: float = 0.5, trail_mult: float = 0.3,
        rsi_period: int = 14, rsi_oversold: float = 40.0, rsi_overbought: float = 60.0,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.atr_period = atr_period
        self.stop_mult = stop_mult
        self.breakeven_at_mult = breakeven_at_mult
        self.trail_mult = trail_mult
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        touch_min_bars = max(bb_period, atr_period) + 5
        self.min_bars = max(touch_min_bars, atr_period + 5, rsi_period + 50)

    def precompute(self, df: pd.DataFrame) -> dict:
        bb = bollinger_bands(df, self.bb_period, self.bb_std)
        return {
            "bb_lower": bb["lower"].to_numpy(),
            "bb_upper": bb["upper"].to_numpy(),
            "atr": atr(df, self.atr_period).to_numpy(),
            "rsi": rsi(df["Close"], self.rsi_period).to_numpy(),
            "close": df["Close"].to_numpy(),
            "high": df["High"].to_numpy(),
            "low": df["Low"].to_numpy(),
        }

    def check_entry(self, k: int, ctx: dict) -> dict | None:
        if k < 1:
            return None
        lower_prev, lower_now = ctx["bb_lower"][k - 1], ctx["bb_lower"][k]
        upper_prev, upper_now = ctx["bb_upper"][k - 1], ctx["bb_upper"][k]
        atr_now = ctx["atr"][k]
        if any(np.isnan(v) for v in (lower_prev, lower_now, upper_prev, upper_now, atr_now)) or atr_now <= 0:
            return None

        close, high, low = ctx["close"], ctx["high"], ctx["low"]
        entry_price = float(close[k])

        # 밴드에 "신선하게"(직전 봉엔 안 닿았다가 이번 봉에 처음) 닿았는지 확인
        direction = None
        if low[k] <= lower_now and low[k - 1] > lower_prev:
            direction = "LONG"
        elif high[k] >= upper_now and high[k - 1] < upper_prev:
            direction = "SHORT"
        if direction is None:
            return None

        # RSI 확인 필터: 밴드 터치만으로는 노이즈성 되돌림도 많이 걸려서, 같은 봉의
        # RSI도 과매도/과매수 쪽에 있어야만 진입한다 (실제 수수료 반영 재검증에서
        # 확인된 핵심 개선 포인트 - 자세한 근거는 원본 저장소 README 참고)
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
            "direction": direction, "entry_price": entry_price,
            "stop_price": stop, "breakeven_trigger_price": trigger,
            "trail_mult": self.trail_mult,
        }


# ===========================================================================
# 4) 백테스트 엔진 (원본 app/lab_backtest.py 중 이 전략이 실제로 쓰는 "본전 이동
#    트레일링" 청산 경로만 - 이 전략은 항상 이 청산 방식 하나만 쓰므로 다른 청산
#    분기(고정 익절/동적밴드/순수 트레일링 등)는 이 전략에 대해선 죽은 코드라 뺐다)
# ===========================================================================

def simulate(df: pd.DataFrame, strategy: WickBreakevenTrailStrategy, fee_pct: float = 0.1) -> list[dict]:
    """`fee_pct`: 거래 1건(진입~청산 왕복)당 총 notional 대비 수수료 비용(%) -
    기본값 0.1은 바이낸스 선물 테이커 왕복 수수료(0.05%×2) 가정."""
    n = len(df)
    if n < strategy.min_bars:
        return []

    ctx = strategy.precompute(df)
    index = df.index
    high, low, close = ctx["high"], ctx["low"], ctx["close"]

    trades: list[dict] = []
    k = strategy.min_bars - 1
    while k < n:
        entry = strategy.check_entry(k, ctx)
        if entry is None:
            k += 1
            continue

        exit_reason, exit_price, exit_idx = _walk_forward_breakeven_trail(high, low, close, ctx["atr"], k, entry, n)
        entry_price, direction = entry["entry_price"], entry["direction"]
        pct = (exit_price - entry_price) / entry_price * 100 if direction == "LONG" else (entry_price - exit_price) / entry_price * 100

        trades.append({
            "entry_time": str(index[k]), "exit_time": str(index[exit_idx]),
            "direction": direction,
            "entry_price": round(float(entry_price), 6), "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason,
            "pct_return": round(pct - fee_pct, 4), "gross_pct_return": round(pct, 4),
        })
        k = max(exit_idx + 1, k + 1)  # 포지션 종료 이후부터 다음 진입 탐색 (중복 포지션 방지)

    return trades


def _walk_forward_breakeven_trail(high, low, close, atr_values, entry_idx: int, entry: dict, n: int) -> tuple[str, float, int]:
    """① 진입 직후엔 고정 손절선만 있음 → ② 가격이 트리거에 처음 닿으면 손절선을
    본전(진입가)으로 이동 → ③ 이후 고점/저점 - trail_mult×ATR로 계속 따라감(익절
    상한 없음)."""
    direction = entry["direction"]
    entry_price = entry["entry_price"]
    stop_price = entry["stop_price"]
    trigger_price = entry["breakeven_trigger_price"]
    trail_mult = entry["trail_mult"]
    moved_to_breakeven = False
    extreme = high[entry_idx] if direction == "LONG" else low[entry_idx]

    for j in range(entry_idx + 1, n):
        if direction == "LONG":
            extreme = max(extreme, high[j])
            if not moved_to_breakeven and high[j] >= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if moved_to_breakeven:
                stop_price = max(stop_price, extreme - trail_mult * atr_values[j])
            if low[j] <= stop_price:
                return ("TRAIL" if moved_to_breakeven else "SL"), stop_price, j
        else:
            extreme = min(extreme, low[j])
            if not moved_to_breakeven and low[j] <= trigger_price:
                moved_to_breakeven = True
                stop_price = entry_price
            if moved_to_breakeven:
                stop_price = min(stop_price, extreme + trail_mult * atr_values[j])
            if high[j] >= stop_price:
                return ("TRAIL" if moved_to_breakeven else "SL"), stop_price, j

    return "TIME", float(close[-1]), n - 1


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    pct = [t["pct_return"] for t in trades]
    wins = [p for p in pct if p > 0]
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3),
        "avg_pct_per_trade": round(sum(pct) / len(trades), 4),
        "total_pct": round(sum(pct), 4),
        "best_pct": round(max(pct), 4),
        "worst_pct": round(min(pct), 4),
    }


# ===========================================================================
# 5) CLI
# ===========================================================================

def cmd_backtest(args: argparse.Namespace) -> None:
    total_bars = _bars_for_years(args.timeframe, args.years)
    print(f"{args.symbol} {args.timeframe} 최근 {args.years}년치 데이터 수집 중 (최대 {total_bars}봉, 시간이 걸릴 수 있음)...")
    df = fetch_extended_history(args.symbol, args.timeframe, total_bars, testnet=not args.live)
    if df is None or df.empty:
        print("데이터 수집 실패", file=sys.stderr)
        sys.exit(1)
    print(f"수집 완료: {len(df)}봉 ({df.index[0]} ~ {df.index[-1]})")

    strategy = WickBreakevenTrailStrategy()
    trades = simulate(df, strategy, fee_pct=args.fee_pct)
    stats = summarize(trades)

    print("\n=== 백테스트 결과 (수수료 {:.2f}% 반영) ===".format(args.fee_pct))
    if stats["trades"] == 0:
        print("거래 없음 (기간이 너무 짧거나 신호가 없었음)")
        return
    print(f"거래 수: {stats['trades']}건")
    print(f"승률: {stats['win_rate'] * 100:.1f}%")
    print(f"거래당 평균 수익률: {stats['avg_pct_per_trade']:+.4f}%")
    print(f"총 수익률(단순 합산): {stats['total_pct']:+.2f}%")
    print(f"최고/최악 단일 거래: {stats['best_pct']:+.2f}% / {stats['worst_pct']:+.2f}%")

    if args.out_csv:
        with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            writer.writeheader()
            writer.writerows(trades)
        print(f"\n거래 내역 저장: {args.out_csv} ({len(trades)}행)")


def cmd_signal(args: argparse.Namespace) -> None:
    strategy = WickBreakevenTrailStrategy()
    df = fetch_klines(args.symbol, args.timeframe, limit=max(strategy.min_bars + 10, 300), testnet=not args.live)
    if df is None or df.empty:
        print("데이터 조회 실패", file=sys.stderr)
        sys.exit(1)
    if not is_candle_closed(df, args.timeframe):
        df = df.iloc[:-1]
    if len(df) < strategy.min_bars:
        print("데이터가 부족합니다")
        return

    ctx = strategy.precompute(df)
    k = len(df) - 1
    entry = strategy.check_entry(k, ctx)
    ts = df.index[k]
    if entry is None:
        print(f"[{ts}] 신호 없음")
    else:
        print(f"[{ts}] 🔔 {entry['direction']} 진입 신호 — 가격 {entry['entry_price']:.4f}, "
              f"손절 {entry['stop_price']:.4f}, 본전이동 트리거 {entry['breakeven_trigger_price']:.4f}")
        print("⚠️ 이 스크립트는 주문을 내지 않습니다 - 신호 확인용입니다.")


def cmd_watch(args: argparse.Namespace) -> None:
    """새 봉이 완결될 때마다 신호를 콘솔에 출력한다 (배포용 - 실제 주문은 없음).
    Ctrl+C로 종료."""
    strategy = WickBreakevenTrailStrategy()
    last_seen_ts: pd.Timestamp | None = None
    print(f"{args.symbol} {args.timeframe} 감시 시작 ({'테스트넷' if not args.live else '실계좌 조회'}, "
          f"{args.poll_seconds}초 주기, Ctrl+C로 종료)")
    while True:
        try:
            df = fetch_klines(args.symbol, args.timeframe, limit=max(strategy.min_bars + 10, 300), testnet=not args.live)
            if df is not None and not df.empty:
                if not is_candle_closed(df, args.timeframe):
                    df = df.iloc[:-1]
                if len(df) >= strategy.min_bars:
                    latest_ts = df.index[-1]
                    if last_seen_ts is None or latest_ts > last_seen_ts:
                        ctx = strategy.precompute(df)
                        entry = strategy.check_entry(len(df) - 1, ctx)
                        if entry is not None:
                            print(f"[{latest_ts}] 🔔 {entry['direction']} 진입 신호 — 가격 {entry['entry_price']:.4f}, "
                                  f"손절 {entry['stop_price']:.4f}, 본전이동 트리거 {entry['breakeven_trigger_price']:.4f}")
                        else:
                            print(f"[{latest_ts}] 신호 없음")
                        last_seen_ts = latest_ts
            time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("\n감시 종료")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"오류 (계속 진행): {exc}", file=sys.stderr)
            time.sleep(args.poll_seconds)


def _bars_for_years(timeframe: str, years: float) -> int:
    span = timedelta(days=365 * years)
    return int(span / interval_to_timedelta(timeframe)) + 50  # 지표 워밍업 여유분


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = dict(symbol="BTCUSDT", timeframe="15m")

    p_bt = sub.add_parser("backtest", help="과거 데이터로 백테스트")
    p_bt.add_argument("--symbol", default=common["symbol"])
    p_bt.add_argument("--timeframe", default=common["timeframe"], help="예: 15m, 5m, 1h")
    p_bt.add_argument("--years", type=float, default=3.0, help="최근 몇 년치 데이터로 검증할지 (기본 3년)")
    p_bt.add_argument("--fee-pct", type=float, default=0.1, help="왕복 수수료 %% 가정 (기본 0.1 = 바이낸스 선물 테이커)")
    p_bt.add_argument("--live", action="store_true", help="테스트넷 대신 실계좌 공개 데이터 조회 (주문은 여전히 없음)")
    p_bt.add_argument("--out-csv", default=None, help="거래 내역을 이 경로에 CSV로 저장")
    p_bt.set_defaults(func=cmd_backtest)

    p_sig = sub.add_parser("signal", help="지금 이 순간 신호가 떠 있는지 1회 확인")
    p_sig.add_argument("--symbol", default=common["symbol"])
    p_sig.add_argument("--timeframe", default=common["timeframe"])
    p_sig.add_argument("--live", action="store_true")
    p_sig.set_defaults(func=cmd_signal)

    p_watch = sub.add_parser("watch", help="배포용 - 새 봉마다 신호를 계속 출력 (주문 없음)")
    p_watch.add_argument("--symbol", default=common["symbol"])
    p_watch.add_argument("--timeframe", default=common["timeframe"])
    p_watch.add_argument("--poll-seconds", type=int, default=60)
    p_watch.add_argument("--live", action="store_true")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
