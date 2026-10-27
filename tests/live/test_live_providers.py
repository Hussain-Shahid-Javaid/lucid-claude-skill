"""Live-provider validation. Skipped unless explicitly requested (`pytest -m live`) AND configured.

Uses a dedicated test prefix / document; never touches real memory data.
  LUCID_LIVE_S3_BUCKET=my-test-bucket  [AWS credentials via the standard chain]
  LUCID_LIVE_GDOCS_DOC=<doc id> LUCID_LIVE_GDOCS_TOKEN=<path to authorized-user token json>
"""
import os
import uuid

import pytest

from lucid.memory import Scope

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("LUCID_LIVE_S3_BUCKET"), reason="LUCID_LIVE_S3_BUCKET not set")
def test_live_s3_conditional_writes_and_rebuild(L, tmp_path):
    from lucid.core import Lucid
    from lucid.storage.base import ConflictError
    from lucid.storage.mirror import Mirror, S3Destination, rebuild_from_s3
    from lucid.storage.s3 import S3Backend
    b = S3Backend(os.environ["LUCID_LIVE_S3_BUCKET"], prefix=f"lucid-live-tests/{uuid.uuid4().hex}/")
    b.ensure_manifest()
    v1 = b.put_record("memory", "probe", {"x": 1}, create_only=True)
    with pytest.raises(ConflictError):
        b.put_record("memory", "probe", {"x": 2}, create_only=True)
    b.put_record("memory", "probe", {"x": 2}, expected_version=v1)
    m = L.memory.add("live s3 probe", kind="preference", scope=Scope("default", "p"))
    Mirror(L, S3Destination(b), {"preference"}, Scope("default", "p")).sync()
    fresh = Lucid(str(tmp_path / "f.db"))
    assert rebuild_from_s3(fresh, b)["restored"] >= 1
    assert fresh.memory.get(m["id"])["content"] == "live s3 probe"


@pytest.mark.skipif(not (os.environ.get("LUCID_LIVE_GDOCS_DOC") and os.environ.get("LUCID_LIVE_GDOCS_TOKEN")),
                    reason="Google Docs live test not configured")
def test_live_gdocs_roundtrip(L):
    from google.oauth2.credentials import Credentials  # type: ignore
    from lucid.storage.gdocs import SCOPES, GoogleDocsBackend, GoogleDocsClient
    from lucid.storage.mirror import DocsMirror
    creds = Credentials.from_authorized_user_file(os.environ["LUCID_LIVE_GDOCS_TOKEN"], SCOPES)
    be = GoogleDocsBackend(GoogleDocsClient(creds), [os.environ["LUCID_LIVE_GDOCS_DOC"]])
    L.memory.add("live docs probe", kind="preference")
    rep = DocsMirror(L, be, {"preference"}).sync(op_id=f"live-{uuid.uuid4().hex[:8]}")
    assert rep["receipts"] and "live docs probe" in str(be.read_all())
