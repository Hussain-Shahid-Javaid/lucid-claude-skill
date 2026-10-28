import pytest
from fastapi.testclient import TestClient

from lucid.server.app import _hash, create_app

TOKEN = "test-token-123"


@pytest.fixture
def client(L):
    app = create_app(L, token_map={_hash(TOKEN): "default", _hash("bob-token"): "bob"},
                     allowed_hosts=["127.0.0.1", "localhost", "testserver"], start_timers=False)
    with TestClient(app) as c:
        yield c


H = {"X-Lucid-Token": TOKEN}


def test_requires_token_and_rejects_cross_origin(client):
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Lucid-Token": "wrong"}).status_code == 401
    assert client.get("/api/health", headers=H).status_code == 200
    r = client.post("/api/memories", json={"content": "x"}, headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.get("/api/health", headers={**H, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.get("/api/health", headers={**H, "Host": "attacker.example"})
    assert r.status_code == 421  # DNS rebinding guard
    assert "frame-ancestors 'none'" in client.get("/api/health", headers=H).headers["content-security-policy"]


def test_memory_workflow_and_user_isolation(client):
    m = client.post("/api/memories", json={"content": "Prefers dark mode", "kind": "preference"}, headers=H).json()
    assert m["origin"] == "user"
    hits = client.get("/api/memories", params={"q": "dark mode"}, headers=H).json()["items"]
    assert hits[0]["id"] == m["id"]
    bob = {"X-Lucid-Token": "bob-token"}
    assert client.get("/api/memories", params={"q": "dark mode"}, headers=bob).json()["items"] == []
    assert client.get(f"/api/memories/{m['id']}", headers=bob).status_code == 404
    c = client.post(f"/api/memories/{m['id']}/correct", json={"content": "Prefers light mode"}, headers=H).json()
    assert client.delete(f"/api/memories/{c['id']}", headers=H).json()["deleted"]


def test_secrets_redacted_in_responses(client, L):
    L.store.log("x", "test", detail={"k": "AKIAEXAMPLEABCDEFGHI"})
    body = client.get("/api/activity", headers=H).text
    assert "AKIAEXAMPLEABCDEFGHI" not in body


def test_capture_requires_enabled_site_and_rejects_failed_extraction(client, L):
    payload = {"url": "https://chat.example.com/c/1", "kind": "selection", "text": "useful snippet",
               "extraction": {"ok": True}}
    assert client.post("/api/capture", json=payload, headers=H).status_code == 403
    client.post("/api/capture/sites", json={"site": "chat.example.com", "enabled": True}, headers=H)
    assert client.post("/api/capture", json=payload, headers=H).status_code == 200
    auto = {**payload, "trigger": "auto"}
    assert client.post("/api/capture", json=auto, headers=H).status_code == 403
    bad = {**payload, "extraction": {"ok": False, "errors": ["selector .message not found"]}}
    r = client.post("/api/capture", json=bad, headers=H)
    assert r.status_code == 403 and "couldn't read the conversation" in r.json()["detail"]
    client.post("/api/capture/sites", json={"site": "chat.example.com", "enabled": True, "paused": True}, headers=H)
    assert client.post("/api/capture", json=payload, headers=H).status_code == 403
    client.post("/api/capture/sites", json={"site": "chat.example.com", "enabled": False}, headers=H)
    assert client.post("/api/capture", json=payload, headers=H).status_code == 403  # stopping when disabled


def test_questions_endpoint_and_sharing_preview(client, L):
    L.decisions.authorize("presentation")
    q = L.decisions.ask("Theme?", ["light", "dark"], category="presentation", default="light")
    data = client.get("/api/questions", headers=H).json()
    assert data["questions"][0]["on_timeout"].startswith("default 'light'")
    r = client.post(f"/api/questions/{q['id']}/answer", json={"option": "dark"}, headers=H).json()
    assert r["status"] == "answered"
    prev = client.post("/api/handoff/preview", json={"destination": "gemini", "goal": "x"}, headers=H).json()
    assert prev["preview"] is True


def test_cross_user_isolation_on_every_mutating_path(client, L):
    from lucid.memory import Scope
    bob = {"X-Lucid-Token": "bob-token"}
    a = L.memory.add("alice tabs", kind="preference", key="k", scope=Scope("default", ""))
    b = L.memory.add("alice spaces", kind="preference", key="k", scope=Scope("default", ""))
    conf = L.memory.conflicts(Scope("default", ""))[0]
    r = client.post(f"/api/conflicts/{conf['id']}/resolve", json={"keep": b["id"]}, headers=bob)
    assert r.status_code == 404 and L.memory.get(a["id"])["status"] == "active"
    rule = L.handoffs.add_rule("gemini", ["decision"], Scope("default", ""))
    assert client.post(f"/api/sharing/{rule['id']}/disable", json={}, headers=bob).status_code == 404
    for path in ("/api/activity", "/api/sync", "/api/tasks", "/api/budgets", "/api/questions", "/api/experiments",
                 "/api/verifications", "/api/connectors", "/api/capture/sites"):
        assert client.get(path, headers=bob).status_code == 403, path
    assert client.post("/api/capture/sites", json={"site": "x.com", "enabled": True}, headers=bob).status_code == 403
    assert client.get("/api/health", headers=bob).json()["admin"] is False


def test_capture_of_same_conversation_by_two_users_stays_separate(client, L):
    L.store.ex("INSERT INTO capture_sites (site, enabled, updated_at) VALUES ('chat.example.com', 1, 'x')")
    body = lambda txt: {"url": "https://chat.example.com/c/1", "kind": "conversation", "extraction": {"ok": True},
                        "conversation": {"provider": "example", "id": "conv1",
                                         "messages": [{"id": "m1", "role": "user", "content": txt}]}}
    assert client.post("/api/capture", json=body("alice text"), headers=H).status_code == 200
    assert client.post("/api/capture", json=body("BOB WAS HERE"), headers={"X-Lucid-Token": "bob-token"}).status_code == 200
    alice_conv = [c for c in L.archive.list(user="default")][0]
    msgs = L.archive.get(alice_conv["id"])["messages"]
    assert [m["content"] for m in msgs] == ["alice text"] and msgs[0]["status"] == "active"
