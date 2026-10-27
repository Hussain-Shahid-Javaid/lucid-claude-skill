"""Mirror selected memory categories to a cloud backend via the transactional outbox,
and rebuild local state/indexes from the durable cloud copy.

The user chooses what is stored remotely (`categories`); nothing is uploaded by
default. Every remote write carries an op id so uncertain outcomes are reconciled
with `lookup_op` before any retry.
"""
from __future__ import annotations

from typing import Iterable

from ..core import Lucid
from ..memory import Scope
from ..util import dumps, sha256
from .base import ConflictError
from .gdocs import GoogleDocsBackend
from .s3 import S3Backend


def memory_record(m: dict) -> dict:
    keep = ("id", "schema_version", "user_scope", "project_scope", "kind", "key", "content", "rationale", "origin",
            "verification", "confidence", "uncertainty", "created_at", "observed_at", "updated_at", "expires_at",
            "revision", "supersedes", "superseded_by", "status", "sharing", "retention", "tags", "content_hash")
    rec = {k: m.get(k) for k in keep}
    rec["evidence"] = [{k: e.get(k) for k in ("source_id", "message_id", "quote", "relation", "uri")}
                       for e in m.get("evidence", [])]
    return rec


class S3Destination:
    """Outbox destination applying memory upserts/deletes to S3."""

    def __init__(self, backend: S3Backend):
        self.b = backend
        self.name = f"s3:{backend.bucket}/{backend.prefix}"

    def apply(self, op_id: str, payload: dict) -> dict:
        ev = self.b.append_event(op_id, payload)
        if payload["action"] == "upsert":
            rec = payload["record"]
            cur = self.b.get_record("memory", rec["id"])
            if cur and cur.body.get("status") == rec.get("status") and cur.body.get("revision", 0) > rec.get("revision", 0):
                raise ConflictError(f"remote has newer revision of {rec['id']}")
            etag = self.b.put_record("memory", rec["id"], rec, expected_version=cur.version if cur else None,
                                     create_only=cur is None)
            return {"event": ev, "etag": etag}
        if payload["action"] == "delete":
            res = self.b.delete_record("memory", payload["id"], payload.get("reason", ""))
            # Upsert events carry the deleted content: remove them (all versions), keep this delete event.
            res["events_removed"] = self.b.delete_events(f"mem:{payload['id']}:", keep=op_id)
            return {"event": ev, **res}
        raise ValueError(payload["action"])

    def lookup(self, op_id: str) -> dict | None:
        return self.b.lookup_op(op_id)


class Mirror:
    def __init__(self, lucid: Lucid, destination, categories: Iterable[str], scope: Scope = Scope()):
        self.L = lucid
        self.dest = destination
        self.categories = set(categories)
        self.scope = scope
        lucid.outbox.register(destination)

    def queue_changes(self) -> int:
        """Enqueue every changed memory in the selected categories (idempotent op ids)."""
        n = 0
        rows = self.L.store.q("SELECT id, kind, status, revision, content_hash FROM memories WHERE user_scope=? AND"
                              " project_scope=?", (self.scope.user, self.scope.project))
        for r in rows:
            if r["kind"] not in self.categories:
                continue
            if r["status"] in ("active", "superseded"):
                m = self.L.memory.get(r["id"])
                op = f"mem:{r['id']}:r{r['revision']}:{r['status']}:{r['content_hash'][:12]}"
                self.L.outbox.enqueue(op, self.dest.name, {"action": "upsert", "record": memory_record(m)})
            elif r["status"] in ("deleted", "invalidated"):
                op = f"mem:{r['id']}:delete"
                self.L.outbox.enqueue(op, self.dest.name, {"action": "delete", "id": r["id"], "reason": r["status"]})
            n += 1
        return n

    def sync(self) -> dict:
        queued = self.queue_changes()
        return {"queued": queued, **self.L.outbox.process()}


