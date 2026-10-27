"""Conversation import adapters.

Every adapter declares a manifest. `schema_status` is honest about evidence:

* ``lucid-defined``   – Lucid's own documented exchange format.
* ``community-documented`` – field names come from public community write-ups of real
  exports, not from vendor documentation; the parser is tested only against synthetic
  fixtures shaped after those write-ups. Verify with a real export before relying on it.
* ``unverified`` – plausible structure, weak evidence; treated as experimental.
"""
from __future__ import annotations

from .chatgpt import MANIFEST as CHATGPT, parse as parse_chatgpt
from .claude_ai import MANIFEST as CLAUDE, parse as parse_claude
from .gemini import MANIFEST as GEMINI, parse as parse_gemini
from .generic import MANIFEST as GENERIC, parse as parse_generic
from .markdown import MANIFEST as MARKDOWN, parse as parse_markdown

ADAPTERS = {
    "generic": (GENERIC, parse_generic),
    "markdown": (MARKDOWN, parse_markdown),
    "chatgpt": (CHATGPT, parse_chatgpt),
    "claude": (CLAUDE, parse_claude),
    "gemini": (GEMINI, parse_gemini),
}


def manifests() -> list[dict]:
    return [m for m, _ in ADAPTERS.values()]
