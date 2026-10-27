"""Browser-extension extraction tests against versioned fixtures (Playwright/Chromium).

These test the extraction library and site-adapter failure detection. Loading the
packed extension in a real browser profile is covered by docs/extension.md (manual).
"""
import json
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")

EXT = os.path.join(os.path.dirname(__file__), "..", "extension")


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        yield pg
        b.close()


def load(page, fixture):
    page.goto("file://" + os.path.abspath(os.path.join(EXT, "fixtures", fixture)))
    page.add_script_tag(path=os.path.join(EXT, "lib", "extract.js"))
    page.add_script_tag(path=os.path.join(EXT, "adapters", "index.js"))


def conv(page):
    return page.evaluate("() => LucidExtract.conversation(document, LucidAdapters.find(a => a.id === 'example-chat'))")


def test_adapter_extracts_fixture_without_hidden_or_form_content(page):
    load(page, "example-chat.v1.html")
    r = conv(page)
    assert r["extraction"] == {"ok": True, "errors": []}
    msgs = r["conversation"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and r["conversation"]["id"] == "conv-42"
    blob = json.dumps(r)
    assert "hunter2" not in blob and "me@example.com" not in blob and "hidden tracking" not in blob


def test_changed_layout_reports_failure_not_partial_import(page):
    load(page, "example-chat.v2-changed-layout.html")
    r = conv(page)
    assert r["extraction"]["ok"] is False and r["conversation"]["messages"] == []
    assert "required selector missing" in r["extraction"]["errors"][0]
    load(page, "example-chat.v3-role-renamed.html")
    r = conv(page)
    assert r["extraction"]["ok"] is False and "layout may have changed" in r["extraction"]["errors"][0]


def test_page_capture_excludes_password_and_form_fields(page):
    load(page, "article-with-form.html")
    p = page.evaluate("() => LucidExtract.page(document)")
    assert "conditional writes" in p["text"]
    for secret in ("s3cr3t-pass", "draft private reply", "screen-reader-hidden"):
        assert secret not in p["text"]
    assert "menu" not in p["text"]  # article preferred over chrome


def test_selection_inside_password_or_textarea_is_refused(page):
    load(page, "article-with-form.html")
    page.evaluate("""() => { const t = document.querySelector('textarea'); t.focus(); t.select();
        const r = document.createRange(); r.selectNodeContents(t); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    assert page.evaluate("() => LucidExtract.selection(window)") == ""
    page.evaluate("""() => { const p = document.querySelector('article p'); const r = document.createRange();
        r.selectNodeContents(p); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    assert "conditional writes" in page.evaluate("() => LucidExtract.selection(window)")


def test_manifest_is_least_privilege():
    m = json.load(open(os.path.join(EXT, "manifest.json")))
    assert m["manifest_version"] == 3
    assert "<all_urls>" not in json.dumps(m) and "cookies" not in m["permissions"] and "history" not in m["permissions"]
    assert m["optional_host_permissions"] == ["https://*/*"]
    assert "content_scripts" not in m  # nothing runs on pages until the user acts or enables a site
