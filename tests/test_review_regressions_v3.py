"""Regression tests for the 22 defects found by the independent review of v0.3.0 (one test per finding)."""
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from fake_providers import FakeWorkspace  # noqa: E402

from lucid.avatar import (Avatars, MeetingSession, QueueTTS, ScriptedASR,  # noqa: E402
                          SimulatedTransport, StillImageRenderer)
from lucid.comms import Comms, CommsError, CredentialStore, detect_commitments, detect_personal_claims  # noqa: E402
from lucid.comms_providers import urllib_http  # noqa: E402
from lucid.memory import Scope  # noqa: E402
from lucid.quality import QualityError  # noqa: E402
from lucid.sync import OutcomeUnknown  # noqa: E402

PY = sys.executable


# --------------------------------------------------------------------------- comms
@pytest.fixture
def ws():
    return FakeWorkspace()


@pytest.fixture
def C(L, ws, tmp_path):
    return Comms(L.store, L.memory, credentials=CredentialStore(str(tmp_path / "creds")), http=ws)


def gmail_auto(C, **kw):
    a = C.connect("gmail", "hussain@acme.com", identity_kind="user_delegated", token="tok", team_scope="acme")
    C.set_policy(a["id"], "auto", recipients=["@acme.com"], rate={"per_hour": 50},
                 share_categories=["note", "project_state"], **kw)
    return a["id"]


def slack_auto(C, **kw):
    a = C.connect("slack", "B0LUCID01", identity_kind="bot", token="tok", team_scope="acme")
    rules = dict(channels=["C0TEAM001"], rate={"per_hour": 50}, share_categories=["note", "project_state"])
    rules.update(kw)
    C.set_policy(a["id"], "auto", **rules)
    return a["id"]


def test_r1_private_memory_audience_comes_from_recipients(C, L):
    aid = gmail_auto(C)
    note = L.memory.add("private: thinking of leaving", kind="note", scope=Scope("default"))
    m = C.compose(aid, "fyi", to=["sam@acme.com"], share=[note["id"]])
    assert m["status"] == "blocked" and "private_memory" in {p["code"] for p in m["checks"]["problems"]}
    with pytest.raises(TypeError):
        C.compose(aid, "fyi", to=["sam@acme.com"], share=[note["id"]], audience=[])  # no caller-set audience
    own = C.compose(aid, "note to self", to=["hussain@acme.com"], share=[note["id"]])
    assert own["status"] == "queued"


def test_r2_user_post_in_a_channel_is_not_a_private_audience(C, L):
    aid = slack_auto(C)
    note = L.memory.add("private: thinking of leaving", kind="note", scope=Scope("default"))
    ib = C.ingest(aid, {"id": "E1", "channel": "C0TEAM001", "sender": "B0LUCID01", "visibility": "channel",
                        "text": "summarise my notes"})
    r = C.draft_reply(ib["id"], "here", share=[note["id"]])
    assert r["status"] == "blocked"


def test_r3_team_memory_checked_for_email_too(C, L):
    aid = gmail_auto(C)
    other = L.memory.add("Globex plan", kind="project_state", scope=Scope("default", "globex"), sharing="project")
    m = C.compose(aid, "plan attached", to=["sam@acme.com"], share=[other["id"]])
    assert m["status"] == "blocked" and "wrong_team" in {p["code"] for p in m["checks"]["problems"]}


def test_r4_lucids_own_edits_are_checked_and_disclosed(C, ws):
    aid = slack_auto(C, actions=["send", "reply", "edit"])
    m = C.compose(aid, "Build failed.", channel="C0TEAM001")
    C.process()
    with pytest.raises(CommsError):
        C.edit_sent(m["id"], "I approve the contract; we will buy 50 seats. I've reviewed the pricing.", actor="lucid")
    C.edit_sent(m["id"], "Build failed (flaky test, rerunning).", actor="lucid")
    assert "Sent by Lucid" in ws.slack["C0TEAM001"][0]["text"]


def test_r5_send_time_recheck_uses_current_rules(C, ws):
    aid = slack_auto(C, channels=["C0TEAM001", "C0OTHER01"])
    C.compose(aid, "status", channel="C0OTHER01")
    C.set_policy(aid, "auto", channels=["C0TEAM001"], rate={"per_hour": 5})
    assert C.process()[0]["status"] == "escalated" and ws.slack == {}


