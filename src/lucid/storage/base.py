"""Common storage-backend interface.

Backends differ materially, and the interface exposes those differences through
`capabilities()` instead of hiding them:

| backend      | authoritative store            | transactions            | concurrency control          |
|--------------|--------------------------------|-------------------------|------------------------------|
| local        | SQLite file                    | yes (single file)       | SQLite locks                 |
| s3           | S3 objects (records + events)  | per object only         | conditional writes (ETag)    |
| google_docs  | Google Docs managed sections   | per batchUpdate only    | requiredRevisionId           |

In every mode, Lucid keeps local operational state (outbox, checkpoints, FTS index)
in SQLite; cloud records are the durable copy from which the index can be rebuilt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol

from ..sync import ConflictError, OutcomeUnknown, TransientError  # re-exported

__all__ = ["Backend", "Versioned", "ConflictError", "OutcomeUnknown", "TransientError", "PermanentError"]


class PermanentError(Exception):
    """Not retryable: bad credentials, access denied, missing bucket/doc."""


@dataclass
class Versioned:
    body: dict
    version: str  # opaque concurrency token (S3 ETag / Docs revisionId); never assume it is a content hash


class Backend(Protocol):
    name: str

    def capabilities(self) -> dict: ...
    def put_record(self, rtype: str, rid: str, body: dict, expected_version: str | None = None,
                   create_only: bool = False) -> str: ...
    def get_record(self, rtype: str, rid: str) -> Versioned | None: ...
    def list_records(self, rtype: str) -> Iterator[str]: ...
    def delete_record(self, rtype: str, rid: str, reason: str) -> dict: ...
    def append_event(self, op_id: str, event: dict) -> dict: ...
    def lookup_op(self, op_id: str) -> dict | None: ...
