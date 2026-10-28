"""Facade wiring every subsystem to one Store. Used by the CLI, MCP adapter and service."""
from __future__ import annotations

from .budget import BudgetLedger
from .conversations import Archive
from .db import Store
from .decisions import Decisions
from .handoff import Handoffs
from .memory import MemoryStore
from .monitor import Monitor
from .originality import Originality
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

    def close(self) -> None:
        self.store.close()

    def health(self) -> dict:
        s = self.store
        return {
            "db_path": s.path, "schema_version": s.schema_version(),
            "integrity": s.one("PRAGMA quick_check")[0],
            "memories_active": s.one("SELECT COUNT(*) FROM memories WHERE status='active'")[0],
            "conversations": s.one("SELECT COUNT(*) FROM conversations WHERE status='active'")[0],
            "open_conflicts": s.one("SELECT COUNT(*) FROM memory_conflicts WHERE status='open'")[0],
            "open_questions": s.one("SELECT COUNT(*) FROM questions WHERE status='open'")[0],
            "outbox": self.outbox.status(),
        }
