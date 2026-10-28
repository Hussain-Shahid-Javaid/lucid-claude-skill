"""Avatar enrollment: consent, protected media, preview, activation, controls, revocation, deletion
and meeting authorizations.

* Only the user's own likeness and voice can be enrolled. Having someone's photos or recordings is
  not permission to clone them, so the subject must be the account holder and must sign the consent.
* Likeness and voice are separate consents and separate switches.
* Enrollment media lives in its own 0700 vault folder, never in memory, handoffs or exports.
* Media gives appearance and delivery only. It says nothing about the user's knowledge, opinions or
  authority; that comes from the meeting authorization.
"""
from __future__ import annotations

import builtins
import hashlib
import os
import shutil
from typing import Any, Iterable

from ..db import Store
from ..util import dumps, loads, lucid_home, new_id, parse_iso

USES = {"meetings", "recorded_messages", "preview_only"}
MEDIA_KINDS = {"photo": "likeness", "video": "likeness", "voice_sample": "voice", "consent_video": None}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg"}

# What each avatar vendor requires before it will build an avatar, as documented (checked 2026-10-28).
# Lucid has no live adapter for any of them; see docs/avatar.md.
PROVIDER_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "local-still": {"consent_method": "typed_statement", "realtime": "local WebRTC demo only",
                    "deletion": "local files only", "live_adapter": True},
    "heygen": {"consent_method": "consent_video",
               "note": "Consent video by the same person reading HeyGen's text; LiveAvatar custom avatars on Free/"
                       "Essential plans need live webcam consent.",
               "realtime": "LiveAvatar (LiveKit room per session)",
               "deletion": "avatar, voice, source footage and generated videos are deleted separately",
               "sources": ["https://help.heygen.com/en/articles/12092609-recording-your-consent-video",
                           "https://developers.heygen.com/docs/create-avatar"], "live_adapter": False},
    "tavus": {"consent_method": "provider_terms",
              "note": "Docs require that you have the rights and permissions; the old consent-video field is "
                      "deprecated. Lucid still requires its own typed consent.",
              "realtime": "CVI over WebRTC (Daily)", "deletion": "faces/replicas deleted via API (unverified)",
              "sources": ["https://docs.tavus.io/sections/conversational-video-interface/overview-cvi"],
              "live_adapter": False},
}


