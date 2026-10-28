"""Optional personal avatar: an AI representative made from the user's own photos, video and,
separately authorized, voice. It is always disclosed as an AI representative and never as the
user being present. See docs/avatar.md for what is and is not verified."""
from .components import (QueueTTS, ScriptedASR, SimulatedTransport, StillImageRenderer, TransportError)
from .enrollment import AvatarError, Avatars
from .session import MeetingSession

__all__ = ["Avatars", "AvatarError", "MeetingSession", "QueueTTS", "ScriptedASR", "SimulatedTransport",
           "StillImageRenderer", "TransportError"]
