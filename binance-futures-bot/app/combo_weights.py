"""wick 전략 화이트리스트 조합(심볼:시간대)별, 신뢰도 기반 리스크 가중치.

**기본값은 꺼짐이다** (`WICK_RELIABILITY_WEIGHTING_ENABLED=false`) - 이
프로젝트 전체의 원칙대로, 실제 리스크 사이징에 영향을 주는 기능은 명시적
opt-in 전까지 지금까지와 똑같이 동작한다(모든 조합이 RISK_PER_TRADE_USDT를
동일하게 씀).

켜면(opt-in), 매월 1일 00:05 UTC에 스케줄러가 화이트리스트의 각 조합을
과거 `COMBO_WEIGHTS_LOOKBACK_DAYS`일(기본 210일≈7개월) 데이터로 독립
백테스트해서, "거래당 평균수익률 ÷ 표준편차"(변동성 대비 꾸준함, 켈리
공식처럼 승률/수익폭만 보는 것보다 변동성까지 고려하는 신뢰도 점수)를
매기고, 그 점수에 비례해서 가중치를 나눠준다. 한쪽으로 쏠리거나(상한
40%) 0에 가까워지는(하한 10%) 걸 막는다.

결과는 `data/combo_weights.json`에 저장되고, `get_combo_weight()`가 그걸
읽어서 `risk.compute_risk_usdt()`가 계산한 기본 리스크 금액에 곱할
배수(1.0 = 평균/동일가중치 기준선)를 돌려준다 - 신뢰도가 평균보다 높은
조합은 더 크게, 낮은 조합은 더 작게 거는 효과만 추가되고, 전체 평균
리스크 수준 자체는 그대로 유지된다.

⚠️ 과거 데이터 기준 가중치라 미래에도 같은 순위가 유지된다는 보장은
없다 - 그래서 월 1회 자동 재평가한다. 재계산이 실패하거나 파일이
`STALE_AFTER_DAYS`일보다 오래되면 안전하게 "조합별 동일 가중치"로
폴백한다(이 기능이 없는 것과 동일한 동작).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import DATA_DIR, settings
from .history import fetch_extended_history, interval_to_timedelta
from .lab_backtest import simulate_lab
from .lab_strategies import BollingerWickVolumeStochRsiConfirmStrategy

logger = logging.getLogger(__name__)

COMBO_WEIGHTS_FILE = DATA_DIR / "combo_weights.json"
MIN_WEIGHT = 0.10
MAX_WEIGHT = 0.40
MIN_TRADES_FOR_SCORE = 20  # 이보다 표본이 적으면 신뢰도 점수 자체가 불안정해 제외
STALE_AFTER_DAYS = 35


def _combo_key(symbol: str, timeframe: str) -> str:
    return f"{symbol}:{timeframe}"


def _bars_needed(timeframe: str, days: int) -> int:
    span = timedelta(days=days)
    bar_span = interval_to_timedelta(timeframe)
    return int(span / bar_span) + 50  # 지표 워밍업 여유


def _score_combo(symbol: str, timeframe: str, strategy: BollingerWickVolumeStochRsiConfirmStrategy, fee_pct: float) -> float | None:
    bars = _bars_needed(timeframe, settings.combo_weights_lookback_days)
    df = fetch_extended_history(symbol, timeframe, bars)
    if df is None or df.empty:
        logger.warning("%s %s 가중치 계산용 데이터 조회 실패 - 이번 재계산에서 제외", symbol, timeframe)
        return None
    trades = simulate_lab(df, strategy, fee_pct=fee_pct)
    if len(trades) < MIN_TRADES_FOR_SCORE:
        logger.warning("%s %s 표본 부족(%d건 < %d) - 이번 재계산에서 제외", symbol, timeframe, len(trades), MIN_TRADES_FOR_SCORE)
        return None
    returns = pd.Series([t["pct_return"] for t in trades])
    std = returns.std()
    if not std or std <= 0:
        return None
    return float(returns.mean() / std)


def _redistribute_to_bounds(raw_weights: dict[str, float]) -> dict[str, float]:
    """상한(MAX_WEIGHT) 초과분은 다른 조합에 비례 재분배하고, 하한(MIN_WEIGHT)
    미달분은 다른 조합에서 비례 차감한다 - 매 반복마다 현재 상태를 새로
    읽어서 한 쪽만 처리한다(같은 패스에서 두 쪽 다 처리하면, 방금 올려준
    값을 "미달 처리"가 과거 스냅샷 기준으로 다시 깎아버리는 버그가 생김).
    몇 번 반복하면 둘 다 범위 안으로 수렴한다(조합 수가 적어 보통 2~3회
    안에 끝남)."""
    weights = dict(raw_weights)
    for _ in range(40):
        over = {k: w for k, w in weights.items() if w > MAX_WEIGHT + 1e-9}
        if over:
            excess = sum(w - MAX_WEIGHT for w in over.values())
            for k in over:
                weights[k] = MAX_WEIGHT
            flexible = [k for k in weights if k not in over]
            flex_total = sum(weights[k] for k in flexible)
            if flex_total > 0:
                for k in flexible:
                    weights[k] += excess * (weights[k] / flex_total)
            continue

        under = {k: w for k, w in weights.items() if w < MIN_WEIGHT - 1e-9}
        if under:
            deficit = sum(MIN_WEIGHT - w for w in under.values())
            for k in under:
                weights[k] = MIN_WEIGHT
            flexible = [k for k in weights if k not in under]
            flex_total = sum(weights[k] for k in flexible)
            if flex_total > 0:
                for k in flexible:
                    weights[k] -= deficit * (weights[k] / flex_total)
            continue

        break
    return weights


def compute_reliability_weights(combos: list[tuple[str, str]] | None = None) -> dict[str, float]:
    """`combos`(기본값: 현재 WICK_AUTO_TRADE_WHITELIST)의 신뢰도 비례
    가중치를 계산한다. 합계는 항상 1.0 - 점수를 못 구한 조합이 섞여 있으면
    구해진 조합끼리만 나눠 가지고, 전부 실패하면 빈 dict를 돌려준다(호출부가
    그 경우 기존 가중치 파일을 그대로 두도록)."""
    combos = combos or sorted(settings.wick_auto_trade_whitelist)
    if len(combos) < 2:
        # 조합이 1개 이하면 가중치 개념 자체가 의미 없음(항상 100%)
        return {_combo_key(s, tf): 1.0 for s, tf in combos}

    strategy = BollingerWickVolumeStochRsiConfirmStrategy()
    fee_pct = settings.taker_fee_pct_roundtrip
    scores: dict[str, float] = {}
    for symbol, timeframe in combos:
        score = _score_combo(symbol, timeframe, strategy, fee_pct)
        if score is not None and score > 0:
            scores[_combo_key(symbol, timeframe)] = score

    if not scores:
        logger.warning("모든 조합의 신뢰도 점수 계산 실패 - 가중치 재계산 취소")
        return {}

    total = sum(scores.values())
    raw_weights = {k: v / total for k, v in scores.items()}
    weights = _redistribute_to_bounds(raw_weights)

    # 점수를 못 구한 조합(데이터 실패 등)도 whitelist 전체 키로 채워둬야
    # get_combo_weight()가 항상 안전하게 조회할 수 있다 - 0으로 두면
    # 그 조합은 사실상 신규진입 리스크 0이 되니, 대신 하한(MIN_WEIGHT)을
    # 주고 나머지에서 비례 차감한다(완전히 배제하지 않고 보수적으로만 취급).
    # 기존 가중치 합이 이미 1.0인 상태에서 그냥 더하면 합이 1.0을 넘어버리니,
    # 먼저 missing 몫만큼 기존 가중치를 비례 축소해 자리를 만들어준다.
    missing = [_combo_key(s, tf) for s, tf in combos if _combo_key(s, tf) not in weights]
    if missing:
        reserved = MIN_WEIGHT * len(missing)
        existing_total = sum(weights.values())
        if existing_total > 0:
            shrink = (1.0 - reserved) / existing_total
            weights = {k: v * shrink for k, v in weights.items()}
        for k in missing:
            weights[k] = MIN_WEIGHT
        weights = _redistribute_to_bounds(weights)

    return weights


def save_weights(weights: dict[str, float]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": settings.combo_weights_lookback_days,
        "weights": weights,
    }
    COMBO_WEIGHTS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("조합별 리스크 가중치 재계산 완료 및 저장: %s", weights)


def recompute_and_save() -> dict[str, float]:
    """스케줄러(매월 1일)와 수동 재계산에서 공용으로 쓰는 진입점."""
    weights = compute_reliability_weights()
    if weights:
        save_weights(weights)
    return weights


def _equal_weights(combos: list[tuple[str, str]]) -> dict[str, float]:
    n = len(combos)
    if n == 0:
        return {}
    return {_combo_key(s, tf): 1.0 / n for s, tf in combos}


def load_weights() -> dict[str, float]:
    """현재 적용 중인 가중치를 읽는다. 파일이 없거나, `STALE_AFTER_DAYS`일보다
    오래됐거나, 저장된 조합 구성이 지금 화이트리스트와 다르면 안전하게
    "조합별 동일 가중치"로 폴백한다 - 배포 직후나 재계산 실패 중에도 리스크
    사이징이 왜곡되지 않는다(이 기능을 아예 안 쓰는 것과 동일한 동작)."""
    combos = sorted(settings.wick_auto_trade_whitelist)
    if not combos:
        return {}
    fallback = _equal_weights(combos)

    if not COMBO_WEIGHTS_FILE.exists():
        return fallback
    try:
        payload = json.loads(COMBO_WEIGHTS_FILE.read_text(encoding="utf-8"))
        computed_at = datetime.fromisoformat(payload["computed_at"])
        age_days = (datetime.now(timezone.utc) - computed_at).days
        if age_days > STALE_AFTER_DAYS:
            logger.warning("조합 가중치 파일이 %d일 전 거라 오래됨(%d일 초과) - 동일 가중치로 폴백", age_days, STALE_AFTER_DAYS)
            return fallback
        weights = payload["weights"]
        current_keys = {_combo_key(s, tf) for s, tf in combos}
        if set(weights.keys()) != current_keys:
            logger.warning("저장된 가중치의 조합 구성이 현재 화이트리스트와 달라짐 - 동일 가중치로 폴백")
            return fallback
        return weights
    except Exception:
        logger.exception("조합 가중치 파일 읽기 실패 - 동일 가중치로 폴백")
        return fallback


def get_combo_weight(symbol: str, timeframe: str) -> float:
    """`risk.compute_risk_usdt()`가 계산한 기본 리스크 금액에 곱할 배수.

    기능이 꺼져있거나(`WICK_RELIABILITY_WEIGHTING_ENABLED=false`) 가중치를
    못 구했으면 1.0(기존 동작 그대로)을 돌려준다. 켜져 있으면, 가중치가
    "조합 수 분의 1"(동일 분배 기준선)보다 크면 1보다 큰 배수, 작으면 1보다
    작은 배수가 된다 - 모든 조합 가중치가 똑같으면 평균적으로 기존
    RISK_PER_TRADE_USDT 그대로이고, 신뢰도가 높은 조합만 더 크게 거는
    효과만 추가된다."""
    if not settings.wick_reliability_weighting_enabled:
        return 1.0
    weights = load_weights()
    if not weights:
        return 1.0
    key = _combo_key(symbol, timeframe)
    weight = weights.get(key)
    if weight is None:
        return 1.0
    baseline = 1.0 / len(weights)
    if baseline <= 0:
        return 1.0
    return weight / baseline


def compute_combo_weighted_risk_usdt(
    broker, symbol: str, timeframe: str, entry_price: float, stop_price: float,
) -> float:
    """`RISK_MODE=combo_weighted` 전용 사이징 - 계좌를 조합별 신뢰도 비중대로
    나눠 담고(`load_weights()`), 이번 거래는 그 조합에 배분된 몫 전액을
    포지션 규모(증거금이 아니라 **진입가 기준 명목가치**)로 쓴다. 9월~11월
    백테스트 리포트에서 쓴 "조합당 배분액 전액 베팅, 조합별 독립 복리"
    모델을 그대로 실계좌에 옮긴 것이다 - README "실계좌 자동매매" 섹션의
    백테스트 수치가 바로 이 방식 기준이다.

    ⚠️ **`RISK_MODE=fixed`/`percent_balance`보다 훨씬 공격적이다.** 그
    둘은 "거래당 손실액"을 직접 고정하는데(손절폭과 무관하게 항상 같은
    금액만 잃음), 이 모드는 포지션 **명목가치**를 고정하고 손실액은
    "그 거래의 손절폭(%)"에 따라 매번 달라진다 - 손절폭이 넓은 거래일수록
    같은 명목가치라도 손실액이 커진다. `RISK_PERCENT_MAX_USDT`(>0)를
    설정해뒀으면 그 금액을 최종 상한으로 한 번 더 자른다(다른 모드와
    동일한 안전장치를 그대로 공유).

    계산: 명목가치 = 가용잔고 × 그 조합의 가중치(비중, 합계 1.0) → 그
    명목가치가 실제로 나가려면(quantity = risk_usdt / 손절폭) 필요한
    `risk_usdt`를 역산해서 돌려준다 - `broker.enter_position()`의 기존
    리스크기반 주문 로직을 그대로 재사용하기 위함(주문 실행 코드는 전혀
    안 건드림, 숫자만 다르게 넘겨줌).

    잔고 조회 실패/가중치 정보 없음/계산 불가 등 어떤 이유로든 안전하게
    산출 못 하면, 다른 두 모드와 똑같이 `RISK_PER_TRADE_USDT` 고정값으로
    폴백한다."""
    try:
        balance = broker.get_available_balance_usdt()
    except Exception:
        logger.exception("계좌 잔고 조회 실패 - 고정 리스크(RISK_PER_TRADE_USDT)로 폴백")
        return settings.risk_per_trade_usdt

    if not balance or balance <= 0:
        logger.warning("계좌 가용 잔고가 0 이하(%s) - 고정 리스크로 폴백", balance)
        return settings.risk_per_trade_usdt

    weights = load_weights()
    weight = weights.get(_combo_key(symbol, timeframe))
    if weight is None:
        logger.warning("%s %s 조합의 가중치를 못 구함 - 고정 리스크로 폴백", symbol, timeframe)
        return settings.risk_per_trade_usdt

    stop_distance = abs(entry_price - stop_price)
    if entry_price <= 0 or stop_distance <= 0:
        logger.warning("진입가/손절폭이 비정상(entry=%s, stop=%s) - 고정 리스크로 폴백", entry_price, stop_price)
        return settings.risk_per_trade_usdt

    notional_usdt = balance * weight
    risk_usdt = notional_usdt * stop_distance / entry_price
    if settings.risk_percent_max_usdt > 0:
        risk_usdt = min(risk_usdt, settings.risk_percent_max_usdt)
    return risk_usdt
