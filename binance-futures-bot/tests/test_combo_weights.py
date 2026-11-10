"""조합별 신뢰도 가중치(app/combo_weights.py) 검증.

실제 바이낸스 호출/백테스트는 안 하고(느림), compute_reliability_weights()에
넘기는 `_score_combo`를 가짜로 바꿔 점수만 통제해서 상한/하한 재분배 로직과
load/save/폴백 동작만 검증한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import app.combo_weights as cw
from app.config import settings


@pytest.fixture(autouse=True)
def _isolate_file_and_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(cw, "COMBO_WEIGHTS_FILE", tmp_path / "combo_weights.json")
    monkeypatch.setattr(cw, "CPPI_STATE_FILE", tmp_path / "combo_cppi_state.json")
    original_whitelist = set(settings.wick_auto_trade_whitelist)
    original_enabled = settings.wick_reliability_weighting_enabled
    original_cppi_enabled = settings.wick_cppi_enabled
    yield
    settings.wick_auto_trade_whitelist = original_whitelist
    settings.wick_reliability_weighting_enabled = original_enabled
    settings.wick_cppi_enabled = original_cppi_enabled


def test_equal_scores_give_equal_weights(monkeypatch):
    combos = [("BTCUSDT", "15m"), ("BTCUSDT", "5m"), ("ETHUSDT", "15m"), ("ETHUSDT", "5m")]
    monkeypatch.setattr(cw, "_score_combo", lambda s, tf, strat, fee: 1.0)

    weights = cw.compute_reliability_weights(combos)

    assert set(weights.keys()) == {"BTCUSDT:15m", "BTCUSDT:5m", "ETHUSDT:15m", "ETHUSDT:5m"}
    for w in weights.values():
        assert w == pytest.approx(0.25, abs=1e-6)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_dominant_score_is_capped_at_max_weight(monkeypatch):
    combos = [("A", "15m"), ("B", "15m"), ("C", "15m"), ("D", "15m")]
    scores = {"A": 100.0, "B": 1.0, "C": 1.0, "D": 1.0}
    monkeypatch.setattr(cw, "_score_combo", lambda s, tf, strat, fee: scores[s])

    weights = cw.compute_reliability_weights(combos)

    assert weights["A:15m"] == pytest.approx(cw.MAX_WEIGHT, abs=1e-6)
    assert sum(weights.values()) == pytest.approx(1.0)
    for key in ("B:15m", "C:15m", "D:15m"):
        assert weights[key] >= cw.MIN_WEIGHT - 1e-9


def test_weak_score_is_floored_at_min_weight(monkeypatch):
    combos = [("A", "15m"), ("B", "15m"), ("C", "15m"), ("D", "15m")]
    scores = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 0.01}
    monkeypatch.setattr(cw, "_score_combo", lambda s, tf, strat, fee: scores[s])

    weights = cw.compute_reliability_weights(combos)

    assert weights["D:15m"] == pytest.approx(cw.MIN_WEIGHT, abs=1e-6)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_failed_combo_gets_min_weight_not_excluded(monkeypatch):
    combos = [("A", "15m"), ("B", "15m"), ("C", "15m"), ("D", "15m")]

    def fake_score(symbol, tf, strat, fee):
        return None if symbol == "D" else 1.0

    monkeypatch.setattr(cw, "_score_combo", fake_score)

    weights = cw.compute_reliability_weights(combos)

    assert "D:15m" in weights
    assert weights["D:15m"] == pytest.approx(cw.MIN_WEIGHT, abs=1e-6)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_all_combos_fail_returns_empty(monkeypatch):
    combos = [("A", "15m"), ("B", "15m")]
    monkeypatch.setattr(cw, "_score_combo", lambda *a, **k: None)

    assert cw.compute_reliability_weights(combos) == {}


def test_single_combo_gets_full_weight():
    weights = cw.compute_reliability_weights([("A", "15m")])
    assert weights == {"A:15m": 1.0}


def test_save_and_load_roundtrip(tmp_path):
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    weights = {"A:15m": 0.6, "B:15m": 0.4}
    cw.save_weights(weights)

    loaded = cw.load_weights()
    assert loaded == weights


def test_load_falls_back_to_equal_when_file_missing():
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    assert cw.load_weights() == {"A:15m": 0.5, "B:15m": 0.5}


def test_load_falls_back_when_stale(tmp_path):
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    stale_payload = {
        "computed_at": (datetime.now(timezone.utc) - timedelta(days=cw.STALE_AFTER_DAYS + 1)).isoformat(),
        "lookback_days": 210,
        "weights": {"A:15m": 0.9, "B:15m": 0.1},
    }
    cw.COMBO_WEIGHTS_FILE.write_text(json.dumps(stale_payload), encoding="utf-8")

    assert cw.load_weights() == {"A:15m": 0.5, "B:15m": 0.5}


def test_load_falls_back_when_whitelist_changed(tmp_path):
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    cw.save_weights({"A:15m": 0.6, "B:15m": 0.4})

    settings.wick_auto_trade_whitelist = {("A", "15m"), ("C", "15m")}
    assert cw.load_weights() == {"A:15m": 0.5, "C:15m": 0.5}


def test_get_combo_weight_returns_one_when_feature_disabled():
    settings.wick_reliability_weighting_enabled = False
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    cw.save_weights({"A:15m": 0.9, "B:15m": 0.1})

    assert cw.get_combo_weight("A", "15m") == 1.0


def test_get_combo_weight_scales_around_baseline_when_enabled():
    settings.wick_reliability_weighting_enabled = True
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    cw.save_weights({"A:15m": 0.8, "B:15m": 0.2})

    # baseline = 1/2 = 0.5. A: 0.8/0.5=1.6x, B: 0.2/0.5=0.4x
    assert cw.get_combo_weight("A", "15m") == pytest.approx(1.6)
    assert cw.get_combo_weight("B", "15m") == pytest.approx(0.4)


def test_get_combo_weight_unknown_combo_falls_back_to_one():
    settings.wick_reliability_weighting_enabled = True
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    cw.save_weights({"A:15m": 0.8, "B:15m": 0.2})

    assert cw.get_combo_weight("Z", "99m") == 1.0


def test_recompute_and_save_skips_save_when_all_fail(monkeypatch, tmp_path):
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    monkeypatch.setattr(cw, "_score_combo", lambda *a, **k: None)

    result = cw.recompute_and_save()

    assert result == {}
    assert not cw.COMBO_WEIGHTS_FILE.exists()


class FakeBrokerBalance:
    def __init__(self, balance):
        self.balance = balance

    def get_available_balance_usdt(self):
        return self.balance


class FailingBalanceBroker:
    def get_available_balance_usdt(self):
        raise RuntimeError("조회 실패")


def test_compute_combo_weighted_risk_usdt_scales_with_weight_and_stop_distance():
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    cw.save_weights({"A:15m": 0.8, "B:15m": 0.2})
    broker = FakeBrokerBalance(1000.0)

    # A: 명목가치 = 1000*0.8=800, 손절폭/진입가 = 2/100 = 2% -> risk_usdt = 800*0.02 = 16
    risk_usdt = cw.compute_combo_weighted_risk_usdt(broker, "A", "15m", entry_price=100.0, stop_price=98.0)
    assert risk_usdt == pytest.approx(16.0)

    # B: 명목가치 = 1000*0.2=200, 같은 2% 손절폭 -> risk_usdt = 200*0.02 = 4
    risk_usdt_b = cw.compute_combo_weighted_risk_usdt(broker, "B", "15m", entry_price=100.0, stop_price=98.0)
    assert risk_usdt_b == pytest.approx(4.0)


def test_compute_combo_weighted_risk_usdt_wider_stop_means_more_risk():
    settings.wick_auto_trade_whitelist = {("A", "15m"), ("B", "15m")}
    cw.save_weights({"A:15m": 0.5, "B:15m": 0.5})
    broker = FakeBrokerBalance(1000.0)

    tight = cw.compute_combo_weighted_risk_usdt(broker, "A", "15m", entry_price=100.0, stop_price=99.0)
    wide = cw.compute_combo_weighted_risk_usdt(broker, "A", "15m", entry_price=100.0, stop_price=90.0)
    assert wide > tight


def test_compute_combo_weighted_risk_usdt_falls_back_on_balance_error():
    settings.wick_auto_trade_whitelist = {("A", "15m")}
    cw.save_weights({"A:15m": 1.0})
    risk_usdt = cw.compute_combo_weighted_risk_usdt(
        FailingBalanceBroker(), "A", "15m", entry_price=100.0, stop_price=98.0
    )
    assert risk_usdt == settings.risk_per_trade_usdt


def test_compute_combo_weighted_risk_usdt_falls_back_on_zero_balance():
    settings.wick_auto_trade_whitelist = {("A", "15m")}
    cw.save_weights({"A:15m": 1.0})
    risk_usdt = cw.compute_combo_weighted_risk_usdt(
        FakeBrokerBalance(0.0), "A", "15m", entry_price=100.0, stop_price=98.0
    )
    assert risk_usdt == settings.risk_per_trade_usdt


def test_compute_combo_weighted_risk_usdt_falls_back_when_combo_has_no_weight():
    settings.wick_auto_trade_whitelist = {("A", "15m")}
    cw.save_weights({"A:15m": 1.0})
    risk_usdt = cw.compute_combo_weighted_risk_usdt(
        FakeBrokerBalance(1000.0), "Z", "99m", entry_price=100.0, stop_price=98.0
    )
    assert risk_usdt == settings.risk_per_trade_usdt


def test_compute_combo_weighted_risk_usdt_respects_percent_max_cap(monkeypatch):
    settings.wick_auto_trade_whitelist = {("A", "15m")}
    cw.save_weights({"A:15m": 1.0})
    monkeypatch.setattr(settings, "risk_percent_max_usdt", 5.0)
    broker = FakeBrokerBalance(1000.0)

    # 상한 없으면 1000*1.0*0.02 = 20, 상한 5로 잘려야 함
    risk_usdt = cw.compute_combo_weighted_risk_usdt(broker, "A", "15m", entry_price=100.0, stop_price=98.0)
    assert risk_usdt == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 비대칭 CPPI 사이징 오버레이 (WICK_CPPI_ENABLED)
# ---------------------------------------------------------------------------

def test_get_cppi_multiplier_returns_1_when_disabled():
    settings.wick_cppi_enabled = False
    assert cw.get_cppi_multiplier("BTCUSDT", "15m") == 1.0


def test_get_cppi_multiplier_returns_1_when_no_state_yet():
    settings.wick_cppi_enabled = True
    assert cw.get_cppi_multiplier("BTCUSDT", "15m") == pytest.approx(1.0 + cw.CPPI_BOOST)


def test_get_cppi_multiplier_boosts_at_fresh_peak():
    settings.wick_cppi_enabled = True
    cw.save_cppi_state({"BTCUSDT:15m": {"equity": 1.0, "peak": 1.0}})
    assert cw.get_cppi_multiplier("BTCUSDT", "15m") == pytest.approx(1.0 + cw.CPPI_BOOST)


def test_get_cppi_multiplier_shrinks_during_drawdown():
    settings.wick_cppi_enabled = True
    cw.save_cppi_state({"BTCUSDT:15m": {"equity": 0.9, "peak": 1.0}})  # 10% 드로다운
    expected = max(cw.CPPI_FLOOR, 1.0 - cw.CPPI_SENSITIVITY * 0.10)
    assert cw.get_cppi_multiplier("BTCUSDT", "15m") == pytest.approx(expected)


def test_get_cppi_multiplier_never_below_floor():
    settings.wick_cppi_enabled = True
    cw.save_cppi_state({"BTCUSDT:15m": {"equity": 0.01, "peak": 1.0}})  # 거의 전부 드로다운
    assert cw.get_cppi_multiplier("BTCUSDT", "15m") == pytest.approx(cw.CPPI_FLOOR)


def test_update_cppi_state_after_trade_win_at_peak_applies_boost():
    settings.wick_cppi_enabled = True
    cw.update_cppi_state_after_trade("BTCUSDT", "15m", pct_return=10.0)
    state = cw.load_cppi_state()["BTCUSDT:15m"]
    expected_equity = 1.0 * (1 + (1.0 + cw.CPPI_BOOST) * 10.0 / 100)
    assert state["equity"] == pytest.approx(expected_equity)
    assert state["peak"] == pytest.approx(expected_equity)  # 신고점 갱신


def test_update_cppi_state_after_trade_loss_then_recovery_tracks_peak_correctly():
    settings.wick_cppi_enabled = True
    cw.update_cppi_state_after_trade("BTCUSDT", "15m", pct_return=-10.0)
    after_loss = cw.load_cppi_state()["BTCUSDT:15m"]
    assert after_loss["equity"] < 1.0
    assert after_loss["peak"] == pytest.approx(1.0)  # 손실이어도 고점은 그대로

    cw.update_cppi_state_after_trade("BTCUSDT", "15m", pct_return=1.0)
    after_small_win = cw.load_cppi_state()["BTCUSDT:15m"]
    assert after_small_win["equity"] > after_loss["equity"]
    assert after_small_win["peak"] == pytest.approx(1.0)  # 아직 신고점 회복 전


def test_update_cppi_state_after_trade_noop_when_disabled(tmp_path):
    settings.wick_cppi_enabled = False
    cw.update_cppi_state_after_trade("BTCUSDT", "15m", pct_return=10.0)
    assert not cw.CPPI_STATE_FILE.exists()


def test_reset_cppi_state_for_rebalance_resets_all_combos_to_1():
    settings.wick_cppi_enabled = True
    cw.update_cppi_state_after_trade("BTCUSDT", "15m", pct_return=50.0)
    cw.update_cppi_state_after_trade("ETHUSDT", "5m", pct_return=-20.0)

    cw.reset_cppi_state_for_rebalance([("BTCUSDT", "15m"), ("ETHUSDT", "5m")])

    state = cw.load_cppi_state()
    assert state == {
        "BTCUSDT:15m": {"equity": 1.0, "peak": 1.0},
        "ETHUSDT:5m": {"equity": 1.0, "peak": 1.0},
    }


def test_recompute_and_save_also_resets_cppi_state(monkeypatch):
    settings.wick_cppi_enabled = True
    settings.wick_auto_trade_whitelist = {("BTCUSDT", "15m")}
    cw.update_cppi_state_after_trade("BTCUSDT", "15m", pct_return=50.0)
    monkeypatch.setattr(cw, "_score_combo", lambda s, tf, strat, fee: None)  # 가중치 계산은 실패하게

    cw.recompute_and_save()

    # 가중치 재계산이 실패해도 CPPI 리밸런싱(리셋)은 별개로 수행돼야 함
    assert cw.load_cppi_state()["BTCUSDT:15m"] == {"equity": 1.0, "peak": 1.0}


def test_compute_combo_weighted_risk_usdt_applies_cppi_multiplier(monkeypatch):
    settings.wick_cppi_enabled = True
    settings.wick_auto_trade_whitelist = {("A", "15m")}
    cw.save_weights({"A:15m": 1.0})
    cw.save_cppi_state({"A:15m": {"equity": 0.9, "peak": 1.0}})  # 10% 드로다운 -> 배율 축소
    monkeypatch.setattr(settings, "risk_percent_max_usdt", 0)
    broker = FakeBrokerBalance(1000.0)

    risk_usdt = cw.compute_combo_weighted_risk_usdt(broker, "A", "15m", entry_price=100.0, stop_price=98.0)

    cppi_mult = max(cw.CPPI_FLOOR, 1.0 - cw.CPPI_SENSITIVITY * 0.10)
    expected_notional = 1000.0 * 1.0 * cppi_mult
    expected_risk = expected_notional * 2.0 / 100.0
    assert risk_usdt == pytest.approx(expected_risk)
