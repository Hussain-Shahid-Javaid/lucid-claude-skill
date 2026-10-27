import os
import shutil

import pytest

from conftest import FIXTURES
from lucid.connectors import ConnectorManifest, ManifestError, Registry
from lucid.connectors.sdk import run_incremental


def test_registry_requires_enable_and_incremental_checkpoint(L, tmp_path):
    reg = Registry(L.store)
    assert "folder" in reg.discover()
    with pytest.raises(PermissionError):
        reg.load("folder", {"path": str(tmp_path)})
    reg.enable("folder")
    shutil.copy(os.path.join(FIXTURES, "generic.json"), tmp_path / "a.json")
    con = reg.load("folder", {"path": str(tmp_path)})
    assert run_incremental(L, con)["imported"] == 1
    assert run_incremental(L, con)["imported"] == 0  # checkpoint prevents re-reading
    shutil.copy(os.path.join(FIXTURES, "transcript.md"), tmp_path / "z.md")
    assert run_incremental(L, con)["imported"] == 1
    assert {m["id"] for m in reg.manifests()} == {"folder"}


def test_manifest_validation():
    base = dict(id="x", name="x", product_account_types=[], authentication="none", operations=["import"],
                conversation_coverage="", branches="", attachments="", formats=[], incremental_sync=False,
                deletion="", rate_limits="", permissions=[], verification="unverified")
    ConnectorManifest(**base).validate()
    with pytest.raises(ManifestError):
        ConnectorManifest(**{**base, "operations": ["native_sync"]}).validate()  # needs documented sources
    with pytest.raises(ManifestError):
        ConnectorManifest(**{**base, "operations": ["teleport"]}).validate()


def test_source_deletion_invalidates_dependent_memories(L):
    from lucid.memory import Evidence
    sid = L.memory.add_source("web", uri="https://x.example/page", content="captured text")
    imp = L.memory.add("Imported fact", kind="fact", origin="import", evidence=[Evidence(source_id=sid)])
    ver = L.memory.add("User-stated fact", kind="fact", origin="user", verification="verified",
                       evidence=[Evidence(source_id=sid)])
    assert ver["verification"] == "verified"
    res = L.memory.delete_source(sid)
    assert res["invalidated_memories"] == [imp["id"]]
    assert L.memory.get(ver["id"])["verification"] == "unverified"  # lost its only evidence
