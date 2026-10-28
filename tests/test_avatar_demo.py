"""Controlled WebRTC demonstration, run in headless Chromium. Local loopback only: this tests the
disclosure, captions, stop and takeover behaviour over a real RTCPeerConnection, not any meeting platform."""
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")

DEMO = os.path.join(os.path.dirname(__file__), "..", "src", "lucid", "avatar", "web", "demo.html")


@pytest.fixture
def page():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        yield pg
        b.close()


def test_webrtc_demo_discloses_streams_and_stops(page):
    page.goto("file://" + os.path.abspath(DEMO) + "?name=Hussain")
    page.click("#start")
    page.wait_for_function("document.getElementById('remote').videoWidth > 0", timeout=15000)
    assert "AI representative for Hussain" in page.text_content("#disclosure")
    assert "not here in person" in page.text_content("#disclosure")
    page.click("#speak")
    page.wait_for_function("document.getElementById('caption').textContent.length > 0")
    page.click("#stop")
    page.wait_for_function("document.getElementById('caption').dataset.stopped === '1'", timeout=2000)
    after_stop = page.text_content("#caption")
    page.wait_for_timeout(1600)
    assert page.text_content("#caption") == after_stop  # no stale captions after stopping
    assert page.evaluate("window.demoState().queue") == 0
    page.click("#late")
    page.wait_for_function("document.getElementById('late-disclosure').textContent.length > 0", timeout=15000)
    assert "AI representative for Hussain" in page.text_content("#late-disclosure")
    page.click("#speak")
    page.click("#takeover")
    st = page.evaluate("window.demoState()")
    assert st["mode"] == "human" and not st["speaking"] and st["queue"] == 0
    page.click("#leave")
    page.wait_for_function("document.getElementById('state').textContent === 'left'")
    assert "left the meeting" in page.text_content("#disclosure")
    kinds = [m["type"] for m in page.evaluate("window.demoLog") if m["viewer"] == "remote"]
    assert kinds[:2] == ["identity", "disclosure"]


def test_demo_fits_a_phone_screen(page):
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto("file://" + os.path.abspath(DEMO))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
