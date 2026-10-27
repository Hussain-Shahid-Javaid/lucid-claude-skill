"""Google Docs backend: readable, structured memory sections inside user-selected docs.

Google Docs is a *document* backend, not a transactional database. Lucid keeps its
operational state (outbox, checkpoints, index) locally and treats each managed
section as a durable, human-editable view.

Managed section format (everything outside the markers is user content and is
preserved byte-for-byte)::

    ⟦lucid:begin v1⟧
    [mem_ab12…] (preference · verified · 2026-10-27) User prefers pytest.
    ⟦lucid:end op=<op_id>⟧

* Writes use `writeControl.requiredRevisionId`; a mismatch → ConflictError, and the
  caller re-reads, reconciles user edits, then retries (bounded).
* Uncertain write outcomes are resolved by re-reading the doc and checking the
  op id recorded in the end marker before retrying.
* User edits to a line become `supersede` operations (origin=user) on reconcile;
  lines removed by the user are reported for confirmation, never auto-deleted.
* Large collections are partitioned across documents (`max_per_doc`).

OAuth: request the narrow `https://www.googleapis.com/auth/drive.file` scope (files
the user picked or Lucid created) plus `documents`. Tokens are stored by
`TokenStore` with 0600 permissions under $LUCID_HOME/credentials, never in the
memory database, skill files, logs or the dashboard bundle.
"""
from __future__ import annotations

import json
import os
import re
import stat
from typing import Any, Protocol

from ..util import lucid_home
from .base import ConflictError, OutcomeUnknown, PermanentError, TransientError

BEGIN = "⟦lucid:begin v1⟧"
END_RE = re.compile(r"⟦lucid:end op=([^⟧]*)⟧")
LINE_RE = re.compile(r"^\[(mem_[0-9a-f]{20})\] \(([^)]*)\) (.*)$")
SCOPES = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/documents"]


class DocsClient(Protocol):
    def get(self, doc_id: str) -> dict: ...
    def batch_update(self, doc_id: str, requests: list[dict], required_revision_id: str | None) -> dict: ...
    def create(self, title: str) -> str: ...


class GoogleDocsClient:
    """Thin wrapper over google-api-python-client (optional dependency)."""

    def __init__(self, credentials):
        from googleapiclient.discovery import build  # type: ignore
        self.svc = build("docs", "v1", credentials=credentials, cache_discovery=False)

    def get(self, doc_id):
        return self._call(self.svc.documents().get(documentId=doc_id))

    def batch_update(self, doc_id, requests, required_revision_id):
        body: dict[str, Any] = {"requests": requests}
        if required_revision_id:
            body["writeControl"] = {"requiredRevisionId": required_revision_id}
        return self._call(self.svc.documents().batchUpdate(documentId=doc_id, body=body))

    def create(self, title):
        return self._call(self.svc.documents().create(body={"title": title}))["documentId"]

    @staticmethod
    def _call(req):
        try:
            return req.execute(num_retries=0)
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            msg = str(e)
            if status == 400 and "revision" in msg.lower():
                raise ConflictError("document revision changed") from e
            if status in (429, 500, 502, 503, 504):
                raise TransientError(f"HTTP {status}") from e
            if status in (401, 403, 404):
                raise PermanentError(f"HTTP {status}: check OAuth consent/scopes and document access") from e
            if "timed out" in msg.lower():
                raise OutcomeUnknown(msg) from e
            raise


