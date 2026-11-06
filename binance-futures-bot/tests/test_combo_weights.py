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
    original_whitelist = set(settings.wick_auto_trade_whitelist)
    original_enabled = settings.wick_reliability_weighting_enabled
    yield
    settings.wick_auto_trade_whitelist = original_whitelist
    settings.wick_reliability_weighting_enabled = original_enabled


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
