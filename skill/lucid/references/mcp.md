# MCP adapter

`lucid mcp` (or `lucid-mcp`) runs a stdio server exposing: memory_search, memory_get, memory_save,
memory_correct, memory_delete, context_prepare, conversation_import, sync_status, task_status,
decision_answer, verification_record, evaluation_results. No shell tool.

Claude Code: `claude mcp add lucid -- python3 -m lucid.mcp_server --user "$USER"`
(or point at `scripts/lucid.py mcp`). The user scope is fixed by the server's `--user` flag; tools
cannot choose a user. Restrict projects with `--projects a,b`. Imports read only from `--import-dir`.

Protocol: supports initialize-style 2025-11-25/2025-06-18/2025-03-26 and the per-request `_meta` style
of 2026-07-28 (`server/discover`). Verified 2026-10-27 with Claude Code 2.1.311: `claude mcp list`
negotiated 2026-07-28 via `server/discover` and listed tools ("Connected"). Tool calls from a live host
were not exercised.
