"""Centralised token / cost / time budget ledger with atomic reservations.

Accounting convention (documented in docs/budgets.md):
  charged_tokens = input_tokens + output_tokens (+ reasoning_tokens only when the
  provider reports them separately from output). Cached input tokens are a subset
  of input_tokens and are NOT added again; they are recorded for cost purposes.

Rules:
* Before dispatch a call reserves `input_estimate + max_output_tokens`.
* In-flight reservations count against the allowance.
* After the call, `reconcile` replaces the reservation with actual usage.
* Canceled/timed-out calls whose usage is unknown keep their full reservation
  (`mark_unknown`) until a late usage report arrives.
* `reserve_fraction` of the limit is held back for integration, verification,
  checkpointing and final reporting; only reservations with use_reserve=True
  may draw on it.
* mode='hard' is only claimed when every relevant call goes through this ledger;
  otherwise the ledger reports 'estimated'.

Context window != remaining allowance: `context_window` checks are separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import Store
from .util import new_id, parse_iso


class InsufficientBudget(RuntimeError):
    def __init__(self, msg: str, status: dict):
        super().__init__(msg)
        self.status = status


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_in_output: bool = True
    cost: float | None = None

    @property
    def charged(self) -> int:
        return self.input_tokens + self.output_tokens + (0 if self.reasoning_in_output else self.reasoning_tokens)


class BudgetLedger:
    def __init__(self, store: Store):
        self.s = store

    def create(self, name: str, *, token_limit: int | None = None, cost_limit: float | None = None,
               time_limit_s: float | None = None, reserve_fraction: float = 0.15, mode: str = "estimated") -> str:
        if mode not in ("hard", "estimated"):
            raise ValueError("mode must be 'hard' or 'estimated'")
        if not 0 <= reserve_fraction < 1:
            raise ValueError("reserve_fraction must be in [0,1)")
        lid = new_id("bud")
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO budget_ledgers (id, name, token_limit, cost_limit, time_limit_s, reserve_fraction, mode,"
                  " created_at, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (lid, name, token_limit, cost_limit, time_limit_s, reserve_fraction, mode, now, now))
        return lid

    def _totals(self, c, lid: str) -> dict:
        led = c.execute("SELECT * FROM budget_ledgers WHERE id=?", (lid,)).fetchone()
        if not led:
            raise KeyError(lid)
        rows = c.execute("SELECT status, reserved_tokens, reserved_cost, input_tokens, output_tokens,"
                         " reasoning_tokens, actual_cost, use_reserve FROM budget_entries WHERE ledger_id=?",
                         (lid,)).fetchall()
        used = inflight = unknown = 0
        cost_used = cost_held = 0.0
        for r in rows:
            if r["status"] == "reconciled":
                used += (r["input_tokens"] or 0) + (r["output_tokens"] or 0) + (r["reasoning_tokens"] or 0)
                cost_used += r["actual_cost"] or 0.0
            elif r["status"] == "reserved":
                inflight += r["reserved_tokens"]
                cost_held += r["reserved_cost"] or 0.0
            elif r["status"] == "unknown":
                unknown += r["reserved_tokens"]
                cost_held += r["reserved_cost"] or 0.0
        limit = led["token_limit"]
        reserve_amt = int(limit * led["reserve_fraction"]) if limit else 0
        committed = used + inflight + unknown
        elapsed = (self.s.clock.now() - parse_iso(led["started_at"])).total_seconds()
        return {
            "ledger_id": lid, "name": led["name"], "mode": led["mode"], "token_limit": limit,
            "used_tokens": used, "inflight_reserved_tokens": inflight, "unknown_held_tokens": unknown,
            "committed_tokens": committed, "verification_reserve_tokens": reserve_amt,
            "available_tokens": None if limit is None else max(0, limit - reserve_amt - committed),
            "available_including_reserve": None if limit is None else max(0, limit - committed),
            "cost_limit": led["cost_limit"], "cost_used": round(cost_used, 6), "cost_held": round(cost_held, 6),
            "time_limit_s": led["time_limit_s"], "elapsed_s": round(elapsed, 3),
            "coverage_note": ("hard ceiling: every controlled call is reserved through this ledger" if led["mode"] == "hard"
                              else "estimated: covers only calls routed through Lucid; host/model usage outside "
                                   "Lucid (e.g. the chat itself) is not counted"),
        }

    def status(self, lid: str) -> dict:
        with self.s.tx() as c:
            return self._totals(c, lid)

    def reserve(self, lid: str, *, purpose: str, input_tokens: int, max_output_tokens: int, actor: str = "lucid",
                use_reserve: bool = False, est_cost: float = 0.0, context_window: int | None = None) -> str:
        need = int(input_tokens) + int(max_output_tokens)
        if need <= 0:
            raise ValueError("reservation must be positive")
        if context_window is not None and need > context_window:
            raise InsufficientBudget(f"call needs {need} tokens but the model context window is {context_window}",
                                     {"context_window": context_window})
        with self.s.tx() as c:  # BEGIN IMMEDIATE: atomic across threads and processes
            t = self._totals(c, lid)
            if t["time_limit_s"] is not None and t["elapsed_s"] >= t["time_limit_s"]:
                raise InsufficientBudget("time budget exhausted", t)
            if t["token_limit"] is not None:
                avail = t["available_including_reserve"] if use_reserve else t["available_tokens"]
                if need > avail:
                    raise InsufficientBudget(f"{purpose}: needs {need} tokens, {avail} available"
                                             f"{' (incl. reserve)' if use_reserve else ' (reserve held back)'}", t)
            if t["cost_limit"] is not None:
                if t["mode"] == "hard" and not est_cost:
                    raise InsufficientBudget(f"{purpose}: a hard cost limit needs a cost estimate per call", t)
                if t["cost_used"] + t["cost_held"] + est_cost > t["cost_limit"] or \
                        t["cost_used"] + t["cost_held"] >= t["cost_limit"]:
                    raise InsufficientBudget(f"{purpose}: cost budget exhausted or would be exceeded", t)
            eid = new_id("res")
            now = self.s.clock.iso()
            c.execute("INSERT INTO budget_entries (id, ledger_id, purpose, actor, status, reserved_tokens, reserved_cost,"
                      " use_reserve, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (eid, lid, purpose, actor, "reserved", need, est_cost, int(use_reserve), now, now))
        return eid

    def reconcile(self, eid: str, usage: Usage) -> dict:
        with self.s.tx() as c:
            r = c.execute("SELECT status, reserved_tokens FROM budget_entries WHERE id=?", (eid,)).fetchone()
            if not r:
                raise KeyError(eid)
            if r["status"] == "reconciled":
                return {"id": eid, "status": "reconciled", "note": "already reconciled (idempotent)"}
            c.execute("UPDATE budget_entries SET status='reconciled', input_tokens=?, output_tokens=?, cached_tokens=?,"
                      " reasoning_tokens=?, actual_cost=?, updated_at=? WHERE id=?",
                      (usage.input_tokens, usage.output_tokens, usage.cached_tokens,
                       0 if usage.reasoning_in_output else usage.reasoning_tokens, usage.cost,
                       self.s.clock.iso(), eid))
        over = usage.charged - r["reserved_tokens"]
        return {"id": eid, "status": "reconciled", "charged": usage.charged, "overrun": max(0, over)}

    def release(self, eid: str) -> None:
        """Call was never dispatched: return the reservation."""
        self.s.ex("UPDATE budget_entries SET status='released', updated_at=? WHERE id=? AND status='reserved'",
                  (self.s.clock.iso(), eid))

    def mark_unknown(self, eid: str) -> None:
        """Canceled/timed out after dispatch: keep the conservative reservation."""
        self.s.ex("UPDATE budget_entries SET status='unknown', updated_at=? WHERE id=? AND status='reserved'",
                  (self.s.clock.iso(), eid))

    def entries(self, lid: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.s.q("SELECT * FROM budget_entries WHERE ledger_id=? ORDER BY created_at", (lid,))]

    def ledgers(self) -> list[dict]:
        return [self.status(r[0]) for r in self.s.q("SELECT id FROM budget_ledgers ORDER BY created_at DESC")]
