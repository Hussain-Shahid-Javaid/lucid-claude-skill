"""Lucid generic conversation exchange format (lucid.conversation.v1).

JSON document::

    {"format": "lucid.conversation.v1",
     "conversations": [
        {"provider": "my-tool", "account": "work", "id": "c-1", "title": "...",
         "created_at": "2026-01-01T00:00:00Z", "updated_at": "...", "current_leaf": "m3",
         "messages": [
            {"id": "m1", "parent": null, "role": "user", "content": "...", "created_at": "...",
             "attachments": [{"name": "a.png", "uri": "...", "mime": "image/png"}]}
         ]}]}

JSONL: one conversation object per line (same shape as an element of "conversations").
See docs/exchange-format.md for the full specification.
"""
from __future__ import annotations

import json
from typing import Iterator

from ..conversations import ImportErrorBadInput, NormalizedConversation, NormalizedMessage

MANIFEST = {
    "id": "generic", "name": "Lucid exchange format (JSON/JSONL)", "schema_status": "lucid-defined",
    "formats": ["json", "jsonl"], "branches": "preserved via parent ids", "attachments": "references only",
    "incremental": True, "deletion": "tombstones respected on reimport", "verified": "2026-10-27",
}


def _conv(obj: dict, default_provider: str) -> NormalizedConversation:
    if not isinstance(obj, dict):
        raise ImportErrorBadInput("conversation must be an object")
    cid = obj.get("id") or obj.get("external_id")
    if not cid:
        raise ImportErrorBadInput("conversation missing 'id'")
    msgs = []
    for i, m in enumerate(obj.get("messages") or []):
        if not isinstance(m, dict) or "role" not in m or "content" not in m:
            raise ImportErrorBadInput(f"message #{i} needs role and content")
        content = m["content"]
        if isinstance(content, list):
            content = "\n".join(str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content)
        msgs.append(NormalizedMessage(external_id=str(m.get("id") or f"{cid}:{i}"), role=str(m["role"]),
                                      content=str(content), parent=m.get("parent"), created_at=m.get("created_at"),
                                      attachments=list(m.get("attachments") or [])))
    # Without explicit parents, messages form a linear chain in listed order.
    if msgs and not any(isinstance(m, dict) and "parent" in m for m in obj.get("messages") or []):
        for prev, cur in zip(msgs, msgs[1:]):
            cur.parent = prev.external_id
    return NormalizedConversation(provider=str(obj.get("provider") or default_provider),
                                  account=str(obj.get("account") or "default"), external_id=str(cid),
                                  title=obj.get("title"), created_at=obj.get("created_at"),
                                  updated_at=obj.get("updated_at"), current_leaf=obj.get("current_leaf"),
                                  messages=msgs, coverage=obj.get("coverage", "full"))


def parse(data: bytes, provider: str = "generic") -> Iterator[NormalizedConversation]:
    text = data.decode("utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("{") and '"conversations"' in stripped[:2000]:
        doc = json.loads(text)
        if doc.get("format") != "lucid.conversation.v1":
            raise ImportErrorBadInput("expected format 'lucid.conversation.v1'")
        for obj in doc["conversations"]:
            yield _conv(obj, provider)
        return
    for ln, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ImportErrorBadInput(f"line {ln}: invalid JSON ({e.msg})") from None
        yield _conv(obj, provider)
