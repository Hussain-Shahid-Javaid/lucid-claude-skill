"""Offline contract tests for cloud storage adapters.

S3 runs against moto (an AWS emulator) and Google Docs against FakeDocsClient.
These are NOT live-provider validations; see tests/live/ for those (skipped by default).
"""
import boto3
import pytest
from moto import mock_aws

from lucid.memory import Scope
from lucid.storage.base import ConflictError, OutcomeUnknown
from lucid.storage.gdocs import FakeDocsClient, GoogleDocsBackend, find_section
from lucid.storage.mirror import DocsMirror, Mirror, S3Destination, rebuild_from_s3
from lucid.storage.s3 import S3Backend

P = Scope("default", "p")


@pytest.fixture
def s3():
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="lucid-test")
        c.put_bucket_versioning(Bucket="lucid-test", VersioningConfiguration={"Status": "Enabled"})
        yield c


def test_s3_conditional_writes(s3):
    b = S3Backend("lucid-test", "t1/", s3)
    b.ensure_manifest()
    v1 = b.put_record("memory", "m1", {"x": 1}, create_only=True)
    with pytest.raises(ConflictError):
        b.put_record("memory", "m1", {"x": 2}, create_only=True)  # If-None-Match
    v2 = b.put_record("memory", "m1", {"x": 2}, expected_version=v1)
    with pytest.raises(ConflictError):
        b.put_record("memory", "m1", {"x": 3}, expected_version=v1)  # stale ETag -> If-Match fails
    assert b.get_record("memory", "m1").body == {"x": 2} and v2 != v1


def test_s3_events_idempotent_and_lookup(s3):
    b = S3Backend("lucid-test", "t2/", s3)
    r1 = b.append_event("op-1", {"a": 1})
    r2 = b.append_event("op-1", {"a": 1})
    assert r1["created"] and not r2["created"]
    assert b.lookup_op("op-1") and b.lookup_op("op-2") is None


def test_s3_delete_marker_vs_version_purge_and_no_resurrection(s3):
    b = S3Backend("lucid-test", "t3/", s3)
    b.put_record("memory", "m1", {"v": 1})
    b.put_record("memory", "m1", {"v": 2})
    d = b.delete_record("memory", "m1", "user request")
    assert d["delete_marker"] is True and b.get_record("memory", "m1") is None
    vers = s3.list_object_versions(Bucket="lucid-test", Prefix="t3/records/memory/m1.json")
    assert len(vers.get("Versions", [])) == 2  # prior versions still exist until purged
    purged = b.purge_versions("memory", "m1")
    assert len(purged["versions_removed"]) == 3  # 2 versions + delete marker
    with pytest.raises(ConflictError, match="tombstoned"):
        b.put_record("memory", "m1", {"v": 3})


def test_s3_mirror_lost_response_and_rebuild(L, s3, tmp_path, clock):
    b = S3Backend("lucid-test", "t4/", s3)
    dest = S3Destination(b)
    keep = L.memory.add("Prefers British spelling", kind="preference", scope=P)
    gone = L.memory.add("Old office address", kind="fact", scope=P)
    L.memory.add("private diary note", kind="note", scope=P)  # category not selected -> never uploaded
    mirror = Mirror(L, dest, categories={"preference", "fact"}, scope=P)

    real_apply = dest.apply
    calls = {"n": 0}

    def lossy(op_id, payload):  # first write lands but the response is lost
        calls["n"] += 1
        res = real_apply(op_id, payload)
        if calls["n"] == 1:
            raise OutcomeUnknown("read timeout after send")
        return res
    dest.apply = lossy
    s1 = mirror.sync()
    assert s1["unknown"] == 1 and s1["done"] == 1
    clock.advance(30)
    s2 = L.outbox.process()
    assert s2["reconciled"] == 1  # verified via lookup, not blindly retried
    assert sorted(b.list_records("memory")) == sorted([keep["id"], gone["id"]])

    L.memory.delete(gone["id"])
    mirror.sync()
    assert b.get_record("memory", gone["id"]) is None

    # disaster recovery into a fresh local store
    from lucid.core import Lucid
    fresh = Lucid(str(tmp_path / "fresh.db"), clock)
    rep = rebuild_from_s3(fresh, b)
    assert rep["restored"] == 1 and rep["skipped_tombstoned"] == 0
    assert [m["id"] for m in fresh.memory.search("British spelling", P).items] == [keep["id"]]
    assert fresh.memory.search("office address", P).items == []
    fresh.close()


def test_gdocs_preserves_user_content_and_detects_concurrent_edits(L):
    fake = FakeDocsClient()
    did = fake.create("Notes")
    fake.user_edit(did, lambda t: "My own notes at the top.\n" + t)
    be = GoogleDocsBackend(fake, [did])
    m = L.memory.add("Prefers metric units", kind="preference", scope=P)
    mirror = DocsMirror(L, be, {"preference"}, P)
    mirror.sync("op-1")
    text = fake.docs[did]["text"]
    assert text.startswith("My own notes at the top.\n") and m["id"] in text

    # concurrent edit between our read and write -> revision conflict -> re-read, retry
    real_get = fake.get
    state = {"n": 0}

    def racing_get(d):
        doc = real_get(d)
        state["n"] += 1
        if state["n"] == 2:  # after the mirror's pull read, the user types elsewhere in the doc
            fake.user_edit(d, lambda t: t + "User added a line at the end.\n")
        return doc
    fake.get = racing_get
    L.memory.add("Prefers dark theme", kind="preference", scope=P)
    rep = mirror.sync("op-2")
    fake.get = real_get
    text = fake.docs[did]["text"]
    assert "User added a line at the end." in text and "Prefers dark theme" in text
    assert text.count("⟦lucid:begin v1⟧") == 1 and rep["receipts"]


