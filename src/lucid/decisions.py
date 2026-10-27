"""Timed questions, standing decision policies and authorizations.

"Ask when useful; if I do not respond within the configured time, apply the
documented default and continue."

Safety rules enforced in code:
* A default is applied on timeout ONLY if the question's category is covered by an
  active authorization (standing policy). Otherwise the question becomes `blocked`.
* Questions with `required_approval=True` are never auto-resolved.
* Silence never invents facts/credentials, expands spend or scope, or authorises a
  new external destination: those categories cannot be default-authorised.
* Each question resolves exactly once via an atomic UPDATE ... WHERE status='open'.
* An answer that arrives before the default is applied wins. Later answers are
  recorded as late responses and reported, with a reversal hook when registered.
* Defaults with side effects run through `ActionLedger`, so a restart never runs
  the same external action twice.
* Timers use a runtime scheduler (threading), never model polling.
"""
from __future__ import annotations

import datetime as _dt
import threading
from typing import Any, Callable, Iterable

from .db import Store
from .memory import MemoryStore, Scope
from .util import dumps, iso, loads, new_id, parse_iso

DEFAULTABLE = {"internal_naming", "file_organization", "equivalent_implementation", "presentation",
               "ordering", "optional_preference"}
NEVER_DEFAULT = {"credentials", "essential_fact", "spending", "scope_expansion", "external_destination",
                 "destructive_action", "publish"}
DEFAULT_TIMEOUT_S = 120


class DecisionError(ValueError):
    pass


class ActionLedger:
    """Exactly-once execution of side effects keyed by an idempotency key."""

    def __init__(self, store: Store):
        self.s = store

    def run_once(self, key: str, fn: Callable[[], Any]) -> tuple[bool, Any]:
        with self.s.tx() as c:
            r = c.execute("SELECT result FROM operations WHERE op_id=?", (key,)).fetchone()
            if r:
                return False, loads(r[0])
            # Claim first (in the same transaction as the check) so concurrent callers cannot both run it.
            c.execute("INSERT INTO operations (op_id, kind, created_at, result) VALUES (?,?,?,?)",
                      (key, "action", self.s.clock.iso(), dumps({"state": "claimed"})))
        try:
            result = fn()
        except Exception as e:
            self.s.ex("UPDATE operations SET result=? WHERE op_id=?",
                      (dumps({"state": "failed", "error": str(e)}), key))
            raise
        self.s.ex("UPDATE operations SET result=? WHERE op_id=?", (dumps({"state": "done", "result": result}), key))
        return True, result