class AvatarError(ValueError):
    pass


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Avatars:
    def __init__(self, store: Store, vault: str | None = None):
        self.s = store
        if vault is None:
            vault = os.path.join(lucid_home() if store.path == ":memory:" else os.path.dirname(os.path.abspath(store.path)),
                                 "avatar-vault")
        self.vault = vault

    # ---------------------------------------------------------------- enrollment
    def enroll(self, user: str, display_name: str, *, provider: str = "local-still") -> dict:
        if provider not in PROVIDER_REQUIREMENTS:
            raise AvatarError(f"unknown provider {provider!r}")
        if not display_name.strip():
            raise AvatarError("a display name is needed for the disclosure")
        eid = new_id("avt")
        now = self.s.clock.iso()
        retention = {"local": f"{self.vault}/{eid} until you delete the enrollment",
                     "provider": "not uploaded" if provider == "local-still" else
                     "not uploaded by Lucid (no live adapter); if you upload yourself, the vendor's retention applies",
                     "memory": "enrollment media is never stored in Lucid memory, handoffs or exports"}
        self.s.ex("INSERT INTO avatar_enrollments (id, user_scope, display_name, provider, status, retention,"
                  " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                  (eid, user, display_name.strip(), provider, "draft", dumps(retention), now, now))
        return self.get(eid)

    def get(self, eid: str) -> dict:
        r = self.s.one("SELECT * FROM avatar_enrollments WHERE id=?", (eid,))
        if not r:
            raise KeyError(eid)
        d = dict(r)
        d["permitted_uses"] = loads(d["permitted_uses"], [])
        d["retention"] = loads(d["retention"], {})
        d["preview"] = loads(d["preview"], None)
        d["appearance_enabled"] = bool(d["appearance_enabled"])
        d["voice_enabled"] = bool(d["voice_enabled"])
        d["consents"] = {k: self.active_consent(eid, k) for k in ("likeness", "voice")}
        return d

    def consent(self, eid: str, kind: str, statement: str, *, signed_by: str, method: str,
                evidence: str | None = None) -> dict:
        e = self.get(eid)
        if kind not in ("likeness", "voice"):
            raise AvatarError("consent kind is likeness or voice")
        if signed_by != e["user_scope"]:
            raise AvatarError("only the person being represented can consent; possessing their media is not consent")
        need = PROVIDER_REQUIREMENTS[e["provider"]]["consent_method"]
        if need == "consent_video" and method != "consent_video":
            raise AvatarError(f"{e['provider']} requires a consent video recorded by the same person")
        if method == "consent_video":
            m = self.s.one("SELECT id FROM avatar_media WHERE id=? AND enrollment_id=? AND kind='consent_video' AND"
                           " deleted_at IS NULL", (evidence or "", eid))
            if not m:
                raise AvatarError("attach the consent video first and pass its media id as evidence")
        low = statement.lower()
        word = "likeness" if kind == "likeness" else "voice"
        if word not in low or "ai representative" not in low or e["display_name"].lower() not in low:
            raise AvatarError(f"the statement must name you ({e['display_name']}), your {word}, and say it creates "
                              "an AI representative")
        cid = new_id("cons")
        self.s.ex("INSERT INTO avatar_consents (id, enrollment_id, kind, statement, signed_by, method, evidence,"
                  " granted_at) VALUES (?,?,?,?,?,?,?,?)", (cid, eid, kind, statement, signed_by, method, evidence,
                                                          self.s.clock.iso()))
        self._touch(eid)
        return {"id": cid, "kind": kind}

    def active_consent(self, eid: str, kind: str) -> dict | None:
        r = self.s.one("SELECT * FROM avatar_consents WHERE enrollment_id=? AND kind=? AND revoked_at IS NULL"
                       " ORDER BY granted_at DESC, rowid DESC LIMIT 1", (eid, kind))
        return dict(r) if r else None

    def add_media(self, eid: str, kind: str, path: str, *, subject: str) -> dict:
        e = self.get(eid)
        if kind not in MEDIA_KINDS:
            raise AvatarError(f"media kind must be one of {sorted(MEDIA_KINDS)}")
        if subject != "self":
            raise AvatarError("only your own photos, video and voice can be enrolled")
        if e["status"] in ("revoked", "deleted"):
            raise AvatarError(f"enrollment is {e['status']}")
        need = MEDIA_KINDS[kind]
        if need and not self.active_consent(eid, need):
            raise AvatarError(f"record {need} consent before adding {kind} media")
        ext = os.path.splitext(path)[1].lower()
        allowed = {"photo": IMAGE_EXT, "video": VIDEO_EXT, "consent_video": VIDEO_EXT, "voice_sample": AUDIO_EXT}[kind]
        if ext not in allowed:
            raise AvatarError(f"{kind} must be one of {sorted(allowed)}")
        if not os.path.isfile(path):
            raise AvatarError(f"file not found: {path}")
        d = os.path.join(self.vault, eid)
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        mid = new_id("med")
        dest = os.path.join(d, mid + ext)
        shutil.copyfile(path, dest)
        os.chmod(dest, 0o600)
        self.s.ex("INSERT INTO avatar_media (id, enrollment_id, kind, path, sha256, bytes, added_at) VALUES (?,?,?,?,?,?,?)",
                  (mid, eid, kind, dest, _sha(dest), os.path.getsize(dest), self.s.clock.iso()))
        self._touch(eid)
        return {"id": mid, "kind": kind, "bytes": os.path.getsize(dest)}

    def media(self, eid: str, kind: str | None = None) -> builtins.list[dict]:
        sql = "SELECT * FROM avatar_media WHERE enrollment_id=? AND deleted_at IS NULL"
        rows = self.s.q(sql + (" AND kind=?" if kind else ""), (eid, kind) if kind else (eid,))
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- preview and activation
    def build_preview(self, eid: str, renderer) -> dict:
        e = self.get(eid)
        if not self.active_consent(eid, "likeness"):
            raise AvatarError("likeness consent is required for a preview")
        photos = self.media(eid, "photo")
        if not photos:
            raise AvatarError("add at least one photo")
        out = os.path.join(self.vault, eid, "preview.svg")
        renderer.render_preview(photos[0]["path"], e["display_name"], out)
        os.chmod(out, 0o600)
        prev = {"path": out, "sha256": _sha(out), "renderer": renderer.name,
                "label": f"Lucid — AI representative for {e['display_name']}"}
        self.s.ex("UPDATE avatar_enrollments SET status='preview_ready', preview=?, updated_at=? WHERE id=?",
                  (dumps(prev), self.s.clock.iso(), eid))
        return self.get(eid)

    def activate(self, eid: str, *, actor: str, reviewed_preview_sha256: str, uses: Iterable[str] = ("meetings",),
                 appearance: bool = True, voice: bool = False) -> dict:
        e = self.get(eid)
        if actor != "user":
            raise AvatarError("only the user can activate their avatar")
        if e["status"] != "preview_ready" or not e["preview"]:
            raise AvatarError("build and review a preview first")
        if reviewed_preview_sha256 != e["preview"]["sha256"] or _sha(e["preview"]["path"]) != e["preview"]["sha256"]:
            raise AvatarError("the preview you reviewed is not the current one; review it again")
        self._check_controls(eid, appearance, voice, uses)  # validate before anything changes
        self.s.ex("UPDATE avatar_enrollments SET status='active', updated_at=? WHERE id=?", (self.s.clock.iso(), eid))
        return self.set_controls(eid, appearance=appearance, voice=voice, uses=uses, actor=actor)

    def set_controls(self, eid: str, *, appearance: bool, voice: bool, uses: Iterable[str], actor: str) -> dict:
        if actor != "user":
            raise AvatarError("only the user can change avatar controls")
        uses = self._check_controls(eid, appearance, voice, uses)
        self.s.ex("UPDATE avatar_enrollments SET appearance_enabled=?, voice_enabled=?, permitted_uses=?, updated_at=?"
                  " WHERE id=?", (int(appearance), int(voice), dumps(uses), self.s.clock.iso(), eid))
        return self.get(eid)

    def _check_controls(self, eid: str, appearance: bool, voice: bool, uses: Iterable[str]) -> builtins.list[str]:
        uses = sorted(set(uses))
        if set(uses) - USES:
            raise AvatarError(f"uses must be from {sorted(USES)}")
        if appearance and not self.active_consent(eid, "likeness"):
            raise AvatarError("appearance needs likeness consent")
        if voice and (not self.active_consent(eid, "voice") or not self.media(eid, "voice_sample")):
            raise AvatarError("voice needs its own consent and a voice sample")
        return uses

    def revoke(self, eid: str, kind: str | None = None, *, actor: str = "user") -> dict:
        """Revoke likeness, voice, or both. Live sessions stop at their next step."""
        if actor != "user":
            raise AvatarError("only the user can revoke")
        kinds = [kind] if kind else ["likeness", "voice"]
        now = self.s.clock.iso()
        for k in kinds:
            self.s.ex("UPDATE avatar_consents SET revoked_at=? WHERE enrollment_id=? AND kind=? AND revoked_at IS NULL",
                      (now, eid, k))
        if "likeness" in kinds:
            self.s.ex("UPDATE avatar_enrollments SET status='revoked', appearance_enabled=0, voice_enabled=0,"
                      " updated_at=? WHERE id=?", (now, eid))
        else:
            self.s.ex("UPDATE avatar_enrollments SET voice_enabled=0, updated_at=? WHERE id=?", (now, eid))
        if "likeness" in kinds:
            self.s.ex("UPDATE avatar_meeting_auth SET revoked=1 WHERE enrollment_id=?", (eid,))
        return self.get(eid)

    def delete(self, eid: str, *, actor: str = "user", provider_client=None) -> dict:
        """Delete local media and ask the provider to delete each kind of data it may hold. Returns a report."""
        if actor != "user":
            raise AvatarError("only the user can delete their avatar")
        e = self.get(eid)
        self.revoke(eid, actor="user")
        report = []
        now = self.s.clock.iso()
        for m in self.media(eid):
            try:
                os.remove(m["path"])
                report.append({"item": f"local {m['kind']} {m['id']}", "status": "deleted"})
            except FileNotFoundError:
                report.append({"item": f"local {m['kind']} {m['id']}", "status": "already gone"})
            self.s.ex("UPDATE avatar_media SET deleted_at=? WHERE id=?", (now, m["id"]))
        if e["preview"]:
            try:
                os.remove(e["preview"]["path"])
                report.append({"item": "local preview", "status": "deleted"})
            except FileNotFoundError:
                pass
        d = os.path.join(self.vault, eid)
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
        if e["provider"] != "local-still":
            for item in ("avatar", "voice", "source footage", "generated videos"):
                if provider_client is None:
                    report.append({"item": f"{e['provider']} {item}", "status": "not uploaded by Lucid",
                                   "note": "if you uploaded media yourself, delete it in the vendor's account"})
                else:
                    try:
                        st = provider_client.delete(item)
                        report.append({"item": f"{e['provider']} {item}", "status": st})
                    except Exception as ex:  # report, never hide, a failed deletion
                        report.append({"item": f"{e['provider']} {item}", "status": "failed", "note": str(ex)})
        self.s.ex("UPDATE avatar_enrollments SET status='deleted', preview=NULL, updated_at=? WHERE id=?", (now, eid))
        return {"enrollment": eid, "report": report, "retention_after": "consent records and an audit trail are kept "
                "(no media); delete the Lucid database to remove those too"}

    # ---------------------------------------------------------------- meeting authorization
    def authorize_meetings(self, eid: str, *, actor: str, expires_at: str | None = None, **rules: Any) -> dict:
        if actor != "user":
            raise AvatarError("only the user can authorize meetings")
        e = self.get(eid)
        if e["status"] != "active" or "meetings" not in e["permitted_uses"]:
            raise AvatarError("activate the avatar for meetings first")
        if not (rules.get("meetings") or rules.get("categories")):
            raise AvatarError("name the approved meetings or meeting categories")
        if not rules.get("hosts"):
            raise AvatarError("list the hosts whose meetings it may join")
        for k in ("max_duration_s", "max_cost_usd"):
            if not rules.get(k):
                raise AvatarError(f"{k} is required")
        rules.setdefault("commitments", [])
        rules.setdefault("escalate_on", ["price", "contract", "salary", "legal", "purchase", "budget", "hire"])
        rules.setdefault("recording", False)
        rules.setdefault("transcription", False)
        rules.setdefault("knowledge", [])
        aid = new_id("mauth")
        self.s.ex("INSERT INTO avatar_meeting_auth (id, enrollment_id, rules, created_at, expires_at) VALUES (?,?,?,?,?)",
                  (aid, eid, dumps(rules), self.s.clock.iso(), expires_at))
        return {"id": aid, "rules": rules}

    def find_authorization(self, eid: str, meeting: dict) -> dict | None:
        """A calendar entry is not authorization: the meeting must match an approval the user gave."""
        for r in self.s.q("SELECT * FROM avatar_meeting_auth WHERE enrollment_id=? AND revoked=0 ORDER BY created_at"
                          " DESC, rowid DESC", (eid,)):
            exp = parse_iso(r["expires_at"])
            if exp and exp <= self.s.clock.now():
                continue
            rules = loads(r["rules"], {})
            if meeting.get("id") in rules.get("meetings", []) or meeting.get("category") in rules.get("categories", []):
                if meeting.get("host") in rules["hosts"]:
                    return {"id": r["id"], "rules": rules}
        return None

    def overview(self, user: str) -> dict:
        """Enrollments and meeting sessions for the dashboard: identity, state, access, costs, recording."""
        ens = [self.get(r[0]) for r in self.s.q("SELECT id FROM avatar_enrollments WHERE user_scope=? ORDER BY"
                                               " created_at DESC", (user,))]
        for e in ens:
            e.pop("preview", None)
        sessions = []
        for r in self.s.q("SELECT s.* FROM avatar_sessions s JOIN avatar_enrollments e ON e.id=s.enrollment_id WHERE"
                          " e.user_scope=? ORDER BY s.started_at DESC LIMIT 50", (user,)):
            d = dict(r)
            d["summary"] = loads(d["summary"], None)
            costs = self.s.q("SELECT category, SUM(units), MAX(unit), SUM(usd) FROM avatar_costs WHERE session_id=?"
                             " GROUP BY category", (d["id"],))
            d["costs"] = {c[0]: {"units": round(c[1], 3), "unit": c[2], "usd": round(c[3], 4)} for c in costs}
            ev = [(x[0], loads(x[1], {})) for x in self.s.q("SELECT kind, detail FROM avatar_events WHERE"
                                                           " session_id=? ORDER BY id", (d["id"],))]
            d["information_accessed"] = sorted({x[1].get("source") for x in ev if x[0] == "said" and x[1].get("source")})
            d["escalations"] = [x[1].get("request") for x in ev if x[0] == "escalation"]
            d["commitments"] = sum(1 for x in ev if x[0] == "said" and x[1].get("what") == "commitment")
            d["disclosures"] = sum(1 for x in ev if x[0] == "disclosure")
            sessions.append(d)
        return {"enrollments": ens, "sessions": sessions,
                "note": "Sessions marked 'simulated' ran against Lucid's test transport, not a meeting platform."}

    def _touch(self, eid: str) -> None:
        self.s.ex("UPDATE avatar_enrollments SET updated_at=? WHERE id=?", (self.s.clock.iso(), eid))
