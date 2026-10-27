import pytest

from lucid.handoff import SharingDenied
from lucid.mcp_server import LucidMCP
from lucid.memory import Scope
from lucid.sync import ConflictError, OutcomeUnknown, TransientError

P = Scope("default", "proj")


def test_handoff_requires_sharing_rule_and_limits_categories(L):
    L.memory.add("Use SQLite for local mode", kind="decision", scope=P)
    L.memory.add("User prefers short answers", kind="preference", scope=P)
    L.memory.add("Personal: dentist on Friday", kind="note", scope=P)
    with pytest.raises(SharingDenied):
        L.handoffs.prepare("gemini", scope=P, goal="continue the storage work")
    prev = L.handoffs.prepare("gemini", scope=P, goal="storage", query="sqlite short answers dentist", preview=True)
    assert prev["preview"] and len(prev["memories"]) >= 2
    L.handoffs.add_rule("gemini", ["decision", "preference"], P)
    h = L.handoffs.prepare("gemini", scope=P, goal="storage", query="sqlite short answers dentist friday")
    contents = [m["content"] for m in h["memories"]] + [m["content"] for m in h["decisions"]]
    assert "Personal: dentist on Friday" not in contents
    assert "note" in h["categories_denied"]
    assert h["marker"] in h["text"]


def test_handoff_retrieved_by_another_client_via_mcp(L):
    L.memory.add("Chosen DB: SQLite", kind="decision", scope=P)
    L.handoffs.add_rule("claude-desktop", ["decision"], P)
    server = LucidMCP(L, user="default")
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "context_prepare", "arguments": {"destination": "claude-desktop", "project": "proj", "query": "DB"}}})
    data = resp["result"]["structuredContent"]
    assert data["decisions"][0]["content"] == "Chosen DB: SQLite"
    assert L.handoffs.get(data["id"], consumer="claude-desktop")["id"] == data["id"]


class FlakyDest:
    name = "flaky"

    def __init__(self, script):
        self.script = list(script)
        self.applied = {}

    def apply(self, op_id, payload):
        step = self.script.pop(0) if self.script else "ok"
        if step == "transient":
            raise TransientError("503")
        if step == "lost":  # applied remotely, response lost
            self.applied[op_id] = payload
            raise OutcomeUnknown("read timeout")
        if step == "conflict":
            raise ConflictError("412")
        if op_id in self.applied:
            raise AssertionError("duplicate apply")
        self.applied[op_id] = payload
        return {"ok": True}

    def lookup(self, op_id):
        return {"found": True} if op_id in self.applied else None


def test_outbox_retry_unknown_conflict(L, clock):
    d = FlakyDest(["transient", "lost", "conflict"])
    L.outbox.register(d)
    a = L.outbox.enqueue("op-a", "flaky", {"v": 1})
    assert L.outbox.enqueue("op-a", "flaky", {"v": 1}) == a  # idempotent enqueue
    L.outbox.enqueue("op-b", "flaky", {"v": 2})
    L.outbox.enqueue("op-c", "flaky", {"v": 3})
    s1 = L.outbox.process()
    assert s1["retry"] == 1 and s1["unknown"] == 1 and s1["conflict"] == 1
    clock.advance(60)
    s2 = L.outbox.process()
    assert s2["reconciled"] == 1 and s2["done"] == 1  # lost write found via lookup, NOT re-applied
    assert len(d.applied) == 2
    st = L.outbox.status()["flaky"]["counts"]
    assert st == {"done": 2, "conflict": 1}


def test_outbox_restart_recovery_and_dead_letter(L, clock):
    class Always:
        name = "down"

        def apply(self, op_id, payload):
            raise TransientError("down")

        def lookup(self, op_id):
            return None
    L.outbox.max_attempts = 2
    L.outbox.register(Always())
    L.outbox.enqueue("x", "down", {})
    L.store.ex("UPDATE outbox SET status='in_flight'")
    assert L.outbox.recover_after_restart() == 1
    L.outbox.process()
    clock.advance(3600)
    L.outbox.process()
    assert L.outbox.items()[0]["status"] == "dead"
