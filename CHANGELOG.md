# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-10-28

See [docs/releases/v0.3.0.md](docs/releases/v0.3.0.md).

### Added
- Quality plans and release gates (`lucid quality`):
  - acceptance criteria, sized to the risk of the change
  - check results tied to the exact files they ran on
  - detection of tests weakened after a failure
  - concrete reviews and defect tracking by severity
  - comparison of alternative approaches
  - metrics that report uncertainty
- Lucid's own release gate in `quality.json`, plus:
  - lint and security rules
  - mypy and TypeScript type checks
  - a secret scan and a dependency audit
  - a docs consistency check
  - a benchmark
- Email, Slack and Teams messaging (`lucid comms`):
  - three modes: draft only, send after review, automatic within a policy
  - checks on identity, recipients, commitments, personal claims and secrets
  - an idempotent send queue that looks for an unconfirmed send before retrying
  - Retry-After handling, working hours and rate limits
  - credential revocation and an audit trail
  - incoming messages treated as untrusted
  - personal and team memory kept apart
  - all tested against fakes of the documented APIs
- Optional avatar representative (`lucid avatar`):
  - your own likeness only, with consent, a preview step, controls, revocation and a deletion report
  - meetings only when you approved them
  - disclosed participation
  - answers limited to knowledge approved for the people present
  - commitments and opinions passed to you; allowed commitments are accepted only provisionally, for you to confirm
  - interruption and takeover, and graceful recovery after network loss
  - separate cost meters with limits
  - an evidence-based meeting summary
  - a controlled WebRTC demo
- Dashboard tab "Messaging & avatar".
- Migrations `0004` to `0006`.

### Fixed
- Decision timer errors were silently swallowed; they are now logged.
- Activating an avatar could leave it half-active when the controls were invalid.
- S3 could return no manifest after a concurrent create.
- Mirror reconcile caught every exception instead of only "not found".
- The arithmetic checker caught every exception.
- The skill zip was not reproducible.
- Lint and type checking found these issues, plus about 50 type errors. All are now clean under the new configuration.
- An independent review of the new modules found 22 defects (1 blocker, 14 major, 7 minor). Ten confirmation reviews of the fixes found 30 more (10 major). All are fixed, and each has a regression test in `tests/test_review_regressions_v3.py`. They included:
  - a typed result that could override a failed automated check
  - private memory reachable through a caller-supplied audience
  - Lucid's own edits skipping the policy
  - unknown-outcome handling for bodiless Outlook sends
  - knowledge leaking to a speaker who was not in the participant list
  - queued audio still playing after voice consent was revoked

## [0.2.0] - 2026-10-28

See [docs/releases/v0.2.0.md](docs/releases/v0.2.0.md).

### Added
- Originality checking (`lucid invent ...`):
  - target definition and candidates that must state a mechanism and a difference
  - cosmetic-novelty flags
  - checks against Lucid's own earlier proposals and memories
  - a logged prior-art search, including failed searches, with confidential terms kept out of external queries
  - scoped statuses and an "original only" gate
  - value tests kept separate from novelty
  - parallel ideation, prior-art and skeptic workers on a shared budget
  - invention records stored with provenance
- Memory kind and handoff category `invention`.
- Migration `0003_originality.sql`.
- Originality evaluation suite with dev and held-out probe sets and baselines.
- Plain-language rules in the skill, and a local checker: `lucid plain check`, with `--fix` and `--strict`.
- 32 new tests (124 total).

### Fixed
- An Anthropic API key was also reported as an OpenAI key by the secret redactor.

### Changed
- User-facing error messages and dashboard text were rewritten in plain language. A test now audits them.

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

[0.3.0]: https://github.com/Hussain-Shahid-Javaid/lucid-claude-skill/releases/tag/v0.3.0
[0.2.0]: https://github.com/Hussain-Shahid-Javaid/lucid-claude-skill/releases/tag/v0.2.0
[0.1.0]: https://github.com/Hussain-Shahid-Javaid/lucid-claude-skill/releases/tag/v0.1.0
