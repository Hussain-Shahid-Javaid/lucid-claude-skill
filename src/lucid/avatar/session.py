"""A disclosed avatar session in one meeting.

Rules this engine enforces:
* It joins only meetings the user approved (by id or category, and host). A calendar entry is not
  approval, and timed default decisions cannot create this authority.
* The visible name is always "Lucid — AI representative for <user>". The disclosure is spoken and
  posted at the start, and posted again to everyone who joins later.
* It answers only from knowledge the user authorized for the current audience. When the audience
  changes, what it may say changes with it. Private memory is never used.
* It does not invent the user's opinions or make commitments the user did not allow; it says the
  user needs to confirm and records an escalation.
* Speech stops at once on interruption, takeover, mute, stop or leave, and queued audio is dropped.
* Recording starts only after the meeting shows it is recording, and only if the user allowed it.
* Avatar, speech and meeting costs are metered separately from model tokens; the session leaves
  when the time or cost limit is reached.
"""
from __future__ import annotations

import builtins
import re
import sqlite3
from typing import Any, Callable

from ..comms import detect_commitments, detect_personal_claims
from ..util import dumps, loads, new_id
from .components import TransportError
from .enrollment import AvatarError, Avatars

DEFAULT_RATES = {"avatar_per_min": 0.10, "meeting_per_min": 0.0, "tts_per_1k_chars": 0.015, "asr_per_min": 0.006}
LOW_CONFIDENCE = 0.6
REJOIN_DELAYS = (2.0, 4.0, 8.0)
_OPINION = re.compile(r"\b(?:what does \w+ think|(?:his|her|their|your) (?:opinion|view|take)|does \w+ (?:like|prefer|"
                      r"support|agree))\b", re.I)
_QUESTION = re.compile(r"\?\s*$|^\s*(?:what|when|where|who|why|how|which|can|could|will|would|does|do|did|is|are|"
                       r"should|has|have)\b", re.I)
_REQUEST = re.compile(r"^\s*(?:please|kindly)\b|\b(?:i|we) (?:need|want) (?:you|him|her|them)\b|"
                      r"\bgo ahead and\b|\bcan you\b|\bcould you\b|\bwould you\b", re.I)
_REPORTED = re.compile(r"\b(?:asked|said|told|wrote|mentioned)\b[^?]*[:\"\u201c]|\b(?:asked|told) (?:me|us|him|her)\b",
                       re.I)
_NEGATED = re.compile(r"\b(?:not|never|no longer|cancel|\w+n'?t)\b|\bcannot\b", re.I)
# An imperative verb at the start, followed by what it acts on ("book a call", "sign the NDA", "ship it"), so that
# noun phrases like "Book club…" or "Order of business…" are not read as requests.
_IMPERATIVE = re.compile(r"^\s*(?:please\s+|kindly\s+)?(?:approve|sign|buy|pay|purchase|order|confirm|agree|commit|"
                         r"accept|authori[sz]e|book|schedule|send|deliver|ship|decide|choose|pick|meet|join)\s+"
                         r"(?:(?:the|a|an|this|that|these|those|it|them|me|us|our|my|your|his|her|their|to|on|off|up|"
                         r"today|tomorrow|now|here|asap|please)\b|\d|(?-i:[A-Z]))", re.I)
COMMIT_VERBS = {
    "meeting": ("meet", "join", "attend", "book", "schedule"),
    "contract": ("sign", "accept"),
    "approval": ("approve", "authorize", "authorise", "ok", "okay"),
    "purchase": ("buy", "pay", "purchase", "order"),
    "deadline": ("deliver", "ship", "finish"),
    "promise": ("commit", "promise", "guarantee"),
}
_VERB_KIND = {v: k for k, vs in COMMIT_VERBS.items() for v in vs}
_COMMIT_VERB_RE = r"\b(?:" + "|".join(sorted(_VERB_KIND)) + r")\b"


