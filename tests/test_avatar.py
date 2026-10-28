"""Avatar acceptance tests. Every call here is SIMULATED: a scripted transport, recogniser and speech
queue. They test Lucid's rules, not a meeting platform or an avatar vendor."""
import os

import pytest

from lucid.avatar import (AvatarError, Avatars, MeetingSession, QueueTTS, ScriptedASR, SimulatedTransport,
                          StillImageRenderer)
from lucid.comms import Comms, CredentialStore

LIKENESS = "I, Hussain, consent to Lucid using my likeness to create an AI representative of me."
VOICE = "I, Hussain, consent to Lucid using my voice for an AI representative of me."


class T:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def A(L, tmp_path):
    return Avatars(L.store, vault=str(tmp_path / "vault"))


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "me.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake-but-local")
    return str(p)


def active(A, photo, tmp_path, *, voice=True):
    e = A.enroll("hussain", "Hussain")
    A.consent(e["id"], "likeness", LIKENESS, signed_by="hussain", method="typed_statement")
    A.add_media(e["id"], "photo", photo, subject="self")
    if voice:
        A.consent(e["id"], "voice", VOICE, signed_by="hussain", method="typed_statement")
        wav = tmp_path / "v.wav"
        wav.write_bytes(b"RIFF....WAVE")
        A.add_media(e["id"], "voice_sample", str(wav), subject="self")
    e = A.build_preview(e["id"], StillImageRenderer())
    A.activate(e["id"], actor="user", reviewed_preview_sha256=e["preview"]["sha256"], voice=voice)
    return e["id"]


def auth(A, eid, **kw):
    rules = dict(categories=["weekly-sync"], hosts=["sam@acme.com"], max_duration_s=1800, max_cost_usd=2.0,
                 knowledge=[{"id": "k-status", "text": "The storage migration finished on Monday.", "audience": ["@acme.com"]},
                            {"id": "k-roadmap", "text": "The Q3 roadmap is still in draft.", "audience": ["sam@acme.com"]}],
                 platform="webrtc-demo")
    rules.update(kw)
    return A.authorize_meetings(eid, actor="user", **rules)


def session(A, eid, *, participants=("sam@acme.com",), meeting=None, **kw):
    clock = T()
    tr = SimulatedTransport(participants=list(participants), **kw)
    m = meeting or {"id": "mtg-1", "category": "weekly-sync", "host": "sam@acme.com", "title": "Weekly sync"}
    return MeetingSession(A, eid, m, transport=tr, tts=QueueTTS(), asr=ScriptedASR(), now=clock), tr, clock


# --------------------------------------------------------------------------- enrollment
def test_enrollment_requires_own_consent_and_media(A, photo, tmp_path):
    e = A.enroll("hussain", "Hussain")
    with pytest.raises(AvatarError):
        A.add_media(e["id"], "photo", photo, subject="self")  # no consent yet
    with pytest.raises(AvatarError):
        A.consent(e["id"], "likeness", LIKENESS, signed_by="someone-else", method="typed_statement")
    with pytest.raises(AvatarError):
        A.consent(e["id"], "likeness", "ok", signed_by="hussain", method="typed_statement")
    A.consent(e["id"], "likeness", LIKENESS, signed_by="hussain", method="typed_statement")
    with pytest.raises(AvatarError):
        A.add_media(e["id"], "photo", photo, subject="my colleague")  # possession is not permission
    m = A.add_media(e["id"], "photo", photo, subject="self")
    stored = A.media(e["id"])[0]["path"]
    assert oct(os.stat(stored).st_mode & 0o777) == "0o600"
    assert oct(os.stat(os.path.dirname(stored)).st_mode & 0o777) == "0o700"
    with pytest.raises(AvatarError):
        A.add_media(e["id"], "voice_sample", photo, subject="self")  # voice needs its own consent
    assert m["kind"] == "photo"