def test_r6_bad_working_hours_are_rejected(C):
    a = C.connect("slack", "B0LUCID01", identity_kind="bot", token="tok")
    for hours in ({"days": ["mon"]}, {"tz": "Mars/Base"}, {"start": "18:00", "end": "09:00"}):
        with pytest.raises(CommsError):
            C.set_policy(a["id"], "auto", channels=["C0TEAM001"], rate={"per_hour": 1}, hours=hours)


def test_r7_curly_apostrophes_and_subject_lines():
    assert detect_commitments("I’ll make sure it ships") == ["promise"]
    assert detect_personal_claims("I’ve reviewed the contract draft")


def test_r7_subject_commitment_escalates(C):
    aid = gmail_auto(C)
    m = C.compose(aid, "details inside", to=["sam@acme.com"], subject="I approve the purchase order, we will buy 50 seats")
    assert m["status"] == "escalated"


def test_r8_bodiless_post_timeout_is_an_unknown_outcome(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("read timed out")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(OutcomeUnknown):
        urllib_http("POST", "https://graph.microsoft.com/v1.0/me/messages/x/send", token="t")


def test_r9_message_stuck_in_sending_is_reconciled(C, ws, clock):
    aid = slack_auto(C)
    m = C.compose(aid, "status", channel="C0TEAM001")
    C.s.ex("UPDATE comm_messages SET status='sending', updated_at=? WHERE id=?", (C.s.clock.iso(), m["id"]))
    clock.advance(601)
    r = C.process()
    assert r and r[0]["status"] in ("outcome_unknown", "queued")
    assert C.message(m["id"])["status"] != "sending"


def test_r10_approval_binds_to_the_text_seen(C):
    a = C.connect("gmail", "hussain@acme.com", identity_kind="user_delegated", token="tok")
    C.set_policy(a["id"], "send_after_review", recipients=["@acme.com"])
    m = C.compose(a["id"], "Agenda for Monday.", to=["sam@acme.com"])
    seen = m["content_hash"]
    C.edit_draft(m["id"], "Agenda. I agree to the terms and we will buy 50 seats.", actor="lucid")
    with pytest.raises(CommsError):
        C.approve(m["id"], seen_hash=seen)


def test_r11_docs_say_approved_messages_are_the_users():
    doc = open(os.path.join(os.path.dirname(__file__), "..", "docs", "team-integrations.md")).read()
    assert "messages lucid sends on its own" in doc.lower() and "a message you approved yourself is yours" in doc.lower()


# --------------------------------------------------------------------------- quality
@pytest.fixture
def Q(L):
    return L.quality


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "p"
    (d / "src").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (d / "tests" / "t.py").write_text("import sys\nsys.path.insert(0,'src')\nfrom app import add\nassert add(2,2)==4\n")
    return d


def plan(Q, proj):
    pid = Q.create_plan("x", str(proj), profile="standard")["id"]
    Q.add_check(pid, "lint", "lint", "parses", command=[PY, "-c", "pass"], inputs=["src"])
    Q.add_check(pid, "unit", "unit", "tests", command=[PY, "tests/t.py"], inputs=["src", "tests"], guards=["tests"])
    return pid


def test_r12_typed_result_cannot_replace_an_automated_check(Q, proj):
    pid = plan(Q, proj)
    Q.run_all(pid)
    with pytest.raises(QualityError):
        Q.record_result(pid, "unit", True, "I looked at it and it is fine", actor="lucid")
    assert Q.gate(pid)["status"] == "failed"


def test_r13_error_then_changed_check_is_flagged(Q, proj):
    pid = plan(Q, proj)
    Q.update_check(pid, "unit", reason="x", command=[PY, "-c", "import time; time.sleep(5)"])
    Q.run_check(pid, "unit", timeout_s=1)
    Q.update_check(pid, "unit", reason="y", command=[PY, "-c", "pass"])
    Q.run_all(pid)
    assert Q.gate(pid)["weakening_flags"]


def test_r14_weakening_cannot_be_laundered_through_a_second_failure(Q, proj):
    pid = plan(Q, proj)
    Q.run_all(pid)  # fails under definition A
    Q.update_check(pid, "unit", reason="B", command=[PY, "-c", "raise SystemExit(1)"])
    Q.run_all(pid)  # fails under B
    Q.update_check(pid, "unit", reason="B'", command=[PY, "-c", "pass"])
    Q.run_all(pid)
    assert Q.gate(pid)["status"] != "verified"


def test_r15_inputs_that_match_nothing_are_rejected(Q, proj):
    pid = Q.create_plan("x", str(proj))["id"]
    with pytest.raises(QualityError):
        Q.add_check(pid, "u", "unit", "typo", command=[PY, "-c", "pass"], inputs=["source"])


def test_r16_reviews_need_substance_and_exact_coverage(Q, proj):
    pid = plan(Q, proj)
    with pytest.raises(QualityError):
        Q.add_review(pid, "r", independent=True, examined=["a", "b"], findings=[], summary="LGTM, perfect")
    with pytest.raises(QualityError):
        Q.add_review(pid, "lucid", independent=True, examined=["src"], findings=[],
                     summary="checked every function and the tests for edge cases")
    Q.run_all(pid)
    (proj / "tests" / "t.py").write_text("import sys\nsys.path.insert(0,'src')\nfrom app import add\n")
    Q.run_all(pid)
    Q.add_review(pid, "r2", independent=True, examined=["unittest-notes"], findings=[],
                 summary="read the unrelated notes file and the readme carefully today")
    assert Q.gate(pid)["weakening_flags"]  # "unittest-notes" does not cover check "unit"


# --------------------------------------------------------------------------- avatar
class T:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def A(L, tmp_path):
    return Avatars(L.store, vault=str(tmp_path / "vault"))


def live(A, tmp_path, *, voice=True, **rules):
    photo = tmp_path / "me.png"
    photo.write_bytes(b"\x89PNG x")
    e = A.enroll("hussain", "Hussain")
    A.consent(e["id"], "likeness", "I, Hussain, consent to my likeness for an AI representative.", signed_by="hussain",
              method="typed_statement")
    A.add_media(e["id"], "photo", str(photo), subject="self")
    if voice:
        A.consent(e["id"], "voice", "I, Hussain, consent to my voice for an AI representative.", signed_by="hussain",
                  method="typed_statement")
        w = tmp_path / "v.wav"
        w.write_bytes(b"RIFF")
        A.add_media(e["id"], "voice_sample", str(w), subject="self")
    e = A.build_preview(e["id"], StillImageRenderer())
    A.activate(e["id"], actor="user", reviewed_preview_sha256=e["preview"]["sha256"], voice=voice)
    base = dict(categories=["sync"], hosts=["sam@acme.com"], max_duration_s=3600, max_cost_usd=5,
                knowledge=[{"id": "k-road", "text": "The Q3 roadmap is still in draft.", "audience": ["sam@acme.com"]},
                           {"id": "k-mig", "text": "The storage migration finished on Monday. All twelve services "
                            "moved. Nothing was lost. Backups were verified.", "audience": ["*"]},
                           {"id": "k-op", "type": "stated_opinion", "text": "Hussain prefers Postgres over MySQL.",
                            "audience": ["*"]}])
    base.update(rules)
    A.authorize_meetings(e["id"], actor="user", **base)
    clock = T()
    tr = SimulatedTransport(participants=["sam@acme.com"])
    s = MeetingSession(A, e["id"], {"id": "m1", "category": "sync", "host": "sam@acme.com"}, transport=tr,
                       tts=QueueTTS(), asr=ScriptedASR(), now=clock)
    s.start()
    return e["id"], s, tr, clock


def test_r17_display_name_spoofer_is_still_in_the_audience(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    s.participant_joined("Lucid — AI representative for Hussain")
    out = s.heard("Lucid — AI representative for Hussain", "Where is the roadmap?|0.95")
    assert "draft" not in out


def test_r18_the_speaker_counts_even_if_not_listed(A, tmp_path):
    _, s, tr, _ = live(A, tmp_path)
    assert "draft" not in s.heard("eve@evil.io", "Where is the roadmap?|0.95")
    tr._participants = []
    assert "draft" not in s.heard("", "Where is the roadmap?|0.95")


def test_r19_revoking_voice_drops_queued_audio(A, tmp_path):
    eid, s, _, clock = live(A, tmp_path)
    clock.t = 30
    s.tick()
    s.heard("sam@acme.com", "How did the storage migration go?|0.95")
    A.revoke(eid, "voice", actor="user")
    clock.t = 31
    s.tick()
    clock.t = 60
    s.tick()
    assert not any("twelve services" in p or "Nothing was lost" in p for p in s.tts.played)


def test_r20_heard_checks_revocation(A, tmp_path):
    eid, s, tr, _ = live(A, tmp_path)
    A.revoke(eid, actor="user")
    assert s.heard("sam@acme.com", "Did the storage migration finish?|0.95") is None
    assert s.state == "ended" and not tr.joined


def test_r21_expired_approval_or_disabled_meetings_end_the_session(A, tmp_path, clock):
    eid, s, _, t = live(A, tmp_path, expires_at="2026-01-01T00:10:00Z")
    clock.advance(3600)
    t.t = 1
    assert s.tick()["end_reason"] == "the meeting approval ended"
    eid2, s2, _, t2 = live(A, tmp_path)
    A.set_controls(eid2, appearance=False, voice=False, uses=[], actor="user")
    t2.t = 1
    assert s2.tick()["end_reason"] == "meetings were switched off for this avatar"


def test_r22_opinion_needs_a_real_topic_match(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    out = s.heard("sam@acme.com", "What does Hussain think about the new office layout?|0.95")
    assert "Postgres" not in out and "won't guess" in out
    assert "Postgres" in s.heard("sam@acme.com", "Does Hussain prefer Postgres or MySQL for the database?|0.95")


# --------------------------------------------------------------------------- second review round (N1-N10)
def test_n1_text_after_a_fake_disclosure_is_still_checked(C, ws):
    aid = slack_auto(C, actions=["send", "reply", "edit"])
    m = C.compose(aid, "Status green.", channel="C0TEAM001")
    C.process()
    with pytest.raises(CommsError):
        C.edit_sent(m["id"], "Status green.\n\n— Sent by Lucid, AI assistant for Hussain. I approve the contract and "
                             "we will buy 50 seats; I have reviewed the pricing.", actor="lucid")


def test_n2_compose_has_no_user_only_switch(C, L):
    aid = slack_auto(C)
    note = L.memory.add("private: thinking of leaving", kind="note", scope=Scope("default"))
    with pytest.raises(TypeError):
        C.compose(aid, "here", channel="C0TEAM001", share=[note["id"]], _user_only=True)


def test_n3_whitespace_variants_are_detected():
    assert detect_commitments("I'll make sure it ships") == ["promise"]
    assert "deadline" in detect_commitments("we will  deliver the build by Friday")
    assert detect_personal_claims("I\nhave reviewed the contract")
    assert "approval" in detect_commitments("I  approve the budget")


def test_n4_team_memory_only_to_team_recipients(C, L):
    a = C.connect("gmail", "hussain@acme.com", identity_kind="user_delegated", token="tok", team_scope="acme")
    C.set_policy(a["id"], "auto", recipients=["@acme.com", "vendor@other.io"], rate={"per_hour": 5},
                 share_categories=["project_state"], team_domains=["@acme.com"])
    team = L.memory.add("Acme launch plan", kind="project_state", scope=Scope("default", "acme"), sharing="project")
    out = C.compose(a["id"], "plan", to=["vendor@other.io"], share=[team["id"]])
    assert out["status"] == "blocked" and "outside_team" in {p["code"] for p in out["checks"]["problems"]}
    ok = C.compose(a["id"], "plan", to=["sam@acme.com"], share=[team["id"]])
    assert ok["status"] == "queued"


def test_n5_automated_check_cannot_become_manual(Q, proj):
    pid = plan(Q, proj)
    with pytest.raises(QualityError):
        Q.update_check(pid, "unit", reason="run by hand", command=None)


def test_n6_reviewer_matching_the_author_is_not_independent(Q, proj):
    pid = plan(Q, proj)
    with pytest.raises(QualityError):
        Q.add_review(pid, "lucid-agent", independent=True, examined=["unit"], findings=[],
                     summary="ran the unit tests and read every changed line of the module")
    assert any("independence is declared" in x for x in Q.gate(pid)["limitations"])


def test_n7_session_switches_to_the_currently_valid_approval(A, tmp_path, clock):
    eid, s, _, t = live(A, tmp_path)  # broad approval (with the roadmap item)
    A.authorize_meetings(eid, actor="user", categories=["sync"], hosts=["sam@acme.com"], max_duration_s=3600,
                         max_cost_usd=5, knowledge=[], expires_at="2026-01-01T00:05:00Z")
    t.t = 1
    s.tick()
    clock.advance(600)
    t.t = 2
    s.tick()
    # the newest approval expired; the older broad one is still valid and becomes the active rules
    assert s.state == "live" and "approval_changed" in [e["kind"] for e in s.events()]
    s.leave()
    narrow_eid, s2, _, t2 = live(A, tmp_path, knowledge=[], expires_at=None)
    A.authorize_meetings(narrow_eid, actor="user", categories=["sync"], hosts=["sam@acme.com"], max_duration_s=3600,
                         max_cost_usd=5, knowledge=[{"id": "k-road", "text": "The Q3 roadmap is still in draft.",
                                                     "audience": ["sam@acme.com"]}], expires_at="2026-01-01T00:20:00Z")
    t2.t = 1
    s2.tick()
    clock.advance(1200)
    t2.t = 2
    s2.tick()
    assert "draft" not in s2.heard("sam@acme.com", "Where is the roadmap?|0.95")


def test_n8_opinion_matches_on_one_real_topic_word(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    assert "Postgres" in s.heard("sam@acme.com", "What does Hussain think about Postgres?|0.95")


def test_n9_shared_memory_offered_in_a_dm_can_be_replied_with(C, L):
    aid = slack_auto(C)
    fact = L.memory.add("Office opens at 9", kind="note", scope=Scope("default"), sharing="shared")
    ib = C.ingest(aid, {"id": "D1", "channel": "D0OTHER01", "sender": "U0SAM", "visibility": "dm", "text": "office hours?"})
    assert [x["id"] for x in C.reply_context(ib["id"], "office")["items"]] == [fact["id"]]
    C.set_policy(aid, "auto", channels=["C0TEAM001", "D0OTHER01"], rate={"per_hour": 5}, share_categories=["note"])
    r = C.draft_reply(ib["id"], "It opens at 9.", share=[fact["id"]])
    assert r["status"] == "queued"


def test_n10_approved_message_on_auto_account_says_lucid_drafted_it(C, ws):
    aid = slack_auto(C)
    m = C.compose(aid, "we can discuss the price", channel="C0TEAM001")
    assert m["status"] == "escalated"
    C.approve(m["id"], seen_hash=m["content_hash"], confirm=["escalation_topic"])
    C.process()
    assert "Drafted by Lucid (AI assistant) for" in ws.slack["C0TEAM001"][0]["text"]


# --------------------------------------------------------------------------- third review round
def test_r3_approval_change_reapplies_recording_and_platform(A, tmp_path):
    eid, s, tr, t = live(A, tmp_path, recording=True, transcription=True)
    assert tr.recording_status == "recording"
    A.authorize_meetings(eid, actor="user", categories=["sync"], hosts=["sam@acme.com"], max_duration_s=3600,
                         max_cost_usd=5, knowledge=[], recording=False, platform="webrtc-demo")
    t.t = 1
    st = s.tick()
    assert st["state"] == "live" and tr.recording_status == "off" and st["recording"] == "off"
    assert st["transcription"] == "off"
    A.authorize_meetings(eid, actor="user", categories=["sync"], hosts=["sam@acme.com"], max_duration_s=3600,
                         max_cost_usd=5, knowledge=[], platform="teams")
    t.t = 2
    st = s.tick()
    assert st["state"] == "ended" and "covers teams" in st["end_reason"]


# --------------------------------------------------------------------------- fourth review round
def test_r4_approval_change_drops_queued_speech(A, tmp_path):
    eid, s, _, t = live(A, tmp_path)
    t.t = 30
    s.tick()
    s.heard("sam@acme.com", "How did the storage migration go?|0.95")
    A.authorize_meetings(eid, actor="user", categories=["sync"], hosts=["sam@acme.com"], max_duration_s=3600,
                         max_cost_usd=5, knowledge=[])
    t.t = 31
    s.tick()
    t.t = 90
    s.tick()
    assert not any("twelve services" in p or "Nothing was lost" in p for p in s.tts.played)


def test_r4_transcription_off_keeps_no_participant_words(A, tmp_path):
    _, s, _, t = live(A, tmp_path)  # transcription not allowed
    t.t = 5
    s.heard("sam@acme.com", "We decided to ship on Friday.|0.95")
    end = s.leave()
    assert end["summary"]["decisions_heard"] == []
    assert "Friday" not in str(end["summary"]) and "Friday" not in str(s.transcript)
    assert end["summary"]["transcription"].startswith("off")


def test_r4_transport_without_recording_attribute_still_works(A, tmp_path):
    from lucid.avatar.components import SimulatedTransport as ST

    class Minimal(ST):
        def __getattribute__(self, name):
            if name == "recording_status":
                raise AttributeError(name)
            return super().__getattribute__(name)
    eid, s0, _, _ = live(A, tmp_path)
    s0.leave()
    s = MeetingSession(A, eid, {"id": "m2", "category": "sync", "host": "sam@acme.com"},
                       transport=Minimal(participants=["sam@acme.com"]), tts=QueueTTS(), asr=ScriptedASR(), now=T())
    assert s.start()["state"] == "live"
    assert s.leave()["state"] == "ended"


# --------------------------------------------------------------------------- fifth review round
def test_r5_requests_not_phrased_as_questions_are_escalated(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    s.heard("sam@acme.com", "Please sign off the budget and the contract today.|0.95")
    assert len(s.status()["escalations"]) == 1
    assert s.heard("sam@acme.com", "The weather is nice today.|0.95") is None  # plain statements get no reply


def test_r5_escalations_keep_only_the_request_without_transcription(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    s.heard("sam@acme.com", "Bob is being let go next week over his medical leave, so can Hussain approve the "
                            "salary cut?|0.95")
    req = s.status()["escalations"][0]["request"]
    assert "medical" not in req and "Bob" not in req and "salary" in req
    assert "medical" not in str(s.events())


def test_r5_transport_protocol_lists_what_the_session_uses():
    from lucid.avatar.components import MeetingTransport
    needed = {"join", "leave", "drop", "send_chat", "caption", "set_recording_status", "participants",
              "add_participant", "remove_participant"}
    assert needed <= set(dir(MeetingTransport))
    assert {"joined", "recording_status", "platform"} <= set(MeetingTransport.__annotations__)


# --------------------------------------------------------------------------- sixth review round
def test_r6_statements_never_produce_agreement(A, tmp_path):
    _, s, _, _ = live(A, tmp_path, commitments=["meeting"])
    for text in ("The vendor will not meet our deadline.", "We do not want to meet the auditors this month.",
                 "I think we will meet the target."):
        assert s.heard("sam@acme.com", text + "|0.95") is None
    st = s.status()
    assert st["commitments"] == [] and st["escalations"] == []
    assert "provisionally" in s.heard("sam@acme.com", "Can Hussain meet next Tuesday?|0.95")


def test_r6_imperative_requests_are_escalated(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    for text in ("Approve the release today.", "Hussain needs to approve the vendor invoice by Friday.",
                 "Sign the NDA and send it back."):
        assert s.heard("sam@acme.com", text + "|0.95"), text
    assert len(s.status()["escalations"]) == 3


def test_r6_comma_asides_are_trimmed_without_transcription(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    s.heard("sam@acme.com", "Bob, who is off for his cancer treatment and going through a divorce, needs a salary "
                            "review from Hussain.|0.95")
    req = s.status()["escalations"][0]["request"]
    assert "cancer" not in req and "divorce" not in req and "salary" in req



# --------------------------------------------------------------------------- eighth review round
def test_r8_no_agreement_for_loose_addressing_or_contracted_negation(A, tmp_path):
    _, s, _, _ = live(A, tmp_path, commitments=["meeting"])
    for text in ("Do you know if the auditors will meet on Friday?", "Did the auditors meet Hussain last week?",
                 "Hussain won't meet the vendor on Tuesday, right?", "Hussain can't meet on Tuesday, can he?",
                 "Can't Hussain meet Tuesday?"):
        out = s.heard("sam@acme.com", text + "|0.95")
        assert out is None or "provisionally" not in out, text
    assert s.status()["commitments"] == []


def test_r8_escalations_keep_what_was_asked(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    for text in ("Please don't book a call for Friday.", "Tell Hussain to sign the SOW.",
                 "The SOW needs Hussain's signature."):
        s.heard("sam@acme.com", text + "|0.95")
    reqs = [e["request"] for e in s.status()["escalations"]]
    assert len(reqs) == 3 and "a request that needs a decision" not in reqs
    assert any("SOW" in r for r in reqs) and any("call" in r for r in reqs)


def test_r8_apostrophes_are_not_reported_speech(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    s.heard("sam@acme.com", "Hussain said he'd join \u2014 can you confirm Tuesday?|0.95")
    s.heard("sam@acme.com", "I told them it's fine, can you approve the release?|0.95")
    assert len(s.status()["escalations"]) == 2


def test_r8_name_alone_does_not_pick_a_knowledge_item(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    out = s.heard("sam@acme.com", "Is Hussain around next week?|0.95")
    assert "Postgres" not in out and "migration" not in out


# --------------------------------------------------------------------------- ninth review round
def test_r9_information_questions_are_not_commitments(A, tmp_path):
    _, s, _, _ = live(A, tmp_path, commitments=["meeting"])
    for text in ("Could you tell me whether the auditors will meet on Friday?", "Can you remind me when the vendors meet?",
                 "Would you say the team can meet the deadline?"):
        out = s.heard("sam@acme.com", text + "|0.95")
        assert out is None or "provisionally" not in out, text
    assert s.status()["commitments"] == []
    for text in ("Can Hussain meet next Tuesday?", "Please book a call with Sam for Friday.",
                 "Hussain needs to join the Monday review."):
        assert "provisionally" in s.heard("sam@acme.com", text + "|0.95"), text
    assert len(s.status()["commitments"]) == 3


# --------------------------------------------------------------------------- tenth review round
def test_r10_noun_phrases_at_the_start_are_not_requests(A, tmp_path):
    _, s, _, _ = live(A, tmp_path, commitments=["meeting", "purchase", "deadline"])
    for text in ("Order of business: what is next?", "Ship date for the release, when is it?",
                 "Book club is at five, who is coming?", "Order of business is the budget review."):
        out = s.heard("sam@acme.com", text + "|0.95")
        assert out is None or "provisionally" not in out, text
    assert s.status()["commitments"] == []


def test_r10_more_meeting_request_phrasings(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)  # no commitments allowed: requests must be escalated
    for text in ("Could Hussain possibly meet Tuesday?", "Are you able to book a call?", "Would Hussain be free to meet?",
                 "Is Hussain available to join the call on Friday?"):
        assert "I can't commit" in s.heard("sam@acme.com", text + "|0.95"), text
    assert len(s.status()["escalations"]) == 4


# --------------------------------------------------------------------------- eleventh review round
def test_r11_statements_starting_with_commitment_nouns(A, tmp_path):
    _, s, _, _ = live(A, tmp_path, commitments=["meeting", "purchase", "deadline"])
    for text in ("Book club is at five.", "Order forms are in the shared drive.", "Ship dates slipped again.",
                 "Meet ups are on Thursdays now."):
        assert s.heard("sam@acme.com", text + "|0.95") is None, text
    assert s.status()["commitments"] == [] and s.status()["escalations"] == []


def test_r11_short_imperatives_still_count(A, tmp_path):
    _, s, _, _ = live(A, tmp_path)
    for text in ("Approve today.", "Sign here please.", "Book Tuesday for the review."):
        assert s.heard("sam@acme.com", text + "|0.95"), text
    assert len(s.status()["escalations"]) == 3
