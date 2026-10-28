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



# --------------------------------------------------------------------------- invent
def _split_kv(items):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"expected NAME=COMMAND, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _run_check_cmd(cmd: str, timeout: int = 300):
    import shlex
    import subprocess
    argv = shlex.split(cmd)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, f"$ {cmd}\nexit={p.returncode}\n{(p.stdout + p.stderr)[-1200:]}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"$ {cmd}\nerror: {e}"


def cmd_invent(args) -> int:
    from .originality import LocalCollectionProvider, OriginalityError, UnavailableProvider
    L = _lucid(args)
    O = L.originality
    a = args.action
    try:
        if a == "target":
            st = O.create_study(args.problem, scope=_scope(args), users=args.users or "",
                                constraints=args.constraint or [], existing_approaches=args.existing or [],
                                improvement=args.improvement or "", level=args.level,
                                confidential_terms=args.confidential or [],
                                allowed_providers=args.allow or ["local:reference"],
                                required_providers=args.require, ledger_id=args.ledger)
            _out(args, st, f"study {st['id']}\n  problem: {st['target']['problem']}\n"
                           f"  searches allowed: {', '.join(st['target']['allowed_providers'])}")
        elif a == "candidate":
            c = O.add_candidate(args.study, args.title, args.mechanism, args.difference or "", args.method,
                                advantage=args.advantage, components=args.component or [], domain=args.domain,
                                revisit_of=args.revisit_of, revisit_reason=args.revisit_reason)
            warn = ("\n  warning: " + ", ".join(c["cosmetic_flags"])) if c["cosmetic_flags"] else ""
            _out(args, c, f"candidate {c['id']}: {c['title']}{warn}")
        elif a == "check":
            provs: list = []
            if not args.no_local:
                provs.append(LocalCollectionProvider(args.collection) if args.collection else LocalCollectionProvider())
            for name in args.offline or []:
                provs.append(UnavailableProvider(name))
            res = O.check(args.id, provs)
            lines = [f"{res['status_text']}", f"  {res['claim']}"]
            for r in res["reasons"]:
                lines.append(f"  - {r}")
            for p in res["precedents"][:3]:
                lines.append(f"  closest: {p['title']} (similarity {p['similarity']:.2f}"
                             f"{', inspected' if p['inspected'] else ', not inspected'})")
            cov = res["coverage"]
            lines.append(f"  searched: {cov['queries_run']} queries ran, {cov['queries_failed']} failed; "
                         f"kinds {', '.join(cov['query_kinds']) or 'none'}")
            _out(args, res, "\n".join(lines))
        elif a == "not-a-repeat":
            _out(args, O.not_a_repeat(args.id, args.match, args.reason), f"noted: {args.id} is not a repeat of {args.match}")
        elif a == "plan":
            _out(args, O.plan_queries(args.id), "\n".join(f"[{q['kind']}] {q['external_query']}"
                                                           for q in O.plan_queries(args.id)))
        elif a == "search-log":
            sid = O.log_search(args.id, args.provider, args.query, args.kind, args.status,
                               result_count=args.results, note=args.note,
                               covers=args.covers.split(",") if args.covers else ())
            _out(args, {"id": sid}, f"logged search {sid} ({args.status})")
        elif a == "add-source":
            s = O.add_source(args.id, args.provider, kind=args.kind, ref=args.ref, title=args.title,
                             summary=args.summary, inspected=args.inspected, relation=args.relation,
                             differences=args.differences, found_by=args.found_by)
            _out(args, s, f"source {s['id']}: {s['title']} (similarity {s['similarity']:.2f})")
        elif a == "inspect":
            s = O.inspect(args.id, relation=args.relation, differences=args.differences)
            _out(args, s, f"inspected {s['id']}: {s['relation'] or 'no relation set'}")
        elif a == "assess":
            if args.role:
                aid = O.record_assessment(args.id, args.role, args.verdict, args.rationale or "",
                                          saw_others=args.saw_others)
                _out(args, {"id": aid}, f"recorded {args.role} assessment {aid}")
            else:
                res = O.assess(args.id)
                _out(args, res, f"{res['status_text']}\n  {res['claim']}")
        elif a == "value":
            results = {name: _run_check_cmd(cmd) for name, cmd in _split_kv(args.check).items()}
            v = O.assess_value(args.id, results=results, feasibility=args.feasibility, usefulness=args.usefulness,
                               resources_=args.resources, advantage_evidence=args.advantage_evidence)
            lines = [f"value: {v['status']}"] + [f"  {n}: {'passed' if o['passed'] else 'failed'}"
                                                 for n, o in v["checks"].items()]
            _out(args, v, "\n".join(lines))
        elif a == "gate":
            g = O.gate(args.study, original_only=not args.include_incremental)
            lines = [g["message"]]
            for q in g["qualifying"]:
                lines.append(f"  keep  {q['id']}  {q['title']}")
            for q in g["incremental"] + g["incremental_held_back"]:
                tag = "incr " if g["incremental"] else "held "
                lines.append(f"  {tag} {q['id']}  {q['title']} (builds on existing work)")
            for q in g["excluded"]:
                lines.append(f"  drop  {q['id']}  {q['title']}: {q['reason']}")
            _out(args, g, "\n".join(lines))
        elif a == "record":
            if args.save:
                m = O.save_record(args.id)
                _out(args, m, f"saved invention record as memory {m['id']}")
            elif args.markdown:
                print(O.render_record(args.id))
            else:
                _out(args, O.record(args.id))
        elif a == "workers":
            _out(args, O.worker_plan(args.study))
        elif a == "show":
            st = O.study(args.study)
            st["candidates"] = [O.candidate(c) for c in st["candidates"]]
            _out(args, st, "\n".join([f"study {st['id']}: {st['target']['problem']}"] +
                                     [f"  {c['id']}  {c['status']:<22} {c['title']}" for c in st["candidates"]]))
    except (OriginalityError, KeyError) as e:
        print(f"lucid invent: {e}", file=sys.stderr)
        return 2
    return 0



