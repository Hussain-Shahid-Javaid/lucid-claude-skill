# Implementation status (2026-10-28, v0.3.0)

Legend:
- **Implemented:** the code exists.
- **Tested offline:** automated tests pass without live services.
- **Live-verified:** exercised against the real provider or host.
- **Unconfigured:** needs credentials or setup you provide.
- **Unsupported:** no supported interface exists.
- **Incomplete:** known gaps remain.

| Component | Status | Evidence / gaps |
|---|---|---|
| Core memory: provenance, scopes, conflicts, expiry, supersede/correct, tombstones, export, backup/restore, reindex | implemented, tested offline | `tests/test_memory.py` |
| Retrieval | implemented, tested offline; **incomplete** | Lexical FTS5 plus recency and verification weighting. Fails on paraphrased queries (eval report). Embedding retrieval is **not implemented**. |
| Conversation archive and importers (generic, markdown) | implemented, tested offline | branches, revisions, out-of-order and duplicate events, resume after crash, tombstones, echo flags, unsafe ZIPs |
| ChatGPT / Claude.ai importers | implemented, tested offline on **synthetic** fixtures | schemas are community-documented. Not validated on real exports. |
| Gemini importer | implemented, **experimental** | Takeout schema unverified; per-prompt only |
| Native-history sync (ChatGPT, Claude.ai, Gemini app) | **unsupported** | no documented write interface found |
| Context handoff and sharing rules | implemented, tested offline | retrieved by a second client via MCP in tests |
| Outbox: idempotency, backoff, outcome-unknown reconcile, conflicts, restart recovery | implemented, tested offline | `tests/test_handoff_sync.py`, `tests/test_storage.py` |
| S3 mirror | implemented, tested offline (moto); **unconfigured / not live-verified** | `pytest -m live` with `LUCID_LIVE_S3_BUCKET` |
| Google Docs mirror | implemented, tested offline (fake client); **unconfigured / not live-verified** | OAuth flow helper not bundled; bring credentials via google-auth |
| Budget ledger | implemented, tested offline | atomic reservations under 8-thread contention, unknown/late usage, reserve hold-back, time limit |
| Tasks and completion-first reporting | implemented, tested offline | |
| Timed decisions, authorizations, scheduler | implemented, tested offline | deadline races, restart without duplicate actions, real-time scheduler test |
| Orchestrator | implemented, tested offline | DAG, concurrency 3, ownership, budget exhaustion, cancel propagation, per-task timeouts that keep file ownership until the thread exits, at-most-once external actions (crash mid-action → surfaced as outcome-unknown, never re-run blindly) |
| Orchestrator: host-native subagents | **partial** | emits delegation plans; does not drive Claude Code subagents |
| Orchestrator: `AnthropicRunner` paid workers | implemented, **not run** | disabled unless `LUCID_ENABLE_PAID_CALLS=1` |
| Correction cycle and regression guard | implemented, tested offline | seeded bug fixed; correct answer preserved; bounded loop |
| Self-monitoring and calibration | implemented, tested offline | explicit records, not hidden internals |
| MCP server | implemented, tested offline; **live-verified (handshake)** | Claude Code 2.1.311: `server/discover` (2026-07-28) and `tools/list` → Connected. Live `tools/call` not exercised. |
| Remote authenticated MCP deployment | **not implemented** | would require OAuth 2.1 per spec |
| Service API and security | implemented, tested offline | token, Host and Origin checks, CSP, user isolation, redaction |
| Dashboard | implemented, tested (Playwright e2e) | loading, empty, error, unauthorised and demo states |
| Browser extension | implemented; extraction tested on fixtures | MV3 packaging not loaded into a browser in CI. ChatGPT and Claude site adapters are **unverified**. |
| Connector SDK and folder connector | implemented, tested offline | entry-point plugins only, no downloaded code |
| Claude Code skill package | implemented, tested offline | install, isolated run and uninstall in a temp HOME. Skill invocation in a live Claude Code model session **not exercised**. |
| claude.ai skill upload | packaged (`dist/lucid-skill.zip`); **not live-verified** | |
| Evaluations | offline suites run (held-out); **live-model suite not run** | `reports/evaluation-heldout.md` |
| Discovery experiments | run | rediscovery (positive control) and falsification control |
| Originality checking and prior-art workflow | implemented, tested offline | `tests/test_originality.py` covers every case in the spec. Lucid **cannot browse**: web searches are the host's own, logged through the CLI. The bundled reference collection has 14 entries; the concept thesaurus has 44 concepts. Held-out probes: 0/13 false originality claims, but 5/8 known equivalents detected (keyword baseline: 7/8). |
| Originality: parallel ideation / prior-art / skeptic workers | implemented, tested offline (deterministic workers) | shared budget ledger; independent assessments stored. Host-native subagents get a delegation plan (`invent workers`) and are not driven by Lucid. |
| Plain-language replies | rules in SKILL.md; checker implemented, tested offline | The checker is heuristic. Whether a live model follows the rules in a Claude Code session has **not been measured**. |
| Quality plans and release gates | implemented, tested offline | Evidence is bound to file hashes, weakened tests are detected, reviews must be concrete, and defects are tracked by severity (`tests/test_quality.py`). Lucid's own release plan is `quality.json`; from v0.3.0 on, each release's gate report is attached to its GitHub release. |
| Email / Slack / Teams messaging | implemented, tested against fakes; **not live-verified** | Request shapes follow the official docs (`docs/team-integrations.md`). OAuth sign-in is not bundled. Slack drafts, Teams bot identity and Teams edit/delete are **unsupported**. |
| Avatar representative | implemented; **simulated** sessions and a **local WebRTC demo** tested | No avatar vendor or meeting platform is integrated. Slack huddles have no API for apps. Teams meeting bots need a separate .NET/Windows media bot. |
| MCP tools for originality and plain-language checks | **not implemented** | CLI only in v0.2.0 |
| Consciousness-related experiments | run (functional, simulated) | GW-1, HO-1, with bounded claims; `ActivationProbe` needs real open-weight access |

## Independent review (2026-10-28)

A separate reviewer, briefed only with the source, found 15 defects. Most were confirmed with reproducing scripts. They include:
- cross-user access paths in the service
- capture overwriting another user's conversation
- per-user op-id leaks
- deleted text surviving in correction records and S3 events
- superseded records resurrected by an S3 rebuild
- Google Docs UTF-16 index errors
- `NoSuchBucket` read as "record missing"
- timed-out workers releasing file ownership
- prefix-matching authorization scopes
- failed default actions never retried
- cost limits bypassable without estimates
- world-readable backups
- tombstones bypassable by changing `kind`

All 15 are fixed, and each has a regression test (`test_review_regressions*`, `test_cross_user_isolation_on_every_mutating_path`, `test_capture_of_same_conversation_by_two_users_stays_separate`, `test_timed_out_worker_keeps_paths_reserved`). Migration `0002` makes conversations unique per user and was checked on an existing v1 database.

## Known limitations

- **Retrieval.** Lexical only. Paraphrased recall is poor; this is measured and reported.
- **Skill-only use.** Without `lucid serve`, timers do not run in the background, and budgets that cover the host chat's own usage are estimates.
- **Secret redaction** is pattern-based and can miss formats it does not know.
- **Vendor export formats** change without notice. Adapters report unrecognised shapes instead of guessing.
