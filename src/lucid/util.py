"""Small shared helpers: ids, clocks, hashing, JSON."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import uuid
from typing import Any, Callable

SCHEMA_VERSION = 1


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Clock:
    """Injectable clock so timers, expiry and deadlines are testable."""

    def __init__(self, fn: Callable[[], _dt.datetime] | None = None):
        self._fn = fn or (lambda: _dt.datetime.now(_dt.timezone.utc))

    def now(self) -> _dt.datetime:
        return self._fn()

    def iso(self) -> str:
        return iso(self.now())


class FakeClock(Clock):
    def __init__(self, start: _dt.datetime | None = None):
        self.t = start or _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
        super().__init__(lambda: self.t)

    def advance(self, seconds: float) -> None:
        self.t = self.t + _dt.timedelta(seconds=seconds)


def iso(d: _dt.datetime) -> str:
    return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def from_epoch(x: Any) -> str | None:
    if x in (None, ""):
        return None
    try:
        return iso(_dt.datetime.fromtimestamp(float(x), tz=_dt.timezone.utc))
    except (TypeError, ValueError, OSError):
        return None


def sha256(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, default=str)


def loads(s: str | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def approx_tokens(text: str) -> int:
    """Conservative, provider-independent token estimate (~4 chars/token, rounded up)."""
    return max(1, (len(text) + 3) // 4)


def lucid_home() -> str:
    """Persistent data directory. Never a temp/execution dir.

    Order: $LUCID_HOME, $XDG_DATA_HOME/lucid, ~/.lucid
    """
    if os.environ.get("LUCID_HOME"):
        return os.path.abspath(os.path.expanduser(os.environ["LUCID_HOME"]))
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "lucid")
    return os.path.join(os.path.expanduser("~"), ".lucid")