def _direct_commitment(text: str, first: str) -> str | None:
    """The kind of commitment a request directly asks for, or None. The commitment verb must be the
    action requested: right after "can/could/will/would you|<user>", at the start of an imperative,
    or after "<user> needs to" / "tell <user> to"."""
    adv = r"(?:(?:please|also|just|possibly|still|maybe)\s+)?"
    able = r"(?:(?:be\s+)?(?:able|free|available|willing)\s+to\s+)?"
    pats = [rf"\b(?:can|could|will|would|shall)\s+(?:you|{first})\s+{adv}{able}(\w+)",
            rf"\b(?:are\s+you|is\s+{first})\s+{adv}(?:able|free|available|willing)\s+to\s+(\w+)",
            rf"\b{first}\s+(?:needs|has|should|must|will need|would need|ought)\s+to\s+(\w+)",
            rf"\b(?:tell|get|have|remind|ask)\s+{first}\s+to\s+(\w+)"]
    if not _QUESTION.search(text) and _IMPERATIVE.search(text):
        pats.append(r"^\s*(?:please\s+|kindly\s+)?(\w+)")
    for p in pats:
        for m in re.finditer(p, text, re.I):
            kind = _VERB_KIND.get(m.group(1).lower())
            if kind:
                return kind
    return None


_DECISION = re.compile(r"\b(?:we (?:decided|agreed)|let'?s go with|the decision is|we'?ll go with)\b", re.I)
_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "where", "which", "about", "your", "you",
         "does", "have", "from", "will", "would", "could", "there", "they", "them", "please", "tell"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in _STOP}