def test_vendor_consent_video_requirement(A, tmp_path):
    e = A.enroll("hussain", "Hussain", provider="heygen")
    with pytest.raises(AvatarError):
        A.consent(e["id"], "likeness", LIKENESS, signed_by="hussain", method="typed_statement")
    vid = tmp_path / "consent.mp4"
    vid.write_bytes(b"....")
    cv = A.add_media(e["id"], "consent_video", str(vid), subject="self")
    A.consent(e["id"], "likeness", LIKENESS, signed_by="hussain", method="consent_video", evidence=cv["id"])


def test_preview_must_be_reviewed_before_activation(A, photo, L):
    e = A.enroll("hussain", "Hussain")
    A.consent(e["id"], "likeness", LIKENESS, signed_by="hussain", method="typed_statement")
    A.add_media(e["id"], "photo", photo, subject="self")
    with pytest.raises(AvatarError):
        A.activate(e["id"], actor="user", reviewed_preview_sha256="x")
    e = A.build_preview(e["id"], StillImageRenderer())
    assert "AI representative for Hussain" in open(e["preview"]["path"]).read()
    with pytest.raises(AvatarError):
        A.activate(e["id"], actor="lucid", reviewed_preview_sha256=e["preview"]["sha256"])
    with pytest.raises(AvatarError):
        A.activate(e["id"], actor="user", reviewed_preview_sha256=e["preview"]["sha256"], voice=True)  # no voice consent
    A.activate(e["id"], actor="user", reviewed_preview_sha256=e["preview"]["sha256"])
    assert A.get(e["id"])["status"] == "active"
    dump = "\n".join(L.store.conn.iterdump())
    assert "fake-but-local" not in dump  # media bytes never enter the database or memory