def rebuild_from_s3(lucid: Lucid, backend: S3Backend) -> dict:
    """Disaster recovery: restore memory records + FTS index from S3, honouring tombstones."""
    tomb = backend.tombstoned("memory")
    restored = skipped = 0
    for rid in backend.list_records("memory"):
        if rid in tomb:
            skipped += 1
            continue
        v = backend.get_record("memory", rid)
        if not v:
            continue
        rec = v.body
        with lucid.store.tx() as c:
            if c.execute("SELECT 1 FROM tombstones WHERE entity_type='memory' AND entity_id=?", (rid,)).fetchone():
                skipped += 1
                continue
            c.execute("INSERT OR REPLACE INTO memories (id, schema_version, user_scope, project_scope, kind, key, content,"
                      " rationale, origin, verification, confidence, uncertainty, created_at, observed_at, updated_at,"
                      " expires_at, revision, supersedes, superseded_by, status, sharing, retention, tags, content_hash)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rec["id"], rec.get("schema_version", 1), rec["user_scope"], rec["project_scope"], rec["kind"],
                       rec.get("key"), rec["content"], rec.get("rationale"), rec["origin"], rec["verification"],
                       rec.get("confidence"), rec.get("uncertainty"), rec["created_at"], rec.get("observed_at"),
                       rec["updated_at"], rec.get("expires_at"), rec.get("revision", 1), rec.get("supersedes"),
                       rec.get("superseded_by"), rec.get("status", "active"), rec.get("sharing", "private"),
                       rec.get("retention"), dumps(rec.get("tags") or []),
                       rec.get("content_hash") or sha256(rec["content"])))
            for e in rec.get("evidence", []):
                c.execute("INSERT OR IGNORE INTO memory_evidence (memory_id, source_id, message_id, quote, relation)"
                          " VALUES (?,?,?,?,?)", (rid, e.get("source_id") or "", e.get("message_id") or "",
                                                 e.get("quote"), e.get("relation", "supports")))
        restored += 1
    indexed = lucid.memory.rebuild_index()
    return {"restored": restored, "skipped_tombstoned": skipped, "indexed": indexed}


class DocsMirror:
    """Mirror selected categories into Google Docs managed sections, reconciling user edits first."""

    def __init__(self, lucid: Lucid, backend: GoogleDocsBackend, categories: Iterable[str], scope: Scope = Scope()):
        self.L = lucid
        self.b = backend
        self.categories = set(categories)
        self.scope = scope
        self.report: dict = {"user_edits_applied": [], "removed_in_doc_pending_confirmation": []}

    def _selected(self) -> list[dict]:
        out = []
        for k in sorted(self.categories):
            out += self.L.memory.list(self.scope, kind=k, limit=100000)
        return sorted(out, key=lambda m: m["created_at"])

    def reconcile(self, doc_id: str, remote: dict[str, str]) -> None:
        for mid, text in remote.items():
            try:
                m = self.L.memory.get(mid, scope=self.scope)  # a doc line cannot touch other scopes
            except Exception:
                continue
            if m["kind"] not in self.categories:
                continue
            if m["status"] == "active" and text.strip() != m["content"].replace("\n", " ").strip():
                new = self.L.memory.supersede(mid, text.strip(), reason=f"edited by user in Google Doc {doc_id}",
                                              origin="user", actor="user:google_docs")
                self.report["user_edits_applied"].append({"old": mid, "new": new["id"]})

    def _get_set(self, key: str) -> set[str]:
        import json
        return set(json.loads(self.L.store.get_setting(key, "[]")))

    def _put_set(self, key: str, val: set[str]) -> None:
        import json
        self.L.store.set_setting(key, json.dumps(sorted(val)))

    def sync(self, op_id: str) -> dict:
        from .gdocs import find_section, parse_section
        written_key, pending_key = "gdocs:last_written", "gdocs:removed_pending"
        last_written = self._get_set(written_key)
        pending = self._get_set(pending_key)
        remote_ids: set[str] = set()
        # 1. Pull: user edits in the doc are newer user statements -> supersede locally.
        for d in self.b.doc_ids:
            sec = find_section(self.b.c.get(d))
            if sec:
                remote = parse_section(sec[2])
                remote_ids |= set(remote)
                self.reconcile(d, remote)
        # 2. Lines the user removed from the doc are NOT auto-deleted; they are held for confirmation
        #    and excluded from the rewrite so the doc does not silently undo the user's edit.
        if self.b.doc_ids:
            pending |= {mid for mid in last_written - remote_ids}
        superseded = {e["old"] for e in self.report["user_edits_applied"]}
        pending -= superseded
        self.report["removed_in_doc_pending_confirmation"] = sorted(pending)
        selected = [m for m in self._selected() if m["id"] not in pending]
        receipts = self.b.write_memories(selected, op_id, reconcile=self.reconcile)
        self._put_set(written_key, {m["id"] for m in selected})
        self._put_set(pending_key, pending)
        return {"written": len(selected), "receipts": receipts, **self.report}

    def confirm_removal(self, mid: str, delete: bool) -> None:
        """User decision for a line removed in the doc: delete locally, or restore it on next sync."""
        pending = self._get_set("gdocs:removed_pending")
        pending.discard(mid)
        self._put_set("gdocs:removed_pending", pending)
        if delete:
            self.L.memory.delete(mid, reason="removed by user in Google Doc", actor="user:google_docs")
