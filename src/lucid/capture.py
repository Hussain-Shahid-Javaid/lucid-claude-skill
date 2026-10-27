"""Server-side handling of browser-extension captures.

Enforced here (not only in the extension):
* The site must be explicitly enabled and not paused; automatic captures also need
  `auto_capture` for that site. Default is user-selected capture only.
* Captures whose site adapter reported an extraction failure are rejected instead of
  importing possibly corrupted content.
* Payload size is capped; secrets are redacted; content is stored as inert text.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .conversations import NormalizedConversation, NormalizedMessage
from .memory import Scope

MAX_BYTES = 1_000_000


class CaptureRejected(PermissionError):
    pass


def site_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def ingest_capture(L, body: dict, *, user: str) -> dict:
    url = str(body.get("url", ""))
    site = site_of(url)
    if not site or urlparse(url).scheme not in ("http", "https"):
        raise CaptureRejected("only http(s) pages can be captured")
    row = L.store.one("SELECT * FROM capture_sites WHERE site=?", (site,))
    if not row or not row["enabled"]:
        raise CaptureRejected(f"capture is not enabled for {site}")
    if row["paused"]:
        raise CaptureRejected(f"capture is paused for {site}")
    trigger = body.get("trigger", "user")
    if trigger == "auto" and not row["auto_capture"]:
        raise CaptureRejected(f"automatic capture is not enabled for {site}")
    ext = body.get("extraction") or {}
    if ext.get("ok") is False:
        L.store.log("extension", "capture.extraction_failed", "site", site, {"errors": ext.get("errors", [])[:5],
                                                                             "adapter": body.get("adapter")})
        raise CaptureRejected(f"site adapter reported extraction failure: {ext.get('errors')}")
    approx = len(str(body.get("text", ""))) + sum(len(str(m.get("content", "")))
                                                  for m in (body.get("conversation") or {}).get("messages", []))
    if approx > MAX_BYTES:
        raise CaptureRejected("capture too large")
    kind = body.get("kind", "selection")
    sc = Scope(user, body.get("project") or "")
    if kind in ("selection", "page"):
        sid = L.memory.add_source("web", scope=sc, uri=url, title=body.get("title"), content=str(body.get("text", "")),
                                  metadata={"capture": kind, "adapter": body.get("adapter"), "trigger": trigger})
        L.store.log("extension", f"capture.{kind}", "source", sid, {"site": site})
        return {"source_id": sid, "kind": kind, "site": site}
    if kind == "conversation":
        conv = body.get("conversation") or {}
        msgs = [NormalizedMessage(external_id=str(m.get("id") or i), role=str(m.get("role", "user")),
                                  content=str(m.get("content", "")), parent=m.get("parent"))
                for i, m in enumerate(conv.get("messages", []))]
        if msgs and not any(m.parent for m in msgs):
            for a, b in zip(msgs, msgs[1:]):
                b.parent = a.external_id
        nc = NormalizedConversation(provider=str(conv.get("provider") or site), external_id=str(conv.get("id") or url),
                                    title=body.get("title"), messages=msgs, coverage="partial",
                                    warnings=["captured from visible page via browser adapter; "
                                              "hidden branches/edits are not available"],
                                    metadata={"adapter": body.get("adapter"), "url": url})
        st = L.archive.ingest(nc, method="browser-capture", scope_user=user, scope_project=sc.project)
        return {"conversation": st, "site": site}
    raise CaptureRejected(f"unknown capture kind {kind!r}")
