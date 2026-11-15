import json, os, time
import pytest, stripe
from fastapi.testclient import TestClient

os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
os.environ["BASE_DELAY_SECONDS"] = "0"
os.environ["MAX_ATTEMPTS"] = "3"

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import importlib, app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app), appmod

def signed(event):
    payload = json.dumps(event)
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{payload}", "whsec_test")
    return payload, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}

def evt(eid="evt_1", typ="checkout.session.completed"):
    return {"id": eid, "type": typ, "data": {"object": {"id": "cs_1", "amount_total": 4900, "currency": "usd"}}}

def test_rejects_bad_signature(client):
    c, _ = client
    r = c.post("/webhook", content=json.dumps(evt()), headers={"stripe-signature": "t=1,v1=bad"})
    assert r.status_code == 400

def test_processes_and_dedupes(client):
    c, appmod = client
    p, h = signed(evt())
    assert c.post("/webhook", content=p, headers=h).json()["status"] == "received"
    assert c.post("/webhook", content=p, headers=h).json()["status"] == "duplicate"
    row = appmod.db().execute("SELECT status, amount FROM orders WHERE id='cs_1'").fetchone()
    assert (row["status"], row["amount"]) == ("paid", 4900)

def test_retry_then_dead_letter(client, monkeypatch):
    c, appmod = client
    def boom(con, obj): raise RuntimeError("downstream CRM 500")
    monkeypatch.setitem(appmod.HANDLERS, "checkout.session.completed", boom)
    p, h = signed(evt("evt_2"))
    c.post("/webhook", content=p, headers=h)
    for _ in range(3):
        c.post("/retry-due")
    status = appmod.db().execute("SELECT status, attempts FROM events WHERE id='evt_2'").fetchone()
    assert (status["status"], status["attempts"]) == ("dead", 3)

def test_retry_recovers(client, monkeypatch):
    c, appmod = client
    calls = {"n": 0}
    orig = appmod.HANDLERS["checkout.session.completed"]
    def flaky(con, obj):
        calls["n"] += 1
        if calls["n"] == 1: raise RuntimeError("transient")
        orig(con, obj)
    monkeypatch.setitem(appmod.HANDLERS, "checkout.session.completed", flaky)
    p, h = signed(evt("evt_3"))
    c.post("/webhook", content=p, headers=h)
    assert c.post("/retry-due").json()["succeeded"] == 1
    assert appmod.db().execute("SELECT status FROM events WHERE id='evt_3'").fetchone()["status"] == "done"