# --------------------------------------------------------------------------- plain
def cmd_plain(args) -> int:
    from . import plain
    if args.file and args.file != "-":
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    if args.fix:
        new, info = plain.fix(text)
        if args.json:
            _out(args, {"text": new, **info})
        else:
            print(new, end="" if new.endswith("\n") else "\n")
            lost = [k for k, ok in info["preserved"].items() if not ok]
            if lost:
                print(f"warning: the fix changed {', '.join(lost)}; check it", file=sys.stderr)
        return 0
    rep = plain.check(text, question=args.question)
    _out(args, rep.as_dict(), plain.render(rep))
    return 0 if rep.ok or not args.strict else 1



# --------------------------------------------------------------------------- quality
def cmd_quality(args) -> int:
    from .quality import QualityError
    L = _lucid(args)
    Q = L.quality
    a = args.action
    try:
        if a == "init":
            p = Q.load_plan(args.config, artifact=args.artifact)
            _out(args, p, f"plan {p['id']} ({p['profile']}): {len(p['checks'])} checks for {p['artifact']}")
        elif a == "plans":
            rows = [dict(r) for r in L.store.q("SELECT id, subject, profile, created_at FROM quality_plans"
                                                 " ORDER BY created_at DESC")]
            _out(args, rows, "\n".join(f"{r['id']}  {r['profile']:<10} {r['subject']}" for r in rows) or "(none)")
        elif a == "show":
            p = Q.plan(args.plan)
            lines = [f"{p['subject']} ({p['profile']})"]
            for c in p["checks"]:
                st = Q.check_state(p["id"], c)["state"]
                lines.append(f"  {st:<8} {c['key']:<18} {c['kind']:<12} {'required' if c['mandatory'] else 'optional'}")
            _out(args, p, "\n".join(lines))
        elif a == "run":
            res = Q.run_all(args.plan, time_budget_s=args.time_budget, include_optional=not args.no_optional,
                            only=args.only)
            lines = [f"  {r['outcome']:<7} {r['key']}" + (" (already passed on these files)" if r["reused"] else "")
                     for r in res["ran"]] + [f"  skipped {s['key']}: {s['reason']}" for s in res["skipped"]]
            _out(args, res, "\n".join(lines) or "nothing to run")
        elif a == "record":
            if args.passed == args.failed:
                print("lucid quality record: pass exactly one of --passed or --failed", file=sys.stderr)
                return 2
            r = Q.record_result(args.plan, args.key, args.passed, args.evidence)
            _out(args, r, f"recorded {args.key}: {r['outcome']}")
        elif a == "defect":
            if args.op == "add":
                d = Q.add_defect(args.target, args.severity, args.title, args.evidence or "", check_key=args.check)
            elif args.op == "escaped":
                d = Q.report_escaped(args.target, args.severity, args.title, args.evidence or "")
            elif args.op == "resolve":
                d = Q.resolve_defect(args.target, resolution=args.resolution or "", verified_by=args.verified_by)
            else:
                d = Q.accept_defect(args.target, reason=args.reason or "", actor="user")
            _out(args, d, f"defect {d['id']} [{d['severity']}] {d['status']}: {d['title']}")
        elif a == "review":
            with open(args.file, encoding="utf-8") as fh:
                rv = json.load(fh)
            r = Q.add_review(args.plan, rv["reviewer"], independent=bool(rv.get("independent")),
                             examined=rv.get("examined", []), findings=rv.get("findings", []),
                             summary=rv.get("summary", ""))
            _out(args, r, f"review {r['id']}: {len(r['findings'])} findings recorded as defects")
        elif a == "compare":
            with open(args.file, encoding="utf-8") as fh:
                cfg = json.load(fh)
            r = Q.compare(args.plan, cfg["options"], chosen=cfg["chosen"], rationale=cfg.get("rationale", ""),
                          originality=L.originality)
            _out(args, r, f"chose {r['chosen']}" + "".join(f"\n  warning: {w}" for w in r["warnings"]))
        elif a == "gate":
            rep = Q.gate(args.plan)
            text = Q.render(rep)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(text)
            _out(args, rep, text if args.markdown else
                 f"{rep['status'].replace('_', ' ')}" + "".join(f"\n  - {b}" for b in rep["blockers"]))
            return {"verified": 0, "failed": 1}.get(rep["status"], 3)
        elif a == "metrics":
            m = Q.metrics()
            _out(args, m, "\n".join(f"{k}: {v}" for k, v in m.items()))
    except (QualityError, KeyError) as e:
        print(f"lucid quality: {e}", file=sys.stderr)
        return 2
    return 0



