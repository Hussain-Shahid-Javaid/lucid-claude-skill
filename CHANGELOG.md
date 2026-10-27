# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-28

First public release. See [docs/releases/v0.1.0.md](docs/releases/v0.1.0.md) for full release notes.

### Added
- Agent Skill package (`skill/lucid`), Claude Code installer, and claude.ai upload zip.
- Core library: memory that records sources and revisions, a conversation archive that keeps branches, importers, handoffs, a transactional sync queue, a budget ledger, tasks, timed decisions, an orchestrator, a correction cycle and self-monitoring.
- S3 and Google Docs storage mirrors.
- MCP server, FastAPI service, React dashboard, MV3 browser extension, connector SDK.
- Evaluation runner, discovery experiments, and an optional consciousness-research module.
- 92 tests, CI workflow, docs.

### Fixed (before release, from independent review)
- Cross-user access in service endpoints; per-user op ids; per-user conversation namespacing (migration 0002).
- Deleted content lingering in derived records and S3 events; superseded state mirrored to S3.
- Google Docs UTF-16 index handling; S3 `NoSuchBucket` mapping; per-task worker timeouts; exact authorization scopes; retry of failed default actions; cost-limit enforcement; owner-only backups.

[0.1.0]: https://github.com/Hussain-Shahid-Javaid/lucid-claude-skill/releases/tag/v0.1.0
