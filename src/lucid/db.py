"""SQLite store: connection management, migrations, transactions, backup/restore.

One `Store` wraps one SQLite file. Writes go through `tx()`, which takes a
process-local lock and an IMMEDIATE transaction (cross-process write lock) so
read-modify-write sequences such as budget reservations are atomic.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import threading
from importlib import resources
from typing import Any, Iterator

from .redact import redact
from .util import Clock, dumps, lucid_home


def _migration_files() -> list[tuple[int, str, str]]:
    out = []
    pkg = resources.files("lucid") / "migrations"
    for entry in pkg.iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:4].isdigit():
            out.append((int(name[:4]), name, entry.read_text(encoding="utf-8")))
    return sorted(out)


class Store:
    def __init__(self, path: str | None = None, clock: Clock | None = None):
        if path is None:
            home = lucid_home()
            os.makedirs(home, mode=0o700, exist_ok=True)
            path = os.path.join(home, "lucid.db")
        self.path = path
        self.clock = clock or Clock()
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = self._open()
        self.migrate()

    # -- connection -------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            d = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(d, exist_ok=True)
            if not os.path.exists(self.path):  # private by default: memories are personal data
                os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        return conn

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Atomic write transaction (re-entrant: nested calls join the outer one)."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self.conn
                finally:
                    self._depth -= 1
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")
            finally:
                self._depth = 0

    def q(self, sql: str, params: tuple | list | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def ex(self, sql: str, params: tuple | list | dict = ()) -> sqlite3.Cursor:
        with self.tx() as c:
            return c.execute(sql, params)

    # -- migrations -------------------------------------------------------
    def migrate(self) -> list[str]:
        applied = []
        with self._lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
            )
            done = {r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")}
            for version, name, sql in _migration_files():
                if version in done:
                    continue
                # Table rebuilds need FK enforcement off (standard SQLite procedure); checked after.
                self.conn.execute("PRAGMA foreign_keys=OFF")
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _split_sql(sql):
                        self.conn.execute(stmt)
                    self.conn.execute(
                        "INSERT INTO schema_migrations VALUES (?,?,?)", (version, name, self.clock.iso())
                    )
                    bad = self.conn.execute("PRAGMA foreign_key_check").fetchall()
                    if bad:
                        raise RuntimeError(f"migration {name} broke foreign keys: {bad[:3]}")
                    self.conn.execute("COMMIT")
                except BaseException:
                    self.conn.execute("ROLLBACK")
                    raise
                finally:
                    self.conn.execute("PRAGMA foreign_keys=ON")
                applied.append(name)
        return applied

    def schema_version(self) -> int:
        r = self.one("SELECT MAX(version) FROM schema_migrations")
        return int(r[0] or 0)

    # -- activity log (observed telemetry only) ---------------------------
    def log(self, actor: str, action: str, entity_type: str | None = None,
            entity_id: str | None = None, detail: dict[str, Any] | None = None) -> None:
        self.ex(
            "INSERT INTO activity (ts, actor, action, entity_type, entity_id, detail) VALUES (?,?,?,?,?,?)",
            (self.clock.iso(), actor, action, entity_type, entity_id, redact(dumps(detail or {}))),
        )

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))

    # -- backup / restore -------------------------------------------------
    def backup(self, dest: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        if not os.path.exists(dest):  # backups contain personal data: owner-only
            os.close(os.open(dest, os.O_CREAT | os.O_WRONLY, 0o600))
        with self._lock:
            target = sqlite3.connect(dest)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        check = sqlite3.connect(dest)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if ok != "ok":
            raise RuntimeError(f"backup integrity check failed: {ok}")
        return dest

    def restore(self, src: str) -> None:
        check = sqlite3.connect(src)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0]
            has = check.execute("SELECT name FROM sqlite_master WHERE name='memories'").fetchone()
        finally:
            check.close()
        if ok != "ok" or not has:
            raise ValueError("restore source is not a valid Lucid database")
        with self._lock:
            if self.path == ":memory:":
                srcc = sqlite3.connect(src)
                try:
                    srcc.backup(self.conn)
                finally:
                    srcc.close()
            else:
                pre = self.path + ".pre-restore"
                self.backup(pre)
                self.conn.close()
                for suffix in ("-wal", "-shm"):
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(self.path + suffix)
                shutil.copyfile(src, self.path)
                self.conn = self._open()
        self.migrate()


def _split_sql(sql: str) -> list[str]:
    """Split a migration script into statements (no triggers/semicolons in strings in our files)."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    parts = [p.strip() for p in "\n".join(lines).split(";")]
    return [p for p in parts if p]
