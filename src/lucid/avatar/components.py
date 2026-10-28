"""Separate, swappable parts of a live avatar: rendering, speech recognition, speech generation,
meeting transport. The implementations here are local and simulated. They exist so the session
rules can be tested end to end; they are not a realistic avatar or a meeting-platform integration."""
from __future__ import annotations

import base64
import builtins
import html
import re
from dataclasses import dataclass, field
from typing import Protocol


class TransportError(RuntimeError):
    pass


class Renderer(Protocol):
    name: str

    def render_preview(self, photo_path: str, display_name: str, out_path: str) -> None: ...


class SpeechRecognizer(Protocol):
    def transcribe(self, audio_ref: str) -> tuple[str, float]: ...


class SpeechSynthesizer(Protocol):
    speaking: bool

    def say(self, text: str) -> None: ...
    def cancel(self) -> int: ...
    def advance(self, seconds: float) -> builtins.list[str]: ...


class MeetingTransport(Protocol):
    platform: str
    recording_status: str   # "recording" or "off", as shown to participants
    joined: bool

    def join(self, meeting_id: str, display_name: str) -> None: ...
    def leave(self) -> None: ...
    def drop(self) -> None: ...              # the connection was lost (not a deliberate leave)
    def add_participant(self, name: str) -> None: ...
    def remove_participant(self, name: str) -> None: ...
    def send_chat(self, text: str, to: str | None = None) -> None: ...
    def caption(self, text: str) -> None: ...
    def set_recording_status(self, status: str) -> bool: ...
    def participants(self) -> builtins.list[str]: ...


class StillImageRenderer:
    """Local preview: the user's photo with a permanent 'AI representative' label. Not a lip-synced avatar."""

    name = "local-still"

    def render_preview(self, photo_path: str, display_name: str, out_path: str) -> None:
        ext = photo_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
        with open(photo_path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        label = html.escape(f"Lucid — AI representative for {display_name}")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400">'
               f'<rect width="640" height="400" fill="#0e1117"/>'
               f'<image href="data:image/{mime};base64,{data}" x="170" y="20" width="300" height="300"/>'
               f'<rect x="0" y="340" width="640" height="60" fill="#8ea2ff"/>'
               f'<text x="320" y="378" font-family="sans-serif" font-size="22" text-anchor="middle" '
               f'fill="#0e1117">{label}</text></svg>')
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(svg)


class ScriptedASR:
    """Speech recognition stand-in: utterances arrive already transcribed, with a confidence score."""

    def transcribe(self, audio_ref: str) -> tuple[str, float]:
        text, _, conf = audio_ref.rpartition("|")
        return text, float(conf or 1.0)


@dataclass
class QueueTTS:
    """Speech generation stand-in. Audio is queued in sentence-sized chunks so that cancelling drops
    everything not yet played: stale audio never plays after a stop or a takeover."""

    chars_per_second: float = 15.0
    queue: builtins.list[str] = field(default_factory=list)
    current: str | None = None
    remaining: float = 0.0
    played: builtins.list[str] = field(default_factory=list)
    dropped: builtins.list[str] = field(default_factory=list)
    chars: int = 0

    @property
    def speaking(self) -> bool:
        return self.current is not None or bool(self.queue)

    def say(self, text: str) -> None:
        for chunk in re.split(r"(?<=[.!?])\s+", text.strip()):
            if chunk:
                self.queue.append(chunk)
                self.chars += len(chunk)
        if self.current is None:
            self._next()

    def _next(self) -> None:
        if self.queue:
            self.current = self.queue.pop(0)
            self.remaining = len(self.current) / self.chars_per_second
        else:
            self.current = None

    def advance(self, seconds: float) -> builtins.list[str]:
        done = []
        while self.current is not None and seconds > 0:
            step = min(seconds, self.remaining)
            self.remaining -= step
            seconds -= step
            if self.remaining <= 1e-9:
                done.append(self.current)
                self.played.append(self.current)
                self._next()
        return done

    def cancel(self) -> int:
        n = len(self.queue) + (1 if self.current else 0)
        if self.current:
            self.dropped.append(self.current)
        self.dropped += self.queue
        self.queue.clear()
        self.current = None
        self.remaining = 0.0
        return n


class SimulatedTransport:
    """A meeting stand-in with the parts the rules depend on: a visible name, chat, captions,
    participants joining late, a recording indicator that must be set before recording, and network loss."""

    def __init__(self, platform: str = "webrtc-demo", *, participants: builtins.list[str] | None = None,
                 recording_supported: bool = True, fail_joins: int = 0):
        self.platform = platform
        self._participants = list(participants or [])
        self.recording_supported = recording_supported
        self.fail_joins = fail_joins
        self.joined = False
        self.display_name: str | None = None
        self.chat: builtins.list[tuple[str | None, str]] = []
        self.captions: builtins.list[str] = []
        self.recording_status = "off"
        self.joins = 0

    def join(self, meeting_id: str, display_name: str) -> None:
        self.joins += 1
        if self.fail_joins > 0:
            self.fail_joins -= 1
            raise TransportError("could not reach the meeting")
        self.joined = True
        self.display_name = display_name

    def leave(self) -> None:
        self.joined = False

    def drop(self) -> None:
        self.joined = False

    def add_participant(self, name: str) -> None:
        self._participants.append(name)

    def remove_participant(self, name: str) -> None:
        self._participants = [p for p in self._participants if p != name]

    def participants(self) -> builtins.list[str]:
        return list(self._participants)

    def send_chat(self, text: str, to: str | None = None) -> None:
        if not self.joined:
            raise TransportError("not connected")
        self.chat.append((to, text))

    def caption(self, text: str) -> None:
        if self.joined:
            self.captions.append(text)

    def set_recording_status(self, status: str) -> bool:
        if not self.recording_supported:
            return False
        self.recording_status = status
        return True
