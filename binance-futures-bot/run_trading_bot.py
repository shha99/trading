"""순수 매매 루프 진입점 - 웹서버/대시보드 없이 "시그널 감지 → 자동매매 →
포지션 감시"만 24시간 반복하는 헤드리스 프로세스.

**용도**: 집 PC/서버처럼 브라우저·외부 접속 없이 그냥 켜두기만 하면 되는
환경에서 실제(또는 테스트넷) 자동매매를 돌리기 위한 것. `server.py`(FastAPI
대시보드)와 하는 일이 100% 같은 백그라운드 작업만 그대로 재사용하고
(app/signal_engine.py, app/wick_signal_engine.py, app/position_manager.py,
app/wick_position_manager.py 등 - 이미 테스트된 엔진 코드를 새로 만들지
않음), FastAPI/uvicorn/웹소켓/정적파일 서빙/백테스트 성적표 재계산처럼
"화면을 보여주기 위한" 부분만 뺐다. 그래서:

- 무접속시 슬립되는 PaaS 무료 플랜 정책의 영향을 아예 받지 않는다
  (애초에 HTTP 요청을 받는 서버가 아니라서 그런 정책이 걸릴 대상이 없음).
- Render 같은 곳의 "재배포/재시작마다 DB 초기화" 문제도 이 프로세스 자체를
  안 재배포하면 안 겪는다 - 다만 그건 "어디서 실행하느냐"의 문제지 이
  스크립트가 해결해주는 게 아니다: 이 프로세스가 켜져 있는 동안만 매매가
  돌아가므로, 결국 어딘가에는 "항상 켜져 있는 컴퓨터"가 필요한 건 동일하다.

⚠️ **이 프로세스가 살아있는 동안만 동작한다.** 컴퓨터가 꺼지거나 잠들거나
네트워크가 끊기면 그 순간부터:
- 신규 시그널 감지/신규 진입이 멈춘다.
- wick 전략의 "본전 이동 트레일링" 손절 갱신이 멈춘다 (단, 거래소에 이미
  걸려있는 손절 주문 자체는 거래소가 계속 지켜주므로 파국적 손실까지
  막지 못하게 되는 건 아니다 - 다만 유리한 방향으로 손절선을 옮겨 이익을
  지키는 기능만 멈춘다).
- 노트북이라면 반드시 전원 설정에서 "덮개를 닫아도 절전모드 안 들어가기",
  화면보호기/자동 절전 비활성화를 해둘 것. Windows는 작업 스케줄러로,
  macOS/Linux는 `caffeinate`/systemd 등으로 재부팅 시 자동 재시작까지
  구성해두는 걸 강력히 권장한다 (README "헤드리스로 24시간 돌리기" 섹션 참고).

**실행**: `python run_trading_bot.py`
**종료**: Ctrl+C (SIGINT) 또는 SIGTERM - 진행 중인 스캔이 끝나는 대로
멈춘다 (거래소에 이미 나가있는 주문에는 영향 없음 - 취소되지 않는다).
"""
from __future__ import annotations

import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import settings
from app.db import init_db
from app.paper_trading import run_once as run_paper_trading_once
from app.position_manager import check_time_stops, reconcile_open_positions
from app.signal_engine import run_once as run_signal_once
from app.signal_outcome_tracker import check_signal_outcomes
from app.wick_position_manager import manage_wick_positions
from app.wick_signal_engine import run_once as run_wick_signal_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _position_watch_tick() -> None:
    """server.py:_position_watch_tick()과 완전히 동일한 4가지 점검 - 시간손절
    청산 + 켈트너 SL/TP 체결 반영 + (화이트리스트 밖 시그널의) 가상 체결
    결과 갱신 + wick 엔진 트레일링 스탑 갱신/청산 반영."""
    check_time_stops()
    reconcile_open_positions()
    check_signal_outcomes()
    manage_wick_positions()


def _log_startup_banner() -> None:
    mode = "테스트넷" if settings.binance_testnet else "⚠️ 실계좌(LIVE) - 실제 돈이 걸려 있습니다"
    logger.info("=" * 72)
    logger.info("바이낸스 선물 시그널 엔진 - 헤드리스 매매 루프(웹서버 없음)")
    logger.info("모드: %s", mode)
    logger.info(
        "켈트너 자동매매: %s (화이트리스트 %s)",
        "ON" if settings.auto_trade_enabled else "off",
        sorted(f"{s}:{tf}" for s, tf in settings.auto_trade_whitelist) or "없음",
    )
    logger.info(
        "wick(볼린저 꼬리터치+RSI) 자동매매: %s (화이트리스트 %s)",
        "ON" if settings.wick_auto_trade_enabled else "off",
        sorted(f"{s}:{tf}" for s, tf in settings.wick_auto_trade_whitelist) or "없음",
    )
    logger.info(
        "리스크 사이징: %s%s",
        settings.risk_mode,
        f" ({settings.risk_percent_of_balance}% of balance)" if settings.risk_mode == "percent_balance" else f" ({settings.risk_per_trade_usdt} USDT 고정)",
    )
    logger.info("일일 손실 한도(킬스위치): %s USDT", settings.daily_loss_limit_usdt)
    logger.info("=" * 72)
    if not settings.binance_testnet and (settings.auto_trade_enabled or settings.wick_auto_trade_enabled):
        logger.warning("⚠️⚠️⚠️ 실계좌 모드로 자동매매가 켜진 채로 시작합니다 - 진짜 주문이 나갑니다 ⚠️⚠️⚠️")


def _run_initial_scan() -> None:
    """스케줄러의 첫 주기(SCAN_INTERVAL_SECONDS)를 기다리지 않도록, 시작
    직후 한 번씩 즉시 실행한다 (server.py:on_startup()과 동일한 방식)."""
    for fn, name in (
        (run_signal_once, "켈트너 시그널 스캔"),
        (run_wick_signal_once, "wick 시그널 스캔"),
        (run_paper_trading_once, "모의투자 스캔"),
    ):
        try:
            fn()
        except Exception:
            logger.exception("%s 최초 실행 실패 (다음 주기에 재시도)", name)


def main() -> None:
    init_db()
    _log_startup_banner()
    _run_initial_scan()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_signal_once, trigger="interval", seconds=settings.scan_interval_seconds,
        id="signal_scan", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        run_wick_signal_once, trigger="interval", seconds=settings.scan_interval_seconds,
        id="wick_signal_scan", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _position_watch_tick, trigger="interval", seconds=settings.position_watch_interval_seconds,
        id="position_watch", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        run_paper_trading_once, trigger="interval", seconds=settings.scan_interval_seconds,
        id="paper_trading_scan", max_instances=1, coalesce=True,
    )
    logger.info(
        "스케줄러 시작: 시그널 스캔 %d초(켈트너+wick+모의투자), 포지션 점검 %d초 간격 - Ctrl+C로 종료",
        settings.scan_interval_seconds, settings.position_watch_interval_seconds,
    )

    def _handle_term(signum, frame) -> None:  # noqa: ANN001
        logger.info("종료 신호(%s) 수신 - 진행 중인 작업이 끝나는 대로 정지합니다", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_term)

    try:
        scheduler.start()  # 블로킹 - Ctrl+C(SIGINT) 또는 위 SIGTERM 핸들러가 멈출 때까지 계속 돈다
    except (KeyboardInterrupt, SystemExit):
        logger.info("종료 신호 수신 - 스케줄러 정지")


if __name__ == "__main__":
    main()
