"""Markdown / plain-text transcript import (reconstructed, lossy).

Recognised speaker markers (case-insensitive), at line start:
  "## User", "### Assistant", "**User:**", "User:", "Human:", "Assistant:", "Claude:", "ChatGPT:", "Gemini:", "System:"

Limitations (recorded on every imported conversation):
* No branches, edits or message ids exist in the source; ids are positional.
* Timestamps are unknown unless present in a front-matter "date:" line.
* Attachments, tool calls and formatting fidelity are lost.
"""
from __future__ import annotations

import re
from typing import Iterator

from ..conversations import NormalizedConversation, NormalizedMessage
from ..util import sha256

MANIFEST = {
    "id": "markdown", "name": "Markdown / text transcript", "schema_status": "lucid-defined",
    "formats": ["md", "txt"], "branches": "not available (linear reconstruction)",
    "attachments": "lost", "incremental": False, "coverage": "reconstructed", "verified": "2026-10-27",
}

ROLE_MAP = {"user": "user", "human": "user", "you": "user", "me": "user",
            "assistant": "assistant", "claude": "assistant", "chatgpt": "assistant", "gemini": "assistant",
            "ai": "assistant", "model": "assistant", "system": "system", "tool": "tool"}
MARK = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)?(user|human|you|assistant|claude|chatgpt|gemini|ai|model|system|tool)"
                  r"\s*(?::\*\*|\*\*:|:|\*\*)?\s*(.*)$", re.I)


def parse(data: bytes, provider: str = "markdown", title: str | None = None) -> Iterator[NormalizedConversation]:
    text = data.decode("utf-8-sig")
    date = None
    m = re.search(r"^date:\s*(\S+)", text, re.M)
    if m:
        date = m.group(1)
    msgs: list[NormalizedMessage] = []
    role, buf = None, []

    def flush():
        if role and "\n".join(buf).strip():
            i = len(msgs)
            msgs.append(NormalizedMessage(external_id=f"md-{i}", role=role, content="\n".join(buf).strip(),
                                          parent=msgs[-1].external_id if msgs else None, created_at=date))

    for line in text.splitlines():
        mm = MARK.match(line)
        if mm and (line.lstrip().startswith(("#", "**")) or ":" in line[: len(mm.group(1)) + 4]):
            flush()
            role, buf = ROLE_MAP[mm.group(1).lower()], [mm.group(2)] if mm.group(2) else []
        elif role:
            buf.append(line)
    flush()
    if not msgs:
        return
    yield NormalizedConversation(
        provider=provider, external_id="md-" + sha256(text)[:16], title=title or msgs[0].content[:60],
        messages=msgs, coverage="reconstructed", created_at=date,
        warnings=["reconstructed from text: no branches, message ids, attachments or reliable timestamps"])
