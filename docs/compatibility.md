# Host capability matrix

Verified **2026-10-27** by reviewing official documentation, unless a row says "live". Capabilities belong to each *application surface*; they are never inferred from the name of the underlying model. Machine-readable copy: `src/lucid/data/capabilities.json` (`lucid capabilities`).

Status values: supported · unsupported · experimental · unconfigured · unverified · n/a.

| Capability | Claude Code | Claude.ai web/desktop | ChatGPT | Gemini app | Gemini CLI / API | Generic skills hosts (e.g. Codex) | Generic MCP clients |
|---|---|---|---|---|---|---|---|
| Skill discovery | supported [2] | supported, ZIP upload, standard frontmatter only [13][2] | supported: desktop; web/mobile only inside plugins [18] | supported [24] | supported (CLI) [29]; consumer accounts moved to Antigravity CLI [33] | supported (`.agents/skills`) [18][22] | unsupported [8] |
| Script execution | supported [2] | supported if code execution is on [13] | unverified | experimental [24] | unverified [29] | supported [18] | n/a |
| Persistent storage | supported [7] | unverified [17] | unverified | supported (Keep Activity) [27] | supported (sessions) [32] | unverified | n/a |
| Tool calling | supported | supported (connectors) [14] | supported [19] | supported [25] | supported [34] | supported [23] | supported [11] |
| Subagents | supported [4] | unverified | unverified | unverified | experimental [31][32] | unverified | unsupported |
| Background / scheduling | supported [5] | supported (Cowork, paid) [15] | supported [20] | supported, gradual rollout [26] | unverified | unverified | experimental (tasks ext.) [8] |
| MCP | supported; **live: 2026-07-28 handshake + tools/list with v2.1.311** | supported (remote connectors; local in Desktop) [14] | supported (Streamable HTTP) [19] | supported, restricted [25] | supported [30] | supported [23] | supported [8] |
| Activity telemetry | supported (OTel) [6] | unverified | unverified | unsupported (My Activity only) [27] | supported [32] | unverified | experimental [8] |
| Transcript import/export | supported (`/export`, JSONL) [7] | export supported [16]; memory import experimental [17] | export supported [21] | export (Takeout) [27]; imports ChatGPT/Claude ZIPs outside EEA/UK/CH [28] | unverified | unverified | n/a |
| Native conversation updates | supported for local sessions only [7] | unverified (no API found) | unverified (API Conversations ≠ ChatGPT history) [43] | unsupported [28] | unverified | unverified | n/a |

## How Lucid uses each host

- **Claude Code.** This is the primary target. The skill folder (`tools/install_claude_code.sh`) provides `/lucid`, the bundled CLI runs through the Bash tool, data lives in `~/.lucid`, subagents are planned with `HostNativeRunner`, and the optional MCP server is available. *Live check:* `claude mcp list` with Claude Code 2.1.311 reported "Connected"; the client negotiated protocol 2026-07-28 via `server/discover`. Discovering the skill inside a live model session was **not** exercised, because that needs a model turn and paid usage.
- **Claude.ai.** Upload `dist/lucid-skill.zip`. The frontmatter uses only the six standard fields because extra fields are rejected [2]. The code-execution sandbox does not keep `~/.lucid` between chats, so memory there lasts only within a session unless you connect a remote MCP deployment (authentication required; not shipped as a hosted service).
- **ChatGPT and Gemini.** Lucid imports their exports and builds handoffs you paste or load. Native history sync is not possible through documented interfaces.
- **Generic MCP clients.** Run `lucid mcp`. Both initialize-style and 2026-07-28 per-request styles are supported.

## Agent Skills spec facts used [1]

- `name` is 1–64 characters of `[a-z0-9-]`, has no leading, trailing or double hyphens, and matches the folder name.
- `description` is 1–1024 characters. Optional fields: `license`, `compatibility` (≤500), `metadata` (string map), and `allowed-tools` (experimental).
- Keep `SKILL.md` under 500 lines and about 5k tokens, with references one level deep.
- `tools/validate_skill.py` checks these rules.

