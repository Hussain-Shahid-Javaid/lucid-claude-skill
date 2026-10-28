"""Self-monitoring records: assumptions, missing information, claims needing
verification, available tools, and confidence predictions made *before* outcomes.

These are explicit, inspectable records written by the assistant via the CLI/MCP —
not a view into hidden model internals.
"""
from __future__ import annotations

from typing import Any

from .db import Store
from .util import new_id

ITEM_KINDS = {"assumption", "missing_info", "claim", "tool", "premise", "counterevidence", "revision"}


class Monitor:
    def __init__(self, store: Store):
        self.s = store

    def note(self, kind: str, text: str, *, task_id: str | None = None, evidence: str | None = None) -> str:
        if kind not in ITEM_KINDS:
            raise ValueError(f"kind must be one of {sorted(ITEM_KINDS)}")
        iid = new_id("mon")
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO monitor_items (id, task_id, kind, text, evidence, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?,?)", (iid, task_id, kind, text, evidence, now, now))
        return iid

    def resolve(self, iid: str, status: str, evidence: str) -> None:
        if status not in ("supported", "refuted", "resolved", "unresolvable", "open"):
            raise ValueError(status)
        self.s.ex("UPDATE monitor_items SET status=?, evidence=?, updated_at=? WHERE id=?",
                  (status, evidence, self.s.clock.iso(), iid))

    def items(self, task_id: str | None = None, status: str | None = None) -> list[dict]:
        sql, p = "SELECT * FROM monitor_items WHERE 1=1", []
        if task_id:
            sql += " AND task_id=?"
            p.append(task_id)
        if status:
            sql += " AND status=?"
            p.append(status)
        return [dict(r) for r in self.s.q(sql + " ORDER BY created_at", p)]

    # -------------------------------------------------- predictions / calibration
    def predict(self, claim: str, confidence: float, task_id: str | None = None) -> str:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        pid = new_id("pred")
        self.s.ex("INSERT INTO predictions (id, task_id, claim, confidence, created_at) VALUES (?,?,?,?,?)",
                  (pid, task_id, claim, confidence, self.s.clock.iso()))
        return pid

    def resolve_prediction(self, pid: str, outcome: bool) -> None:
        cur = self.s.ex("UPDATE predictions SET outcome=?, resolved_at=? WHERE id=? AND outcome IS NULL",
                        (int(outcome), self.s.clock.iso(), pid))
        if cur.rowcount != 1:
            raise ValueError("no open prediction with that id (an outcome can only be recorded once)")

    def calibration(self, task_id: str | None = None, bins: int = 5) -> dict[str, Any]:
        sql = "SELECT confidence, outcome FROM predictions WHERE outcome IS NOT NULL"
        rows = self.s.q(sql + (" AND task_id=?" if task_id else ""), (task_id,) if task_id else ())
        return calibration_stats([(r[0], r[1]) for r in rows], bins)


def brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def calibration_stats(pairs: list[tuple[float, int]], bins: int = 5) -> dict[str, Any]:
    table: list[dict[str, Any]] = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, o) for p, o in pairs if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if sel:
            table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel),
                          "mean_confidence": round(sum(p for p, _ in sel) / len(sel), 3),
                          "observed_rate": round(sum(o for _, o in sel) / len(sel), 3)})
    ece = sum(t["n"] * abs(t["mean_confidence"] - t["observed_rate"]) for t in table) / len(pairs) if pairs else None
    return {"n": len(pairs), "brier": None if not pairs else round(brier(pairs) or 0.0, 4),
            "ece": None if ece is None else round(ece, 4), "bins": table}
