"""네트워크 없이 DART 응답을 흉내 내서 정규화/폴링/API를 검증한다."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import store as store_mod
from app.dart import DartClient, DartError, normalize, tags_for


def raw(n: int, name: str = "삼성전자", title: str = "주요사항보고서", rm: str = "", cls: str = "Y", code: str = "005930"):
    return {
        "corp_code": "00126380", "corp_name": name, "stock_code": code, "corp_cls": cls,
        "report_nm": title, "rcept_no": f"20261012{n:06d}", "flr_nm": name, "rcept_dt": "20261012", "rm": rm,
    }


class FakeClient(DartClient):
    """페이지당 2건, rcept_no 내림차순으로 돌려주는 가짜 DART."""

    def __init__(self, rows):
        super().__init__("test-key")
        self.rows = rows
        self.calls = 0
        self.fail = False

    def fetch_page(self, date, page_no):
        self.calls += 1
        if self.fail:
            raise DartError("boom")
        rows = sorted(self.rows, key=lambda r: r["rcept_no"], reverse=True)
        per = 2
        total = max(1, -(-len(rows) // per))
        chunk = rows[(page_no - 1) * per: page_no * per]
        return [normalize(r) for r in chunk], total if rows else 0


def test_normalize_flags_and_tags():
    it = normalize(raw(1, title="[기재정정]단일판매ㆍ공급계약체결", rm="유정"))
    assert it["market"] == "유가"
    assert it["is_exchange"] is True
    assert it["is_correction"] is True
    assert "계약" in it["tags"] and it["important"]
    assert it["url"].endswith("rcpNo=20261012000001")

    plain = normalize(raw(2, title="임원ㆍ주요주주특정증권등소유상황보고서", rm="", cls="K"))
    assert plain["market"] == "코스닥"
    assert plain["is_exchange"] is False and plain["is_correction"] is False
    assert plain["tags"] == [] and plain["important"] is False


def test_tags_ignore_spaces():
    assert "최대주주" in tags_for("최대주주 변경")
    assert "실적" in tags_for("연결재무제표기준영업(잠정)실적(공정공시)")


def test_poll_initial_then_incremental(monkeypatch):
    monkeypatch.setattr(store_mod, "today_kst", lambda: "20261012")
    client = FakeClient([raw(i) for i in range(1, 6)])
    s = store_mod.DisclosureStore(client)

    assert s.poll_today() == 5
    first = s.today()
    assert len(first["items"]) == 5
    assert all(it["first_seen"] is None for it in first["items"])  # 첫 적재분은 감지 시각 없음
    seq_after_initial = first["seq"]

    client.rows.append(raw(6, name="SK하이닉스", code="000660"))
    client.calls = 0
    assert s.poll_today() == 1
    assert client.calls == 1  # 1페이지에 아는 공시가 섞여 있어 거기서 멈춤

    inc = s.today(after_seq=seq_after_initial)
    assert [it["rcept_no"] for it in inc["items"]] == ["20261012000006"]
    assert inc["items"][0]["first_seen"] is not None

    client.calls = 0
    assert s.poll_today() == 0
    assert client.calls == 1


def test_poll_error_is_reported_and_recovers(monkeypatch):
    monkeypatch.setattr(store_mod, "today_kst", lambda: "20261012")
    client = FakeClient([raw(1)])
    client.fail = True
    s = store_mod.DisclosureStore(client)
    assert s.poll_today() == 0
    assert s.status()["last_error"] == "boom"
    client.fail = False
    assert s.poll_today() == 1
    assert s.status()["last_error"] is None


def test_day_rollover_resets(monkeypatch):
    day = {"v": "20261012"}
    monkeypatch.setattr(store_mod, "today_kst", lambda: day["v"])
    client = FakeClient([raw(1), raw(2)])
    s = store_mod.DisclosureStore(client)
    s.poll_today()
    day["v"] = "20261013"
    client.rows = [raw(9)]
    s.poll_today()
    assert [it["rcept_no"] for it in s.today()["items"]] == ["20261012000009"]
    assert s.today()["date"] == "20261013"


def test_missing_api_key_raises():
    with pytest.raises(DartError):
        DartClient("").fetch_page("20261012", 1)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    import server

    monkeypatch.setattr(server.settings, "enable_scheduler", False)
    monkeypatch.setattr(store_mod, "today_kst", lambda: "20261012")
    monkeypatch.setattr(server, "today_kst", lambda: "20261012")
    fake = store_mod.DisclosureStore(FakeClient([raw(1), raw(2), raw(3)]))
    fake.poll_today()
    monkeypatch.setattr(server, "store", fake)
    with TestClient(server.app) as c:
        yield c


def test_api_today_and_past(api):
    r = api.get("/api/disclosures")
    assert r.status_code == 200
    body = r.json()
    assert body["is_today"] and len(body["items"]) == 3 and body["boot"]

    r = api.get("/api/disclosures", params={"after_seq": body["seq"]})
    assert r.json()["items"] == []

    r = api.get("/api/disclosures", params={"date": "20261001"})
    assert r.status_code == 200 and r.json()["is_today"] is False

    assert api.get("/api/disclosures", params={"date": "2026-10-01"}).status_code == 400
    assert api.get("/api/disclosures", params={"date": "20991231"}).status_code == 400
    assert api.get("/api/status").json()["api_key_set"] is True
    assert "공시 포털" in api.get("/").text


class OrderedClient(DartClient):
    """rows를 주어진 순서(=DART 최신순) 그대로 한 페이지로 돌려준다."""

    def __init__(self, rows):
        super().__init__("test-key")
        self.rows = rows

    def fetch_page(self, date, page_no):
        return [normalize(r) for r in self.rows], 1


def test_order_follows_dart_not_rcept_no(monkeypatch):
    # 거래소 공시는 접수번호가 800/900번대라 번호순으로 정렬하면 순서가 틀어진다
    monkeypatch.setattr(store_mod, "today_kst", lambda: "20261012")
    client = OrderedClient([raw(800155, rm="유"), raw(24), raw(900154, rm="코"), raw(1)])
    s = store_mod.DisclosureStore(client)
    s.poll_today()
    assert [it["rcept_no"][-6:] for it in s.today()["items"]] == ["800155", "000024", "900154", "000001"]

    client.rows = [raw(30)] + client.rows  # 새 일반 공시가 맨 위로
    assert s.poll_today() == 1
    assert s.today()["items"][0]["rcept_no"].endswith("000030")

    past = s.for_date("20261008")["items"]
    assert [it["rcept_no"][-6:] for it in past] == ["000030", "800155", "000024", "900154", "000001"]


def test_title_whitespace_collapsed():
    assert normalize(raw(1, title="주주총회소집결의              (임시주주총회)"))["report_nm"] == "주주총회소집결의 (임시주주총회)"
