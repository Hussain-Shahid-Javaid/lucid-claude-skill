# Lucid

Lucid is a portable AI **skill** plus a small local toolkit that gives assistants such as Claude Code:

- **Persistent memory with provenance.** Every record keeps its origin, evidence, revisions, conflicts and expiry. Records are scoped per user and per project.
- **Cross-tool context.** Lucid imports conversation exports and builds scoped **context handoffs** for other tools.
- **Working discipline enforced in code.**
  - Acceptance-checked task completion
  - Atomic token/cost/time budgets
  - Parallel workers with file ownership
  - Timed default decisions limited by explicit authorizations
  - A bounded verify-and-correct cycle that keeps correct answers from being broken
- **Observation, not replacement.** A local web dashboard, an optional MCP server, a browser capture extension and storage mirrors to S3 or Google Docs. You keep chatting in your existing AI tools.
- **Measurement.** It ships an evaluation runner, a discovery-experiment ledger, and an optional, clearly bounded consciousness-research module.

Lucid works fully offline with **no API keys**. Normal reasoning stays in your host assistant's model. Saving or recalling memory never calls a paid API.

> **Honest scope.** Lucid does not guarantee accuracy, remove bias, sync native chat history of ChatGPT/Claude.ai/Gemini, bypass provider limits, or create or prove subjective experience. See [docs/status.md](docs/status.md) for exactly what is implemented, tested offline, live-verified, unconfigured, or unsupported.

## Quick start

```bash
git clone https://github.com/Hussain-Shahid-Javaid/lucid-claude-skill && cd lucid-claude-skill
pip install -e ".[service]"               # core needs only the standard library; [service] adds the dashboard

# Claude Code skill (personal install; use --project for .claude/skills in the current repo)
tools/install_claude_code.sh
# then in Claude Code:  /lucid remember that I prefer pnpm

# CLI
lucid memory add "Prefers pnpm over npm" --kind preference --key pref:package-manager
lucid memory search "which package manager"
lucid memory correct <id> "Prefers bun" --reason "switched in October"
lucid import chatgpt ~/Downloads/chatgpt-export.zip
lucid share add claude-desktop --categories decision,preference,project_state
lucid handoff prepare --to claude-desktop --goal "continue the storage refactor"

# Dashboard (localhost only; prints a one-time link containing the access token)
lucid serve
# MCP server for MCP-capable hosts
claude mcp add lucid -- python3 -m lucid.mcp_server --user "$USER"
```

To use it on claude.ai, upload `dist/lucid-skill.zip` through claude.ai's Skills settings (see support.claude.com article 12512180). Code execution must be enabled there, and that sandbox does not keep data between chats. See [docs/compatibility.md](docs/compatibility.md).

## Repository layout

| path | what |
|---|---|
| `skill/lucid/` | Agent Skill: `SKILL.md`, `references/`, `scripts/lucid.py` (the library is copied into `scripts/lib` at build time) |
| `src/lucid/` | Core library: `memory`, `conversations`, `importers/`, `handoff`, `sync`, `budget`, `tasks`, `decisions`, `orchestrator`, `verification`, `monitor`, `storage/` (S3, Google Docs), `connectors/`, `capture`, `mcp_server`, `server/` (FastAPI), `evals/`, `research_modules/` |
| `dashboard/` | React + TypeScript dashboard. `npm run build` writes to `src/lucid/web/` |
| `extension/` | Manifest V3 browser capture extension, site adapters and versioned fixtures |
| `tests/` | 92 tests: unit, contract (moto S3, fake Docs), MCP, CLI, service security, Playwright dashboard and extension |
| `evals/`, `reports/` | Pre-registration and generated evaluation/research reports |
| `docs/` | Architecture, storage, compatibility matrix, security, budgets, decisions, research protocols, status |
| `tools/` | Skill build/validate, Claude Code installer |

## Test and reproduce

```bash
pip install -e ".[dev]" && python -m playwright install chromium   # browser only if not already present
pytest                                   # 92 tests; live-provider tests are marked `live` and skipped
(cd dashboard && npm ci && npm run build)
python3 tools/build_skill.py             # validates SKILL.md and writes dist/lucid-skill.zip
lucid eval run --split heldout           # regenerates reports/evaluation-heldout.{md,json}; 0 model calls
```

## Documentation

- [Architecture & data flow](docs/architecture.md)
- [Installation, upgrade, backup, recovery, deletion](docs/installation.md)
- [Storage modes: local, Google Docs, S3](docs/storage.md)
- [Host capability matrix with sources](docs/compatibility.md)
- [Conversation exchange format](docs/exchange-format.md)
- [Budgets](docs/budgets.md) · [Decisions & autonomy](docs/decisions.md) · [Security & privacy](docs/security.md)
- [Browser extension](docs/extension.md) · [Evaluation methodology](docs/evaluation.md) · [Research protocols](docs/research-protocols.md)
- [Implementation status & limitations](docs/status.md)

License: MIT.