class TokenStore:
    """OAuth token file with owner-only permissions. Prefer the OS keyring when available."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(lucid_home(), "credentials", "google_token.json")

    def save(self, token_json: str) -> None:
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(token_json)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def load(self) -> str | None:
        try:
            with open(self.path) as fh:
                return fh.read()
        except FileNotFoundError:
            return None


def doc_text(doc: dict) -> str:
    out = []
    for el in doc.get("body", {}).get("content", []):
        for pe in (el.get("paragraph") or {}).get("elements", []):
            out.append((pe.get("textRun") or {}).get("content", ""))
    return "".join(out)


def find_section(doc: dict) -> tuple[int, int, str, str | None] | None:
    """Return (start_index, end_index, inner_text, last_op) of the managed section in Docs index space."""
    text = doc_text(doc)
    b = text.find(BEGIN)
    if b < 0:
        return None
    m = END_RE.search(text, b)
    if not m:
        raise ConflictError("managed section end marker missing (edited by user?) — manual repair needed")
    base = _base_index(doc)
    inner = text[b + len(BEGIN) + 1: m.start()]
    return base + b, base + m.end(), inner, m.group(1) or None


def _base_index(doc: dict) -> int:
    content = doc.get("body", {}).get("content", [])
    for el in content:
        if "paragraph" in el:
            return el.get("startIndex", 1)
    return 1


def render_section(memories: list[dict], op_id: str) -> str:
    lines = [BEGIN]
    for m in memories:
        content = m["content"].replace("\n", " ")
        lines.append(f"[{m['id']}] ({m['kind']} · {m['verification']} · {m['updated_at'][:10]}) {content}")
    lines.append(f"⟦lucid:end op={op_id}⟧")
    return "\n".join(lines)


def parse_section(inner: str) -> dict[str, str]:
    out = {}
    for line in inner.splitlines():
        mm = LINE_RE.match(line.strip())
        if mm:
            out[mm.group(1)] = mm.group(3)
    return out


class GoogleDocsBackend:
    name = "google_docs"

    def __init__(self, client: DocsClient, doc_ids: list[str] | None = None, *, max_per_doc: int = 300,
                 title: str = "Lucid memory"):
        self.c = client
        self.doc_ids = list(doc_ids or [])
        self.max_per_doc = max_per_doc
        self.title = title

    def capabilities(self) -> dict:
        return {"authoritative": "managed sections in Google Docs (human-editable)", "multi_object_transactions": False,
                "conditional_writes": "requiredRevisionId per batchUpdate", "versioning": "Docs revision history",
                "offline": "local queue via outbox", "deletion": "line removed from section; Docs revision history "
                "may retain it (Google retention applies)"}

    def _ensure_docs(self, n_needed: int) -> None:
        while len(self.doc_ids) < max(1, n_needed):
            self.doc_ids.append(self.c.create(f"{self.title} ({len(self.doc_ids) + 1})"))

    def write_memories(self, memories: list[dict], op_id: str, *, max_conflict_retries: int = 3,
                       reconcile=None) -> list[dict]:
        """Write partitions; on revision conflict, re-read + reconcile + retry (bounded)."""
        parts = [memories[i:i + self.max_per_doc] for i in range(0, len(memories), self.max_per_doc)] or [[]]
        self._ensure_docs(len(parts))
        receipts = []
        for idx, part in enumerate(parts):
            doc_id = self.doc_ids[idx]
            for attempt in range(max_conflict_retries + 1):
                doc = self.c.get(doc_id)
                sec = find_section(doc)
                if sec and sec[3] == op_id:
                    receipts.append({"doc_id": doc_id, "revision": doc.get("revisionId"), "already_applied": True})
                    break
                if sec and reconcile is not None:
                    reconcile(doc_id, parse_section(sec[2]))
                requests = []
                new_text = render_section(part, op_id)
                if sec:
                    start, end = sec[0], sec[1]
                    requests.append({"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}})
                    requests.append({"insertText": {"location": {"index": start}, "text": new_text}})
                else:
                    end_index = _end_index(doc)
                    requests.append({"insertText": {"location": {"index": end_index}, "text": "\n" + new_text}})
                try:
                    r = self.c.batch_update(doc_id, requests, doc.get("revisionId"))
                    receipts.append({"doc_id": doc_id, "revision": (r.get("writeControl") or {}).get("requiredRevisionId"),
                                     "already_applied": False})
                    break
                except ConflictError:
                    if attempt == max_conflict_retries:
                        raise
                except OutcomeUnknown:
                    # Re-read before any retry: if our op marker is present, the write landed.
                    sec2 = find_section(self.c.get(doc_id))
                    if sec2 and sec2[3] == op_id:
                        receipts.append({"doc_id": doc_id, "already_applied": True})
                        break
                    raise
        return receipts

    def read_all(self) -> dict[str, str]:
        out = {}
        for d in self.doc_ids:
            sec = find_section(self.c.get(d))
            if sec:
                out.update(parse_section(sec[2]))
        return out

    def lookup_op(self, op_id: str) -> dict | None:
        for d in self.doc_ids:
            sec = find_section(self.c.get(d))
            if sec and sec[3] == op_id:
                return {"doc_id": d}
        return None


def _end_index(doc: dict) -> int:
    content = doc.get("body", {}).get("content", [])
    return max(1, (content[-1].get("endIndex", 2) - 1) if content else 1)


class FakeDocsClient:
    """In-memory Docs emulation for offline contract tests (index base 1, revision ids, conflicts)."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.fail_next: list[Exception] = []
        self.apply_then_fail: list[Exception] = []

    def create(self, title):
        did = f"doc{len(self.docs) + 1}"
        self.docs[did] = {"title": title, "text": "\n", "rev": 1}
        return did

    def user_edit(self, did: str, fn) -> None:
        d = self.docs[did]
        d["text"] = fn(d["text"])
        d["rev"] += 1

    def get(self, did):
        d = self.docs[did]
        content, idx = [], 1
        for line in d["text"].splitlines(keepends=True):
            content.append({"startIndex": idx, "endIndex": idx + len(line),
                            "paragraph": {"elements": [{"textRun": {"content": line}}]}})
            idx += len(line)
        return {"documentId": did, "title": d["title"], "revisionId": f"r{d['rev']}", "body": {"content": content}}

    def batch_update(self, did, requests, required_revision_id):
        if self.fail_next:
            raise self.fail_next.pop(0)
        d = self.docs[did]
        if required_revision_id and required_revision_id != f"r{d['rev']}":
            raise ConflictError("revision mismatch")
        text = d["text"]
        for r in requests:
            if "deleteContentRange" in r:
                rg = r["deleteContentRange"]["range"]
                text = text[: rg["startIndex"] - 1] + text[rg["endIndex"] - 1:]
            elif "insertText" in r:
                i = r["insertText"]["location"]["index"] - 1
                text = text[:i] + r["insertText"]["text"] + text[i:]
        d["text"] = text
        d["rev"] += 1
        if self.apply_then_fail:
            raise self.apply_then_fail.pop(0)
        return {"documentId": did, "writeControl": {"requiredRevisionId": f"r{d['rev']}"}}


def dump_debug(doc: dict) -> str:  # pragma: no cover - debugging aid
    return json.dumps(doc, indent=1)