# --------------------------------------------------------------------------- comms
def cmd_comms(args) -> int:
    from .comms import CommsError
    L = _lucid(args)
    C = L.comms
    a = args.action
    try:
        if a == "connect":
            token = os.environ.get(args.token_env) if args.token_env else None
            if args.token_env and not token:
                print(f"lucid comms: environment variable {args.token_env} is empty", file=sys.stderr)
                return 2
            acct = C.connect(args.provider, args.identity, identity_kind=args.identity_kind,
                             user=_scope(args).user, display=args.display or "", workspace=args.workspace or "",
                             team_scope=args.team_scope or "", token=token)
            _out(args, acct, f"connected {acct['id']} ({acct['provider']} as {acct['identity']}). "
                             "It can only draft until you set a policy.")
        elif a == "policy":
            rules = json.loads(args.rules) if args.rules else {}
            pol = C.set_policy(args.account, args.mode, **rules)
            _out(args, pol, f"policy {pol['id']}: {pol['mode']}")
        elif a == "compose":
            m = C.compose(args.account, args.body, to=args.to or [], channel=args.channel, thread=args.thread,
                          subject=args.subject, attachments=args.attach or [], topic=args.topic,
                          evidence=args.evidence or [], op_id=args.op_id)
            _out(args, m, C.preview(m["id"]))
        elif a == "preview":
            print(C.preview(args.id))
        elif a == "approve":
            m = C.approve(args.id, seen_hash=args.seen, confirm=args.confirm or [])
            _out(args, m, f"{m['id']} queued")
        elif a == "process":
            res = C.process()
            _out(args, res, "\n".join(f"{r['id']}  {r['status']}" + (f"  ({r['reason']})" if r.get("reason") else "")
                                      for r in res) or "nothing due")
        elif a == "outbox":
            ms = C.outbox(args.account)
            _out(args, ms, "\n".join(f"{m['id']}  {m['status']:<16} {(m['subject'] or m['body'])[:60]!r}"
                                     for m in ms) or "(empty)")
        elif a == "mark":
            m = C.mark_outcome(args.id, sent=args.sent, note=args.note or "")
            _out(args, m, f"{m['id']} {m['status']}")
        elif a == "revoke":
            _out(args, C.revoke_account(args.account))
        elif a == "audit":
            _out(args, C.audit(args.id))
    except (CommsError, KeyError) as e:
        print(f"lucid comms: {e}", file=sys.stderr)
        return 2
    return 0



