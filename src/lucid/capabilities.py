"""Host capability matrix (data in data/capabilities.json; sources in docs/compatibility.md).

Statuses: supported | unsupported | experimental | unconfigured | unverified.
Capabilities are recorded per *application surface*, never inferred from the model name.
"""
from __future__ import annotations

import json
from importlib import resources

STATUSES = {"supported", "unsupported", "experimental", "unconfigured", "unverified", "n/a"}
CAPABILITIES = ["skill_discovery", "script_execution", "persistent_storage", "tool_calling", "subagents",
                "background_scheduling", "mcp", "activity_telemetry", "transcript_import_export",
                "native_conversation_updates"]


def load_matrix() -> dict:
    data = json.loads((resources.files("lucid") / "data" / "capabilities.json").read_text(encoding="utf-8"))
    for h in data["hosts"]:
        for cap in CAPABILITIES:
            v = h["capabilities"].get(cap)
            if v is None or v["status"] not in STATUSES:
                raise ValueError(f"{h['id']}.{cap}: missing or invalid status")
    return data
