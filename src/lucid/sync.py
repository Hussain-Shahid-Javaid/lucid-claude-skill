"""Transactional outbox for outbound writes (storage backends, connectors).

Guarantees:
* Enqueue is idempotent on (op_id, destination).
* A write whose outcome is unknown (timeout, lost response, crash mid-flight) is
  NEVER blindly retried: the destination's `lookup(op_id)` is consulted first.
* Bounded retries with exponential backoff; exhausted items become `dead`.
* Conflicts go to a conflict queue for reconciliation instead of overwriting.
* Checkpoints are stored separately and only advanced after durable success.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Protocol

from .db import Store
from .redact import redact
from .util import dumps, iso, loads, new_id, parse_iso


class TransientError(Exception):
    """Retryable (rate limit, 5xx, network before send)."""


class OutcomeUnknown(Exception):
    """The request may or may not have been applied (timeout after send, lost response)."""


class ConflictError(Exception):
    """Destination changed concurrently (precondition failed)."""


class Destination(Protocol):
    name: str

    def apply(self, op_id: str, payload: dict) -> dict: ...          # returns a receipt
    def lookup(self, op_id: str) -> dict | None: ...                 # receipt if op_id was applied


class Outbox:
    def __init__(self, store: Store, *, max_attempts: int = 6, base_delay_s: float = 2.0, max_delay_s: float = 600):
        self.s = store
        self.destinations: dict[str, Destination] = {}
        self.max_attempts = max_attempts
        self.base = base_delay_s
        self.cap = max_delay_s

    def register(self, dest: Destination) -> None:
        self.destinations[dest.name] = dest

    def enqueue(self, op_id: str, destination: str, payload: dict) -> str:
        now = self.s.clock.iso()
        with self.s.tx() as c:
            r = c.execute("SELECT id FROM outbox WHERE op_id=? AND destination=?", (op_id, destination)).fetchone()
            if r:
                return r[0]
            oid = new_id("out")
            c.execute("INSERT INTO outbox (id, op_id, destination, payload, status, next_attempt_at, created_at,"
                      " updated_at) VALUES (?,?,?,?, 'pending', ?, ?, ?)",
                      (oid, op_id, destination, dumps(payload), now, now, now))
        return oid

    def recover_after_restart(self) -> int:
        """Items left in_flight by a crash have unknown outcomes."""
        cur = self.s.ex("UPDATE outbox SET status='outcome_unknown', last_error='process restarted while in flight',"
                        " updated_at=? WHERE status='in_flight'", (self.s.clock.iso(),))
        return cur.rowcount

    def _backoff(self, attempts: int) -> str:
        delay = min(self.cap, self.base * (2 ** max(0, attempts - 1)))
        return iso(self.s.clock.now() + _dt.timedelta(seconds=delay))

    def _claim(self) -> dict | None:
        now = self.s.clock.iso()
        with self.s.tx() as c:
            r = c.execute("SELECT * FROM outbox WHERE status IN ('pending','outcome_unknown') AND"
                          " (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT 1",
                          (now,)).fetchone()
            if not r:
                return None
            c.execute("UPDATE outbox SET status='in_flight', updated_at=? WHERE id=?", (now, r["id"]))
            d = dict(r)
            d["prior_status"] = r["status"]
            return d

    def _finish(self, oid: str, **fields: Any) -> None:
        fields["updated_at"] = self.s.clock.iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.s.ex(f"UPDATE outbox SET {sets} WHERE id=?", [*fields.values(), oid])  # noqa: S608 - placeholders only; column names are internal

    def process(self, limit: int = 100) -> dict:
        stats = {"done": 0, "retry": 0, "unknown": 0, "conflict": 0, "dead": 0, "reconciled": 0, "no_destination": 0}
        for _ in range(limit):
            item = self._claim()
            if not item:
                break
            dest = self.destinations.get(item["destination"])
            if not dest:
                self._finish(item["id"], status="pending", next_attempt_at=self._backoff(1),
                             last_error="destination not registered/configured")
                stats["no_destination"] += 1
                continue
            payload = loads(item["payload"], {})
            if item["prior_status"] == "outcome_unknown":
                try:
                    receipt = dest.lookup(item["op_id"])
                except Exception as e:  # lookup itself failed; try again later
                    self._finish(item["id"], status="outcome_unknown", next_attempt_at=self._backoff(item["attempts"]),
                                 last_error=redact(f"lookup failed: {e}"))
                    stats["unknown"] += 1
                    continue
                if receipt is not None:
                    self._finish(item["id"], status="done", receipt=dumps(receipt))
                    stats["reconciled"] += 1
                    continue
            attempts = item["attempts"] + 1
            try:
                receipt = dest.apply(item["op_id"], payload)
            except OutcomeUnknown as e:
                self._finish(item["id"], status="outcome_unknown", attempts=attempts,
                             next_attempt_at=self._backoff(attempts), last_error=redact(str(e)))
                stats["unknown"] += 1
            except ConflictError as e:
                self._finish(item["id"], status="conflict", attempts=attempts, last_error=redact(str(e)))
                stats["conflict"] += 1
            except TransientError as e:
                if attempts >= self.max_attempts:
                    self._finish(item["id"], status="dead", attempts=attempts, last_error=redact(str(e)))
                    stats["dead"] += 1
                else:
                    self._finish(item["id"], status="pending", attempts=attempts,
                                 next_attempt_at=self._backoff(attempts), last_error=redact(str(e)))
                    stats["retry"] += 1
            except Exception as e:  # permanent failure
                self._finish(item["id"], status="failed", attempts=attempts, last_error=redact(f"{type(e).__name__}: {e}"))
                stats["dead"] += 1
            else:
                self._finish(item["id"], status="done", attempts=attempts, receipt=dumps(receipt))
                stats["done"] += 1
        return stats

    def retry_conflict(self, oid: str, payload: dict | None = None) -> None:
        """After reconciliation, re-queue a conflicted item (optionally with a merged payload)."""
        fields: dict[str, Any] = {"status": "pending", "next_attempt_at": self.s.clock.iso()}
        if payload is not None:
            fields["payload"] = dumps(payload)
        self._finish(oid, **fields)

    def status(self) -> dict:
        rows = self.s.q("SELECT destination, status, COUNT(*) n, MIN(created_at) oldest FROM outbox"
                        " GROUP BY destination, status")
        out: dict[str, dict] = {}
        now = self.s.clock.now()
        for r in rows:
            d = out.setdefault(r["destination"], {"counts": {}, "oldest_pending_age_s": None})
            d["counts"][r["status"]] = r["n"]
            if r["status"] in ("pending", "outcome_unknown", "in_flight"):
                age = (now - (parse_iso(r["oldest"]) or now)).total_seconds()
                d["oldest_pending_age_s"] = max(d["oldest_pending_age_s"] or 0, age)
        return out

    def items(self, status: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT id, op_id, destination, status, attempts, next_attempt_at, last_error, created_at, updated_at" \
              " FROM outbox"
        p: list[Any] = []
        if status:
            sql += " WHERE status=?"
            p.append(status)
        return [dict(r) for r in self.s.q(sql + " ORDER BY updated_at DESC LIMIT ?", [*p, limit])]


class Checkpoints:
    def __init__(self, store: Store):
        self.s = store

    def get(self, stream: str) -> str | None:
        r = self.s.one("SELECT position FROM checkpoints WHERE stream=?", (stream,))
        return r[0] if r else None

    def set(self, stream: str, position: str, conn=None) -> None:
        sql = ("INSERT INTO checkpoints (stream, position, updated_at) VALUES (?,?,?) ON CONFLICT(stream)"
               " DO UPDATE SET position=excluded.position, updated_at=excluded.updated_at")
        params = (stream, position, self.s.clock.iso())
        if conn is not None:
            conn.execute(sql, params)  # caller's transaction: commit with the data it covers
        else:
            self.s.ex(sql, params)
