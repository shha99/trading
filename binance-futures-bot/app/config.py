"""애플리케이션 설정. 전부 환경변수로 오버라이드 가능하다.

안전 기본값 원칙:
- BINANCE_TESTNET 기본 true, AUTO_TRADE_ENABLED 기본 false.
- 자동매매는 AUTO_TRADE_WHITELIST에 명시된 (symbol, timeframe)만 실행된다.
  화이트리스트가 비어있으면 시그널/알림만 동작하고 주문은 절대 나가지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [v.strip() for v in raw.split(",") if v.strip()]


def _parse_whitelist(raw: str) -> set[tuple[str, str]]:
    """"BTCUSDT:1h,ETHUSDT:4h" -> {("BTCUSDT","1h"), ("ETHUSDT","4h")}"""
    pairs: set[tuple[str, str]] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            continue
        symbol, _, timeframe = item.partition(":")
        symbol, timeframe = symbol.strip().upper(), timeframe.strip()
        if symbol and timeframe:
            pairs.add((symbol, timeframe))
    return pairs


@dataclass
class Settings:
    # --- 바이낸스 계정/네트워크 ---
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    binance_api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    binance_testnet: bool = field(default_factory=lambda: _bool("BINANCE_TESTNET", "true"))

    # --- 대상 심볼/시간대 ---
    symbols: list[str] = field(default_factory=lambda: _csv("SYMBOLS", "BTCUSDT,ETHUSDT") or ["BTCUSDT"])
    timeframes: list[str] = field(default_factory=lambda: _csv("TIMEFRAMES", "15m,1h,4h,1d"))

    # --- 전략 파라미터 (켈트너 하단 복귀 + 200EMA) ---
    trend_ema_period: int = field(default_factory=lambda: int(os.getenv("TREND_EMA_PERIOD", "200")))
    keltner_ema_period: int = field(default_factory=lambda: int(os.getenv("KELTNER_EMA_PERIOD", "20")))
    keltner_atr_period: int = field(default_factory=lambda: int(os.getenv("KELTNER_ATR_PERIOD", "10")))
    keltner_atr_mult: float = field(default_factory=lambda: float(os.getenv("KELTNER_ATR_MULT", "2.0")))
    stop_atr_mult: float = field(default_factory=lambda: float(os.getenv("STOP_ATR_MULT", "2.0")))
    target_atr_mult: float = field(default_factory=lambda: float(os.getenv("TARGET_ATR_MULT", "4.0")))
    time_stop_days: float = field(default_factory=lambda: float(os.getenv("TIME_STOP_DAYS", "3")))

    # --- 자동매매 게이트 (기본 전부 안전 쪽) ---
    auto_trade_enabled: bool = field(default_factory=lambda: _bool("AUTO_TRADE_ENABLED", "false"))
    auto_trade_whitelist: set[tuple[str, str]] = field(
        default_factory=lambda: _parse_whitelist(os.getenv("AUTO_TRADE_WHITELIST", ""))
    )
    risk_per_trade_usdt: float = field(default_factory=lambda: float(os.getenv("RISK_PER_TRADE_USDT", "10")))
    leverage: int = field(default_factory=lambda: int(os.getenv("LEVERAGE", "1")))
    max_open_positions: int = field(default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "3")))
    daily_loss_limit_usdt: float = field(default_factory=lambda: float(os.getenv("DAILY_LOSS_LIMIT_USDT", "50")))

    # --- 알림 ---
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # --- 인프라 ---
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'bot.db'}")
    )
    scan_interval_seconds: int = field(default_factory=lambda: int(os.getenv("SCAN_INTERVAL_SECONDS", "60")))
    position_watch_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("POSITION_WATCH_INTERVAL_SECONDS", "300"))
    )
    # /strategy, /lab 페이지의 백테스트 성적표(strategy_stats.json/lab_stats.json/
    # validated_lab_stats.json) 3종을 브라우저를 안 열어놔도 서버가 스스로 주기적으로
    # 다시 계산하는 간격. 서버 프로세스가 켜져 있는 동안만 동작한다(무료 플랜처럼
    # 유휴 시 슬립하는 환경에서는 잠든 동안 갱신도 같이 멈춘다 - README 참고).
    stats_refresh_interval_hours: float = field(
        default_factory=lambda: float(os.getenv("STATS_REFRESH_INTERVAL_HOURS", "24"))
    )
    cors_origins: list[str] = field(default_factory=lambda: _csv("CORS_ORIGINS", "*"))

    # --- 실시간 차트 대시보드 ---
    # 캔들(15m/1h/4h/1d)은 REST 폴링만으로 충분하다(바이낸스 klines 응답
    # 자체가 진행 중인 마지막 봉을 실시간으로 갱신해줌). bookTicker
    # 웹소켓은 "현재가" 숫자 표시 전용.
    live_poll_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("LIVE_POLL_INTERVAL_SECONDS", "3"))
    )
    ticker24h_poll_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("TICKER24H_POLL_INTERVAL_SECONDS", "30"))
    )
    dashboard_timeframes: list[str] = field(
        default_factory=lambda: _csv("DASHBOARD_TIMEFRAMES", "15m,1h,4h,1d")
    )

    # --- 전략 페이지 백테스트 학습/검증 구간 ---
    backtest_train_start: str = field(default_factory=lambda: os.getenv("BACKTEST_TRAIN_START", "2021-07-01"))
    backtest_train_end: str = field(default_factory=lambda: os.getenv("BACKTEST_TRAIN_END", "2025-02-01"))
    backtest_validation_end: str = field(
        default_factory=lambda: os.getenv("BACKTEST_VALIDATION_END", "")
    )  # 비어있으면 "지금"

    @property
    def futures_ws_base_url(self) -> str:
        return "wss://stream.binancefuture.com" if self.binance_testnet else "wss://fstream.binance.com"

    def is_whitelisted(self, symbol: str, timeframe: str) -> bool:
        return self.auto_trade_enabled and (symbol.upper(), timeframe) in self.auto_trade_whitelist


settings = Settings()