class Decisions:
    def __init__(self, store: Store, memory: MemoryStore | None = None):
        self.s = store
        self.mem = memory
        self.actions = ActionLedger(store)
        self.on_default: dict[str, Callable[[dict], Any]] = {}
        self.on_reverse: dict[str, Callable[[dict, str], Any]] = {}
        self._wake = threading.Event()

    # ---------------------------------------------------------- authorizations
    def authorize(self, category: str, *, scope: str = "*", limits: dict | None = None,
                  expires_in_s: float | None = None) -> dict:
        if category in NEVER_DEFAULT:
            raise DecisionError(f"category {category!r} can never be resolved by silence; it requires an explicit "
                                "answer each time (use a standing external-action authorization with explicit "
                                "scope via `authorize_action` instead)")
        if category not in DEFAULTABLE:
            raise DecisionError(f"unknown defaultable category {category!r}; allowed: {sorted(DEFAULTABLE)}")
        return self._insert_auth(category, scope, limits, expires_in_s)

    def authorize_action(self, action: str, *, scope: str, limits: dict, expires_in_s: float | None = None) -> dict:
        """Separately configured standing authorization for a specific external action, with explicit scope/limits."""
        if not scope or scope == "*":
            raise DecisionError("external-action authorizations need an explicit, narrow scope")
        if not limits:
            raise DecisionError("external-action authorizations need explicit limits (e.g. max_count)")
        return self._insert_auth(f"action:{action}", scope, limits, expires_in_s)

    def _insert_auth(self, category: str, scope: str, limits: dict | None, expires_in_s: float | None) -> dict:
        aid = new_id("auth")
        exp = iso(self.s.clock.now() + _dt.timedelta(seconds=expires_in_s)) if expires_in_s else None
        self.s.ex("INSERT INTO authorizations (id, category, scope, limits, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                  (aid, category, scope, dumps(limits or {}), self.s.clock.iso(), exp))
        self.s.log("user", "authorization.grant", "authorization", aid, {"category": category, "scope": scope})
        return {"id": aid, "category": category, "scope": scope, "limits": limits or {}, "expires_at": exp}

    def revoke(self, aid: str) -> None:
        self.s.ex("UPDATE authorizations SET revoked=1 WHERE id=?", (aid,))

    def find_authorization(self, category: str, scope: str | None) -> dict | None:
        now = self.s.clock.iso()
        for r in self.s.q("SELECT * FROM authorizations WHERE category=? AND revoked=0 AND (expires_at IS NULL OR"
                          " expires_at>?)", (category, now)):
            if r["scope"] == "*" or (scope and (scope == r["scope"] or scope.startswith(r["scope"].rstrip("*")))):
                return {**dict(r), "limits": loads(r["limits"], {})}
        return None

    def authorizations(self) -> list[dict]:
        return [{**dict(r), "limits": loads(r["limits"], {})} for r in self.s.q(
            "SELECT * FROM authorizations WHERE revoked=0 ORDER BY created_at DESC")]

    # ---------------------------------------------------------- questions
    def default_timeout(self) -> float:
        return float(self.s.get_setting("decision_timeout_s", str(DEFAULT_TIMEOUT_S)))

    def ask(self, decision: str, options: list[str], *, category: str, default: str | None = None,
            timeout_s: float | None = None, task_id: str | None = None, scope: str | None = None,
            consequence: str = "", required_approval: bool = False, pref_key: str | None = None,
            mem_scope: Scope = Scope()) -> dict:
        if len(options) < 1:
            raise DecisionError("need at least one option")
        if default is not None and default not in options:
            raise DecisionError("default must be one of the options")
        if category in NEVER_DEFAULT:
            required_approval = True
        # 1. Don't ask what's already answered: consult remembered preferences.
        if pref_key and self.mem:
            res = self.mem.search(pref_key, mem_scope, kinds={"preference"}, budget_tokens=400)
            for m in res.items:
                if m.get("key") == pref_key:
                    for opt in options:
                        if opt.lower() in m["content"].lower():
                            return {"id": None, "status": "answered_from_preference", "answer": opt,
                                    "memory_id": m["id"], "decision": decision}
        qid = new_id("q")
        now = self.s.clock.now()
        deadline = iso(now + _dt.timedelta(seconds=timeout_s if timeout_s is not None else self.default_timeout()))
        auth = None if required_approval else self.find_authorization(category, scope)
        self.s.ex("INSERT INTO questions (id, task_id, category, decision, options, default_option, deadline, scope,"
                  " consequence, authorization_id, required_approval, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (qid, task_id, category, decision, dumps(options), default, deadline, scope, consequence,
                   auth["id"] if auth else None, int(required_approval), iso(now)))
        self.s.log("lucid", "question.ask", "question", qid, {"category": category, "deadline": deadline})
        self._wake.set()
        q = self.get(qid)
        q["on_timeout"] = self.timeout_behavior(q)
        return q

    def timeout_behavior(self, q: dict) -> str:
        if q["required_approval"]:
            return "requires your explicit answer; work depending on it will wait (independent work continues)"
        if q["default_option"] and q["authorization_id"]:
            return f"default {q['default_option']!r} will be applied at {q['deadline']} (authorized by {q['authorization_id']})"
        return "no standing authorization covers this category: it will be marked blocked, not defaulted"

    def get(self, qid: str) -> dict:
        r = self.s.one("SELECT * FROM questions WHERE id=?", (qid,))
        if not r:
            raise KeyError(qid)
        d = dict(r)
        d["options"] = loads(d["options"], [])
        d["late_responses"] = loads(d["late_responses"], [])
        return d

    def list(self, status: str | None = None) -> list[dict]:
        sql = "SELECT id FROM questions" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC"
        out = []
        for r in self.s.q(sql, (status,) if status else ()):
            q = self.get(r[0])
            q["on_timeout"] = self.timeout_behavior(q)
            out.append(q)
        return out

    def _transition(self, qid: str, to: str, answer: str | None, by: str, from_status: str = "open") -> bool:
        cur = self.s.ex("UPDATE questions SET status=?, answer=?, resolved_by=?, resolved_at=? WHERE id=? AND status=?",
                        (to, answer, by, self.s.clock.iso(), qid, from_status))
        return cur.rowcount == 1

    def answer(self, qid: str, option: str, *, by: str = "user", remember: bool = False,
               mem_scope: Scope = Scope(), pref_key: str | None = None) -> dict:
        q = self.get(qid)
        if option not in q["options"]:
            raise DecisionError(f"{option!r} is not an option: {q['options']}")
        if self._transition(qid, "answered", option, by) or self._transition(qid, "answered", option, by, "paused"):
            if remember and self.mem:
                self.mem.add(f"For '{q['decision']}' choose: {option}", kind="preference", origin="user",
                             scope=mem_scope, key=pref_key or f"decision:{q['category']}:{q['decision']}",
                             tags=["decision-policy"])
            self.s.log(by, "question.answer", "question", qid, {"answer": option})
            return {**self.get(qid), "late": False}
        # Lost the race: record as a late response and tell the user what happened.
        q = self.get(qid)
        late = q["late_responses"] + [{"answer": option, "by": by, "at": self.s.clock.iso()}]
        self.s.ex("UPDATE questions SET late_responses=? WHERE id=?", (dumps(late), qid))
        reversal = None
        if q["status"] == "defaulted" and option != q["answer"]:
            hook = self.on_reverse.get(q["category"])
            if hook:
                ok, reversal = self.actions.run_once(f"reverse:{qid}:{option}", lambda: hook(q, option))
            else:
                reversal = "no reversal hook registered for this category: remaining work uses your answer; "\
                           "already-applied default stays unless you undo it"
        self.s.log(by, "question.late_answer", "question", qid, {"answer": option, "status": q["status"]})
        return {**self.get(qid), "late": True, "applied": q["answer"], "your_answer": option, "reversal": reversal}

    def extend(self, qid: str, seconds: float) -> dict:
        q = self.get(qid)
        if q["status"] not in ("open", "paused"):
            raise DecisionError(f"question is {q['status']}")
        dl = parse_iso(q["deadline"]) or self.s.clock.now()
        self.s.ex("UPDATE questions SET deadline=? WHERE id=?", (iso(max(dl, self.s.clock.now()) +
                                                                     _dt.timedelta(seconds=seconds)), qid))
        self._wake.set()
        return self.get(qid)

    def accept_default(self, qid: str, by: str = "user") -> dict:
        q = self.get(qid)
        if not q["default_option"]:
            raise DecisionError("question has no default")
        return self.answer(qid, q["default_option"], by=by)

    def pause(self, qid: str) -> bool:
        return self._transition(qid, "paused", None, "user")

    def resume(self, qid: str) -> bool:
        ok = self.s.ex("UPDATE questions SET status='open' WHERE id=? AND status='paused'", (qid,)).rowcount == 1
        self._wake.set()
        return ok

    def cancel(self, qid: str) -> bool:
        return self._transition(qid, "canceled", None, "user") or self._transition(qid, "canceled", None, "user",
                                                                                     "paused")

    def cancel_for_task(self, task_id: str) -> int:
        return self.s.ex("UPDATE questions SET status='canceled', resolved_by='task-cancel', resolved_at=? WHERE"
                         " task_id=? AND status IN ('open','paused')", (self.s.clock.iso(), task_id)).rowcount

    # ---------------------------------------------------------- timer
    def tick(self) -> list[dict]:
        """Resolve every expired open question. Safe to call repeatedly and after restarts."""
        now = self.s.clock.iso()
        out = []
        for r in self.s.q("SELECT id FROM questions WHERE status='open' AND deadline<=?", (now,)):
            q = self.get(r[0])
            auth = None
            if not q["required_approval"] and q["default_option"] and q["category"] not in NEVER_DEFAULT:
                # Re-check at expiry: a revoked/expired authorization must not apply.
                auth = self.find_authorization(q["category"], q["scope"])
            if auth:
                if self._transition(q["id"], "defaulted", q["default_option"], f"timeout:{auth['id']}"):
                    hook = self.on_default.get(q["category"])
                    effect = None
                    if hook:
                        _, effect = self.actions.run_once(f"default:{q['id']}", lambda q=q: hook(q))
                    self.s.log("lucid", "question.default", "question", q["id"],
                               {"applied": q["default_option"], "authorization": auth["id"]})
                    out.append({"id": q["id"], "status": "defaulted", "applied": q["default_option"], "effect": effect})
            else:
                if self._transition(q["id"], "blocked", None, "timeout:no-authorization"):
                    self.s.log("lucid", "question.blocked", "question", q["id"], {})
                    out.append({"id": q["id"], "status": "blocked"})
        return out

    def next_deadline(self) -> _dt.datetime | None:
        r = self.s.one("SELECT MIN(deadline) FROM questions WHERE status='open'")
        return parse_iso(r[0]) if r and r[0] else None


class DecisionScheduler(threading.Thread):
    """Runtime timer: sleeps until the next deadline (or a wake-up), then ticks."""

    def __init__(self, decisions: Decisions, max_sleep_s: float = 30.0):
        super().__init__(daemon=True, name="lucid-decisions")
        self.d = decisions
        self.max_sleep = max_sleep_s
        self._stop = threading.Event()

    def run(self) -> None:
        self.d.tick()  # reconcile deadlines that passed while we were down
        while not self._stop.is_set():
            nd = self.d.next_deadline()
            wait = self.max_sleep
            if nd:
                wait = max(0.05, min(self.max_sleep, (nd - self.d.s.clock.now()).total_seconds()))
            self.d._wake.wait(wait)
            self.d._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.d.tick()
            except Exception:  # keep the timer alive; errors are visible in activity/logs
                pass

    def stop(self) -> None:
        self._stop.set()
        self.d._wake.set()


def ensure_iterable(x: Iterable[str] | None) -> list[str]:
    return list(x or [])
