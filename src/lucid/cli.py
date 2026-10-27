"""`lucid` command-line helper. Works with no service running and no network.

Every command accepts --json for machine-readable output. Data lives in $LUCID_HOME
(default ~/.lucid), outside temporary execution directories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any

from . import __version__
from .memory import Evidence, MemoryError, Scope


def _out(args, obj: Any, text: str | None = None) -> None:
    if args.json or text is None:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        print(text)


def _lucid(args):
    from .core import Lucid
    return Lucid(args.db)


def _scope(args) -> Scope:
    return Scope.of(args.user or os.environ.get("LUCID_USER"), args.project if args.project is not None
                    else os.environ.get("LUCID_PROJECT", ""))


def _src_evidence(L, args, scope) -> list[Evidence]:
    ev = []
    for uri in getattr(args, "source", None) or []:
        sid = L.memory.add_source("web" if uri.startswith("http") else "file", scope=scope, uri=uri,
                                  title=uri, content=getattr(args, "quote", None))
        ev.append(Evidence(source_id=sid, quote=getattr(args, "quote", None)))
    return ev


# --------------------------------------------------------------------------- memory
def cmd_memory(args) -> int:
    L = _lucid(args)
    sc = _scope(args)
    a = args.action
    try:
        if a == "add":
            ev = _src_evidence(L, args, sc)
            res = L.memory.add(args.text, kind=args.kind, origin=args.origin, scope=sc, key=args.key,
                               rationale=args.rationale, verification="verified" if args.verified else "unverified",
                               confidence=args.confidence, uncertainty=args.uncertainty, ttl=args.ttl,
                               sharing=args.sharing, tags=args.tags.split(",") if args.tags else (), evidence=ev,
                               op_id=args.op_id, allow_resurrect=args.allow_resurrect, actor=args.origin)
            extra = ""
            if res.get("conflicts"):
                extra = f"\n  ! conflicts with: {[c['memory_a'] if c['memory_b'] == res['id'] else c['memory_b'] for c in res['conflicts']]}"
            if res.get("notes"):
                extra += "\n  note: " + "; ".join(res["notes"])
            _out(args, res, f"saved {res['id']} ({res['kind']}, {res['verification']}){extra}")
        elif a == "search":
            r = L.memory.search(args.query, sc, kinds=args.kind.split(",") if args.kind else None,
                                limit=args.limit, budget_tokens=args.budget)
            obj = {"items": r.items, "used_tokens": r.used_tokens, "budget_tokens": r.budget_tokens,
                   "omitted": r.omitted, "explanation": r.explanation, "context": r.context}
            _out(args, obj, (r.context or "(no matching memories)") +
                 (f"\n[{len(r.items)} shown, {r.omitted} omitted, ~{r.used_tokens}/{r.budget_tokens} tokens]"))
        elif a == "get":
            _out(args, L.memory.get(args.id, scope=sc))
        elif a == "history":
            _out(args, L.memory.history(args.id))
        elif a == "list":
            items = L.memory.list(sc, kind=args.kind, status=args.status, limit=args.limit)
            _out(args, items, "\n".join(f"{m['id']}  [{m['kind']}|{m['verification']}] {m['content'][:100]}"
                                        for m in items) or "(none)")
        elif a == "edit":
            fields = {}
            if args.tags is not None:
                fields["tags"] = [t for t in args.tags.split(",") if t]
            for k in ("sharing", "expires_at", "uncertainty", "retention", "key"):
                if getattr(args, k, None) is not None:
                    fields[k] = getattr(args, k)
            _out(args, L.memory.edit(args.id, **fields))
        elif a in ("supersede", "correct"):
            ev = _src_evidence(L, args, sc)
            fn = L.memory.correct if a == "correct" else L.memory.supersede
            res = fn(args.id, args.text, reason=args.reason, evidence=ev)
            _out(args, res, f"{args.id} -> {res['id']} ({res['verification']})")
        elif a == "delete":
            res = L.memory.delete(args.id, reason=args.reason)
            _out(args, res, f"deleted {args.id}; invalidated derived: {res['invalidated_derived']}")
        elif a == "verify":
            ev = _src_evidence(L, args, sc)
            if not ev:
                print("verify needs --source", file=sys.stderr)
                return 2
            res = L.memory.verify(args.id, ev)
            _out(args, res, f"{args.id}: {res['verify_outcome']}")
        elif a == "conflicts":
            _out(args, L.memory.conflicts(sc))
        elif a == "resolve":
            _out(args, L.memory.resolve_conflict(args.id, keep=args.keep, resolution=args.reason))
        elif a == "export":
            data = L.memory.export(None if args.all_scopes else sc)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
                print(f"exported {len(data['memories'])} memories to {args.out}")
            else:
                _out(args, data)
        elif a == "purge-expired":
            _out(args, {"deleted": L.memory.purge_expired()})
    except (MemoryError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- conversations
def cmd_import(args) -> int:
    from .importers import ADAPTERS
    from .importers.archive import read_input
    L = _lucid(args)
    manifest, parse = ADAPTERS[args.provider]
    member = {"chatgpt": "conversations.json", "claude": "conversations.json", "gemini": "MyActivity.json"}.get(
        args.provider, os.path.basename(args.path))
    data = read_input(args.path, member)
    fh = hashlib.sha256(data).hexdigest()
    kwargs = {"account": args.account} if args.provider in ("chatgpt", "claude", "gemini") else {}
    res = L.archive.import_many(parse(data, **kwargs), provider=args.provider, method=f"{args.provider}-export",
                                file_hash=fh, scope_user=_scope(args).user, scope_project=_scope(args).project)
    res["schema_status"] = manifest["schema_status"]
    _out(args, res, f"imported {res['conversations']} conversations ({res['messages_new']} new messages, "
                    f"{res['messages_revised']} revised, {res['skipped_tombstoned']} skipped as deleted, "
                    f"{res['echo_messages']} Lucid echoes flagged) — adapter status: {manifest['schema_status']}")
    return 0


def cmd_conv(args) -> int:
    L = _lucid(args)
    if args.action == "list":
        items = L.archive.list(provider=args.provider, user=_scope(args).user)
        _out(args, items, "\n".join(f"{c['id']}  {c['provider']}/{c['account']}  {c['message_count']} msgs  "
                                    f"{(c['title'] or '')[:60]}" for c in items) or "(none)")
    elif args.action == "show":
        if args.thread:
            th = L.archive.thread(args.id, args.leaf)
            _out(args, th, "\n\n".join(f"[{m['role']}] {m['content']}" for m in th))
        else:
            _out(args, L.archive.get(args.id))
    elif args.action == "delete":
        _out(args, L.archive.delete(args.id, reason=args.reason))
    return 0


def cmd_handoff(args) -> int:
    from .handoff import SharingDenied
    L = _lucid(args)
    if args.action == "prepare":
        try:
            p = L.handoffs.prepare(args.to, scope=_scope(args), goal=args.goal or "", query=args.query or "",
                                   categories=args.categories.split(",") if args.categories else None,
                                   task_id=args.task, budget_tokens=args.budget, preview=args.preview)
        except SharingDenied as e:
            print(f"denied: {e}", file=sys.stderr)
            return 3
        _out(args, p, p["text"])
    elif args.action == "get":
        p = L.handoffs.get(args.id, consumer=args.consumer)
        _out(args, p, p.get("text"))
    elif args.action == "list":
        _out(args, L.handoffs.list())
    return 0


def cmd_share(args) -> int:
    L = _lucid(args)
    if args.action == "add":
        _out(args, L.handoffs.add_rule(args.destination, args.categories.split(","), _scope(args)))
    elif args.action == "list":
        _out(args, L.handoffs.rules())
    elif args.action == "disable":
        L.handoffs.disable_rule(args.id)
        print("disabled")
    return 0


# --------------------------------------------------------------------------- tasks / budget / decisions
def cmd_task(args) -> int:
    L = _lucid(args)
    if args.action == "create":
        _out(args, L.tasks.create(args.title, objective=args.objective or "", acceptance=args.accept or [],
                                  owned_paths=args.owns or [], ledger_id=args.ledger))
    elif args.action == "show":
        _out(args, L.tasks.get(args.id))
    elif args.action == "list":
        _out(args, L.tasks.list(status=args.status))
    elif args.action == "check":
        if args.cmd:
            _out(args, L.tasks.run_command_check(args.id, args.check_id, args.cmd))
        else:
            _out(args, L.tasks.record_check(args.id, args.check_id, args.passed, args.evidence or ""))
    elif args.action == "checkpoint":
        L.tasks.checkpoint(args.id, json.loads(args.data))
        print("ok")
    elif args.action == "finish":
        _out(args, L.tasks.finish(args.id, budget_exhausted=args.budget_exhausted, blocked_reason=args.blocked,
                                  canceled=args.canceled))
    return 0


def cmd_budget(args) -> int:
    from .budget import InsufficientBudget, Usage
    L = _lucid(args)
    try:
        if args.action == "create":
            lid = L.budget.create(args.name, token_limit=args.tokens, cost_limit=args.cost, time_limit_s=args.seconds,
                                  reserve_fraction=args.reserve, mode=args.mode)
            _out(args, L.budget.status(lid))
        elif args.action == "status":
            _out(args, L.budget.status(args.id) if args.id else L.budget.ledgers())
        elif args.action == "reserve":
            _out(args, {"reservation": L.budget.reserve(args.id, purpose=args.purpose, input_tokens=args.input,
                                                         max_output_tokens=args.max_output,
                                                         use_reserve=args.use_reserve)})
        elif args.action == "reconcile":
            _out(args, L.budget.reconcile(args.id, Usage(input_tokens=args.input, output_tokens=args.output,
                                                         cached_tokens=args.cached)))
        elif args.action == "release":
            L.budget.release(args.id)
            print("released")
        elif args.action == "unknown":
            L.budget.mark_unknown(args.id)
            print("held as unknown")
    except InsufficientBudget as e:
        _out(args, {"error": str(e), "status": e.status}, f"insufficient budget: {e}")
        return 4
    return 0


def cmd_decide(args) -> int:
    from .decisions import DecisionError
    L = _lucid(args)
    d = L.decisions
    try:
        if args.action == "ask":
            q = d.ask(args.decision, args.options.split(","), category=args.category, default=args.default,
                      timeout_s=args.timeout, task_id=args.task, scope=args.scope, consequence=args.consequence or "",
                      required_approval=args.required, pref_key=args.pref_key, mem_scope=_scope(args))
            _out(args, q, f"{q.get('id')}: {q.get('on_timeout') or q.get('status')}")
        elif args.action == "answer":
            _out(args, d.answer(args.id, args.option, remember=args.remember, mem_scope=_scope(args)))
        elif args.action == "list":
            _out(args, d.list(args.status))
        elif args.action == "tick":
            _out(args, d.tick())
        elif args.action == "extend":
            _out(args, d.extend(args.id, args.seconds))
        elif args.action in ("pause", "resume", "cancel"):
            _out(args, {"ok": getattr(d, args.action)(args.id)})
        elif args.action == "accept-default":
            _out(args, d.accept_default(args.id))
        elif args.action == "authorize":
            _out(args, d.authorize(args.category, scope=args.scope or "*", expires_in_s=args.expires))
        elif args.action == "authorizations":
            _out(args, d.authorizations())
        elif args.action == "revoke":
            d.revoke(args.id)
            print("revoked")
    except DecisionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_monitor(args) -> int:
    L = _lucid(args)
    m = L.monitor
    if args.action == "note":
        _out(args, {"id": m.note(args.kind, args.text, task_id=args.task)})
    elif args.action == "resolve":
        m.resolve(args.id, args.status, args.evidence)
        print("ok")
    elif args.action == "items":
        _out(args, m.items(args.task, args.status))
    elif args.action == "predict":
        _out(args, {"id": m.predict(args.claim, args.confidence, args.task)})
    elif args.action == "outcome":
        m.resolve_prediction(args.id, args.outcome == "true")
        print("ok")
    elif args.action == "calibration":
        _out(args, m.calibration(args.task))
    return 0


# --------------------------------------------------------------------------- admin
def cmd_admin(args) -> int:
    L = _lucid(args)
    a = args.cmd
    if a == "status":
        _out(args, L.health())
    elif a == "init":
        h = L.health()
        _out(args, h, f"Lucid {__version__} ready at {h['db_path']} (schema v{h['schema_version']})")
    elif a == "backup":
        _out(args, {"backup": L.store.backup(args.path)})
    elif a == "restore":
        L.store.restore(args.path)
        n = L.memory.rebuild_index()
        _out(args, {"restored": args.path, "reindexed": n})
    elif a == "reindex":
        _out(args, {"reindexed": L.memory.rebuild_index()})
    elif a == "activity":
        rows = [dict(r) for r in L.store.q("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (args.limit,))]
        _out(args, rows)
    return 0


def cmd_capabilities(args) -> int:
    from .capabilities import load_matrix
    m = load_matrix()
    if args.host:
        m = {**m, "hosts": [h for h in m["hosts"] if h["id"] == args.host]}
    if args.json:
        _out(args, m)
    else:
        for h in m["hosts"]:
            print(f"\n{h['name']}")
            for cap, v in h["capabilities"].items():
                print(f"  {cap:28s} {v['status']:13s} {v.get('note', '')}")
        print(f"\nverified: {m['verified']}  (sources: docs/compatibility.md)")
    return 0


def cmd_sync(args) -> int:
    L = _lucid(args)
    if args.action == "status":
        _out(args, {"destinations": L.outbox.status(), "items": L.outbox.items(limit=20)})
    elif args.action == "recover":
        _out(args, {"marked_outcome_unknown": L.outbox.recover_after_restart()})
    return 0


def cmd_serve(args) -> int:
    from .server.app import serve
    serve(args.db, host=args.host, port=args.port)
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main
    return mcp_main(["--db", args.db] if args.db else [])


def cmd_eval(args) -> int:
    from .evals.runner import main as eval_main
    return eval_main(args.rest)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lucid", description="Lucid memory, handoff, budget and decision helper")
    p.add_argument("--version", action="version", version=f"lucid {__version__}")
    p.add_argument("--db", default=os.environ.get("LUCID_DB"), help="SQLite path (default $LUCID_HOME/lucid.db)")
    p.add_argument("--user", help="user scope (default $LUCID_USER or 'default')")
    p.add_argument("--project", help="project scope (default $LUCID_PROJECT or '')")
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="group", required=True)

    def common(sp):
        # allow global flags after the subcommand too
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        sp.add_argument("--user", default=argparse.SUPPRESS)
        sp.add_argument("--project", default=argparse.SUPPRESS)
        sp.add_argument("--db", default=argparse.SUPPRESS)
        return sp

    # memory
    m = sub.add_parser("memory", help="memory operations").add_subparsers(dest="action", required=True)
    x = common(m.add_parser("add"))
    x.add_argument("text")
    x.add_argument("--kind", default="note")
    x.add_argument("--origin", default="user", choices=["user", "assistant", "tool", "import", "lucid_summary", "experiment"])
    x.add_argument("--key")
    x.add_argument("--rationale")
    x.add_argument("--verified", action="store_true", help="request 'verified' (needs --source)")
    x.add_argument("--source", action="append", help="evidence URI (repeatable)")
    x.add_argument("--quote")
    x.add_argument("--confidence", type=float)
    x.add_argument("--uncertainty")
    x.add_argument("--ttl")
    x.add_argument("--sharing", default="private", choices=["private", "project", "shared"])
    x.add_argument("--tags")
    x.add_argument("--op-id")
    x.add_argument("--allow-resurrect", action="store_true")
    x = common(m.add_parser("search"))
    x.add_argument("query")
    x.add_argument("--kind")
    x.add_argument("--limit", type=int, default=20)
    x.add_argument("--budget", type=int, default=1500, help="context budget in tokens (approx)")
    for name in ("get", "history"):
        x = common(m.add_parser(name))
        x.add_argument("id")
    x = common(m.add_parser("list"))
    x.add_argument("--kind")
    x.add_argument("--status", default="active")
    x.add_argument("--limit", type=int, default=100)
    x = common(m.add_parser("edit"))
    x.add_argument("id")
    x.add_argument("--tags")
    x.add_argument("--sharing", choices=["private", "project", "shared"])
    x.add_argument("--expires-at", dest="expires_at")
    x.add_argument("--uncertainty")
    x.add_argument("--retention")
    x.add_argument("--key")
    for name in ("supersede", "correct"):
        x = common(m.add_parser(name))
        x.add_argument("id")
        x.add_argument("text")
        x.add_argument("--reason", required=True)
        x.add_argument("--source", action="append")
        x.add_argument("--quote")
    x = common(m.add_parser("delete"))
    x.add_argument("id")
    x.add_argument("--reason", default="user request")
    x = common(m.add_parser("verify"))
    x.add_argument("id")
    x.add_argument("--source", action="append")
    x.add_argument("--quote")
    common(m.add_parser("conflicts"))
    x = common(m.add_parser("resolve"))
    x.add_argument("id")
    x.add_argument("--keep")
    x.add_argument("--reason", required=True)
    x = common(m.add_parser("export"))
    x.add_argument("--out")
    x.add_argument("--all-scopes", action="store_true")
    common(m.add_parser("purge-expired"))

    x = common(sub.add_parser("import", help="import a conversation export"))
    x.add_argument("provider", choices=["generic", "markdown", "chatgpt", "claude", "gemini"])
    x.add_argument("path")
    x.add_argument("--account", default="default")

    c = sub.add_parser("conv", help="conversation archive").add_subparsers(dest="action", required=True)
    x = common(c.add_parser("list"))
    x.add_argument("--provider")
    x = common(c.add_parser("show"))
    x.add_argument("id")
    x.add_argument("--thread", action="store_true")
    x.add_argument("--leaf")
    x = common(c.add_parser("delete"))
    x.add_argument("id")
    x.add_argument("--reason", default="user request")

    h = sub.add_parser("handoff", help="context handoff").add_subparsers(dest="action", required=True)
    x = common(h.add_parser("prepare"))
    x.add_argument("--to", required=True)
    x.add_argument("--goal")
    x.add_argument("--query")
    x.add_argument("--categories")
    x.add_argument("--task")
    x.add_argument("--budget", type=int, default=1500)
    x.add_argument("--preview", action="store_true")
    x = common(h.add_parser("get"))
    x.add_argument("id")
    x.add_argument("--consumer")
    common(h.add_parser("list"))

    s = sub.add_parser("share", help="sharing rules").add_subparsers(dest="action", required=True)
    x = common(s.add_parser("add"))
    x.add_argument("destination")
    x.add_argument("--categories", required=True)
    common(s.add_parser("list"))
    x = common(s.add_parser("disable"))
    x.add_argument("id")

    t = sub.add_parser("task", help="tasks & acceptance").add_subparsers(dest="action", required=True)
    x = common(t.add_parser("create"))
    x.add_argument("title")
    x.add_argument("--objective")
    x.add_argument("--accept", action="append")
    x.add_argument("--owns", action="append")
    x.add_argument("--ledger")
    x = common(t.add_parser("show"))
    x.add_argument("id")
    x = common(t.add_parser("list"))
    x.add_argument("--status")
    x = common(t.add_parser("check"))
    x.add_argument("id")
    x.add_argument("check_id")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--passed", action="store_true")
    g.add_argument("--failed", action="store_true")
    x.add_argument("--evidence")
    x.add_argument("--cmd", nargs=argparse.REMAINDER, help="run a command; exit 0 == passed")
    x = common(t.add_parser("checkpoint"))
    x.add_argument("id")
    x.add_argument("data", help="JSON object")
    x = common(t.add_parser("finish"))
    x.add_argument("id")
    x.add_argument("--budget-exhausted", action="store_true")
    x.add_argument("--blocked")
    x.add_argument("--canceled", action="store_true")

    b = sub.add_parser("budget", help="token/cost/time ledger").add_subparsers(dest="action", required=True)
    x = common(b.add_parser("create"))
    x.add_argument("name")
    x.add_argument("--tokens", type=int)
    x.add_argument("--cost", type=float)
    x.add_argument("--seconds", type=float)
    x.add_argument("--reserve", type=float, default=0.15)
    x.add_argument("--mode", default="estimated", choices=["estimated", "hard"])
    x = common(b.add_parser("status"))
    x.add_argument("id", nargs="?")
    x = common(b.add_parser("reserve"))
    x.add_argument("id")
    x.add_argument("--purpose", required=True)
    x.add_argument("--input", type=int, required=True)
    x.add_argument("--max-output", type=int, required=True)
    x.add_argument("--use-reserve", action="store_true")
    x = common(b.add_parser("reconcile"))
    x.add_argument("id")
    x.add_argument("--input", type=int, required=True)
    x.add_argument("--output", type=int, required=True)
    x.add_argument("--cached", type=int, default=0)
    for name in ("release", "unknown"):
        x = common(b.add_parser(name))
        x.add_argument("id")

    d = sub.add_parser("decide", help="timed questions & standing policies").add_subparsers(dest="action", required=True)
    x = common(d.add_parser("ask"))
    x.add_argument("decision")
    x.add_argument("--options", required=True)
    x.add_argument("--default")
    x.add_argument("--category", required=True)
    x.add_argument("--timeout", type=float)
    x.add_argument("--task")
    x.add_argument("--scope")
    x.add_argument("--consequence")
    x.add_argument("--required", action="store_true")
    x.add_argument("--pref-key")
    x = common(d.add_parser("answer"))
    x.add_argument("id")
    x.add_argument("option")
    x.add_argument("--remember", action="store_true")
    x = common(d.add_parser("list"))
    x.add_argument("--status")
    common(d.add_parser("tick"))
    x = common(d.add_parser("extend"))
    x.add_argument("id")
    x.add_argument("seconds", type=float)
    for name in ("pause", "resume", "cancel", "accept-default", "revoke"):
        x = common(d.add_parser(name))
        x.add_argument("id")
    x = common(d.add_parser("authorize"))
    x.add_argument("category")
    x.add_argument("--scope")
    x.add_argument("--expires", type=float)
    common(d.add_parser("authorizations"))

    mo = sub.add_parser("monitor", help="assumptions, claims, predictions").add_subparsers(dest="action", required=True)
    x = common(mo.add_parser("note"))
    x.add_argument("kind")
    x.add_argument("text")
    x.add_argument("--task")
    x = common(mo.add_parser("resolve"))
    x.add_argument("id")
    x.add_argument("status")
    x.add_argument("--evidence", required=True)
    x = common(mo.add_parser("items"))
    x.add_argument("--task")
    x.add_argument("--status")
    x = common(mo.add_parser("predict"))
    x.add_argument("claim")
    x.add_argument("confidence", type=float)
    x.add_argument("--task")
    x = common(mo.add_parser("outcome"))
    x.add_argument("id")
    x.add_argument("outcome", choices=["true", "false"])
    x = common(mo.add_parser("calibration"))
    x.add_argument("--task")

    for name in ("status", "init", "reindex"):
        common(sub.add_parser(name))
    for name in ("backup", "restore"):
        x = common(sub.add_parser(name))
        x.add_argument("path")
    x = common(sub.add_parser("activity"))
    x.add_argument("--limit", type=int, default=50)

    x = common(sub.add_parser("capabilities", help="host capability matrix"))
    x.add_argument("--host")
    sy = sub.add_parser("sync").add_subparsers(dest="action", required=True)
    common(sy.add_parser("status"))
    common(sy.add_parser("recover"))

    x = common(sub.add_parser("serve", help="run dashboard + API (localhost)"))
    x.add_argument("--host", default="127.0.0.1")
    x.add_argument("--port", type=int, default=8765)
    common(sub.add_parser("mcp", help="run MCP server on stdio"))
    x = sub.add_parser("eval", help="evaluation runner")
    x.add_argument("rest", nargs=argparse.REMAINDER)
    return p


DISPATCH = {"memory": cmd_memory, "import": cmd_import, "conv": cmd_conv, "handoff": cmd_handoff,
            "share": cmd_share, "task": cmd_task, "budget": cmd_budget, "decide": cmd_decide,
            "monitor": cmd_monitor, "capabilities": cmd_capabilities, "sync": cmd_sync, "serve": cmd_serve,
            "mcp": cmd_mcp, "eval": cmd_eval}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "action", None) == "check" and args.group == "task" and not args.cmd and not (
            args.passed or args.failed):
        print("task check needs --passed, --failed or --cmd", file=sys.stderr)
        return 2
    if args.group in ("status", "init", "backup", "restore", "reindex", "activity"):
        args.cmd = args.group
        return cmd_admin(args)
    return DISPATCH[args.group](args)


if __name__ == "__main__":
    sys.exit(main())
