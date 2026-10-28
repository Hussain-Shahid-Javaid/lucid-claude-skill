"""Connector SDK.

A connector adapts one external system (an official API, an MCP server, an export
folder, a native-app interface) to Lucid. "Adaptive" means Lucid selects and
configures *installed, enabled* connectors based on context — it never downloads
or executes arbitrary code. Connectors are discovered only from:
  * built-ins in this package, and
  * Python entry points in the `lucid.connectors` group of packages the user installed.

Every connector ships a capability manifest (validated below) and is loaded only
when the user enables it.
"""
from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from importlib import metadata
from typing import Any, Iterator

from ..conversations import NormalizedConversation
from ..util import dumps, loads

OPERATIONS = {"import", "export", "context_handoff", "native_sync", "capture", "storage", "messaging"}


class ManifestError(ValueError):
    pass


@dataclass
class ConnectorManifest:
    id: str
    name: str
    product_account_types: list[str]
    authentication: str                      # none|oauth|api_key|file|local
    operations: list[str]
    conversation_coverage: str
    branches: str
    attachments: str
    formats: list[str]
    incremental_sync: bool
    deletion: str
    rate_limits: str
    permissions: list[str]
    verification: str                        # e.g. "fixture-tested 2026-10-27" / "unverified"
    schema_status: str = "lucid-defined"
    notes: str = ""
    sources: list[str] = field(default_factory=list)

    def validate(self) -> "ConnectorManifest":
        if not self.id or not self.id.replace("-", "").replace("_", "").isalnum():
            raise ManifestError("id must be alphanumeric with - or _")
        bad = set(self.operations) - OPERATIONS
        if bad:
            raise ManifestError(f"unknown operations {sorted(bad)}")
        if "native_sync" in self.operations and not self.sources:
            raise ManifestError("native_sync requires documented sources for the provider interface")
        if not self.verification:
            raise ManifestError("verification statement required")
        return self


class Connector(abc.ABC):
    manifest: ConnectorManifest

    def configure(self, config: dict) -> None:  # credentials must come from a secret store, not config
        self.config = config

    def health(self) -> dict:
        return {"status": "unconfigured"}

    def fetch(self, since: str | None) -> Iterator[tuple[str, NormalizedConversation]]:
        """Yield (position, conversation) for incremental import. Position is checkpointed after commit."""
        raise NotImplementedError


class Registry:
    def __init__(self, store):
        self.s = store
        self._classes: dict[str, type[Connector]] = {}

    def register(self, cls: type[Connector]) -> None:
        cls.manifest.validate()
        self._classes[cls.manifest.id] = cls
        now = self.s.clock.iso()
        self.s.ex("INSERT INTO connectors (id, manifest, updated_at) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET"
                  " manifest=excluded.manifest, updated_at=excluded.updated_at", (cls.manifest.id,
                                                                                  dumps(asdict(cls.manifest)), now))

    def discover(self) -> list[str]:
        from .folder import FolderConnector
        self.register(FolderConnector)
        found = [FolderConnector.manifest.id]
        try:
            eps = list(metadata.entry_points(group="lucid.connectors"))
        except TypeError:  # pragma: no cover
            eps = []
        for ep in eps:
            cls = ep.load()
            self.register(cls)
            found.append(cls.manifest.id)
        return found

    def enable(self, cid: str, enabled: bool = True) -> None:
        if cid not in self._classes:
            raise KeyError(cid)
        self.s.ex("UPDATE connectors SET enabled=?, status=?, updated_at=? WHERE id=?",
                  (int(enabled), "enabled" if enabled else "disabled", self.s.clock.iso(), cid))

    def load(self, cid: str, config: dict | None = None) -> Connector:
        r = self.s.one("SELECT enabled FROM connectors WHERE id=?", (cid,))
        if not r or not r[0]:
            raise PermissionError(f"connector {cid!r} is not enabled")
        inst = self._classes[cid]()
        inst.configure(config or {})
        return inst

    def manifests(self) -> list[dict[str, Any]]:
        return [{**loads(r["manifest"], {}), "enabled": bool(r["enabled"]), "status": r["status"]}
                for r in self.s.q("SELECT * FROM connectors ORDER BY id")]


def run_incremental(lucid, connector: Connector, *, stream: str | None = None) -> dict:
    """Import from a connector with a durable checkpoint committed together with each conversation."""
    stream = stream or f"connector:{connector.manifest.id}"
    pos = lucid.checkpoints.get(stream)
    n = 0
    for position, conv in connector.fetch(pos):
        with lucid.store.tx() as c:
            lucid.archive.ingest(conv, method=f"connector:{connector.manifest.id}")
            lucid.checkpoints.set(stream, position, conn=c)
        n += 1
    return {"imported": n, "checkpoint": lucid.checkpoints.get(stream)}