# --------------------------------------------------------------------------- authorization
def test_only_approved_meetings_with_approved_hosts(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    s, _, _ = session(A, eid)
    with pytest.raises(AvatarError):
        s.start()  # nothing authorized yet; an invitation alone is not approval
    auth(A, eid)
    for meeting in ({"id": "mtg-2", "category": "board", "host": "sam@acme.com"},
                    {"id": "mtg-3", "category": "weekly-sync", "host": "stranger@x.io"}):
        s2, _, _ = session(A, eid, meeting=meeting)
        with pytest.raises(AvatarError):
            s2.start()
    s3, _, _ = session(A, eid, platform="slack-huddle")
    with pytest.raises(AvatarError):
        s3.start()  # approved for the WebRTC demo only


def test_disclosure_at_entry_and_for_late_participants(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s, tr, clock = session(A, eid)
    s.start()
    assert tr.display_name == "Lucid — AI representative for Hussain"
    assert "AI representative" in tr.chat[0][1] and tr.chat[0][0] is None and "not here in person" in tr.chat[0][1]
    clock.t = 120
    s.participant_joined("kim@acme.com")
    assert tr.chat[-1][0] == "kim@acme.com" and "AI representative for Hussain" in tr.chat[-1][1]
    assert [e["kind"] for e in s.events()].count("disclosure") == 2


def test_duplicate_join_is_refused(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s1, _, _ = session(A, eid)
    s1.start()
    s2, tr2, _ = session(A, eid)
    with pytest.raises(AvatarError):
        s2.start()
    assert tr2.joins == 0


# --------------------------------------------------------------------------- live behaviour
def test_answers_only_from_knowledge_allowed_for_this_audience(A, photo, tmp_path, L):
    L.memory.add("Hussain is interviewing with another company", kind="note")  # private memory: never used
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s, tr, clock = session(A, eid)
    s.start()
    assert "storage migration finished" in s.heard("sam@acme.com", "Did the storage migration finish?|0.95")
    assert "roadmap is still in draft" in s.heard("sam@acme.com", "Where is the roadmap?|0.95")
    s.participant_joined("kim@acme.com")
    out = s.heard("kim@acme.com", "Where is the roadmap?|0.95")
    assert "draft" not in out and "I don't have that information" in out  # kim is not cleared for it
    s.participant_joined("vendor@other.io")
    assert "I don't have that information" in s.heard("vendor@other.io", "Did the storage migration finish?|0.9")
    out = s.heard("sam@acme.com", "Is Hussain interviewing somewhere?|0.95")
    assert "interviewing" not in out
    assert s.status()["information_accessed"] == ["k-status", "k-roadmap"]


def test_uncertain_speech_recognition_asks_again(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s, tr, _ = session(A, eid)
    s.start()
    out = s.heard("sam@acme.com", "did the stor mig fin|0.31")
    assert out.startswith("Sorry, I didn't catch that")
    assert s.status()["information_accessed"] == []


def test_unauthorized_commitments_and_opinions_are_not_given(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid, commitments=["meeting"])
    s, _, _ = session(A, eid)
    s.start()
    for q in ("Can Hussain approve the new vendor?|0.95", "Will you sign the contract today?|0.95",
              "Can you promise delivery by Friday?|0.95", "Can we agree on the budget now?|0.95"):
        out = s.heard("sam@acme.com", q)
        assert "confirm" in out or "pass it to" in out, q
    assert len(s.status()["escalations"]) == 4 and s.status()["commitments"] == []
    ok = s.heard("sam@acme.com", "Can Hussain meet next Tuesday?|0.95")
    c = s.status()["commitments"][0]
    assert "provisionally" in ok and "will confirm" in ok
    assert c["type"] == "meeting" and c["status"].startswith("provisional")
    op = s.heard("sam@acme.com", "What does Hussain think about the redesign?|0.95")
    assert "won't guess" in op


def test_interruption_and_takeover_stop_speech_and_drop_stale_audio(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid, knowledge=[{"id": "k-long", "text": "The migration finished on Monday. It moved four databases. "
                             "Nothing was lost. Backups were verified twice.", "audience": ["*"]}])
    s, tr, clock = session(A, eid)
    s.start()
    clock.t = 30
    s.tick()  # disclosure plays out
    s.heard("sam@acme.com", "How did the migration go?|0.95")
    assert s.tts.speaking
    clock.t = 31
    s.tick()
    s.speech_started("sam@acme.com")  # sam talks over Lucid
    assert not s.tts.speaking and s.tts.dropped
    clock.t = 60
    s.tick()
    assert not any("Backups were verified" in p for p in s.tts.played)  # stale audio never played
    s.takeover()
    assert s.heard("sam@acme.com", "Did the migration finish?|0.95") is None
    with pytest.raises(AvatarError):
        s.resume(actor="lucid")
    s.resume(actor="user")
    assert s.heard("sam@acme.com", "Did the migration finish?|0.95")
    s.mute()
    assert s.heard("sam@acme.com", "Did the migration finish?|0.95") is None and not s.tts.speaking


def test_voice_is_off_without_voice_consent(A, photo, tmp_path):
    eid = active(A, photo, tmp_path, voice=False)
    auth(A, eid)
    s, tr, _ = session(A, eid)
    s.start()
    s.heard("sam@acme.com", "Did the storage migration finish?|0.95")
    assert s.tts.chars == 0 and len(tr.captions) >= 2  # captions and chat only


def test_network_loss_rejoins_and_discloses_again(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s, tr, clock = session(A, eid)
    s.start()
    s.network_lost()
    assert s.state == "reconnecting" and not s.tts.speaking
    tr.fail_joins = 1
    clock.t = 3
    s.tick()
    assert s.state == "reconnecting"
    clock.t = 8
    s.tick()
    assert s.state == "live" and [e["kind"] for e in s.events()].count("disclosure") == 2
    s.network_lost()
    tr.fail_joins = 5
    for t in (11, 16, 25, 40):
        clock.t = t
        st = s.tick()
        if st["state"] == "ended":
            break
    assert st["state"] == "ended" and st["end_reason"] == "network lost; could not rejoin"
    assert st["summary"]["escalations"][-1]["request"] == "lost the meeting connection"


def test_cost_and_time_limits_end_the_session(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid, max_cost_usd=0.15)
    s, tr, clock = session(A, eid)
    s.start()
    clock.t = 60
    assert s.tick()["state"] == "live"
    clock.t = 120
    st = s.tick()
    assert st["state"] == "ended" and st["end_reason"] == "cost limit reached"
    assert "I need to leave now" in tr.chat[-1][1] and not tr.joined
    assert set(st["costs"]) >= {"avatar", "speech_in", "speech_out"}
    eid2 = active(A, photo, tmp_path)
    auth(A, eid2, max_duration_s=90)
    s2, _, c2 = session(A, eid2, meeting={"id": "mtg-9", "category": "weekly-sync", "host": "sam@acme.com"})
    s2.start()
    c2.t = 95
    assert s2.tick()["end_reason"] == "time limit reached"


def test_recording_needs_permission_and_a_visible_notice(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s, tr, _ = session(A, eid)
    s.start()
    assert tr.recording_status == "off" and s.status()["recording"] == "off"
    s.leave()
    auth(A, eid, recording=True, transcription=True)
    s2, tr2, _ = session(A, eid, meeting={"id": "mtg-r", "category": "weekly-sync", "host": "sam@acme.com"})
    s2.start()
    assert tr2.recording_status == "recording" and s2.status()["recording"] == "on"
    end = s2.leave()
    assert tr2.recording_status == "off" and end["recording"] == "off"
    s3, tr3, _ = session(A, eid, meeting={"id": "mtg-s", "category": "weekly-sync", "host": "sam@acme.com"},
                         recording_supported=False)
    s3.start()
    assert s3.status()["recording"] == "off"
    assert "recording_unavailable" in [e["kind"] for e in s3.events()]


def test_revoking_consent_stops_a_live_session(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    auth(A, eid)
    s, tr, clock = session(A, eid)
    s.start()
    A.revoke(eid, actor="user")
    clock.t = 1
    st = s.tick()
    assert st["state"] == "ended" and st["end_reason"] == "avatar consent was revoked" and not tr.joined
    s2, _, _ = session(A, eid)
    with pytest.raises(AvatarError):
        s2.start()


def test_deletion_report_and_retention(A, photo, tmp_path):
    eid = active(A, photo, tmp_path)
    paths = [m["path"] for m in A.media(eid)]
    rep = A.delete(eid, actor="user")
    assert all(not os.path.exists(p) for p in paths)
    assert {r["status"] for r in rep["report"]} == {"deleted"}
    assert A.get(eid)["status"] == "deleted"

    class Vendor:
        def delete(self, item):
            if item == "generated videos":
                raise RuntimeError("HTTP 500")
            return "deleted"
    e2 = A.enroll("hussain", "Hussain", provider="heygen")
    assert "not uploaded by Lucid" in A.get(e2["id"])["retention"]["provider"]
    rep2 = A.delete(e2["id"], actor="user", provider_client=Vendor())
    by = {r["item"]: r["status"] for r in rep2["report"]}
    assert by["heygen avatar"] == "deleted" and by["heygen generated videos"] == "failed"


def test_summary_is_evidence_based_and_followups_are_drafts(A, photo, tmp_path, L, clock):
    C = Comms(L.store, L.memory, credentials=CredentialStore(str(tmp_path / "c")), http=lambda *a, **k: None)
    acct = C.connect("gmail", "hussain@acme.com", identity_kind="user_delegated", token="x")
    C.set_policy(acct["id"], "auto", recipients=["hussain@acme.com"], rate={"per_hour": 5})
    eid = active(A, photo, tmp_path)
    auth(A, eid, follow_up={"account_id": acct["id"], "to": ["hussain@acme.com"]}, transcription=True)
    s, _, t = session(A, eid)
    s.comms = C
    s.start()
    t.t = 10
    s.heard("sam@acme.com", "We decided to ship on the 12th.|0.95")
    s.heard("sam@acme.com", "Can Hussain approve the budget?|0.95")
    end = s.leave()
    sm = end["summary"]
    assert sm["decisions_heard"] == [{"t": 10.0, "by": "sam@acme.com", "quote": "We decided to ship on the 12th."}]
    assert sm["escalations"][0]["request"] == "Can Hussain approve the budget?"
    draft = C.message(sm["follow_up_drafts"][0])
    assert draft["status"] == "draft"  # even on an auto account, meeting follow-ups wait for the user
