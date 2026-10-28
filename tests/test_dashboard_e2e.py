"""Browser tests of key dashboard workflows (Playwright + Chromium). Skipped if Playwright is unavailable."""
import socket
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")

from lucid.conversations import NormalizedConversation, NormalizedMessage  # noqa: E402
from lucid.core import Lucid  # noqa: E402
from lucid.server.app import _hash, _web_dir, create_app  # noqa: E402
from lucid.server.demo import seed_demo  # noqa: E402

TOKEN = "e2e-token"
pytestmark = pytest.mark.skipif(_web_dir() is None, reason="dashboard not built")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    L = Lucid(str(tmp_path_factory.mktemp("e2e") / "e2e.db"))
    seed_demo(L)
    L.archive.ingest(NormalizedConversation(provider="evil", external_id="xss", title="xss test", messages=[
        NormalizedMessage("1", "user", "<img src=x onerror=\"window.__xss=1\"><script>window.__xss=2</script>hello")]),
        method="test")
    app = create_app(L, token_map={_hash(TOKEN): "default"}, allowed_hosts=["127.0.0.1", "localhost"], demo=True)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", L
    srv.should_exit = True
    th.join(5)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_unauthorized_then_login(server, browser):
    url, _ = server
    page = browser.new_page()
    page.goto(url + "/")
    page.get_by_label("Access token").fill("wrong")
    page.get_by_role("button", name="Continue").click()
    page.get_by_test_id("error-state").first.wait_for()
    assert "Not authorised" in page.get_by_test_id("error-state").first.inner_text()
    page.close()


def test_overview_demo_banner_and_matrix(server, browser):
    url, _ = server
    page = browser.new_page()
    page.goto(f"{url}/#token={TOKEN}")
    page.get_by_role("note").wait_for()
    assert "DEMO DATA" in page.get_by_role("note").inner_text()
    page.get_by_test_id("health").wait_for()
    assert page.locator("table.matrix").inner_text().count("supported") > 10
    assert "#token" not in page.url  # token removed from the address bar
    page.close()


def test_memory_search_provenance_and_conflict_resolution(server, browser):
    url, L = server
    page = browser.new_page()
    page.goto(f"{url}/?tab=memory#token={TOKEN}")
    page.get_by_label("Search memories").fill("rate limit")
    page.get_by_role("button", name="Search", exact=True).click()
    page.get_by_test_id("memory-table").get_by_text("rate limit is 100").click()
    detail = page.get_by_test_id("memory-detail")
    detail.wait_for()
    assert "https://example.com/demo" in detail.inner_text() and "verified" in detail.inner_text()
    page.get_by_role("button", name="Keep: [DEMO DATA] User prefers pytest").click()
    page.wait_for_function("() => !document.body.innerText.includes('Keep: [DEMO DATA] User prefers pytest')")
    assert L.store.one("SELECT COUNT(*) FROM memory_conflicts WHERE status='open'")[0] == 0
    page.close()


def test_conversation_branches_render_as_text(server, browser):
    url, _ = server
    page = browser.new_page()
    page.goto(f"{url}/?tab=conversations#token={TOKEN}")
    page.get_by_text("[DEMO DATA] branching chat").click()
    tree = page.get_by_test_id("conversation-tree")
    tree.wait_for()
    assert "2 branch(es)" in tree.inner_text()
    page.get_by_text("xss test").click()
    page.get_by_text("<script>window.__xss=2</script>hello", exact=False).wait_for()
    assert page.evaluate("() => window.__xss") is None  # imported HTML never executes
    page.close()


def test_answer_question_and_budget_view(server, browser):
    url, L = server
    page = browser.new_page()
    page.goto(f"{url}/?tab=questions#token={TOKEN}")
    page.get_by_test_id("questions").wait_for()
    assert "will be applied" in page.get_by_test_id("questions").inner_text()
    page.get_by_role("button", name="helpers", exact=True).click()
    page.wait_for_function("() => document.body.innerText.includes('Answer: helpers')")
    assert L.store.one("SELECT answer FROM questions")[0] == "helpers"
    page.goto(f"{url}/?tab=work#token={TOKEN}")
    led = page.get_by_test_id("ledger")
    led.wait_for()
    assert "In-flight reserved" in led.inner_text() and "estimated" in led.inner_text()
    page.close()


def test_handoff_preview(server, browser):
    url, _ = server
    page = browser.new_page()
    page.goto(f"{url}/?tab=sharing#token={TOKEN}")
    page.get_by_label("Goal").fill("pytest preference")
    page.get_by_role("button", name="Preview").click()
    pv = page.get_by_test_id("handoff-preview")
    pv.wait_for()
    assert "lucid-handoff:hnd_" in pv.inner_text() and "not instructions" in pv.inner_text()
    page.close()


def test_messaging_and_avatar_view(server, browser, tmp_path):
    """Seeds a reviewed-send message and a simulated avatar session, then approves the message in the UI."""
    from lucid.avatar import MeetingSession, QueueTTS, ScriptedASR, SimulatedTransport, StillImageRenderer
    url, L = server
    acct = L.comms.connect("gmail", "hussain@acme.com", identity_kind="user_delegated")
    L.comms.set_policy(acct["id"], "send_after_review", recipients=["@acme.com"])
    m = L.comms.compose(acct["id"], "Notes from the sync.", to=["kim@other.io"], subject="Notes")
    photo = tmp_path / "me.png"
    photo.write_bytes(b"\x89PNG fake")
    A = L.avatars
    A.vault = str(tmp_path / "vault")
    e = A.enroll("default", "Hussain")
    A.consent(e["id"], "likeness", "I, Hussain, consent to my likeness being used for an AI representative.",
              signed_by="default", method="typed_statement")
    A.add_media(e["id"], "photo", str(photo), subject="self")
    e = A.build_preview(e["id"], StillImageRenderer())
    A.activate(e["id"], actor="user", reviewed_preview_sha256=e["preview"]["sha256"])
    A.authorize_meetings(e["id"], actor="user", categories=["sync"], hosts=["sam@acme.com"], max_duration_s=600,
                         max_cost_usd=1, knowledge=[{"id": "k1", "text": "Migration done.", "audience": ["*"]}])
    s = MeetingSession(A, e["id"], {"id": "m-e2e", "category": "sync", "host": "sam@acme.com"},
                       transport=SimulatedTransport(participants=["sam@acme.com"]), tts=QueueTTS(), asr=ScriptedASR(),
                       now=lambda: 0.0)
    s.start()
    s.heard("sam@acme.com", "Is the migration done?|0.9")
    s.leave()
    page = browser.new_page()
    page.goto(f"{url}/?tab=representation#token={TOKEN}")
    page.get_by_text("Lucid — AI representative for Hussain").first.wait_for()
    body = page.inner_text("body")
    assert "simulated" in body and "k1" in body and "kim@other.io is not on this account's recipient list" in body
    page.on("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Approve").click()
    page.get_by_text("Approved and queued.").wait_for()
    assert L.comms.message(m["id"])["status"] == "queued"
    page.close()
