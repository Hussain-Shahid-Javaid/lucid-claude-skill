# Lucid

<p align="center">
  <a href="docs/assets/lucid-launch.mp4"><img src="docs/assets/lucid-demo.gif" alt="Lucid in 25 seconds: a forgetful assistant line is struck through, then real Lucid CLI output flags a conflict, the dashboard shows a corrected memory with its revision history, and an eval shows correct answers broken by a noisy reviewer drop from 64% to 0%." width="800"></a>
  <br><sub>Lucid in 25 seconds. Click for the full-quality MP4. Every screen is real Lucid output.</sub>
</p>

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
- **Originality checks.** Lucid checks a new idea against its own records and against existing work before anyone calls it new. Each check logs every search and gives the idea a status limited to what was searched: known, adaptation, potentially original within that scope, unresolved, or not assessed. It never says "nobody has thought of this". See [docs/originality.md](docs/originality.md).
- **Quality gates.** Lucid checks work against acceptance criteria before calling it done. Each check result is tied to the exact files it ran on, so an edit makes it stale. Weakened tests are caught. The gate reports what was verified, what was not run, and the known defects. Lucid's own releases go through this gate. See [docs/quality-gates.md](docs/quality-gates.md).
- **Email, Slack and Teams (optional).** Lucid can write drafts, send after your review, or send automatically, but only within limits you set. Messages are disclosed as written by Lucid. Sends are idempotent and checked again before any retry. This is tested against fakes, not live accounts. See [docs/team-integrations.md](docs/team-integrations.md).
- **Avatar representative (optional).** Lucid can build an avatar from your own likeness, with consent, a preview step and deletion. In meetings it is always shown as "Lucid — AI representative for you", and only joins meetings you approved. So far this is tested in simulation and in a local WebRTC demo; no meeting platform is integrated. See [docs/avatar.md](docs/avatar.md).
- **Plain-language replies.** The skill tells the assistant to answer first, in plain words, and to keep every fact and caveat. `lucid plain check` flags stiff or wordy text. See [docs/plain-language.md](docs/plain-language.md).
- **Measurement.** It ships an evaluation runner, a discovery-experiment ledger, and an optional, clearly bounded consciousness-research module.

Lucid works fully offline with **no API keys**. Normal reasoning stays in your host assistant's model. Saving or recalling memory never calls a paid API.

> **Honest scope.** Lucid does not guarantee accuracy, remove bias, sync native chat history of ChatGPT/Claude.ai/Gemini, bypass provider limits, or create or prove subjective experience. See [docs/status.md](docs/status.md) for exactly what is implemented, tested offline, live-verified, unconfigured, or unsupported.

## How it works

<p align="center">
  <img src="docs/assets/how-it-works.svg" alt="How Lucid works. Your AI tools (Claude Code via the /lucid skill and CLI, any MCP client, and exports from ChatGPT, Claude.ai or Gemini) talk to the Lucid core, a local SQLite store. The core holds memory with provenance; conflicts, history and tombstones; tasks, budgets and timed decisions; and a conversation archive. Context flows out as handoffs limited to the categories you allow, to a local dashboard, and to optional S3 or Google Docs mirrors. The animation cycles through four steps: save, recall, correct, hand off." width="100%">
</p>

| Step | What happens | Try it |
|---|---|---|
| **1. Save** | The memory is stored with its origin (you, the assistant, a tool, an import) and any evidence. An assistant's guess stays `unverified` until a real source backs it. | `lucid memory add "Prefers pytest" --kind preference --key pref:test-runner` |
| **2. Recall** | Relevant memories are ranked and packed into a token budget. They are framed as data, not instructions, and each comes with a note on why it matched. | `lucid memory search "which test runner"` |
| **3. Correct** | A correction supersedes the old value but keeps it in the history. Two statements that disagree are flagged as a conflict instead of the newest one silently winning. Deleted memories stay deleted. | `lucid memory correct <id> "Prefers unittest" --reason "switched"` |
| **4. Hand off** | Another tool gets a context pack with only the categories you've allowed for it. If that text comes back in a later import, Lucid recognises it as its own and doesn't count it twice. | `lucid handoff prepare --to chatgpt --goal "continue the refactor"` |

Everything above runs locally from the skill's bundled CLI. The dashboard (`lucid serve`) shows the same records with their provenance, revision history, sync queues, budgets and pending questions.

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
lucid invent target "Clients overload a service when they all retry after an outage"
lucid invent candidate <study> --title "..." --mechanism "how it works" --difference "..." --method change_constraint
lucid invent check <candidate>            # Lucid's records + the bundled reference collection
echo "In order to utilize it..." | lucid plain check

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
| `src/lucid/` | Core library: `memory`, `conversations`, `importers/`, `handoff`, `sync`, `budget`, `tasks`, `decisions`, `orchestrator`, `verification`, `monitor`, `originality`, `plain`, `quality`, `comms`, `avatar/`, `storage/` (S3, Google Docs), `connectors/`, `capture`, `mcp_server`, `server/` (FastAPI), `evals/`, `research_modules/` |
| `dashboard/` | React + TypeScript dashboard. `npm run build` writes to `src/lucid/web/` |
| `extension/` | Manifest V3 browser capture extension, site adapters and versioned fixtures |
| `tests/` | 236 tests: unit, contract (moto S3, fake Docs, fake Slack/Gmail/Graph), MCP, CLI, service security, Playwright dashboard, extension and WebRTC demo |
| `evals/`, `reports/` | Pre-registration and generated evaluation/research reports |
| `docs/` | Architecture, storage, compatibility matrix, security, budgets, decisions, research protocols, status |
| `tools/` | Skill build/validate, Claude Code installer |

## Test and reproduce

```bash
pip install -e ".[dev]" && python -m playwright install chromium   # browser only if not already present
pytest                                   # 236 tests; live-provider tests are marked `live` and skipped
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
- [Originality checks](docs/originality.md) · [Plain-language replies](docs/plain-language.md)
- [Quality gates](docs/quality-gates.md) · [Email, Slack and Teams](docs/team-integrations.md) · [Avatar](docs/avatar.md)
- [Browser extension](docs/extension.md) · [Evaluation methodology](docs/evaluation.md) · [Research protocols](docs/research-protocols.md)
- [Implementation status & limitations](docs/status.md) · [Changelog](CHANGELOG.md) · [v0.3.0 release notes](docs/releases/v0.3.0.md)

License: MIT.
