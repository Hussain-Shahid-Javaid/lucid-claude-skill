"""ChatGPT data-export adapter (conversations.json inside the export ZIP).

Evidence: OpenAI documents only that the export is a ZIP with chat history
(help.openai.com/en/articles/7260999). The structure parsed here comes from
community write-ups of real exports (community.openai.com thread 403144; Open WebUI
import docs): an array of conversations with `title`, `create_time`, `update_time`,
`current_node`, and `mapping` — a dict of nodes `{id, message, parent, children}`
where `message.author.role`, `message.content.content_type`, `message.content.parts`.
Users report that the format changes over time, so unknown shapes are reported as
warnings rather than silently dropped.

Status: community-documented; tested against synthetic fixtures only.
"""
from __future__ import annotations

import json
from typing import Iterator

from ..conversations import ImportErrorBadInput, NormalizedConversation, NormalizedMessage
from ..util import from_epoch

MANIFEST = {
    "id": "chatgpt", "name": "ChatGPT data export", "schema_status": "community-documented",
    "formats": ["zip", "conversations.json"], "branches": "preserved (mapping tree)",
    "attachments": "references from metadata only", "incremental": True,
    "deletion": "reimport respects Lucid tombstones; does not delete in ChatGPT",
    "native_history_sync": "unsupported (no documented API to write ChatGPT history)",
    "sources": ["https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data",
                "https://community.openai.com/t/decoding-exported-data-by-parsing-conversations-json-and-or-chat-html/403144"],
    "verified": "2026-10-27 (documentation review; no live export validated)",
}


def _text(content: dict | None, warnings: list[str]) -> str:
    if not content:
        return ""
    ct = content.get("content_type")
    parts = content.get("parts")
    if isinstance(parts, list):
        out = []
        for p in parts:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                if p.get("content_type") == "image_asset_pointer" or "asset_pointer" in p:
                    out.append(f"[image: {p.get('asset_pointer', 'attachment')}]")
                elif "text" in p:
                    out.append(str(p["text"]))
                else:
                    out.append(f"[{p.get('content_type', 'part')}]")
        return "\n".join(out)
    if "text" in content:
        return str(content["text"])
    warnings.append(f"unhandled content_type {ct!r}")
    return f"[unsupported content: {ct}]"


def parse(data: bytes, account: str = "default") -> Iterator[NormalizedConversation]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ImportErrorBadInput(f"conversations.json is not valid JSON: {e}") from None
    if not isinstance(doc, list):
        raise ImportErrorBadInput("expected a JSON array of conversations")
    for conv in doc:
        if not isinstance(conv, dict) or "mapping" not in conv:
            raise ImportErrorBadInput("conversation without 'mapping' — format not recognised")
        warnings: list[str] = []
        mapping = conv["mapping"] or {}
        msgs: list[NormalizedMessage] = []
        # Nodes without a message (e.g. the synthetic root) are skipped, but their
        # children are re-parented to the nearest ancestor with a message.
        def visible_parent(node_id):
            seen = set()
            pid = (mapping.get(node_id) or {}).get("parent")
            while pid and pid not in seen:
                seen.add(pid)
                pnode = mapping.get(pid) or {}
                if pnode.get("message") and _keep(pnode["message"]):
                    return pid
                pid = pnode.get("parent")
            return None

        for nid, node in mapping.items():
            msg = node.get("message")
            if not msg or not _keep(msg):
                continue
            role = (msg.get("author") or {}).get("role") or "unknown"
            att = [{"name": a.get("name"), "id": a.get("id"), "mime": a.get("mime_type")}
                   for a in (msg.get("metadata") or {}).get("attachments", []) if isinstance(a, dict)]
            msgs.append(NormalizedMessage(external_id=str(msg.get("id") or nid), role=role,
                                          content=_text(msg.get("content"), warnings), parent=visible_parent(nid),
                                          created_at=from_epoch(msg.get("create_time")), attachments=att,
                                          metadata={"model": (msg.get("metadata") or {}).get("model_slug")}
                                          if (msg.get("metadata") or {}).get("model_slug") else {}))
        leaf = conv.get("current_node")
        if leaf and leaf in mapping and not (mapping[leaf].get("message") and _keep(mapping[leaf]["message"])):
            leaf = visible_parent(leaf)
        yield NormalizedConversation(
            provider="chatgpt", account=account, external_id=str(conv.get("id") or conv.get("conversation_id")),
            title=conv.get("title"), created_at=from_epoch(conv.get("create_time")),
            updated_at=from_epoch(conv.get("update_time")), current_leaf=leaf, messages=msgs,
            warnings=sorted(set(warnings)))


def _keep(msg: dict) -> bool:
    role = (msg.get("author") or {}).get("role")
    if role == "system" and not _text(msg.get("content"), []).strip():
        return False
    return True
