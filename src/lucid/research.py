"""Experiment ledger shared by discovery research, evaluations and consciousness research.

Hypothesis → prediction → experiment → result → criticism → revision.
Every record keeps its configuration, artifacts, failures, counterexamples and cost.
Outcome labels are restricted so reports cannot overclaim.
"""
from __future__ import annotations

from typing import Any

from .db import Store
from .util import dumps, loads, new_id

MODULES = {"discovery", "evaluation", "consciousness"}
OUTCOMES = {
    "planned", "running",
    "verified_within_bounds",       # proof/certificate or exhaustive check within stated bounds
    "empirically_supported",        # passed held-out tests; not proven
    "unverified_candidate",
    "disproven",                    # counterexample found
    "unresolved",
    "budget_exhausted",
    "not_run",
}


class Experiments:
    def __init__(self, store: Store):
        self.s = store

    def plan(self, module: str, title: str, *, hypothesis: str, prediction: str, config: dict | None = None,
             revision_of: str | None = None) -> str:
        if module not in MODULES:
            raise ValueError(module)
        eid = new_id("exp")
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO experiments (id, module, title, hypothesis, prediction, config, status, revision_of,"
                  " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (eid, module, title, hypothesis, prediction, dumps(config or {}), "planned", revision_of, now, now))
        return eid

    def record(self, eid: str, *, status: str, result: dict, criticism: str = "", cost: dict | None = None) -> dict:
        if status not in OUTCOMES:
            raise ValueError(f"status must be one of {sorted(OUTCOMES)}")
        self.s.ex("UPDATE experiments SET status=?, result=?, criticism=?, cost=?, updated_at=? WHERE id=?",
                  (status, dumps(result), criticism, dumps(cost or {}), self.s.clock.iso(), eid))
        self.s.log("lucid", "experiment.record", "experiment", eid, {"status": status})
        return self.get(eid)

    def get(self, eid: str) -> dict:
        r = self.s.one("SELECT * FROM experiments WHERE id=?", (eid,))
        if not r:
            raise KeyError(eid)
        d = dict(r)
        for k in ("config", "result", "cost"):
            d[k] = loads(d[k], {})
        return d

    def list(self, module: str | None = None) -> list[dict[str, Any]]:
        rows = self.s.q("SELECT id FROM experiments" + (" WHERE module=?" if module else "") + " ORDER BY created_at",
                        (module,) if module else ())
        return [self.get(r[0]) for r in rows]
