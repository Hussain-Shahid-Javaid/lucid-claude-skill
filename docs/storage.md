# Storage modes

| | Local (default) | Google Docs mirror | AWS S3 mirror |
|---|---|---|---|
| Authoritative store | SQLite file `$LUCID_HOME/lucid.db` | Lucid stays authoritative for operational state. Managed doc sections are a durable, human-editable view of the chosen categories, and user edits flow back. | S3 records and immutable events are the durable copy of the chosen categories |
| Local cache / index | FTS5 index, rebuildable (`lucid reindex`) | Local SQLite plus FTS | Local SQLite plus FTS. Rebuild with `rebuild_from_s3` |
| Offline | Full | Writes queue in the outbox and apply when back online | Writes queue in the outbox and apply when back online |
| Concurrency | SQLite IMMEDIATE transactions. Safe across threads and processes. | `requiredRevisionId` on every batchUpdate. On mismatch: re-read, reconcile user edits, retry (bounded). | `If-None-Match: *` for creates and events, `If-Match: <ETag>` for updates. 409/412 → ConflictError → re-read and reconcile |
| Atomicity | Multi-row transactions | One batchUpdate per document. No cross-document transactions. | One object per request. No multi-object transactions. The event is written first (idempotent by op id), then the record. |
| Uncertain writes | n/a | Re-read the doc and look for the op id in the end marker before retrying | `lookup_op(op_id)` checks for the event object before retrying |
| Recovery | `lucid backup` / `lucid restore` (integrity-checked; the pre-restore DB is kept) | Docs revision history (Google), plus local backups | Bucket versioning plus event replay, `rebuild_from_s3` |
| Deletion | Content cleared and a tombstone kept. Derived summaries are invalidated and their text cleared. Indexes are updated. | The line is removed from the section. Google's revision history may still hold it (Google retention applies). | A tombstone and a delete marker are written. Every version of the upsert *events* for that record (which hold its content) is purged. `purge_versions` also removes prior versions of the record object. Lucid reports what it removed. |
| What is stored | Everything you save or import | Only the categories you select | Only the categories you select |

## Local (SQLite)

- **Location.** `$LUCID_HOME` (default `~/.lucid`, or `$XDG_DATA_HOME/lucid`). The file is created with mode 0600. It is never stored inside the skill folder or a temp directory.
- **Migrations.** Migrations run automatically at open. Versions are recorded in `schema_migrations`. To add a migration, create `src/lucid/migrations/000N_*.sql`.
- **Backup and restore.**
  - `lucid backup path.db` uses SQLite's online backup and then runs an integrity check.
  - `lucid restore path.db` validates the file, saves `lucid.db.pre-restore`, swaps the files and reindexes.
- **Index rebuild.** `lucid reindex`

## S3

```python
from lucid.core import Lucid
from lucid.storage.s3 import S3Backend
from lucid.storage.mirror import Mirror, S3Destination, rebuild_from_s3
L = Lucid()
backend = S3Backend("my-private-bucket", prefix=f"lucid/{user}/")   # standard AWS credential chain
Mirror(L, S3Destination(backend), categories={"preference", "decision", "project_state"}).sync()
```

Use a private bucket with Block Public Access, versioning on and default encryption. Grant only `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`, `s3:ListBucketVersions` and `s3:DeleteObjectVersion` on `arn:aws:s3:::bucket/lucid/<user>/*`.

Lucid never synchronizes the SQLite file itself by overwriting one object. ETags are only concurrency tokens, never content hashes, because multipart and SSE-KMS ETags are not MD5s.

**Validation status.** The S3 mirror passes contract tests against **moto** (conditional writes, version purge, a lost-response reconcile, rebuild). It has **not** been validated against live AWS in this project. Live tests are in `tests/live/` and run with `pytest -m live` when `LUCID_LIVE_S3_BUCKET` is set.

## Google Docs

```python
from lucid.storage.gdocs import GoogleDocsBackend, GoogleDocsClient, TokenStore
from lucid.storage.mirror import DocsMirror
backend = GoogleDocsBackend(GoogleDocsClient(credentials), doc_ids=["<doc you picked>"])
DocsMirror(L, backend, categories={"preference", "decision"}).sync(op_id="sync-2026-10-27T10:00")
```

**Scopes.** Request `drive.file` and `documents`. `drive.file` limits Lucid to files you picked or that Lucid created. Tokens are kept by `TokenStore` (mode 0600, under `$LUCID_HOME/credentials`).

**Managed section format.** Lucid writes only between these markers:

```
⟦lucid:begin v1⟧
[mem_…] (preference · verified · 2026-10-27) Prefers metric units
⟦lucid:end op=<op id>⟧
```

**How Lucid handles your edits:**
- Content outside the markers is preserved.
- An edited line becomes a `supersede` with origin=user.
- A deleted line is **held for confirmation** (`confirm_removal`). It is never deleted automatically and never silently re-added.
- Collections larger than `max_per_doc` (default 300) are split across several documents.

**Validation status.** Contract-tested against `FakeDocsClient`, which emulates indices, revisions, conflicts and timeout-after-apply. It has **not** been validated against the live Google Docs API in this project.
