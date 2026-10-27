"""Branch-preserving conversation archive with idempotent, resumable imports.

* Conversations are namespaced by (provider, account, external_id); accounts are
  never merged without an explicit mapping.
* Messages keep parent links, so branches/edits are preserved (no flattening).
* Messages are deduplicated by (conversation, external message id) — never by text,
  because identical text can be separate events.
* A changed message body under the same id is stored as a new revision; the old one
  is kept with status 'revised'.
* Tombstoned conversations/messages are skipped on reimport.
* Content carrying a Lucid handoff marker is flagged as an echo so it never becomes
  independent evidence or a duplicate memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .db import Store
from .redact import redact
from .util import dumps, loads, new_id, sha256

HANDOFF_MARKER = re.compile(r"lucid-handoff:(hnd_[0-9a-f]{20})")
ROLES = {"user", "assistant", "system", "tool"}


@dataclass
class NormalizedMessage:
    external_id: str
    role: str
    content: str
    parent: str | None = None
    created_at: str | None = None
    attachments: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class NormalizedConversation:
    provider: str
    external_id: str
    messages: list[NormalizedMessage]
    account: str = "default"
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    current_leaf: str | None = None
    coverage: str = "full"          # full|partial|reconstructed
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class ImportErrorBadInput(ValueError):
    pass


class Archive:
    def __init__(self, store: Store):
        self.s = store

    # ---------------------------------------------------------------- import
    def start_import(self, provider: str, method: str, file_hash: str | None = None) -> str:
        """Create (or resume) an import job. Same provider+method+file hash resumes the unfinished job."""
        if file_hash:
            r = self.s.one("SELECT id FROM imports WHERE provider=? AND method=? AND file_hash=? AND status IN"
                           " ('running','interrupted')", (provider, method, file_hash))
            if r:
                return r[0]
        iid = new_id("imp")
        self.s.ex("INSERT INTO imports (id, provider, method, file_hash, started_at, status) VALUES (?,?,?,?,?,?)",
                  (iid, provider, method, file_hash, self.s.clock.iso(), "running"))
        return iid

    def import_checkpoint(self, import_id: str) -> int:
        r = self.s.one("SELECT checkpoint FROM imports WHERE id=?", (import_id,))
        return int(r[0]) if r and r[0] is not None else -1

    def finish_import(self, import_id: str, status: str, stats: dict, errors: list) -> None:
        self.s.ex("UPDATE imports SET finished_at=?, status=?, stats=?, errors=? WHERE id=?",
                  (self.s.clock.iso(), status, dumps(stats), redact(dumps(errors)), import_id))

    def ingest(self, conv: NormalizedConversation, *, method: str, scope_user: str = "default",
               scope_project: str = "", import_id: str | None = None, index: int | None = None,
               origin_lucid: bool = False) -> dict:
        """Upsert one conversation atomically. Checkpoint (if given) commits in the same transaction."""
        stats = {"messages_new": 0, "messages_unchanged": 0, "messages_revised": 0, "messages_tombstoned": 0,
                 "echo_messages": 0, "skipped": None}
        if conv.provider == "" or conv.external_id == "":
            raise ImportErrorBadInput("conversation needs provider and external_id")
        nat = f"conv:{conv.provider}|{conv.account}|{conv.external_id}"
        with self.s.tx() as c:
            if c.execute("SELECT 1 FROM tombstones WHERE natural_key=?", (nat,)).fetchone():
                stats["skipped"] = "tombstoned"
                if import_id is not None and index is not None:
                    c.execute("UPDATE imports SET checkpoint=? WHERE id=?", (index, import_id))
                return stats
            row = c.execute("SELECT id FROM conversations WHERE provider=? AND account=? AND external_id=?",
                            (conv.provider, conv.account, conv.external_id)).fetchone()
            if row:
                cid = row[0]
                c.execute("UPDATE conversations SET title=COALESCE(?, title), updated_at=COALESCE(?, updated_at),"
                          " current_leaf=COALESCE(?, current_leaf), coverage=? WHERE id=?",
                          (conv.title, conv.updated_at, conv.current_leaf, conv.coverage, cid))
            else:
                cid = new_id("conv")
                c.execute("INSERT INTO conversations (id, provider, account, external_id, title, created_at, updated_at,"
                          " import_method, coverage, current_leaf, user_scope, project_scope, origin_lucid, metadata)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, conv.provider, conv.account, conv.external_id, redact(conv.title or "") or None,
                           conv.created_at, conv.updated_at, method, conv.coverage, conv.current_leaf, scope_user,
                           scope_project, int(origin_lucid), dumps({**conv.metadata, "warnings": conv.warnings})))
            for m in conv.messages:
                if m.role not in ROLES:
                    m.metadata["original_role"] = m.role
                    m.role = "tool" if m.role in ("function", "plugin") else "system"
                mnat = f"msg:{cid}|{m.external_id}"
                if c.execute("SELECT 1 FROM tombstones WHERE natural_key=?", (mnat,)).fetchone():
                    stats["messages_tombstoned"] += 1
                    continue
                text = redact(m.content or "")
                h = sha256(f"{m.role}|{text}")
                origin = {"provider": conv.provider, "account": conv.account, "method": method,
                          "import_id": import_id, **({"meta": m.metadata} if m.metadata else {})}
                echo = HANDOFF_MARKER.search(text)
                if echo:
                    origin["lucid_echo"] = echo.group(1)
                    stats["echo_messages"] += 1
                ex = c.execute("SELECT id, content_hash FROM messages WHERE conversation_id=? AND external_id=?",
                               (cid, m.external_id)).fetchone()
                if ex and ex[1] == h:
                    stats["messages_unchanged"] += 1
                    continue
                revision_of = None
                if ex:
                    n = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND external_id LIKE ?",
                                  (cid, f"{m.external_id}#rev%")).fetchone()[0]
                    c.execute("UPDATE messages SET external_id=?, status='revised' WHERE id=?",
                              (f"{m.external_id}#rev{n + 1}", ex[0]))
                    revision_of = ex[0]
                    stats["messages_revised"] += 1
                else:
                    stats["messages_new"] += 1
                c.execute("INSERT INTO messages (id, conversation_id, external_id, parent_external_id, role, content,"
                          " created_at, revision_of, attachments, origin, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (new_id("msg"), cid, m.external_id, m.parent, m.role, text, m.created_at, revision_of,
                           dumps(m.attachments), dumps(origin), h))
            if import_id is not None and index is not None:
                c.execute("UPDATE imports SET checkpoint=? WHERE id=?", (index, import_id))
        stats["conversation_id"] = cid
        return stats

    def import_many(self, convs: Iterable[NormalizedConversation], *, provider: str, method: str,
                    file_hash: str | None = None, scope_user: str = "default", scope_project: str = "",
                    fail_after: int | None = None) -> dict:
        """Import a stream of conversations with a durable per-conversation checkpoint.

        Re-running with the same file hash resumes after the last committed conversation.
        `fail_after` exists for crash-recovery tests.
        """
        iid = self.start_import(provider, method, file_hash)
        done_upto = self.import_checkpoint(iid)
        totals: dict[str, Any] = {"conversations": 0, "skipped_checkpoint": 0, "skipped_tombstoned": 0,
                                  "messages_new": 0, "messages_unchanged": 0, "messages_revised": 0,
                                  "messages_tombstoned": 0, "echo_messages": 0}
        errors: list[str] = []
        try:
            for i, conv in enumerate(convs):
                if i <= done_upto:
                    totals["skipped_checkpoint"] += 1
                    continue
                if fail_after is not None and i >= fail_after:
                    raise RuntimeError("simulated crash")
                try:
                    st = self.ingest(conv, method=method, scope_user=scope_user, scope_project=scope_project,
                                     import_id=iid, index=i)
                except ImportErrorBadInput as e:
                    errors.append(f"conversation #{i}: {e}")
                    self.s.ex("UPDATE imports SET checkpoint=? WHERE id=?", (i, iid))
                    continue
                if st.get("skipped") == "tombstoned":
                    totals["skipped_tombstoned"] += 1
                    continue
                totals["conversations"] += 1
                for k in ("messages_new", "messages_unchanged", "messages_revised", "messages_tombstoned",
                          "echo_messages"):
                    totals[k] += st[k]
        except BaseException as e:
            self.finish_import(iid, "interrupted", totals, errors + [f"interrupted: {e}"])
            raise
        status = "completed" if not errors else "completed_with_errors"
        self.finish_import(iid, status, totals, errors)
        self.s.log("lucid", "conversation.import", "import", iid, {"provider": provider, **totals})
        return {"import_id": iid, "status": status, **totals, "errors": errors}

    # ---------------------------------------------------------------- read
    def list(self, *, provider: str | None = None, user: str = "default", project: str | None = None,
             limit: int = 100) -> list[dict]:
        sql = ("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id AND m.status='active')"
               " AS message_count FROM conversations c WHERE c.status='active' AND c.user_scope=?")
        p: list[Any] = [user]
        if provider:
            sql += " AND c.provider=?"
            p.append(provider)
        if project is not None:
            sql += " AND c.project_scope=?"
            p.append(project)
        sql += " ORDER BY COALESCE(c.updated_at, c.created_at) DESC LIMIT ?"
        p.append(limit)
        return [dict(r) for r in self.s.q(sql, p)]

    def get(self, cid: str) -> dict:
        c = self.s.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not c:
            raise KeyError(cid)
        msgs = [dict(r) for r in self.s.q("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,"
                                          " rowid", (cid,))]
        for m in msgs:
            m["origin"] = loads(m["origin"], {})
            m["attachments"] = loads(m["attachments"], [])
        active = [m for m in msgs if m["status"] == "active"]
        ids = {m["external_id"] for m in active}
        children: dict[str | None, list[str]] = {}
        for m in active:
            parent = m["parent_external_id"] if m["parent_external_id"] in ids else None
            children.setdefault(parent, []).append(m["external_id"])
        leaves = [m["external_id"] for m in active if m["external_id"] not in children]
        orphans = [m["external_id"] for m in active if m["parent_external_id"] and m["parent_external_id"] not in ids]
        out = dict(c)
        out["metadata"] = loads(out["metadata"], {})
        out.update(messages=msgs, leaves=leaves, branch_count=len(leaves), orphans=orphans,
                   children={k or "": v for k, v in children.items()})
        return out

    def thread(self, cid: str, leaf: str | None = None) -> list[dict]:
        """Linear path root→leaf for one branch (defaults to the provider's current leaf)."""
        conv = self.get(cid)
        by_ext = {m["external_id"]: m for m in conv["messages"] if m["status"] == "active"}
        leaf = leaf or conv["current_leaf"] or (conv["leaves"][-1] if conv["leaves"] else None)
        path, seen = [], set()
        while leaf and leaf in by_ext and leaf not in seen:
            seen.add(leaf)
            path.append(by_ext[leaf])
            leaf = by_ext[leaf]["parent_external_id"]
        return list(reversed(path))

    def search_messages(self, text: str, user: str = "default", limit: int = 20) -> list[dict]:
        like = f"%{text}%"
        return [dict(r) for r in self.s.q(
            "SELECT m.id, m.conversation_id, m.role, substr(m.content,1,300) AS snippet, m.created_at FROM messages m"
            " JOIN conversations c ON c.id=m.conversation_id WHERE c.user_scope=? AND m.status='active'"
            " AND m.content LIKE ? LIMIT ?", (user, like, limit))]

    # ---------------------------------------------------------------- delete
    def delete(self, cid: str, *, reason: str = "user request", actor: str = "user") -> dict:
        conv = self.s.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv:
            raise KeyError(cid)
        nat = f"conv:{conv['provider']}|{conv['account']}|{conv['external_id']}"
        now = self.s.clock.iso()
        with self.s.tx() as c:
            msg_ids = [r[0] for r in c.execute("SELECT id FROM messages WHERE conversation_id=?", (cid,))]
            c.execute("INSERT OR REPLACE INTO tombstones VALUES ('conversation',?,?,?,?)", (cid, nat, now, reason))
            c.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
            c.execute("UPDATE conversations SET status='deleted', title=NULL, metadata='{}' WHERE id=?", (cid,))
            invalidated = []
            if msg_ids:
                qs = ",".join("?" * len(msg_ids))
                derived = {r[0] for r in c.execute(
                    f"SELECT DISTINCT memory_id FROM memory_evidence WHERE message_id IN ({qs})", msg_ids)}
                for mid in derived:
                    # Invalidate only memories with no remaining evidence outside this conversation.
                    others = c.execute(
                        f"SELECT COUNT(*) FROM memory_evidence WHERE memory_id=? AND (message_id='' OR message_id"
                        f" NOT IN ({qs}))", [mid, *msg_ids]).fetchone()[0]
                    if not others:
                        c.execute("UPDATE memories SET status='invalidated', updated_at=? WHERE id=? AND"
                                  " status='active'", (now, mid))
                        c.execute("DELETE FROM memories_fts WHERE memory_id=?", (mid,))
                        invalidated.append(mid)
                    c.execute(f"DELETE FROM memory_evidence WHERE memory_id=? AND message_id IN ({qs})",
                              [mid, *msg_ids])
            # Queue deletes for destinations that previously received this conversation's content.
            sent = c.execute("SELECT DISTINCT destination FROM outbox WHERE status='done' AND payload LIKE ?",
                             (f'%"{cid}"%',)).fetchall()
            for (dest,) in sent:
                op = f"delete:{cid}:{dest}"
                c.execute("INSERT OR IGNORE INTO outbox (id, op_id, destination, payload, created_at, updated_at)"
                          " VALUES (?,?,?,?,?,?)", (new_id("out"), op, dest,
                                                     dumps({"action": "delete", "conversation_id": cid}), now, now))
        status = {
            "local_archive": "deleted",
            "derived_memories_invalidated": invalidated,
            "destinations": {d: "delete queued" for (d,) in sent},
            "provider_native_history": (f"not deleted by Lucid: delete it in {conv['provider']} itself; "
                                        "provider retention policies may keep copies"),
        }
        self.s.log(actor, "conversation.delete", "conversation", cid, status)
        return status
