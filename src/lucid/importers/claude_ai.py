"""Claude.ai data-export adapter (conversations.json).

Evidence: Anthropic documents that exports contain conversation data but publishes
no schema (support.claude.com/en/articles/9450526). Community write-ups describe a
`conversations.json` array where each conversation has `chat_messages` and each
message has `sender` ("human" | "assistant"). Other field names used below
(`uuid`, `name`, `created_at`, `updated_at`, `text`, `content[].text`,
`parent_message_uuid`, `attachments`, `files`) are handled defensively because they
are NOT confirmed by an inspected real export in this project.

Status: community-documented / partially unverified; synthetic fixtures only.
"""
from __future__ import annotations

import json
from typing import Iterator

from ..conversations import ImportErrorBadInput, NormalizedConversation, NormalizedMessage

MANIFEST = {
    "id": "claude", "name": "Claude.ai data export", "schema_status": "community-documented",
    "formats": ["zip", "conversations.json"],
    "branches": "preserved when parent_message_uuid is present, otherwise linear",
    "attachments": "names/extracted text references only", "incremental": True,
    "native_history_sync": "unsupported (no documented API to write claude.ai history)",
    "sources": ["https://support.claude.com/en/articles/9450526-export-your-claude-data"],
    "verified": "2026-10-27 (documentation review; no live export validated)",
}

ROLE = {"human": "user", "user": "user", "assistant": "assistant"}


def _text(m: dict) -> str:
    if isinstance(m.get("text"), str) and m["text"].strip():
        return m["text"]
    parts = []
    for c in m.get("content") or []:
        if isinstance(c, dict):
            if c.get("type") == "text" and "text" in c:
                parts.append(str(c["text"]))
            elif c.get("type") in ("tool_use", "tool_result"):
                parts.append(f"[{c['type']}: {c.get('name', '')}]")
    return "\n".join(parts)


def parse(data: bytes, account: str = "default") -> Iterator[NormalizedConversation]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ImportErrorBadInput(f"conversations.json is not valid JSON: {e}") from None
    if not isinstance(doc, list):
        raise ImportErrorBadInput("expected a JSON array of conversations")
    for conv in doc:
        if not isinstance(conv, dict) or "chat_messages" not in conv:
            raise ImportErrorBadInput("conversation without 'chat_messages' — format not recognised")
        msgs: list[NormalizedMessage] = []
        warnings = []
        prev = None
        explicit_parents = any("parent_message_uuid" in m for m in conv["chat_messages"] if isinstance(m, dict))
        for i, m in enumerate(conv["chat_messages"]):
            sender = m.get("sender")
            if sender not in ROLE:
                warnings.append(f"unknown sender {sender!r}")
            mid = str(m.get("uuid") or f"{conv.get('uuid')}:{i}")
            parent = m.get("parent_message_uuid") if explicit_parents else prev
            if parent == "00000000-4000-8000-0000-000000000000":
                parent = None  # root sentinel seen in some exports
            att = [{"name": a.get("file_name"), "size": a.get("file_size")} for a in (m.get("attachments") or [])
                   if isinstance(a, dict)] + [{"name": f.get("file_name")} for f in (m.get("files") or [])
                                              if isinstance(f, dict)]
            msgs.append(NormalizedMessage(external_id=mid, role=ROLE.get(sender, "system"), content=_text(m),
                                          parent=parent, created_at=m.get("created_at"), attachments=att))
            prev = mid
        if not explicit_parents and msgs:
            warnings.append("no parent ids in export: messages linked linearly; edits/branches may be merged")
        yield NormalizedConversation(
            provider="claude", account=account, external_id=str(conv.get("uuid") or conv.get("id")),
            title=conv.get("name") or conv.get("title"), created_at=conv.get("created_at"),
            updated_at=conv.get("updated_at"), messages=msgs, warnings=sorted(set(warnings)),
            coverage="full" if explicit_parents else "partial")
