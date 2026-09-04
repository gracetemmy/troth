"""The HTTP surface the dashboard talks to."""

import pytest
from starlette.testclient import TestClient

from troth import memory as M
from troth import server


@pytest.fixture
def client(tmp_path):
    app = server.build_app(str(tmp_path / "api.db"))
    M.remember_pricing_rule(M.connect(app.state.db_path), M.MAX_DISCOUNT_KEY, 15)
    return TestClient(app)


def test_overview_shape(client):
    d = client.get("/api/overview").json()
    for key in ("clients", "promises", "flagged", "archived",
                "commitments", "overdue", "max_discount_pct", "events"):
        assert key in d, key


def test_ingest_then_read_back(client):
    r = client.post("/api/ingest", json={
        "client": "meridian",
        "transcript": ("Rep: I can offer you a 25% bundle discount.\n"
                       "Rep: I'll send the quote by Friday.\n"
                       "Dara: We are Meridian Labs and I am the CTO."),
    })
    assert r.status_code == 200
    assert r.json()["tally"]["WARM"] == 3

    d = client.get("/api/overview").json()
    assert d["clients"] == 1 and d["flagged"] == 1 and d["commitments"] == 1


@pytest.mark.parametrize("payload,code", [
    ({"client": "x", "transcript": "   "}, 400),
    ({"client": "", "transcript": "Rep: hello there"}, 400),
])
def test_ingest_rejects_bad_input(client, payload, code):
    assert client.post("/api/ingest", json=payload).status_code == code


@pytest.mark.parametrize("name", [
    "memory", "clients", "promises", "flags", "deals",
    "policies", "audit", "commitments",
])
def test_every_sidebar_view_responds(client, name):
    d = client.get(f"/api/view/{name}").json()
    assert "title" in d and isinstance(d["rows"], list)


def test_unknown_view_is_404(client):
    assert client.get("/api/view/nope").status_code == 404


def test_resolve_updates_status(client):
    client.post("/api/ingest", json={
        "client": "meridian",
        "transcript": "Rep: I can offer you a 25% bundle discount.",
    })
    pid = client.get("/api/overview").json()["flagged_items"][0]["id"]

    r = client.post("/api/resolve", json={"id": pid, "decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == M.CONFIRMED
    assert client.get("/api/overview").json()["flagged"] == 0


def test_resolve_rejects_unknown_id_and_bad_decision(client):
    assert client.post("/api/resolve",
                       json={"id": "nope", "decision": "approve"}).status_code == 404


def test_export_returns_a_real_workbook(client):
    client.post("/api/ingest", json={
        "client": "nimbus", "transcript": "Rep: I'll send the quote by Friday.",
    })
    r = client.get("/api/export.xlsx?scope=all")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.content[:2] == b"PK"          # a real zip, i.e. a real xlsx


def test_export_bad_dates_are_ignored_not_fatal(client):
    assert client.get("/api/export.xlsx?from=not-a-date").status_code == 200


def test_dashboard_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Troth" in r.content
