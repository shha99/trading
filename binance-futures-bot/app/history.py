"""바이낸스 선물 과거/최신 캔들(klines) 조회.

REST 폴링만 사용한다 (이 프로젝트가 뜨는 네트워크 환경에서 선물 웹소켓의
kline/aggTrade 스트림이 막혀 있는 경우가 있었다는 것이 원본 프로젝트의
관찰이었음 — 15분봉 이상 전략에는 REST 폴링으로 충분하고, 분당 호출량도
바이낸스 제한의 극히 일부라 굳이 웹소켓이 필요 없다).

429(요청 과다)/418(IP 일시 차단) 응답을 받으면 Retry-After 헤더(또는 지수
백오프)를 존중해 자동으로 대기 후 재시도한다.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
from binance.exceptions import BinanceAPIException, BinanceRequestException

from .binance_client import get_binance_client

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
_MAX_RETRIES = 5


def _sleep_seconds_for(exc) -> float:
    """429/418 예외에서 얼마나 대기해야 할지 계산한다."""
    response = getattr(exc, "response", None)
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass
    return 30.0


def fetch_klines(
    symbol: str, interval: str, limit: int = 500, end_time_ms: int | None = None
) -> pd.DataFrame | None:
    """가장 최근 `limit`개의 완결/진행 캔들을 오래된 -> 최신 순으로 반환한다.

    `end_time_ms`를 주면 그 시각 이전 구간을 조회한다 (백테스트에서 여러 번
    호출해 1500봉 제한보다 긴 과거 데이터를 이어붙일 때 사용).

    컬럼: Open/High/Low/Close/Volume, 인덱스: 캔들 오픈 시각(UTC, tz 없음).
    실패(재시도 소진 포함)하면 None.
    """
    client = get_binance_client()
    attempt = 0
    while attempt < _MAX_RETRIES:
        try:
            kwargs = {"symbol": symbol, "interval": interval, "limit": limit}
            if end_time_ms is not None:
                kwargs["endTime"] = end_time_ms
            raw = client.futures_klines(**kwargs)
            return _to_dataframe(raw)
        except BinanceAPIException as exc:
            status = getattr(exc, "status_code", None)
            if status in (418, 429):
                wait_s = _sleep_seconds_for(exc)
                logger.warning(
                    "바이낸스 레이트리밋(%s) - %s초 대기 후 재시도 (%s %s, %d/%d)",
                    status, wait_s, symbol, interval, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(wait_s)
                attempt += 1
                continue
            logger.exception("바이낸스 klines 조회 실패: %s %s", symbol, interval)
            return None
        except BinanceRequestException:
            logger.exception("바이낸스 klines 요청 오류: %s %s", symbol, interval)
            return None
        except Exception:
            logger.exception("klines 조회 중 알 수 없는 오류: %s %s", symbol, interval)
            return None

    logger.error("klines 조회 재시도 소진: %s %s", symbol, interval)
    return None


def _to_dataframe(raw: list[list]) -> pd.DataFrame | None:
    if not raw:
        return None
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


_WICK_FACTOR = 1.5  # 이 배수를 넘는 고가/저가는 "명백히 깨진 값"으로 간주해 보정한다.


def sanitize_klines(df: pd.DataFrame) -> pd.DataFrame:
    """바이낸스 테스트넷 과거 K라인에 간헐적으로 섞여 나오는 명백히 깨진 고가/저가를 보정한다.

    실제로 관측된 사례(테스트넷 히스토리에서만 나타남, 실계좌에서는 보고된 바 없음):
    ETHUSDT 1일봉에서 Open/Low/Close는 전부 ~2,500대인데 High만 104,454.9로 찍힌다거나,
    BTCUSDT 1일봉에서 Low가 100~150까지 떨어졌다가 바로 다음 값이 정상으로 돌아오는 식이다.
    실제 가격이 한 캔들 안에서 시가/종가 대비 이 정도(수십~수백 배)로 벌어지는 일은 없으므로,
    `_WICK_FACTOR`(1.5배)를 넘는 High/Low는 스파이크로 보고 나머지 세 값(Open/Low/Close 혹은
    Open/High/Close) 중 가장 근접한 값으로 눌러준다. 실제 변동성이 큰 정상 캔들(예: 하루에
    20% 넘게 움직인 캔들)도 시가/종가 대비 고가·저가 차이가 이 배수 근처까지 가는 일은
    없어 오탐 위험은 낮다. ATR/EMA 등 모든 지표와 백테스트가 이 함수를 거친 데이터로만
    계산되도록 `_to_dataframe()`에서 한 번만 호출한다(라이브 조회·백테스트 조회 모두
    `fetch_klines()`를 거치므로 자동으로 적용됨).
    """
    if df.empty:
        return df
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    safe_high = pd.concat([o, l, c], axis=1).max(axis=1)
    safe_low = pd.concat([o, h, c], axis=1).min(axis=1)

    bad_high = h > safe_high * _WICK_FACTOR
    bad_low = safe_low > l * _WICK_FACTOR

    if not bad_high.any() and not bad_low.any():
        return df

    out = df.copy()
    if bad_high.any():
        n = int(bad_high.sum())
        logger.warning("klines 스파이크 %d건 보정 (High 이상치) - %s", n, list(df.index[bad_high][:5]))
        out.loc[bad_high, "High"] = safe_high[bad_high]
    if bad_low.any():
        n = int(bad_low.sum())
        logger.warning("klines 스파이크 %d건 보정 (Low 이상치) - %s", n, list(df.index[bad_low][:5]))
        out.loc[bad_low, "Low"] = safe_low[bad_low]
    return out


def is_candle_closed(df: pd.DataFrame, interval: str) -> bool:
    """마지막 행이 완결된 캔들인지(진행 중인 캔들이 아닌지) 판단한다."""
    if df is None or df.empty:
        return False
    last_open = df.index[-1]
    delta = interval_to_timedelta(interval)
    return pd.Timestamp.utcnow().tz_localize(None) >= last_open + delta


def interval_to_timedelta(interval: str) -> pd.Timedelta:
    unit = interval[-1]
    value = int(interval[:-1])
    unit_map = {"m": "min", "h": "h", "d": "D", "w": "W"}
    return pd.Timedelta(value, unit=unit_map.get(unit, "min"))


def fetch_extended_history(symbol: str, interval: str, total_bars: int) -> pd.DataFrame | None:
    """1500봉 제한을 넘는 과거 데이터를 여러 번 나눠 호출해 이어붙인다.

    백테스트(sanity check)용 — 실거래 스캔에서는 사용하지 않는다.
    """
    chunks: list[pd.DataFrame] = []
    end_time_ms: int | None = None
    remaining = total_bars

    while remaining > 0:
        batch = min(remaining, 1500)
        df = fetch_klines(symbol, interval, limit=batch, end_time_ms=end_time_ms)
        if df is None or df.empty:
            break
        chunks.append(df)
        remaining -= len(df)
        earliest = df.index[0]
        end_time_ms = int(earliest.timestamp() * 1000) - 1
        if len(df) < batch:
            break  # 더 이상 과거 데이터가 없음
        time.sleep(0.3)  # 레이트리밋 여유

    if not chunks:
        return None
    merged = pd.concat(chunks[::-1])
    return merged[~merged.index.duplicated(keep="first")].sort_index()
