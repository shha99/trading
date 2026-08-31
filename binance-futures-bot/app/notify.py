"""텔레그램 알림.

TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID가 설정되어 있지 않으면 조용히
아무것도 하지 않는다 (알림은 선택 기능이라 미설정이 에러가 되면 안 됨).
전송 실패도 예외를 올리지 않고 로그만 남긴다 — 알림 실패가 시그널
엔진/매매 실행을 막아서는 안 되기 때문.
"""
from __future__ import annotations

import logging

import requests

from .config import settings
from .strategy import Signal

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    try:
        resp = requests.post(
            _API_URL.format(token=settings.telegram_bot_token),
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("텔레그램 전송 실패: %s %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("텔레그램 전송 중 오류")


def notify_signal(signal: Signal, auto_traded: bool) -> None:
    tag = "🤖 자동매매 실행" if auto_traded else "👀 시그널만 (자동매매 대상 아님)"
    text = (
        f"[{signal.symbol} {signal.timeframe}] {signal.signal_type.value} 시그널\n"
        f"{tag}\n"
        f"진입가: {signal.entry_price:.4f}\n"
        f"손절: {signal.stop_price:.4f} / 익절: {signal.target_price:.4f}\n"
        f"시간손절: {signal.time_stop_at.isoformat()}"
    )
    send_message(text)


def notify_trade_closed(symbol: str, timeframe: str, status: str, pnl_usdt: float | None) -> None:
    pnl_text = f"{pnl_usdt:+.2f} USDT" if pnl_usdt is not None else "미확인"
    send_message(f"[{symbol} {timeframe}] 포지션 종료 ({status})\n손익: {pnl_text}")


def notify_wick_entry(symbol: str, timeframe: str, direction: str, entry_price: float, stop_price: float) -> None:
    """볼린저 꼬리터치+RSI 엔진 전용 - 고정 익절이 없어 notify_signal(Signal
    데이터클래스 의존)을 그대로 못 쓰기 때문에 별도 함수로 둔다."""
    send_message(
        f"[{symbol} {timeframe}] {direction} 진입 (볼린저 꼬리터치+RSI, 본전 이동 트레일링)\n"
        f"진입가: {entry_price:.4f}\n"
        f"손절: {stop_price:.4f} (익절 상한 없음 - 트레일링)"
    )