# --------------------------------------------------------------------------- avatar
def cmd_avatar(args) -> int:
    from .avatar import AvatarError, StillImageRenderer
    L = _lucid(args)
    A = L.avatars
    a = args.action
    user = _scope(args).user
    try:
        if a == "enroll":
            e = A.enroll(user, args.name, provider=args.provider)
            _out(args, e, f"enrollment {e['id']} ({e['provider']}). Next: consent likeness, then add photos.")
        elif a == "consent":
            r = A.consent(args.id, args.kind, args.statement, signed_by=user, method=args.method, evidence=args.evidence)
            _out(args, r, f"{args.kind} consent recorded")
        elif a == "add-media":
            r = A.add_media(args.id, args.kind, args.path, subject="self" if args.mine else "other")
            _out(args, r, f"stored {r['kind']} in the protected vault")
        elif a == "preview":
            e = A.build_preview(args.id, StillImageRenderer())
            _out(args, e, f"preview: {e['preview']['path']}\nreview it, then: lucid avatar activate {args.id} "
                          f"--reviewed {e['preview']['sha256']}")
        elif a == "activate":
            e = A.activate(args.id, actor="user", reviewed_preview_sha256=args.reviewed, uses=args.use or ["meetings"],
                           voice=args.voice)
            _out(args, e, f"active: appearance {'on' if e['appearance_enabled'] else 'off'}, voice "
                          f"{'on' if e['voice_enabled'] else 'off'}")
        elif a == "authorize":
            rules = json.loads(args.rules)
            r = A.authorize_meetings(args.id, actor="user", **rules)
            _out(args, r, f"meeting authorization {r['id']}")
        elif a == "revoke":
            _out(args, A.revoke(args.id, args.kind, actor="user"))
        elif a == "delete":
            r = A.delete(args.id, actor="user")
            _out(args, r, "\n".join(f"{x['item']}: {x['status']}" for x in r["report"]) + "\n" + r["retention_after"])
        elif a == "status":
            _out(args, A.overview(user))
        elif a == "demo":
            from importlib import resources
            path = str(resources.files("lucid") / "avatar" / "web" / "demo.html")
            print(f"Open in a browser: file://{path}?name=YourName\n(local loopback demonstration only)")
    except (AvatarError, KeyError, ValueError) as e:
        print(f"lucid avatar: {e}", file=sys.stderr)
        return 2
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
    from .server.app import main as serve_main
    extra = ["--db", args.db] if args.db else []
    return serve_main(extra + args.rest)


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

    iv = sub.add_parser("invent", help="idea generation and prior-art checks").add_subparsers(dest="action",
                                                                                            required=True)
    x = common(iv.add_parser("target", help="start a study: the problem and what counts as better"))
    x.add_argument("problem")
    x.add_argument("--users")
    x.add_argument("--constraint", action="append")
    x.add_argument("--existing", action="append", help="an existing approach you already know")
    x.add_argument("--improvement", help="what improvement matters")
    x.add_argument("--level", default="mechanism", choices=["mechanism", "implementation", "application",
                                                            "combination"])
    x.add_argument("--confidential", action="append", help="term never sent to external search")
    x.add_argument("--allow", action="append", help="search provider allowed (default local:reference)")
    x.add_argument("--require", action="append", help="provider that must run before any originality claim")
    x.add_argument("--ledger")
    x = common(iv.add_parser("candidate", help="add a candidate idea"))
    x.add_argument("study")
    x.add_argument("--title", required=True)
    x.add_argument("--mechanism", required=True, help="how it works")
    x.add_argument("--difference", help="what is specifically different")
    x.add_argument("--method", required=True, choices=["challenge_assumption", "combine_fields", "change_constraint",
                                                      "reverse_process", "remove_dependency", "new_method"])
    x.add_argument("--advantage")
    x.add_argument("--component", action="append")
    x.add_argument("--domain")
    x.add_argument("--revisit-of")
    x.add_argument("--revisit-reason")
    x = common(iv.add_parser("check", help="check Lucid's records, search, and assess"))
    x.add_argument("id")
    x.add_argument("--no-local", action="store_true", help="skip the bundled reference collection")
    x.add_argument("--collection", help="path to a JSON-lines reference collection")
    x.add_argument("--offline", action="append", help="record a provider as unavailable for this run")
    x = common(iv.add_parser("not-a-repeat", help="record that a close earlier record is a different idea"))
    x.add_argument("id")
    x.add_argument("match")
    x.add_argument("--reason", required=True)
    x = common(iv.add_parser("plan", help="show the planned search queries"))
    x.add_argument("id")
    x = common(iv.add_parser("search-log", help="record a search you ran yourself (including failures)"))
    x.add_argument("id")
    x.add_argument("--provider", required=True)
    x.add_argument("--query", required=True)
    x.add_argument("--kind", required=True, choices=["problem", "mechanism", "synonym", "adjacent", "component",
                                                    "combination"])
    x.add_argument("--status", required=True, choices=["ran", "unavailable", "not_authorized",
                                                      "skipped_confidential"])
    x.add_argument("--results", type=int, default=0)
    x.add_argument("--covers", help="comma list: papers,docs,products,repos,standards,patents,web")
    x.add_argument("--note")
    x = common(iv.add_parser("add-source", help="record a source you found"))
    x.add_argument("id")
    x.add_argument("--provider", required=True)
    x.add_argument("--kind", required=True, choices=["papers", "docs", "products", "repos", "standards", "patents",
                                                    "web"])
    x.add_argument("--ref", required=True)
    x.add_argument("--title", required=True)
    x.add_argument("--summary", required=True)
    x.add_argument("--inspected", action="store_true", help="you read the source itself, not just a snippet")
    x.add_argument("--relation", choices=["equivalent", "adaptation", "different", "unclear"])
    x.add_argument("--differences")
    x.add_argument("--found-by")
    x = common(iv.add_parser("inspect", help="mark a source as read and say how it relates"))
    x.add_argument("id")
    x.add_argument("--relation", choices=["equivalent", "adaptation", "different", "unclear"])
    x.add_argument("--differences")
    x = common(iv.add_parser("assess", help="recompute status, or record one role's independent verdict"))
    x.add_argument("id")
    x.add_argument("--role", choices=["ideation", "prior_art", "skeptic"])
    x.add_argument("--verdict")
    x.add_argument("--rationale")
    x.add_argument("--saw-others", action="store_true")
    x = common(iv.add_parser("value", help="test whether the idea works (separate from novelty)"))
    x.add_argument("id")
    x.add_argument("--check", action="append", help="NAME=COMMAND; exit 0 means passed")
    x.add_argument("--feasibility")
    x.add_argument("--usefulness")
    x.add_argument("--resources")
    x.add_argument("--advantage-evidence")
    x = common(iv.add_parser("gate", help="keep only ideas that look original within the searched scope"))
    x.add_argument("study")
    x.add_argument("--include-incremental", action="store_true")
    x = common(iv.add_parser("record", help="invention record"))
    x.add_argument("id")
    x.add_argument("--markdown", action="store_true")
    x.add_argument("--save", action="store_true", help="store it as an 'invention' memory")
    x = common(iv.add_parser("workers", help="delegation plan for ideation, prior-art and skeptic workers"))
    x.add_argument("study")
    x = common(iv.add_parser("show"))
    x.add_argument("study")
    pl = sub.add_parser("plain", help="plain-language check for a reply or message").add_subparsers(
        dest="action", required=True)
    x = common(pl.add_parser("check", help="flag stiff, wordy or unclear wording (reads a file or stdin)"))
    x.add_argument("file", nargs="?", default="-")
    x.add_argument("--question", help="the question being answered, to judge length")
    x.add_argument("--fix", action="store_true", help="apply only the safe phrase swaps and print the result")
    x.add_argument("--strict", action="store_true", help="exit 1 if anything is flagged")
    qa = sub.add_parser("quality", help="acceptance criteria, checks and release gates").add_subparsers(
        dest="action", required=True)
    x = common(qa.add_parser("init", help="create a plan from a JSON file"))
    x.add_argument("config")
    x.add_argument("--artifact", help="folder or file being checked (default: the config's folder)")
    common(qa.add_parser("plans"))
    x = common(qa.add_parser("show"))
    x.add_argument("plan")
    x = common(qa.add_parser("run", help="run checks: required first, optional only with time left"))
    x.add_argument("plan")
    x.add_argument("--only", action="append")
    x.add_argument("--time-budget", type=float, help="seconds")
    x.add_argument("--no-optional", action="store_true")
    x = common(qa.add_parser("record", help="record a manual check with evidence"))
    x.add_argument("plan")
    x.add_argument("key")
    x.add_argument("--passed", action="store_true")
    x.add_argument("--failed", action="store_true")
    x.add_argument("--evidence", required=True)
    x = common(qa.add_parser("defect", help="add, resolve, accept or report an escaped defect"))
    x.add_argument("op", choices=["add", "resolve", "accept", "escaped"])
    x.add_argument("target", help="plan id (add, escaped) or defect id (resolve, accept)")
    x.add_argument("--severity", choices=["blocker", "major", "minor", "trivial"])
    x.add_argument("--title")
    x.add_argument("--evidence")
    x.add_argument("--check")
    x.add_argument("--resolution")
    x.add_argument("--verified-by")
    x.add_argument("--reason")
    x = common(qa.add_parser("review", help="record a review from a JSON file"))
    x.add_argument("plan")
    x.add_argument("--file", required=True)
    x = common(qa.add_parser("compare", help="compare alternative approaches from a JSON file"))
    x.add_argument("plan")
    x.add_argument("--file", required=True)
    x = common(qa.add_parser("gate", help="evaluate the release gate (exit 0 verified, 1 failed, 3 pending)"))
    x.add_argument("plan")
    x.add_argument("--markdown", action="store_true")
    x.add_argument("--out")
    common(qa.add_parser("metrics"))
    cm = sub.add_parser("comms", help="email, Slack and Teams: drafts, reviewed sends, authorized sends").add_subparsers(
        dest="action", required=True)
    x = common(cm.add_parser("connect", help="connect an account (authorizes drafting only)"))
    x.add_argument("provider", choices=["slack", "gmail", "outlook", "teams"])
    x.add_argument("identity", help="address or user/bot id the provider sends as")
    x.add_argument("--identity-kind", required=True, choices=["user_delegated", "bot", "app", "send_as_alias"])
    x.add_argument("--token-env", help="name of an environment variable holding the token (never pass tokens as arguments)")
    x.add_argument("--display")
    x.add_argument("--workspace")
    x.add_argument("--team-scope")
    x = common(cm.add_parser("policy", help="set the standing authorization for an account"))
    x.add_argument("account")
    x.add_argument("mode", choices=["draft_only", "send_after_review", "auto"])
    x.add_argument("--rules", help='JSON, e.g. {"channels": ["C123"], "rate": {"per_hour": 5}}')
    x = common(cm.add_parser("compose"))
    x.add_argument("account")
    x.add_argument("body")
    x.add_argument("--to", action="append")
    x.add_argument("--channel")
    x.add_argument("--thread")
    x.add_argument("--subject")
    x.add_argument("--attach", action="append")
    x.add_argument("--topic")
    x.add_argument("--evidence", action="append")
    x.add_argument("--op-id")
    for name in ("preview", "audit"):
        x = common(cm.add_parser(name))
        x.add_argument("id")
    x = common(cm.add_parser("approve"))
    x.add_argument("id")
    x.add_argument("--seen", required=True, help="content hash shown by preview: approves exactly that text")
    x.add_argument("--confirm", action="append", help="flag code you have checked, e.g. recipient_not_allowed")
    common(cm.add_parser("process", help="send what is due and settle unknown outcomes"))
    x = common(cm.add_parser("outbox"))
    x.add_argument("--account")
    x = common(cm.add_parser("mark", help="settle an ambiguous send you checked yourself"))
    x.add_argument("id")
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--sent", action="store_true")
    g.add_argument("--not-sent", action="store_true")
    x.add_argument("--note")
    x = common(cm.add_parser("revoke"))
    x.add_argument("account")
    av = sub.add_parser("avatar", help="optional AI-representative avatar (your own likeness only)").add_subparsers(
        dest="action", required=True)
    x = common(av.add_parser("enroll"))
    x.add_argument("name", help="your name as shown in the disclosure")
    x.add_argument("--provider", default="local-still", choices=["local-still", "heygen", "tavus"])
    x = common(av.add_parser("consent"))
    x.add_argument("id")
    x.add_argument("kind", choices=["likeness", "voice"])
    x.add_argument("statement")
    x.add_argument("--method", default="typed_statement", choices=["typed_statement", "consent_video", "provider_terms"])
    x.add_argument("--evidence", help="media id of a consent video")
    x = common(av.add_parser("add-media"))
    x.add_argument("id")
    x.add_argument("kind", choices=["photo", "video", "voice_sample", "consent_video"])
    x.add_argument("path")
    x.add_argument("--mine", action="store_true", help="confirm this media shows you")
    for name in ("preview", "delete"):
        x = common(av.add_parser(name))
        x.add_argument("id")
    x = common(av.add_parser("activate"))
    x.add_argument("id")
    x.add_argument("--reviewed", required=True, help="sha256 of the preview you looked at")
    x.add_argument("--use", action="append", choices=["meetings", "recorded_messages", "preview_only"])
    x.add_argument("--voice", action="store_true")
    x = common(av.add_parser("authorize", help="approve meetings or meeting categories"))
    x.add_argument("id")
    x.add_argument("--rules", required=True, help="JSON: categories/meetings, hosts, knowledge, max_duration_s, max_cost_usd, ...")
    x = common(av.add_parser("revoke"))
    x.add_argument("id")
    x.add_argument("--kind", choices=["likeness", "voice"])
    common(av.add_parser("status"))
    common(av.add_parser("demo", help="path of the local WebRTC demonstration page"))
    x = common(sub.add_parser("capabilities", help="host capability matrix"))
    x.add_argument("--host")
    sy = sub.add_parser("sync").add_subparsers(dest="action", required=True)
    common(sy.add_parser("status"))
    common(sy.add_parser("recover"))

    x = sub.add_parser("serve", help="run dashboard + API (localhost); see `lucid serve --help`")
    x.add_argument("rest", nargs=argparse.REMAINDER)
    common(sub.add_parser("mcp", help="run MCP server on stdio"))
    x = sub.add_parser("eval", help="evaluation runner")
    x.add_argument("rest", nargs=argparse.REMAINDER)
    return p


DISPATCH = {"memory": cmd_memory, "import": cmd_import, "conv": cmd_conv, "handoff": cmd_handoff,
            "share": cmd_share, "task": cmd_task, "budget": cmd_budget, "decide": cmd_decide,
            "monitor": cmd_monitor, "capabilities": cmd_capabilities, "sync": cmd_sync, "serve": cmd_serve,
            "mcp": cmd_mcp, "eval": cmd_eval, "invent": cmd_invent, "plain": cmd_plain, "quality": cmd_quality, "comms": cmd_comms, "avatar": cmd_avatar}


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
