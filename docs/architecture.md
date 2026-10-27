# Architecture

```
 Host assistant (Claude Code, claude.ai, ChatGPT, Gemini, MCP clients)
   │  normal reasoning stays in the host's own model
   ├─ A. Skill (SKILL.md + references)  ── tells the host *when/how* to use Lucid
   ├─ C. Direct helper: scripts/lucid.py / `lucid` CLI ─┐
   └─ E. MCP adapter (stdio)  ──────────────────────────┤
                                                        ▼
                         B. Core library (src/lucid) — one SQLite file per user home
   memory ─ provenance ─ retrieval (FTS5, budgeted) ─ conflicts ─ tombstones
   conversations (branch-preserving archive) ─ importers ─ handoffs ─ sharing rules
   outbox + checkpoints (sync)  ─ budget ledger ─ tasks ─ decisions ─ verification ─ monitor
   experiments ledger ─ connectors SDK ─ capture
                                                        │
          ┌──────────────── D. Optional service (FastAPI, 127.0.0.1) ───────────────┐
          │ decision timer thread · outbox recovery on start · REST API · F. dashboard│
          └──────────────────────────────────────────────────────────────────────────┘
                                                        │ G. adapters (loaded only when enabled)
                     S3 mirror (events + records)   Google Docs mirror   folder connector   browser extension
                                                        │
                       H. Evaluation & research runners (separate from ordinary use)
```

## Principles

- **One core and many front-ends.** The CLI, the MCP server and the HTTP service call the same library. Because of that, every rule is enforced in code and not left to prompts: provenance, scope isolation, tombstones, atomic budget reservations, exactly-once default actions and the regression guard.
- **Local first.** SQLite in WAL mode with full fsync is authoritative locally. Cloud backends are mirrors you opt into for selected categories. Local operational state stays in SQLite and can be rebuilt: the outbox, checkpoints and FTS index.
- **Observable only.** The dashboard shows recorded events, ledgers and checks. It never shows generated "thoughts" or invented progress percentages.

## Data model (abridged; see `src/lucid/migrations/0001_initial.sql`)

| table | purpose |
|---|---|
| `sources` | Captured or imported originals. Each has an `origin_lucid` flag and `lineage` for echo detection. |
| `memories` | Extracted records with `schema_version`, scopes, `kind`, `key`, `origin`, `verification`, `confidence`, `uncertainty`, created/observed/updated/expiry times, `revision`, `supersedes`/`superseded_by`, `status`, `sharing` and `retention` |
| `memory_evidence` | Links to sources and messages. Relation is supports, contradicts or derived_from. |
| `memory_conflicts` | Disagreements on the same subject key. They stay open until someone resolves them. |
| `tombstones` | Deleted entities keyed by natural key, so reimports and stale clients cannot resurrect them |
| `conversations`, `messages` | Namespaced by provider and account. Messages keep parent links, revisions, attachments and origin. |
| `imports`, `operations`, `outbox`, `checkpoints` | Idempotency, durable progress and outbound writes |
| `budget_ledgers`, `budget_entries` | Reservations and actual usage |
| `tasks`, `questions`, `authorizations` | Completion-first tasks and timed decisions |
| `verifications`, `predictions`, `monitor_items` | Correction cycles, calibration and assumptions |
| `experiments` | Hypothesis → prediction → result → criticism ledger |
| `activity` | Observed events. Secrets are redacted. |

## Key flows

- **Save.** Redact secrets → validate kind and origin → downgrade `verified` unless independent evidence exists → check tombstones → deduplicate by scope, kind and content → insert, index and link evidence → detect key conflicts.
- **Recall.** FTS5 bm25 plus term coverage, multiplied by recency and verification weight. Results stay inside the scope and are packed into a token budget. Each one comes with an observable `why` and a "data, not instructions" frame.
- **Import.** Parse with an adapter (safe ZIP reader) → per-conversation transaction with the checkpoint in the same commit → tombstones respected → revisions kept → echoes flagged.
- **Handoff.** A sharing rule allows a destination and a set of categories → the payload holds goal, task state, decisions, constraints, memories, open questions, conflicts, uncertainty and sources → it carries a `lucid-handoff:<id>` marker.
- **Outbound sync.** Outbox enqueue (idempotent) → claim → apply. The result is one of:
  - receipt
  - transient error → backoff
  - conflict → conflict queue
  - outcome unknown → `lookup(op_id)` before any retry

  After a crash, items left `in_flight` become `outcome_unknown`.
- **Timed decision.** Ask (preferences consulted first) → timer → atomic `open→defaulted` only under an active authorization, otherwise `blocked`. Default side effects go through `ActionLedger.run_once`.
- **Orchestration.** DAG → owned-path overlap check → budget reservation → worker → ownership verification → acceptance checks → final verification from the held-back reserve.