## MCP facts used [8]–[12]

- **Latest protocol: 2026-07-28.** There is no initialize handshake. The version and client capabilities are sent in each request's `_meta`, servers implement `server/discover`, and an unsupported version returns error `-32022` with `data.supported`.
- **`tools/list`** results include `resultType`.
- **Remote authorization** uses OAuth 2.1 with Protected Resource Metadata, PKCE, the `resource` parameter and audience-bound tokens. Lucid does not ship a remote MCP deployment. Implementing that authorization is required before exposing one.

## Export format evidence

- **ChatGPT.** Officially, a ZIP of chat history with no published schema [21]. The `mapping` node tree (`id`, `message`, `parent`, `children`, `current_node`) is **community-documented** [35][36][37].
- **Claude.ai.** No published schema [16]. `conversations.json` → `chat_messages[].sender` is community-documented [38][39]. Other field names are unverified.
- **Gemini.** Takeout My Activity, with no published schema [27]. Community reports are inconsistent [40][41], so treat every field as unverified.

All vendor fixtures in `tests/fixtures/` are **synthetic** and labelled as such.

## Sources

| # | URL |
|---|---|
| 1 | https://agentskills.io/specification |
| 2 | https://code.claude.com/docs/en/skills |
| 3 | https://code.claude.com/docs/en/hooks |
| 4 | https://code.claude.com/docs/en/sub-agents |
| 5 | https://code.claude.com/docs/en/scheduled-tasks |
| 6 | https://code.claude.com/docs/en/monitoring-usage |
| 7 | https://code.claude.com/docs/en/sessions |
| 8 | https://modelcontextprotocol.io/specification/2026-07-28/changelog |
| 9 | https://modelcontextprotocol.io/specification/2026-07-28/basic.md |
| 10 | https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning.md |
| 11 | https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md |
| 12 | https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization.md |
| 13 | https://support.claude.com/en/articles/12512180-use-skills-in-claude |
| 14 | https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp |
| 15 | https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork |
| 16 | https://support.claude.com/en/articles/9450526-export-your-claude-data |
| 17 | https://support.claude.com/en/articles/12123587-import-and-export-your-memory-from-claude |
| 18 | https://learn.chatgpt.com/docs/build-skills |
| 19 | https://developers.openai.com/plugins/deploy/connect-chatgpt |
| 20 | https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt |
| 21 | https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data |
| 22 | https://developers.openai.com/api/docs/guides/tools-skills |
| 23 | https://learn.chatgpt.com/docs/extend/mcp?surface=cli |
| 24 | https://support.google.com/gemini/answer/17094296 |
| 25 | https://support.google.com/gemini/answer/17209137 |
| 26 | https://support.google.com/gemini/answer/16316416 |
| 27 | https://support.google.com/gemini/answer/16920332 |
| 28 | https://support.google.com/gemini/answer/16868299 |
| 29 | https://geminicli.com/docs/cli/skills/ |
| 30 | https://geminicli.com/docs/tools/mcp-server |
| 31 | https://geminicli.com/docs/extensions/reference/ |
| 32 | https://geminicli.com/docs/ |
| 33 | https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/ |
| 34 | https://ai.google.dev/gemini-api/docs/function-calling |
| 35 | https://community.openai.com/t/decoding-exported-data-by-parsing-conversations-json-and-or-chat-html/403144 |
| 36 | https://levelup.gitconnected.com/your-chatgpt-history-is-a-tree-not-a-transcript-2026-5660ceb301d4 |
| 37 | https://docs.openwebui.com/features/chat-conversations/data-controls/import-export |
| 38 | https://ai-chat-importer.com/blog/how-to-export-claude-conversations |
| 39 | https://claudeissues.com/issue/40411-claude-ai-data-export-missing-conversation-to-project-associations |
| 40 | https://nexasphere.io/blog/export-gemini-conversations-to-markdown-before-google-deletes-them-a |
| 41 | https://pypi.org/project/google-takeout-parser/ |
| 43 | https://developers.openai.com/api/docs/guides/conversation-state |
