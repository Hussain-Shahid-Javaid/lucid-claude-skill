# Installation, upgrade, backup, recovery, deletion

## Requirements

- Python 3.10 or newer. The core, CLI and MCP server use only the standard library.
- Optional extras:
  - `[service]`: FastAPI and uvicorn, for the dashboard and timers
  - `[s3]`: boto3
  - `[gdocs]`: Google API client
  - `[dev]`: everything needed to run the tests
- Node 18 or newer, only for rebuilding the dashboard. A built copy ships in `src/lucid/web`.

## Claude Code (primary)

```bash
tools/install_claude_code.sh            # → ~/.claude/skills/lucid  (personal)
tools/install_claude_code.sh --project  # → ./.claude/skills/lucid (this repository only)
```

What the script does:
- Builds the skill and copies the Lucid library into `scripts/lib`, so no `pip install` is needed.
- Validates `SKILL.md`.
- Swaps the folder in atomically.
- Runs `lucid init`.

To use it, type `/lucid …` or simply ask Claude to remember or recall something. Claude loads the skill when the description matches.

- **Optional MCP server:** `claude mcp add lucid -- python3 -m lucid.mcp_server --user "$USER"`. This needs `pip install -e .`, or point it at `~/.claude/skills/lucid/scripts/lucid.py mcp`.
- **Upgrade:** pull and re-run the installer. Your data in `~/.lucid` is untouched, and schema migrations run on the next start.
- **Remove:** `tools/install_claude_code.sh --uninstall [--project]`. This keeps `~/.lucid`. Delete that folder yourself to erase all memory.

## claude.ai

1. Run `python3 tools/build_skill.py`.
2. Upload `dist/lucid-skill.zip` through claude.ai's Skills settings.
3. Enable code execution.

Storage in that sandbox does not persist across chats.

## Dashboard / service

```bash
pip install -e ".[service]"
lucid serve                      # 127.0.0.1:8765; prints http://127.0.0.1:8765/#token=… once
lucid serve --rotate-token       # new token (old one revoked)
lucid serve --demo --db /tmp/demo.db   # clearly labelled demo data in a separate DB
```

**Background timers.** Timed-question defaults fire only while the service runs. Without it, `lucid decide tick` reconciles any deadlines that passed.

**Remote access.** Off by default. It needs `--host <addr> --allow-remote --tls-cert … --tls-key …` and per-user tokens. See [security.md](security.md).

## Backup, restore and migration

```bash
lucid backup ~/lucid-backup-$(date +%F).db   # online, integrity-checked copy
lucid restore ~/lucid-backup-2026-10-27.db   # validates, keeps lucid.db.pre-restore, reindexes
lucid reindex                                # rebuild full-text index from records
lucid memory export --out mem.json           # portable JSON with provenance
```

**Moving to a new machine.** Copy the backup file, then run `lucid restore`.

**S3 disaster recovery.** Use `rebuild_from_s3`, described in [storage.md](storage.md).

## Deletion semantics

| action | what happens |
|---|---|
| `memory delete <id>` | Content cleared and a tombstone kept, so reimports and stale clients cannot resurrect it. Derived summaries and corrections are invalidated and the index entries are removed. |
| `conv delete <id>` | Messages removed and the conversation tombstoned. Memories whose only evidence was that conversation are invalidated. Deletes are queued for every destination that received the conversation. The result reports, per destination, what was deleted and what Lucid cannot delete. |
| capture delete (dashboard) | Source content removed. Imported memories that relied only on it are invalidated. Verified memories that lose their only evidence fall back to `unverified`. |
| S3 | A tombstone and a delete marker are written. `purge_versions` permanently removes prior versions. |
| Google Docs | The line is removed. Google's revision history and retention may keep earlier copies. |
| Provider-native copies (ChatGPT, Claude.ai, Gemini) | **Not deleted by Lucid.** Delete them in each product; their retention policies apply. |

## Troubleshooting

- **`Lucid library not found`.** Rebuild the skill (`python3 tools/build_skill.py`) or `pip install -e .`.
- **`database is locked`.** Another process holds a long write. Lucid waits up to 30 s. Check for stuck processes.
- **Dashboard shows "Not authorised".** Use the link printed by `lucid serve`, or rotate the token.
- **Dashboard says "Dashboard not built".** Run `cd dashboard && npm ci && npm run build`.
- **An import reports `format not recognised`.** The vendor export changed shape. Use the generic format ([exchange-format.md](exchange-format.md)) or open an issue with a redacted sample.
