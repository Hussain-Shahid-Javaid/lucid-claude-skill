"""Clearly labelled demo data for the dashboard (`lucid-serve --demo --db demo.db`)."""
from __future__ import annotations

from ..conversations import NormalizedConversation, NormalizedMessage
from ..memory import Evidence, Scope

TAG = "DEMO DATA"


def seed_demo(L) -> None:
    if L.store.get_setting("demo_seeded"):
        return
    P = Scope("default", "")
    a = L.memory.add(f"[{TAG}] User prefers pytest", kind="preference", key="pref:tests", scope=P, tags=["demo"])
    L.memory.add(f"[{TAG}] User prefers unittest", kind="preference", key="pref:tests", scope=P, tags=["demo"],
                 origin="assistant")
    src = L.memory.add_source("web", scope=P, uri="https://example.com/demo", title="demo source")
    L.memory.add(f"[{TAG}] Service rate limit is 100 req/min", kind="fact", scope=P, verification="verified",
                 evidence=[Evidence(source_id=src, quote="100 requests per minute")], tags=["demo"])
    L.archive.ingest(NormalizedConversation(provider="demo", external_id="demo-1", title=f"[{TAG}] branching chat",
                                            messages=[NormalizedMessage("1", "user", "Which DB?"),
                                                      NormalizedMessage("2", "assistant", "Postgres", parent="1"),
                                                      NormalizedMessage("3", "assistant", "SQLite", parent="1")]),
                     method="demo")
    lid = L.budget.create(f"[{TAG}] ledger", token_limit=50_000, reserve_fraction=0.2)
    r = L.budget.reserve(lid, purpose="demo worker", input_tokens=4000, max_output_tokens=2000)
    from ..budget import Usage
    L.budget.reconcile(r, Usage(input_tokens=3500, output_tokens=900))
    L.budget.reserve(lid, purpose="demo in-flight", input_tokens=3000, max_output_tokens=2000)
    t = L.tasks.create(f"[{TAG}] ship feature", acceptance=["unit tests pass", "docs updated"])
    L.tasks.record_check(t["id"], "ac1", True, "demo: 12 passed")
    L.decisions.authorize("internal_naming")
    L.decisions.ask(f"[{TAG}] Name the helper module", ["util", "helpers"], category="internal_naming",
                    default="util", timeout_s=3600, task_id=t["id"])
    L.store.set_setting("demo_seeded", "1")
    _ = a
