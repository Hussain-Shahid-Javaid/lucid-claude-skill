"""Task state, acceptance checks, checkpoints and completion-first status reporting.

A task can only be `completed` when every *essential* acceptance check has passed
with recorded evidence. Running out of budget, a plausible answer, or a placeholder
integration never counts as completion.
"""
from __future__ import annotations

import subprocess
from typing import Any, Iterable

from .db import Store
from .redact import redact
from .util import dumps, loads, new_id

STATUSES = {"pending", "running", "paused", "completed", "partially_completed", "blocked", "budget_exhausted",
            "canceled", "failed"}
TERMINAL = {"completed", "partially_completed", "blocked", "budget_exhausted", "canceled", "failed"}


class NoProgress(RuntimeError):
    pass


class Tasks:
    def __init__(self, store: Store, no_progress_limit: int = 2):
        self.s = store
        self.no_progress_limit = no_progress_limit

    def create(self, title: str, *, objective: str = "", acceptance: Iterable[dict | str] = (), essential: bool = True,
               parent_id: str | None = None, depends_on: Iterable[str] = (), owned_paths: Iterable[str] = (),
               ledger_id: str | None = None, depth: int = 0) -> dict:
        acc = []
        for i, a in enumerate(acceptance):
            a = {"description": a} if isinstance(a, str) else dict(a)
            a.setdefault("id", f"ac{i + 1}")
            a.setdefault("essential", True)
            a.setdefault("status", "pending")
            a.setdefault("check", {"type": "manual"})
            acc.append(a)
        tid = new_id("task")
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO tasks (id, parent_id, title, objective, acceptance, essential, depends_on, owned_paths,"
                  " ledger_id, depth, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, parent_id, title, objective, dumps(acc), int(essential), dumps(list(depends_on)),
                   dumps(list(owned_paths)), ledger_id, depth, now, now))
        self.s.log("lucid", "task.create", "task", tid, {"title": title})
        return self.get(tid)

    def get(self, tid: str) -> dict:
        r = self.s.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if not r:
            raise KeyError(tid)
        d = dict(r)
        for k, dflt in (("acceptance", []), ("depends_on", []), ("owned_paths", []), ("checkpoint", {}),
                        ("result", None)):
            d[k] = loads(d[k], dflt)
        return d

    def list(self, parent_id: str | None = None, status: str | None = None) -> list[dict]:
        sql, p = "SELECT id FROM tasks WHERE 1=1", []
        if parent_id is not None:
            sql += " AND parent_id=?"
            p.append(parent_id)
        if status:
            sql += " AND status=?"
            p.append(status)
        return [self.get(r[0]) for r in self.s.q(sql + " ORDER BY created_at", p)]

    def set_status(self, tid: str, status: str, *, result: Any = None, only_from: Iterable[str] | None = None) -> bool:
        if status not in STATUSES:
            raise ValueError(status)
        sql = "UPDATE tasks SET status=?, updated_at=?" + (", result=?" if result is not None else "") + " WHERE id=?"
        params: list[Any] = [status, self.s.clock.iso()] + ([redact(dumps(result))] if result is not None else []) + [tid]
        if only_from:
            fr = list(only_from)
            sql += f" AND status IN ({','.join('?' * len(fr))})"
            params += fr
        changed = self.s.ex(sql, params).rowcount == 1
        if changed:
            self.s.log("lucid", f"task.{status}", "task", tid, {})
        return changed

    def checkpoint(self, tid: str, data: dict, conn=None) -> None:
        t = self.get(tid)
        cp = {**t["checkpoint"], **data}
        sql, params = "UPDATE tasks SET checkpoint=?, updated_at=? WHERE id=?", (redact(dumps(cp)), self.s.clock.iso(), tid)
        if conn is not None:
            conn.execute(sql, params)
        else:
            self.s.ex(sql, params)

    # -------------------------------------------------------------- acceptance
    def record_check(self, tid: str, check_id: str, passed: bool, evidence: str) -> dict:
        with self.s.tx():
            t = self.get(tid)
            for a in t["acceptance"]:
                if a["id"] == check_id:
                    a["status"] = "passed" if passed else "failed"
                    a["evidence"] = redact(evidence)[:4000]
                    a["checked_at"] = self.s.clock.iso()
                    break
            else:
                raise KeyError(check_id)
            self.s.ex("UPDATE tasks SET acceptance=?, updated_at=? WHERE id=?",
                      (dumps(t["acceptance"]), self.s.clock.iso(), tid))
        return self.get(tid)

    def run_command_check(self, tid: str, check_id: str, cmd: list[str], cwd: str | None = None,
                          timeout: int = 600) -> dict:
        """Executable acceptance check: exit code 0 == passed. Output stored as evidence."""
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            ok, ev = p.returncode == 0, f"$ {' '.join(cmd)}\nexit={p.returncode}\n{(p.stdout + p.stderr)[-3000:]}"
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, ev = False, f"$ {' '.join(cmd)}\nerror: {e}"
        return self.record_check(tid, check_id, ok, ev)

    def record_attempt(self, tid: str, approach: str, new_evidence: bool) -> dict:
        """Detect no-progress loops: the same approach repeated without new evidence."""
        t = self.get(tid)
        hist = t["checkpoint"].get("attempts", [])
        hist.append({"approach": approach, "new_evidence": new_evidence, "at": self.s.clock.iso()})
        self.checkpoint(tid, {"attempts": hist})
        self.s.ex("UPDATE tasks SET attempts=attempts+1 WHERE id=?", (tid,))
        streak = 0
        for h in reversed(hist):
            if h["approach"] == approach and not h["new_evidence"]:
                streak += 1
            else:
                break
        if streak >= self.no_progress_limit:
            raise NoProgress(f"approach {approach!r} repeated {streak}x without new evidence; replan with a "
                             "materially different approach")
        return {"attempts": len(hist), "streak": streak}

    # -------------------------------------------------------------- finish
    def finish(self, tid: str, *, budget_exhausted: bool = False, blocked_reason: str | None = None,
               canceled: bool = False, artifacts: Iterable[str] = (), decisions: Iterable[str] = ()) -> dict:
        t = self.get(tid)
        acc = t["acceptance"]
        ess = [a for a in acc if a.get("essential", True)]
        passed = [a for a in ess if a["status"] == "passed"]
        remaining = [a for a in acc if a["status"] != "passed"]
        if canceled:
            status = "canceled"
        elif ess and len(passed) == len(ess) and not blocked_reason:
            status = "completed"
        elif blocked_reason:
            status = "blocked"
        elif budget_exhausted:
            status = "budget_exhausted"
        elif not acc:
            status = "partially_completed"  # nothing objective was defined: never claim completion
        elif passed or any(a["status"] == "passed" for a in acc):
            status = "partially_completed"
        else:
            status = "failed" if any(a["status"] == "failed" for a in ess) else "partially_completed"
        report = {
            "status": status,
            "completed_checks": [{"id": a["id"], "description": a["description"], "evidence": a.get("evidence")}
                                 for a in acc if a["status"] == "passed"],
            "remaining_requirements": [{"id": a["id"], "description": a["description"], "status": a["status"],
                                        "essential": a.get("essential", True)} for a in remaining],
            "artifacts": list(artifacts), "decisions": list(decisions),
            "blocked_reason": blocked_reason,
            "continuation_plan": [f"Satisfy {a['id']}: {a['description']}" for a in remaining]
            + ([f"Unblock: {blocked_reason}"] if blocked_reason else []),
            "note": None if acc else "no acceptance checks were defined, so completion cannot be claimed",
        }
        self.checkpoint(tid, {"final_report": report})
        self.set_status(tid, status, result=report)
        return report
