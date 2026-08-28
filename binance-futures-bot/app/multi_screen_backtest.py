"""4종목(BTCUSDT/ETHUSDT × 15분/5분봉) 동시 스크리닝 백테스트 — 계좌 1개,
포지션 1개만 들고 "먼저 신호가 뜨는 조합"에 진입한다(다른 조합에서 동시에
신호가 떠도 이미 포지션이 있으면 무시). 4개 조합에 자본을 미리 나눠 담는 게
아니라 "지금 이 순간 감시 중인 4개 차트 중 어디든 먼저 신호가 뜨면 그쪽에
잔고 전액×배팅비율만큼 진입"하는 방식이라, 개별 조합(BTC 15분봉 단독 등)
대비 거래 빈도가 크게 늘어난다.

⚠️ **모의투자가 아니라 "설정을 바꿔가며 실험해보는" 백테스트 도구다.**
`/vf`에 이미 떠 있는 실시간 모의투자(BTC 15분봉, 100% 배팅)는 이 기능과
완전히 별개로 계속 그대로 돈다 — 여긴 "배팅비율을 20%/50%/100% 등으로
바꾸면 과거 데이터에서 수익률이 어떻게 달라지는지"를 그 자리에서 계산해
보여주는 용도다.

**배팅비율(%)에 대한 켈리 기준 경고**: 실측 데이터로 "장기 성장률을
최대화하는 배팅비율"을 구해보면 최근 몇 개월처럼 큰 손실 거래가 우연히
안 낀 구간에서는 100%를 넘어 레버리지(200%, 300%...)까지 계속 유리하다고
나온다 — 이건 켈리 공식이 틀린 게 아니라, 그 구간에 표본으로 안 들어온
"진짜 나쁜 손실"(전체 히스토리 기준 최악 -21.9%, 슬리피지 스트레스 테스트
기준 -43.8%)을 반영 못 해서 생기는 착시다. 그래서 UI에서 배팅비율을
자유롭게 입력하게는 해주되(레버리지 실험 포함, 최대 300%), 최악의 실측
손실 대비 실제 계좌 타격도 같이 보여줘 이 착시를 스스로 확인할 수 있게
한다.

동작 원리: `build_merged_trades()`가 4개 조합을 독립적으로
`app.lab_backtest.simulate_lab()`로 백테스트해 combo 태그를 붙여 진입시각
순으로 합친 "원재료" 거래 리스트를 `data/multi_screen_trades.json`에
저장해두면(네트워크 조회가 필요한 무거운 작업 - 몇 분 걸림), `compute_return()`은
그 파일만 읽어서 배팅비율/시작일이 바뀔 때마다 즉석에서(네트워크 없이)
가볍게 복리 계산한다 - 사용자가 %를 바꿀 때마다 몇 분씩 기다리지 않아도 됨.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from .config import DATA_DIR, settings
from .history import fetch_extended_history
from .lab_backtest import simulate_lab
from .lab_strategies import BollingerWickBreakevenTrailStrategy
from .validated_lab_stats_builder import bars_needed_for_span

logger = logging.getLogger(__name__)

MULTI_SCREEN_TRADES_FILE = DATA_DIR / "multi_screen_trades.json"
SCREEN_COMBOS = [("BTCUSDT", "15m"), ("BTCUSDT", "5m"), ("ETHUSDT", "15m"), ("ETHUSDT", "5m")]
DEFAULT_PRINCIPAL = 1_000_000.0
# 실측 최악 단일거래(전체 히스토리) - README/스트레스 테스트 참고. UI가 배팅비율
# 대비 "최악의 경우 계좌가 실제로 얼마나 깎이는지"를 계산해 보여줄 때 쓴다.
WORST_CASE_SINGLE_TRADE_PCT = -21.9
WORST_CASE_STRESS_TEST_PCT = -43.8


def build_merged_trades() -> dict:
    """4개 조합을 각각 통째로 백테스트해서 combo 태그를 붙여 진입시각순으로
    합친 원장을 `MULTI_SCREEN_TRADES_FILE`에 저장한다. 실행에 몇 분 걸릴 수
    있다(4개 조합 각각 3년 이상 데이터를 페이지네이션으로 받아옴)."""
    strategy = BollingerWickBreakevenTrailStrategy()
    fee_pct = settings.taker_fee_pct_roundtrip
    all_trades: list[dict] = []
    per_combo_meta: dict[str, dict] = {}

    for symbol, timeframe in SCREEN_COMBOS:
        total_bars = bars_needed_for_span(timeframe)
        logger.info("멀티스크리닝 데이터 수집: %s %s (최대 %d봉)", symbol, timeframe, total_bars)
        df = fetch_extended_history(symbol, timeframe, total_bars)
        combo_key = f"{symbol} {timeframe}"
        if df is None or df.empty:
            per_combo_meta[combo_key] = {"error": "데이터를 가져오지 못했습니다"}
            continue
        trades = simulate_lab(df, strategy, fee_pct=fee_pct)
        for t in trades:
            t["combo"] = combo_key
        all_trades.extend(trades)
        per_combo_meta[combo_key] = {
            "bars": len(df), "trades": len(trades),
            "range": {"start": str(df.index[0]), "end": str(df.index[-1])},
        }

    all_trades.sort(key=lambda t: t["entry_time"])

    out = {
        "trades": all_trades,
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy.key,
            "combos": [f"{s} {tf}" for s, tf in SCREEN_COMBOS],
            "taker_fee_pct_roundtrip": fee_pct,
            "per_combo": per_combo_meta,
            "worst_case_single_trade_pct": WORST_CASE_SINGLE_TRADE_PCT,
            "worst_case_stress_test_pct": WORST_CASE_STRESS_TEST_PCT,
        },
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MULTI_SCREEN_TRADES_FILE.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    logger.info("멀티스크리닝 거래 원장 저장 완료: %s (%d건)", MULTI_SCREEN_TRADES_FILE, len(all_trades))
    return out


def _load() -> dict | None:
    if not MULTI_SCREEN_TRADES_FILE.exists():
        return None
    return json.loads(MULTI_SCREEN_TRADES_FILE.read_text(encoding="utf-8"))


def _take_trades(trades: list[dict], start_ts: pd.Timestamp | None) -> list[dict]:
    """진입시각순으로 정렬된 거래 목록에서, "포지션 1개만 동시 보유" 제약 하에
    실제로 체결됐을 거래만 골라낸다 - 이미 포지션이 열려 있는 동안(청산 전까지)
    다른 조합에서 뜨는 신호는 건너뛴다."""
    busy_until: pd.Timestamp | None = None
    taken: list[dict] = []
    for t in trades:
        entry_ts = pd.Timestamp(t["entry_time"])
        if start_ts is not None and entry_ts < start_ts:
            continue
        if busy_until is not None and entry_ts < busy_until:
            continue
        taken.append(t)
        busy_until = pd.Timestamp(t["exit_time"])
    return taken


def compute_return(bet_fraction_pct: float, start: str | None = None, principal: float = DEFAULT_PRINCIPAL) -> dict:
    """배팅비율(%)과 시작일을 받아 캐시된 원장으로 즉석에서 복리 계산한다."""
    data = _load()
    if data is None:
        return {"ready": False}

    start_ts = pd.Timestamp(start) if start else None
    taken = _take_trades(data["trades"], start_ts)

    if not taken:
        return {
            "ready": True, "bet_fraction_pct": bet_fraction_pct, "start": start,
            "principal": principal, "trades": 0, "win_rate": None,
            "final_balance": principal, "return_pct": 0.0, "mdd_pct": 0.0,
            "best_pct": None, "worst_pct": None, "combo_counts": {}, "period": None,
        }

    fraction = bet_fraction_pct / 100.0
    balance = principal
    peak = balance
    mdd = 0.0
    wins = 0
    combo_counts: dict[str, int] = {}
    for t in taken:
        balance *= 1 + fraction * t["pct_return"] / 100.0
        peak = max(peak, balance)
        mdd = max(mdd, (peak - balance) / peak * 100)
        if t["pct_return"] > 0:
            wins += 1
        combo_counts[t["combo"]] = combo_counts.get(t["combo"], 0) + 1

    pct_returns = [t["pct_return"] for t in taken]
    worst_pct = min(pct_returns)
    meta = data.get("_meta", {})
    worst_case = meta.get("worst_case_stress_test_pct", WORST_CASE_STRESS_TEST_PCT)

    return {
        "ready": True,
        "bet_fraction_pct": bet_fraction_pct,
        "start": start,
        "principal": principal,
        "trades": len(taken),
        "win_rate": round(wins / len(taken) * 100, 2),
        "final_balance": round(balance, 2),
        "return_pct": round((balance / principal - 1) * 100, 2),
        "mdd_pct": round(mdd, 2),
        "best_pct": round(max(pct_returns), 4),
        "worst_pct": round(worst_pct, 4),
        "combo_counts": combo_counts,
        "period": {"first_entry": taken[0]["entry_time"], "last_exit": taken[-1]["exit_time"]},
        # 스트레스 테스트 최악 시나리오(-43.8%)가 지금 이 배팅비율로 실제로
        # 터지면 계좌가 몇 % 깎이는지 - 켈리/배팅비율 착시를 스스로 확인하는 용도.
        "worst_case_stress_test_pct": worst_case,
        "worst_case_account_impact_pct": round(fraction * abs(worst_case), 2),
    }
