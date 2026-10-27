import io
import json
import zipfile

import pytest

from conftest import fixture_bytes
from lucid.conversations import NormalizedConversation, NormalizedMessage
from lucid.importers import ADAPTERS
from lucid.importers.archive import UnsafeArchive, open_zip, read_input
from lucid.memory import Evidence, Scope


def conv(cid, msgs, provider="t", account="default"):
    return NormalizedConversation(provider=provider, account=account, external_id=cid,
                                  messages=[NormalizedMessage(**m) for m in msgs])


def test_chatgpt_branches_preserved(L):
    _, parse = ADAPTERS["chatgpt"]
    res = L.archive.import_many(parse(fixture_bytes("chatgpt_conversations.synthetic.json")), provider="chatgpt",
                                method="chatgpt-export")
    assert res["conversations"] == 1
    cid = L.archive.list()[0]["id"]
    c = L.archive.get(cid)
    assert c["branch_count"] == 2 and sorted(c["leaves"]) == ["n3", "n5"]
    thread = L.archive.thread(cid)  # current_node n5
    assert [m["content"] for m in thread][:2] == ["What is 2+2?", "2+2 = 4"]
    assert "[image: file-service://abc]" in thread[-1]["content"]
    assert thread[-1]["attachments"][0]["name"] == "pic.png"
    other = L.archive.thread(cid, "n3")
    assert [m["content"] for m in other] == ["What is 2+2?", "2+2 = 5", "That's wrong."]


def test_claude_identical_text_not_deduped(L):
    _, parse = ADAPTERS["claude"]
    L.archive.import_many(parse(fixture_bytes("claude_conversations.synthetic.json")), provider="claude",
                          method="claude-export")
    c = L.archive.get(L.archive.list()[0]["id"])
    oks = [m for m in c["messages"] if m["content"] == "ok"]
    assert len(oks) == 2  # identical messages are separate events
    assert c["coverage"] == "partial" and "linearly" in json.dumps(c["metadata"])


def test_gemini_html_stripped_and_markdown_reconstructed(L):
    _, gp = ADAPTERS["gemini"]
    convs = list(gp(fixture_bytes("gemini_myactivity.synthetic.json")))
    assert "<script>" not in convs[0].messages[1].content and "itself" in convs[0].messages[1].content
    _, mp = ADAPTERS["markdown"]
    md = list(mp(fixture_bytes("transcript.md")))[0]
    assert [m.role for m in md.messages] == ["user", "assistant", "user"]
    assert md.coverage == "reconstructed" and md.warnings


def test_idempotent_reimport_and_revisions(L):
    c1 = conv("c1", [{"external_id": "1", "role": "user", "content": "hello"},
                     {"external_id": "2", "role": "assistant", "content": "hi", "parent": "1"}])
    L.archive.ingest(c1, method="test")
    st = L.archive.ingest(c1, method="test")
    assert st["messages_new"] == 0 and st["messages_unchanged"] == 2
    c1.messages[1].content = "hi there (edited)"
    st = L.archive.ingest(c1, method="test")
    assert st["messages_revised"] == 1
    c = L.archive.get(st["conversation_id"])
    assert {m["status"] for m in c["messages"]} == {"active", "revised"}
    assert L.archive.thread(c["id"])[-1]["content"] == "hi there (edited)"


def test_out_of_order_and_duplicate_events(L):
    msgs = [{"external_id": "3", "role": "user", "content": "c", "parent": "2"},
            {"external_id": "1", "role": "user", "content": "a"},
            {"external_id": "2", "role": "assistant", "content": "b", "parent": "1"},
            {"external_id": "2", "role": "assistant", "content": "b", "parent": "1"}]
    st = L.archive.ingest(conv("c2", msgs), method="test")
    c = L.archive.get(st["conversation_id"])
    assert len([m for m in c["messages"] if m["status"] == "active"]) == 3
    assert [m["content"] for m in L.archive.thread(c["id"], "3")] == ["a", "b", "c"]
    assert c["orphans"] == []


def test_accounts_not_merged(L):
    L.archive.ingest(conv("same", [{"external_id": "1", "role": "user", "content": "x"}], account="work"), method="t")
    L.archive.ingest(conv("same", [{"external_id": "1", "role": "user", "content": "x"}], account="home"), method="t")
    assert len(L.archive.list()) == 2


def test_interrupted_import_resumes_from_checkpoint(L):
    convs = [conv(f"c{i}", [{"external_id": "1", "role": "user", "content": f"m{i}"}]) for i in range(5)]
    with pytest.raises(RuntimeError):
        L.archive.import_many(iter(convs), provider="t", method="t", file_hash="H", fail_after=3)
    assert len(L.archive.list()) == 3
    res = L.archive.import_many(iter(convs), provider="t", method="t", file_hash="H")
    assert res["skipped_checkpoint"] == 3 and res["conversations"] == 2
    assert len(L.archive.list()) == 5


def test_deleted_conversation_not_resurrected_and_memories_invalidated(L):
    st = L.archive.ingest(conv("c3", [{"external_id": "1", "role": "user", "content": "I prefer dark mode"}]),
                          method="t")
    msg = L.archive.get(st["conversation_id"])["messages"][0]
    m = L.memory.add("User prefers dark mode", kind="preference", origin="import",
                     evidence=[Evidence(message_id=msg["id"])])
    assert m["verification"] == "unverified"
    status = L.archive.delete(st["conversation_id"])
    assert m["id"] in status["derived_memories_invalidated"]
    assert "not deleted by Lucid" in status["provider_native_history"]
    again = L.archive.ingest(conv("c3", [{"external_id": "1", "role": "user", "content": "I prefer dark mode"}]),
                             method="t")
    assert again["skipped"] == "tombstoned"


def test_echo_loop_flagged(L):
    p = L.handoffs.prepare("chatgpt", preview=True, goal="x")
    text = f"Here is context: {p['marker']} ..."
    st = L.archive.ingest(conv("c4", [{"external_id": "1", "role": "user", "content": text}]), method="t")
    assert st["echo_messages"] == 1
    m = L.archive.get(st["conversation_id"])["messages"][0]
    assert m["origin"]["lucid_echo"].startswith("hnd_")


def test_malicious_import_redacted_and_inert(L):
    _, parse = ADAPTERS["generic"]
    L.archive.import_many(parse(fixture_bytes("malicious_transcript.jsonl")), provider="generic", method="t")
    c = L.archive.get(L.archive.list()[0]["id"])
    assert "sk-ant-" not in c["messages"][0]["content"]
    assert c["messages"][1]["content"].startswith("<script>")  # stored as inert text; UI escapes it


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_unsafe_archives_rejected(tmp_path):
    p = tmp_path / "evil.zip"
    p.write_bytes(_zip([("../../etc/passwd", "x")]))
    with pytest.raises(UnsafeArchive):
        open_zip(str(p))
    p.write_bytes(_zip([("/abs/conversations.json", "[]")]))
    with pytest.raises(UnsafeArchive):
        read_input(str(p), "conversations.json")
    ok = tmp_path / "ok.zip"
    ok.write_bytes(_zip([("export/conversations.json", "[]")]))
    assert read_input(str(ok), "conversations.json") == b"[]"


def test_malformed_inputs_report_errors(L):
    _, parse = ADAPTERS["chatgpt"]
    with pytest.raises(ValueError):
        list(parse(b"{not json"))
    with pytest.raises(ValueError):
        list(parse(b'[{"no_mapping": true}]'))
    _, g = ADAPTERS["generic"]
    with pytest.raises(ValueError, match="line 2"):
        list(g(b'{"id":"a","messages":[]}\n{bad\n'))