def test_gdocs_user_edits_reconciled_and_removals_held(L):
    fake = FakeDocsClient()
    did = fake.create("Mem")
    be = GoogleDocsBackend(fake, [did])
    a = L.memory.add("Prefers tea", kind="preference", scope=P)
    b = L.memory.add("Prefers morning meetings", kind="preference", scope=P)
    mirror = DocsMirror(L, be, {"preference"}, P)
    mirror.sync("op-1")
    fake.user_edit(did, lambda t: t.replace("Prefers tea", "Prefers green tea"))
    fake.user_edit(did, lambda t: "\n".join(l for l in t.split("\n") if b["id"] not in l))
    rep = mirror.sync("op-2")
    assert rep["user_edits_applied"][0]["old"] == a["id"]
    new_id = rep["user_edits_applied"][0]["new"]
    assert L.memory.get(new_id)["content"] == "Prefers green tea" and L.memory.get(new_id)["origin"] == "user"
    assert rep["removed_in_doc_pending_confirmation"] == [b["id"]]
    assert L.memory.get(b["id"])["status"] == "active"  # not auto-deleted
    assert b["id"] not in fake.docs[did]["text"]  # and not silently re-added
    mirror.confirm_removal(b["id"], delete=True)
    assert L.memory.get(b["id"])["status"] == "deleted"


def test_gdocs_uncertain_write_verified_by_reread(L):
    fake = FakeDocsClient()
    did = fake.create("Mem")
    be = GoogleDocsBackend(fake, [did])
    L.memory.add("Prefers vim", kind="preference", scope=P)
    fake.apply_then_fail.append(OutcomeUnknown("timeout"))
    receipts = be.write_memories(L.memory.list(P), "op-x")
    assert receipts[0]["already_applied"] is True
    assert find_section(fake.get(did))[3] == "op-x"
    assert be.lookup_op("op-x") == {"doc_id": did}


def test_gdocs_partitioning(L):
    fake = FakeDocsClient()
    be = GoogleDocsBackend(fake, [], max_per_doc=2)
    for i in range(5):
        L.memory.add(f"Preference number {i}", kind="preference", scope=P)
    be.write_memories(L.memory.list(P), "op-p")
    assert len(be.doc_ids) == 3 and len(be.read_all()) == 5


def test_review_regressions_storage(L, s3, tmp_path, clock):
    import json as _j
    from lucid.core import Lucid
    from lucid.storage.base import PermanentError
    from lucid.storage.gdocs import find_section
    b = S3Backend("lucid-test", "t9/", s3)
    dest = S3Destination(b)
    old = L.memory.add("old fact", kind="fact", scope=P)
    mirror = Mirror(L, dest, {"fact"}, P)
    mirror.sync()
    new = L.memory.supersede(old["id"], "new fact", reason="update")
    mirror.sync()
    fresh = Lucid(str(tmp_path / "r.db"), clock)
    rebuild_from_s3(fresh, b)
    assert [m["content"] for m in fresh.memory.list(P)] == ["new fact"]  # superseded stays superseded
    gone = L.memory.add("delete me please", kind="fact", scope=P)
    mirror.sync()
    L.memory.delete(gone["id"])
    mirror.sync()
    blobs = []
    for v in s3.list_object_versions(Bucket="lucid-test", Prefix="t9/events/").get("Versions", []):
        blobs.append(s3.get_object(Bucket="lucid-test", Key=v["Key"], VersionId=v["VersionId"])["Body"].read().decode())
    assert not any("delete me please" in x for x in blobs)
    with pytest.raises(PermanentError):
        S3Backend("no-such-bucket-xyz", "p/", s3).get_record("memory", "x")
    # Docs indices are UTF-16 and come from the API's element indices (emoji + table before the section)
    doc = {"body": {"content": [
        {"startIndex": 1, "endIndex": 10, "paragraph": {"elements": [
            {"startIndex": 1, "endIndex": 10, "textRun": {"content": "Notes 😀\n"}}]}},
        {"startIndex": 10, "endIndex": 20, "table": {"tableRows": [{"tableCells": [{"content": [
            {"startIndex": 12, "endIndex": 17, "paragraph": {"elements": [
                {"startIndex": 12, "endIndex": 17, "textRun": {"content": "cell\n"}}]}}]}]}]}},
        {"startIndex": 20, "endIndex": 60, "paragraph": {"elements": [
            {"startIndex": 20, "endIndex": 60, "textRun": {"content": "⟦lucid:begin v1⟧\n⟦lucid:end op=o1⟧\n"}}]}}]}}
    start, end, inner, op = find_section(doc)
    assert start == 20 and op == "o1"
    fake = FakeDocsClient()
    did = fake.create("emoji")
    fake.user_edit(did, lambda t: "Notes 😀 and 𝄞 music\n" + t)
    be = GoogleDocsBackend(fake, [did])
    L.memory.add("Prefers emoji-safe docs", kind="preference", scope=P)
    be.write_memories(L.memory.list(P, kind="preference"), "op-e1")
    be.write_memories(L.memory.list(P, kind="preference"), "op-e2")
    assert fake.docs[did]["text"].startswith("Notes 😀 and 𝄞 music\n")
    assert find_section(fake.get(did))[3] == "op-e2"
    _ = _j
