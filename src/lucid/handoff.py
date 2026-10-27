"""Context handoff: package selected memories, evidence and task state for another tool.

A handoff is NOT native-history synchronization. It produces a document the
destination tool reads (via the CLI, MCP `context_prepare`, clipboard, or a file),
carrying a `lucid-handoff:<id>` marker so that if it is pasted into a chat that is
later exported and reimported, Lucid recognises it as its own echo.

Sharing rules: automatic handoffs to a destination require an enabled sharing rule
listing the categories allowed. `preview=True` builds the payload without storing or
sending it, so the user can see exactly what would be shared.
"""
from __future__ import annotations

from typing import Any, Iterable

from .db import Store
from .memory import DATA_FRAME, MemoryStore, Scope, render_memory
from .util import dumps, loads, new_id

CATEGORIES = {"preference", "project_state", "task_state", "decision", "action", "outcome", "correction",
              "lesson", "fact", "claim", "summary", "note"}


class SharingDenied(PermissionError):
    pass


class Handoffs:
    def __init__(self, store: Store, memory: MemoryStore):
        self.s = store
        self.mem = memory

    # ------------------------------------------------------------- sharing rules
    def add_rule(self, destination: str, categories: Iterable[str], scope: Scope = Scope()) -> dict:
        cats = sorted(set(categories))
        bad = set(cats) - CATEGORIES
        if bad:
            raise ValueError(f"unknown categories {sorted(bad)}")
        rid = new_id("rule")
        self.s.ex("INSERT INTO sharing_rules (id, destination, categories, user_scope, project_scope, created_at)"
                  " VALUES (?,?,?,?,?,?)", (rid, destination, dumps(cats), scope.user, scope.project,
                                            self.s.clock.iso()))
        self.s.log("user", "sharing.rule.add", "sharing_rule", rid, {"destination": destination, "categories": cats})
        return {"id": rid, "destination": destination, "categories": cats, **scope.__dict__}

    def rules(self, scope: Scope | None = None) -> list[dict]:
        rows = self.s.q("SELECT * FROM sharing_rules WHERE enabled=1")
        out = []
        for r in rows:
            d = dict(r)
            d["categories"] = loads(d["categories"], [])
            if scope and (d["user_scope"] != scope.user or d["project_scope"] != scope.project):
                continue
            out.append(d)
        return out

    def disable_rule(self, rid: str) -> None:
        self.s.ex("UPDATE sharing_rules SET enabled=0 WHERE id=?", (rid,))

    def allowed_categories(self, destination: str, scope: Scope) -> set[str]:
        cats: set[str] = set()
        for r in self.rules(scope):
            if r["destination"] == destination:
                cats |= set(r["categories"])
        return cats

    # ------------------------------------------------------------- prepare
    def prepare(self, destination: str, *, scope: Scope = Scope(), goal: str = "", query: str = "",
                categories: Iterable[str] | None = None, task_id: str | None = None, budget_tokens: int = 1500,
                preview: bool = False, actor: str = "user") -> dict:
        requested = set(categories or CATEGORIES)
        allowed = self.allowed_categories(destination, scope)
        if not preview:
            if not allowed:
                raise SharingDenied(f"no sharing rule authorises destination {destination!r}; "
                                    "add one with `lucid share add` or use preview")
            denied = requested - allowed
            requested &= allowed
        else:
            denied = set()
        res = self.mem.search(query or goal, scope, kinds=requested or {"__none__"}, budget_tokens=budget_tokens)
        hid = new_id("hnd")
        task = None
        if task_id:
            t = self.s.one("SELECT id, title, objective, status, acceptance, checkpoint FROM tasks WHERE id=?",
                           (task_id,))
            if t:
                task = {**dict(t), "acceptance": loads(t["acceptance"], []), "checkpoint": loads(t["checkpoint"], {})}
        questions = [dict(r) for r in self.s.q(
            "SELECT id, decision, default_option, deadline FROM questions WHERE status='open'"
            + (" AND task_id=?" if task_id else ""), (task_id,) if task_id else ())]
        conflicts = [m["id"] for m in res.items if m.get("open_conflicts")]
        payload: dict[str, Any] = {
            "format": "lucid.handoff.v1", "id": hid, "marker": f"lucid-handoff:{hid}", "destination": destination,
            "scope": scope.__dict__, "goal": goal, "task": task,
            "decisions": [m for m in res.items if m["kind"] == "decision"],
            "constraints": [m for m in res.items if m["kind"] in ("preference", "lesson", "correction")],
            "memories": [m for m in res.items if m["kind"] not in ("decision",)],
            "unresolved_questions": questions, "conflicts": conflicts,
            "uncertainty": [{"id": m["id"], "verification": m["verification"], "uncertainty": m.get("uncertainty")}
                            for m in res.items if m["verification"] != "verified" or m.get("uncertainty")],
            "sources": sorted({e for m in res.items for e in self._source_refs(m["id"])}),
            "categories_included": sorted(requested), "categories_denied": sorted(denied),
            "omitted_for_budget": res.omitted, "preview": preview,
        }
        payload["text"] = self.render(payload)
        if not preview:
            self.s.ex("INSERT INTO handoffs (id, destination, payload, created_at, memory_ids) VALUES (?,?,?,?,?)",
                      (hid, destination, dumps(payload), self.s.clock.iso(), dumps([m["id"] for m in res.items])))
            self.s.log(actor, "handoff.prepare", "handoff", hid,
                       {"destination": destination, "memories": len(res.items), "denied": sorted(denied)})
        return payload

    def _source_refs(self, mid: str) -> list[str]:
        return [r[0] or r[1] for r in self.s.q(
            "SELECT s.uri, e.source_id FROM memory_evidence e LEFT JOIN sources s ON s.id=e.source_id"
            " WHERE e.memory_id=? AND e.source_id<>''", (mid,)) if (r[0] or r[1])]

    @staticmethod
    def render(p: dict) -> str:
        lines = [f"# Lucid context handoff ({p['marker']})", "", DATA_FRAME, ""]
        if p.get("goal"):
            lines += ["## Goal", p["goal"], ""]
        if p.get("task"):
            t = p["task"]
            lines += ["## Task state", f"- {t['title']} — status: {t['status']}"]
            lines += [f"  - acceptance: {a}" for a in t.get("acceptance", [])]
            lines.append("")
        for title, key in (("Decisions", "decisions"), ("Constraints & preferences", "constraints")):
            if p.get(key):
                lines += [f"## {title}"] + [render_memory(m) for m in p[key]] + [""]
        others = [m for m in p.get("memories", []) if m not in p.get("constraints", [])]
        if others:
            lines += ["## Relevant memories"] + [render_memory(m) for m in others] + [""]
        if p.get("unresolved_questions"):
            lines += ["## Unresolved questions"] + [f"- {q['decision']} (default: {q['default_option']})"
                                                    for q in p["unresolved_questions"]] + [""]
        if p.get("conflicts"):
            lines += ["## Conflicts (unresolved — do not pick one silently)"] + [f"- {c}" for c in p["conflicts"]] + [""]
        if p.get("sources"):
            lines += ["## Sources"] + [f"- {s}" for s in p["sources"]] + [""]
        lines.append(f"Included categories: {', '.join(p['categories_included']) or 'none'}")
        if p.get("categories_denied"):
            lines.append(f"Withheld by sharing rules: {', '.join(p['categories_denied'])}")
        return "\n".join(lines)

    def get(self, hid: str, *, consumer: str | None = None) -> dict:
        r = self.s.one("SELECT * FROM handoffs WHERE id=?", (hid,))
        if not r:
            raise KeyError(hid)
        if consumer:
            self.s.ex("UPDATE handoffs SET consumed_at=?, consumed_by=? WHERE id=?", (self.s.clock.iso(), consumer, hid))
            self.s.log(consumer, "handoff.consume", "handoff", hid, {})
        return loads(r["payload"], {})

    def list(self, limit: int = 50) -> list[dict]:
        return [{k: r[k] for k in ("id", "destination", "created_at", "consumed_at", "consumed_by")}
                for r in self.s.q("SELECT * FROM handoffs ORDER BY created_at DESC LIMIT ?", (limit,))]
