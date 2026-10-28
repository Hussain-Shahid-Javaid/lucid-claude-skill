import json
import os
import subprocess
import sys

from lucid.mcp_server import LucidMCP

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def rpc(server, method, params=None, id=1):
    return server.handle({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def test_initialize_list_call(L):
    s = LucidMCP(L, user="alice")
    init = rpc(s, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in rpc(s, "tools/list")["result"]["tools"]}
    assert {"memory_search", "memory_save", "context_prepare", "decision_answer", "evaluation_results"} <= names
    assert "shell" not in " ".join(names)
    saved = rpc(s, "tools/call", {"name": "memory_save", "arguments": {"content": "Likes graphs", "kind": "preference"}})
    data = saved["result"]["structuredContent"]
    assert data["origin"] == "assistant" and data["user_scope"] == "alice" and data["verification"] == "unverified"
    found = rpc(s, "tools/call", {"name": "memory_search", "arguments": {"query": "graphs"}})
    assert found["result"]["structuredContent"]["items"][0]["content"] == "Likes graphs"


def test_identity_cannot_be_overridden_and_projects_enforced(L):
    L.memory.add("bob's secret", scope=__import__("lucid.memory", fromlist=["Scope"]).Scope("bob", ""))
    s = LucidMCP(L, user="alice", projects=[""])
    r = rpc(s, "tools/call", {"name": "memory_search", "arguments": {"query": "secret", "user": "bob"}})
    assert r["result"]["isError"] and "identity" in r["result"]["content"][0]["text"]
    r = rpc(s, "tools/call", {"name": "memory_search", "arguments": {"query": "secret"}})
    assert r["result"]["structuredContent"]["items"] == []
    r = rpc(s, "tools/call", {"name": "memory_search", "arguments": {"query": "x", "project": "other"}})
    assert r["result"]["isError"]


def test_protocol_2026_style_and_version_errors(L):
    s = LucidMCP(L, user="u")
    meta = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientCapabilities": {}}}
    r = rpc(s, "tools/list", meta)
    assert r["result"]["resultType"] == "complete" and r["result"]["tools"]
    assert "2026-07-28" in rpc(s, "server/discover", meta)["result"]["supportedVersions"]
    bad = rpc(s, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "1999-01-01"}})
    assert bad["error"]["code"] == -32022 and "2026-07-28" in bad["error"]["data"]["supported"]
    assert rpc(s, "nope")["error"]["code"] == -32601


def test_import_dir_confinement_and_delete_confirmation(L, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "g.json").write_text(open(os.path.join(os.path.dirname(__file__), "fixtures", "generic.json")).read())
    s = LucidMCP(L, user="default", import_dir=str(inbox))
    r = rpc(s, "tools/call", {"name": "conversation_import", "arguments": {"provider": "generic", "filename": "../x"}})
    assert r["result"]["isError"]
    r = rpc(s, "tools/call", {"name": "conversation_import", "arguments": {"provider": "generic", "filename": "g.json"}})
    assert r["result"]["structuredContent"]["conversations"] == 1
    m = L.memory.add("temp", scope=__import__("lucid.memory", fromlist=["Scope"]).Scope("default", ""))
    r = rpc(s, "tools/call", {"name": "memory_delete", "arguments": {"id": m["id"], "confirm": "yes"}})
    assert r["result"]["isError"]
    r = rpc(s, "tools/call", {"name": "memory_delete", "arguments": {"id": m["id"], "confirm": m["id"]}})
    assert r["result"]["structuredContent"]["deleted"]


def test_mcp_stdio_subprocess(tmp_path):
    env = {**os.environ, "PYTHONPATH": SRC, "LUCID_HOME": str(tmp_path)}
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "memory_save", "arguments": {"content": "stdio works", "origin": "user"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "memory_search", "arguments": {"query": "stdio"}}}]
    p = subprocess.run([sys.executable, "-m", "lucid.mcp_server"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, env=env, timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert [l["id"] for l in lines] == [1, 2, 3]
    assert lines[2]["result"]["structuredContent"]["items"][0]["content"] == "stdio works"


def _cli(tmp_path, *args):
    env = {**os.environ, "PYTHONPATH": SRC, "LUCID_HOME": str(tmp_path)}
    p = subprocess.run([sys.executable, "-m", "lucid.cli", "--json", *args], capture_output=True, text=True, env=env,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_cli_memory_lifecycle_across_processes(tmp_path):
    m = _cli(tmp_path, "memory", "add", "Prefers pnpm over npm", "--kind", "preference", "--key", "pref:pm")
    hits = _cli(tmp_path, "memory", "search", "which package manager pnpm")
    assert hits["items"][0]["id"] == m["id"]
    new = _cli(tmp_path, "memory", "correct", m["id"], "Prefers bun", "--reason", "user switched")
    assert _cli(tmp_path, "memory", "get", m["id"])["status"] == "superseded"
    _cli(tmp_path, "memory", "delete", new["id"])
    assert _cli(tmp_path, "memory", "search", "bun")["items"] == []
    st = _cli(tmp_path, "status")
    assert st["schema_version"] == 3 and st["integrity"] == "ok"
    fx = os.path.join(os.path.dirname(__file__), "fixtures", "chatgpt_conversations.synthetic.json")
    imp = _cli(tmp_path, "import", "chatgpt", fx)
    assert imp["conversations"] == 1 and imp["schema_status"] == "community-documented"
