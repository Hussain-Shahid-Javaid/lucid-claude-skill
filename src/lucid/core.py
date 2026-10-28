"""Facade wiring every subsystem to one Store. Used by the CLI, MCP adapter and service."""
from __future__ import annotations

import os

from .budget import BudgetLedger
from .conversations import Archive
from .db import Store
from .decisions import Decisions
from .handoff import Handoffs
from .memory import MemoryStore
from .monitor import Monitor
from .originality import Originality
from .quality import Quality
from .avatar import Avatars
from .comms import Comms, CredentialStore
from .research import Experiments
from .sync import Checkpoints, Outbox
from .tasks import Tasks
from .util import Clock


class Lucid:
    def __init__(self, path: str | None = None, clock: Clock | None = None):
        self.store = Store(path, clock)
        self.memory = MemoryStore(self.store)
        self.archive = Archive(self.store)
        self.handoffs = Handoffs(self.store, self.memory)
        self.outbox = Outbox(self.store)
        self.checkpoints = Checkpoints(self.store)
        self.budget = BudgetLedger(self.store)
        self.tasks = Tasks(self.store)
        self.decisions = Decisions(self.store, self.memory)
        self.monitor = Monitor(self.store)
        self.experiments = Experiments(self.store)
        self.originality = Originality(self.store, self.memory)
        self.quality = Quality(self.store)
        cred_root = None if self.store.path == ":memory:" else os.path.join(os.path.dirname(os.path.abspath(
            self.store.path)), "credentials")
        self.avatars = Avatars(self.store)
        self.comms = Comms(self.store, self.memory, credentials=CredentialStore(cred_root) if cred_root else None)

    def close(self) -> None:
        self.store.close()

    def health(self) -> dict:
        s = self.store

        def one(sql: str):
            r = s.one(sql)
            return r[0] if r else None
        return {
            "db_path": s.path, "schema_version": s.schema_version(),
            "integrity": one("PRAGMA quick_check"),
            "memories_active": one("SELECT COUNT(*) FROM memories WHERE status='active'"),
            "conversations": one("SELECT COUNT(*) FROM conversations WHERE status='active'"),
            "open_conflicts": one("SELECT COUNT(*) FROM memory_conflicts WHERE status='open'"),
            "open_questions": one("SELECT COUNT(*) FROM questions WHERE status='open'"),
            "outbox": self.outbox.status(),
        }