class MeetingSession:
    def __init__(self, avatars: Avatars, enrollment_id: str, meeting: dict, *, transport, tts, asr,
                 now: Callable[[], float], rates: dict | None = None, comms=None):
        self.av = avatars
        self.s = avatars.s
        self.eid = enrollment_id
        self.meeting = meeting
        self.transport = transport
        self.tts = tts
        self.asr = asr
        self.now = now
        self.rates = {**DEFAULT_RATES, **(rates or {})}
        self.comms = comms
        self.sid: str | None = None
        self.state = "idle"
        self.mode = "ai"          # ai | human (after takeover)
        self.muted = False
        self.t0 = 0.0
        self.last_tick = 0.0
        self.rejoin_attempt = 0
        self.next_rejoin: float | None = None
        self.transcript: builtins.list[dict] = []
        self.accessed: builtins.list[str] = []
        self.commitments: builtins.list[dict] = []
        self.escalations: builtins.list[dict] = []
        self.unanswered: builtins.list[str] = []
        self.auth: dict | None = None

    # ---------------------------------------------------------------- start
    @property
    def identity(self) -> str:
        return f"Lucid — AI representative for {self.av.get(self.eid)['display_name']}"

    def disclosure(self) -> str:
        name = self.av.get(self.eid)["display_name"]
        return (f"Hello, I'm Lucid, an AI representative for {name}. {name} is not here in person. "
                f"I can share what {name} has approved for this meeting, and I'll pass anything else to {name} "
                "to confirm.")

    def start(self) -> dict:
        e = self.av.get(self.eid)
        if e["status"] != "active" or not e["appearance_enabled"]:
            raise AvatarError("the avatar is not active")
        if "meetings" not in e["permitted_uses"]:
            raise AvatarError("meetings are not a permitted use")
        auth = self.av.find_authorization(self.eid, self.meeting)
        if not auth:
            raise AvatarError("this meeting is not on the approved list (a calendar invitation is not approval)")
        rules = auth["rules"]
        if rules.get("platform") and rules["platform"] != self.transport.platform:
            raise AvatarError(f"approved for {rules['platform']}, not {self.transport.platform}")
        self.auth = auth
        self.sid = new_id("ses")
        self.t0 = self.last_tick = self.now()
        try:
            self.s.ex("INSERT INTO avatar_sessions (id, enrollment_id, meeting_id, auth_id, transport, verification_level,"
                      " state, identity, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (self.sid, self.eid, self.meeting["id"], auth["id"], self.transport.platform,
                       getattr(self.transport, "verification_level", "simulated"), "joining", self.identity,
                       self.s.clock.iso()))
        except sqlite3.IntegrityError:
            self.sid = None
            raise AvatarError("already in this meeting; a second copy will not join") from None
        try:
            self.transport.join(self.meeting["id"], self.identity)
        except TransportError as ex:
            self._end("could not join: " + str(ex))
            raise AvatarError(f"could not join: {ex}") from ex
        self._set_state("live")
        self._disclose(to=None)
        self._apply_recording(rules)
        return self.status()

    def _apply_recording(self, rules: dict) -> None:
        """Match recording and transcription to the rules in force. Recording needs a visible notice first."""
        on = getattr(self.transport, "recording_status", "off") == "recording"
        if rules.get("recording") and not on:
            if self.transport.set_recording_status("recording"):
                self.s.ex("UPDATE avatar_sessions SET recording='on' WHERE id=?", (self.sid,))
                self._event("recording_on")
            else:
                self._event("recording_unavailable", reason="the meeting could not show a recording notice")
        elif not rules.get("recording") and on:
            self.transport.set_recording_status("off")
            self.s.ex("UPDATE avatar_sessions SET recording='off' WHERE id=?", (self.sid,))
            self._event("recording_off", reason="the current approval does not allow recording")
        self.s.ex("UPDATE avatar_sessions SET transcription=? WHERE id=?",
                  ("on" if rules.get("transcription") else "off", self.sid))

    def _disclose(self, to: str | None) -> None:
        text = self.disclosure()
        self.transport.send_chat(text, to=to)
        self.transport.caption(text)
        if to is None and self.mode == "ai" and not self.muted:
            self._speak(text)
        self._event("disclosure", to=to or "everyone")

    # ---------------------------------------------------------------- events
    def participant_joined(self, name: str) -> None:
        if name not in self.transport.participants():
            self.transport.add_participant(name)
        if self.state == "live":
            self._disclose(to=name)

    def participant_left(self, name: str) -> None:
        self.transport.remove_participant(name)
        self._event("participant_left", name=name)

    def speech_started(self, speaker: str) -> None:
        if self.tts.speaking:
            n = self.tts.cancel()
            self._event("interrupted", by=speaker, dropped_chunks=n)

    def heard(self, speaker: str, audio_ref: str) -> str | None:
        """A participant finished speaking. Returns what Lucid said, if anything."""
        self.speech_started(speaker)
        if self.state == "live" and not self._still_allowed():
            return None
        text, conf = self.asr.transcribe(audio_ref)
        keep = bool(self.auth and self.auth["rules"].get("transcription"))
        # Without transcription, participants' words are used to answer and then not kept.
        self.transcript.append({"t": round(self.now() - self.t0, 2), "speaker": speaker,
                                "text": text if keep else "[not transcribed]", "confidence": conf})
        if self.state != "live" or self.mode != "ai" or self.muted:
            return None
        if conf < LOW_CONFIDENCE:
            return self._say("Sorry, I didn't catch that clearly. Could you say it again?", kind="clarify")
        return self._answer(speaker, text, question=bool(_QUESTION.search(text)))

    def _answer(self, speaker: str, text: str, *, question: bool = True) -> str | None:
        """Only a question, or a request addressed to the user, gets a reply. A statement that merely contains
        words like "will", "meet" or "approve" is never taken as a request, so it can never produce agreement.
        Requests Lucid cannot settle are recorded as escalations for the user."""
        rules = self._rules()
        name = self.av.get(self.eid)["display_name"]
        low = text.lower()
        request = self._is_request(text, name)
        if not (question or request):
            return None
        esc_word = next((w for w in rules["escalate_on"] if re.search(r"\b" + re.escape(w) + r"\b", low)), None)
        if esc_word:
            return self._escalate(speaker, self._request_text(text, r"\b" + re.escape(esc_word) + r"\b"),
                                  f"I can't speak for {name} on that. I'll pass it to {name}.")
        first = re.escape(name.split()[0]) if name.split() else "user"
        kind = None if _REPORTED.search(text) else _direct_commitment(text, first)
        if kind:
            # Only a commitment verb that the request itself asks for counts: "can Hussain meet Tuesday?",
            # "please sign the offer", "Hussain needs to approve it". A commitment word elsewhere does not.
            if kind in rules["commitments"] and not _NEGATED.search(text):
                # Speech recognition and phrasing are fallible, so even an allowed commitment is only provisional:
                # it is said as such and goes to the user to confirm.
                self.commitments.append({"type": kind, "request": self._request_text(text, _COMMIT_VERB_RE),
                                         "by": speaker, "status": "provisional, needs the user's confirmation"})
                return self._say(f"{name} lets me accept requests like this provisionally. I've noted it, and "
                                 f"{name} will confirm in the follow-up.", kind="commitment")
            return self._escalate(speaker, self._request_text(text, _COMMIT_VERB_RE),
                                  f"I can't commit to that for {name}. I'll ask {name} to confirm.")
        if request and not question:
            return self._escalate(speaker, self._request_text(text, _IMPERATIVE.pattern),
                                  f"I'll pass that request to {name}.")
        allowed = self._knowledge_for_audience(speaker)
        if _OPINION.search(text):
            generic = _words(name) | {"think", "thinks", "like", "likes", "prefer", "prefers", "view", "opinion",
                                      "about", "support", "agree", "take"}
            q = _words(text) - generic  # the user's name and opinion verbs are in every such question
            stated = [k for k in allowed if k.get("type") == "stated_opinion" and
                      len(q & (_words(k["text"]) - generic)) >= 1]
            if not stated:
                self.unanswered.append(text)
                return self._say(f"I don't know {name}'s view on that, and I won't guess. "
                                 f"{name} will need to confirm.", kind="uncertain")
            self.accessed.append(stated[0]["id"])
            return self._say(f"{name} has said: {stated[0]['text']}", kind="answer", source=stated[0]["id"])
        q = _words(text) - _words(name)  # the user's name matches everything about them; it is not a topic
        best, score = None, 0
        for k in allowed:
            if k.get("type") == "stated_opinion":
                continue  # opinions are only given when someone asks for the user's view
            s = len(q & (_words(k["text"] + " " + " ".join(k.get("keywords", []))) - _words(name)))
            if s > score:
                best, score = k, s
        if best is None or score < 1:
            if request:  # "can you confirm Tuesday?" asks for an action, not information: it is the user's call
                return self._escalate(speaker, self._request_text(text, r"\b(?:you|can|could|would|please)\b"),
                                      f"I'll pass that request to {name}.")
            self.unanswered.append(self._request_text(text, r"\?"))
            return self._say(f"I don't have that information. I'll note the question for {name}.", kind="uncertain")
        self.accessed.append(best["id"])
        return self._say(best["text"], kind="answer", source=best["id"])

    def _knowledge_for_audience(self, speaker: str | None = None) -> builtins.list[dict]:
        """Items every listener may hear: all current participants plus whoever asked. A participant using
        Lucid's display name is still a participant; with nobody identifiable, only public items qualify."""
        rules = self._rules()
        people = set(self.transport.participants())
        if speaker:
            people.add(speaker)
        out = []
        for k in rules["knowledge"]:
            aud = k.get("audience", [])
            if "*" in aud:
                out.append(k)
            elif people and all(any(p == a or (a.startswith("@") and p.endswith(a)) for a in aud) for p in people):
                out.append(k)
        return out

    def _is_request(self, text: str, name: str) -> bool:
        if _REPORTED.search(text):
            return False  # someone else's words being repeated, not a request now
        if _REQUEST.search(text) or _IMPERATIVE.search(text):
            return True
        n = re.escape(name.split()[0]) if name.split() else ""
        return bool(n) and bool(re.search(
            rf"\b{n}\s+(?:needs|has|should|must|ought|will need|would need)\s+to\b|\bfrom\s+{n}\b|\bask\s+{n}\b"
            rf"|\b(?:tell|get|have|remind)\s+{n}\s+to\b|\b{n}'?s\s+(?:approval|sign-?off|signature|decision|input|ok)\b",
            text, re.I))

    def _request_text(self, text: str, pattern: str) -> str:
        """With transcription off, keep only the clause that holds the request (matched by `pattern`).
        Comma-enclosed asides ("Bob, who is ..., needs") are dropped first. This trimming is best effort."""
        if self._rules().get("transcription"):
            return text
        trimmed = re.sub(r"\([^)]*\)|\[[^\]]*\]|\s[-\u2013\u2014]\s[^-\u2013\u2014]*\s[-\u2013\u2014]\s|,[^,.;!?]*,",
                         " ", text)
        trimmed = re.sub(r"\s+", " ", trimmed)
        parts = [p.strip() for p in re.split(r"[.;!?]|,\s*(?:so|and|but)\b|\bso\b", trimmed) if p and p.strip()]
        hits = [p for p in parts if re.search(pattern, p, re.I)]
        if not hits:  # fall back to the clause that names the user or "you", then to the first clause
            first = re.escape(self.av.get(self.eid)["display_name"].split()[0])
            hits = [p for p in parts if re.search(rf"\b(?:{first}|you)\b", p, re.I)] or parts[:1]
        return (min(hits, key=len) if hits else "a request that needs a decision")[:160]

    def _escalate(self, speaker: str, text: str, reply: str) -> str:
        self.escalations.append({"by": speaker, "request": text, "t": round(self.now() - self.t0, 2)})
        self._event("escalation", request=text)
        return self._say(reply, kind="escalation")

    def _say(self, text: str, *, kind: str, source: str | None = None) -> str:
        # Last line of defence: never speak an unauthorized commitment or an unsupported personal claim.
        bad = [c for c in detect_commitments(text) if c not in self._rules()["commitments"]]
        if kind not in ("escalation", "commitment") and (bad or detect_personal_claims(text)):
            text = f"I can't say that on {self.av.get(self.eid)['display_name']}'s behalf; they'll need to confirm."
            kind = "withheld"
        self.transcript.append({"t": round(self.now() - self.t0, 2), "speaker": self.identity, "text": text,
                                "kind": kind, "source": source})
        self.transport.caption(text)
        self._speak(text)
        self._event("said", what=kind, source=source)
        return text

    def _speak(self, text: str) -> None:
        e = self.av.get(self.eid)
        if not e["voice_enabled"]:
            return  # captions and chat only: no cloned voice without separate consent
        before = self.tts.chars
        self.tts.say(text)
        self._cost("speech_out", (self.tts.chars - before) / 1000, "1k chars", self.rates["tts_per_1k_chars"])

    # ---------------------------------------------------------------- user controls
    def takeover(self) -> None:
        dropped = self.tts.cancel()
        self.mode = "human"
        self._event("takeover", dropped_chunks=dropped)

    def resume(self, *, actor: str) -> None:
        if actor != "user":
            raise AvatarError("only the user can hand the meeting back to Lucid")
        self.mode = "ai"
        self._event("resume")

    def mute(self) -> None:
        self.tts.cancel()
        self.muted = True
        self._event("mute")

    def unmute(self) -> None:
        self.muted = False
        self._event("unmute")

    def stop_speaking(self) -> int:
        n = self.tts.cancel()
        self._event("stop_speaking", dropped_chunks=n)
        return n

    def leave(self, reason: str = "user ended the session") -> dict:
        self.tts.cancel()
        if getattr(self.transport, "joined", True):
            self.transport.leave()
        return self._end(reason)

    # ---------------------------------------------------------------- time, network, limits
    def network_lost(self) -> None:
        self.tts.cancel()
        self.transport.drop()
        self._set_state("reconnecting")
        self.rejoin_attempt = 0
        self.next_rejoin = self.now() + REJOIN_DELAYS[0]
        self._event("network_lost")

    def tick(self) -> dict:
        t = self.now()
        dt = max(0.0, t - self.last_tick)
        self.last_tick = t
        if self.state in ("ended", "idle"):
            return self.status()
        if not self._still_allowed():
            return self.status() if self.state == "ended" else self.leave("no longer authorized")
        self.tts.advance(dt)
        if self.state == "live":
            mins = dt / 60
            self._cost("avatar", mins, "min", self.rates["avatar_per_min"])
            self._cost("meeting", mins, "min", self.rates["meeting_per_min"])
            self._cost("speech_in", mins, "min", self.rates["asr_per_min"])
        if self.state == "reconnecting" and self.next_rejoin is not None and t >= self.next_rejoin:
            try:
                self.transport.join(self.meeting["id"], self.identity)
                self._set_state("live")
                self._event("rejoined", attempt=self.rejoin_attempt + 1)
                self._disclose(to=None)
            except TransportError:
                self.rejoin_attempt += 1
                if self.rejoin_attempt >= len(REJOIN_DELAYS):
                    self.escalations.append({"by": "system", "request": "lost the meeting connection",
                                             "t": round(t - self.t0, 2)})
                    return self._end("network lost; could not rejoin")
                self.next_rejoin = t + REJOIN_DELAYS[self.rejoin_attempt]
        rules = self._rules()
        if t - self.t0 >= rules["max_duration_s"]:
            return self._leave_politely("time limit reached")
        if self.total_cost() >= rules["max_cost_usd"]:
            return self._leave_politely("cost limit reached")
        return self.status()

    def _leave_politely(self, reason: str) -> dict:
        if self.state == "live":
            self.tts.cancel()
            note = f"I need to leave now. {self.av.get(self.eid)['display_name']} will follow up."
            self.transport.send_chat(note)
            self.transport.caption(note)
        return self.leave(reason)

    def _cost(self, category: str, units: float, unit: str, rate: float) -> None:
        if units <= 0 or self.sid is None:
            return
        self.s.ex("INSERT INTO avatar_costs (session_id, category, units, unit, usd, at) VALUES (?,?,?,?,?,?)",
                  (self.sid, category, units, unit, units * rate, self.now() - self.t0))

    def costs(self) -> dict:
        if self.sid is None:
            return {}
        rows = self.s.q("SELECT category, SUM(units), MAX(unit), SUM(usd) FROM avatar_costs WHERE session_id=?"
                        " GROUP BY category", (self.sid,))
        return {r[0]: {"units": round(r[1], 4), "unit": r[2], "usd": round(r[3], 4)} for r in rows}

    def total_cost(self) -> float:
        return round(sum(v["usd"] for v in self.costs().values()), 6)

    # ---------------------------------------------------------------- observability
    def status(self) -> dict:
        row = self.s.one("SELECT recording, transcription, state, end_reason FROM avatar_sessions WHERE id=?",
                         (self.sid,)) if self.sid else None
        return {"session": self.sid, "meeting": self.meeting.get("title") or self.meeting["id"],
                "state": self.state, "mode": self.mode, "muted": self.muted, "identity": self.identity,
                "verification_level": getattr(self.transport, "verification_level", "simulated"),
                "participants": self.transport.participants(),
                "information_accessed": list(dict.fromkeys(self.accessed)), "commitments": self.commitments,
                "escalations": self.escalations, "duration_s": round(self.now() - self.t0, 1) if self.sid else 0,
                "costs": self.costs(), "cost_total_usd": self.total_cost(),
                "cost_note": "avatar, speech and meeting charges; model tokens are tracked in the budget ledger",
                "recording": row["recording"] if row else "off", "transcription": row["transcription"] if row else "off",
                "end_reason": row["end_reason"] if row else None}

    def _end(self, reason: str) -> dict:
        if self.state == "ended":
            return self.status()
        self.state = "ended"
        if getattr(self.transport, "recording_status", "off") == "recording":
            self.transport.set_recording_status("off")
        summary = self.summary()
        if self.sid:
            self.s.ex("UPDATE avatar_sessions SET state='ended', ended_at=?, end_reason=?, recording='off', summary=?"
                      " WHERE id=?", (self.s.clock.iso(), reason, dumps(summary), self.sid))
            self._event("ended", reason=reason)
        return {**self.status(), "summary": summary}

    def summary(self) -> dict:
        """Built only from the transcript and recorded events, with quotes and times as evidence."""
        decisions = [{"t": x["t"], "by": x["speaker"], "quote": x["text"]} for x in self.transcript
                     if x["speaker"] != self.identity and _DECISION.search(x["text"])]
        transcribed = bool(self.auth and self.auth["rules"].get("transcription"))
        drafts = []
        fu = (self.auth or {}).get("rules", {}).get("follow_up")
        if self.comms and fu and (self.escalations or self.unanswered):
            lines = [f"- {e['request']}" for e in self.escalations] + [f"- {q}" for q in self.unanswered]
            body = ("Questions from the meeting that need your answer:\n" + "\n".join(lines))
            m = self.comms.compose(fu["account_id"], body, to=fu.get("to", []), channel=fu.get("channel"),
                                   subject=f"Follow-up: {self.meeting.get('title') or self.meeting['id']}",
                                   force_draft=True)
            drafts.append(m["id"])
        return {"meeting": self.meeting.get("title") or self.meeting["id"],
                "said_by_lucid": [x for x in self.transcript if x["speaker"] == self.identity],
                "decisions_heard": decisions, "unresolved_questions": self.unanswered,
                "escalations": self.escalations, "commitments_made": self.commitments,
                "information_accessed": list(dict.fromkeys(self.accessed)), "follow_up_drafts": drafts,
                "transcription": "on" if transcribed else "off: participants' words were not kept, so decisions "
                                 "cannot be quoted; open questions and escalations keep only the request itself",
                "note": "Only what was said in the meeting is reported; nothing is inferred about the user's views."}

    def events(self) -> builtins.list[dict]:
        return [{**dict(r), "detail": loads(r["detail"], {})} for r in
                self.s.q("SELECT t, kind, detail FROM avatar_events WHERE session_id=? ORDER BY id", (self.sid,))]

    def _event(self, kind: str, **detail: Any) -> None:
        if self.sid:
            self.s.ex("INSERT INTO avatar_events (session_id, t, kind, detail) VALUES (?,?,?,?)",
                      (self.sid, round(self.now() - self.t0, 3), kind, dumps(detail)))

    def _still_allowed(self) -> bool:
        """Checked on every step: consent, controls and the meeting approval can change mid-meeting."""
        if self.state == "ended":
            return False
        e = self.av.get(self.eid)
        if not e["voice_enabled"] and self.tts.speaking:
            n = self.tts.cancel()  # voice consent withdrawn: drop the cloned-voice audio at once
            self._event("voice_revoked", dropped_chunks=n)
        reason = None
        if e["status"] != "active":
            reason = "avatar consent was revoked"
        elif not e["appearance_enabled"] or "meetings" not in e["permitted_uses"]:
            reason = "meetings were switched off for this avatar"
        else:
            current = self.av.find_authorization(self.eid, self.meeting)
            if not current:
                reason = "the meeting approval ended"
            elif self.auth is None or current["id"] != self.auth["id"]:
                # Approvals changed mid-meeting: from now on only the currently valid rules apply,
                # including platform and recording, not just what may be said.
                self.auth = current
                dropped = self.tts.cancel()  # nothing said under the old approval may keep playing
                self.s.ex("UPDATE avatar_sessions SET auth_id=? WHERE id=?", (current["id"], self.sid))
                self._event("approval_changed", auth=current["id"], dropped_chunks=dropped)
                rules = current["rules"]
                if rules.get("platform") and rules["platform"] != self.transport.platform:
                    reason = f"the current approval covers {rules['platform']}, not {self.transport.platform}"
                else:
                    self._apply_recording(rules)
        if reason:
            self.leave(reason)
            return False
        return True

    def _rules(self) -> dict:
        if self.auth is None:
            raise AvatarError("the session has not started")
        return self.auth["rules"]

    def _set_state(self, st: str) -> None:
        self.state = st
        if self.sid:
            self.s.ex("UPDATE avatar_sessions SET state=? WHERE id=?", (st, self.sid))
