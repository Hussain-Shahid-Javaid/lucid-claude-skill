"""Gemini Apps activity adapter (Google Takeout → My Activity → Gemini Apps, JSON).

Evidence: Google documents that Gemini Apps Activity can be exported via Takeout
(support.google.com/gemini/answer/16920332) but publishes no schema. Community reports
are inconsistent; one describes `MyActivity.json` entries with `title`, `time`,
`subtitles` and `safeHtmlItem`/`details`, where replies may be HTML fragments.

Takeout activity is per-prompt, not per-conversation, so each entry becomes a
single-turn record; conversation grouping and branches are NOT recoverable.

Status: unverified / experimental. Select the JSON (not HTML) format in Takeout.
"""
from __future__ import annotations

import html
import json
import re
from typing import Iterator

from ..conversations import ImportErrorBadInput, NormalizedConversation, NormalizedMessage
from ..util import sha256

MANIFEST = {
    "id": "gemini", "name": "Gemini Apps activity (Google Takeout JSON)", "schema_status": "unverified",
    "formats": ["zip", "MyActivity.json"], "branches": "not available (activity log is per prompt)",
    "attachments": "not imported", "incremental": True, "coverage": "partial",
    "native_history_sync": "unsupported",
    "sources": ["https://support.google.com/gemini/answer/16920332"],
    "verified": "2026-10-27 (documentation review; no live export validated)",
}

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    # Imported HTML is never rendered or executed; we reduce it to text.
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", s)
    return html.unescape(_TAG.sub("", s)).strip()


def parse(data: bytes, account: str = "default") -> Iterator[NormalizedConversation]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ImportErrorBadInput(f"activity file is not valid JSON (choose JSON format in Takeout): {e}") from None
    if not isinstance(doc, list):
        raise ImportErrorBadInput("expected a JSON array of activity entries")
    for i, entry in enumerate(doc):
        if not isinstance(entry, dict) or "title" not in entry:
            raise ImportErrorBadInput(f"activity entry #{i} has no 'title' — format not recognised")
        title = str(entry.get("title", ""))
        prompt = re.sub(r"^(Prompted|Asked|Searched for)\s+", "", title)
        reply_html = ""
        for item in entry.get("safeHtmlItem") or []:
            if isinstance(item, dict) and item.get("html"):
                reply_html += item["html"]
        eid = "gem-" + sha256(f"{entry.get('time')}|{title}")[:20]
        msgs = [NormalizedMessage(external_id=f"{eid}:p", role="user", content=prompt, created_at=entry.get("time"))]
        if reply_html:
            msgs.append(NormalizedMessage(external_id=f"{eid}:r", role="assistant", content=_strip_html(reply_html),
                                          parent=f"{eid}:p", created_at=entry.get("time")))
        yield NormalizedConversation(provider="gemini", account=account, external_id=eid, title=prompt[:80],
                                     created_at=entry.get("time"), updated_at=entry.get("time"), messages=msgs,
                                     coverage="partial",
                                     warnings=["Takeout activity entry: single turn; grouping/branches unavailable"])
