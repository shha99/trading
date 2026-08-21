import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import cache_store
from app.api import routes


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _sector_etf_fixture():
    dates = pd.bdate_range("2024-01-02", periods=40)
    rng = np.random.default_rng(7)
    base = rng.normal(0, 0.01, 40).cumsum()
    a = 100 * np.exp(base)
    b = 100 * np.exp(-base)
    c = 100 * np.exp(rng.normal(0, 0.01, 40).cumsum())
    prices = pd.DataFrame({"091160": a, "244580": b, "228790": c}, index=dates)
    meta = pd.DataFrame(
        {
            "name": {"091160": "KODEX 반도체", "244580": "KODEX 바이오", "228790": "TIGER 화장품"},
            "theme": {"091160": "반도체", "244580": "바이오", "228790": "화장품"},
            "sector": {"091160": "반도체", "244580": "바이오", "228790": "화장품"},
            "market_cap": {"091160": None, "244580": None, "228790": None},
        }
    ).rename_axis("ticker")
    return prices, meta


def test_sector_instruments_endpoint_lists_etfs(client, monkeypatch):
    prices, meta = _sector_etf_fixture()
    monkeypatch.setattr(cache_store, "load_sector_etf_prices", lambda: prices)
    monkeypatch.setattr(cache_store, "load_sector_etf_meta", lambda: meta)

    r = client.get("/api/sector/instruments", params={"source": "etf"})
    assert r.status_code == 200
    tickers = {row["ticker"] for row in r.json()}
    assert tickers == {"091160", "244580", "228790"}


def test_sector_scan_endpoint_reuses_tiered_fallback(client, monkeypatch):
    """섹터 ETF 3종목(N=3) 조합은 상관계수 후보가 2페어뿐이라, top_n=5를 요청해도
    2개만 있는 그대로 반환돼야 한다(1: 계층적 fallback - 빈 결과 아님, 있는 만큼 표시)."""
    prices, meta = _sector_etf_fixture()
    monkeypatch.setattr(cache_store, "load_sector_etf_prices", lambda: prices)
    monkeypatch.setattr(cache_store, "load_sector_etf_meta", lambda: meta)
    monkeypatch.setattr(cache_store, "load_state", lambda: {})

    r = client.get(
        "/api/sector/scan",
        params={"source": "etf", "start": "2024-01-02", "end": "2024-02-28", "top_n": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pool_size"] == 2
    assert len(body["pairs"]) + len(body["low_significance_pairs"]) == 2
    assert body["same_sector_highlights"] == []  # 섹터/ETF 비교에는 해당 개념이 없음
    top = body["pairs"][0]
    assert {top["stock_a"]["ticker"], top["stock_b"]["ticker"]} == {"091160", "244580"}
    assert top["correlation"] < -0.9  # 구성상 거의 완전한 음의 상관


def test_sector_search_endpoint(client, monkeypatch):
    prices, meta = _sector_etf_fixture()
    monkeypatch.setattr(cache_store, "load_sector_etf_prices", lambda: prices)
    monkeypatch.setattr(cache_store, "load_sector_etf_meta", lambda: meta)
    monkeypatch.setattr(cache_store, "load_state", lambda: {})

    r = client.get(
        "/api/sector/search",
        params={"source": "etf", "ticker": "091160", "start": "2024-01-02", "end": "2024-02-28", "top_n": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["base"]["ticker"] == "091160"
    assert len(body["pairs"]) + len(body["low_significance_pairs"]) == 2


def test_sector_scan_returns_503_without_cache(client, monkeypatch):
    monkeypatch.setattr(cache_store, "load_sector_etf_prices", lambda: pd.DataFrame())
    monkeypatch.setattr(cache_store, "load_sector_etf_meta", lambda: pd.DataFrame())

    r = client.get(
        "/api/sector/scan",
        params={"source": "etf", "start": "2024-01-02", "end": "2024-02-28"},
    )
    assert r.status_code == 503
