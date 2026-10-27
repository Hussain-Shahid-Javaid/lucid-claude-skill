"""Parallel agent coordinator.

* Task dependency graph; a task runs only when its dependencies completed.
* Configurable concurrency (default 3) and bounded delegation depth/retries.
* Every dispatch reserves budget first; tasks that cannot fit are checkpointed as
  `budget_exhausted` instead of being started.
* Conflicting edits are prevented by declared file ownership: two running tasks
  never own overlapping paths, and a worker's changes outside its owned paths are
  rejected.
* Pause/cancel propagate to pending tasks, running workers (cooperative token) and
  open questions.
* External side effects go through ActionLedger (exactly once), so parallelism
  cannot duplicate them.

Runners:
* FunctionRunner – in-process callables (tests, demos, deterministic tools).
* HostNativeRunner – emits a delegation plan for host-native subagents (e.g. Claude
  Code Task tool). It does not execute anything itself.
* AnthropicRunner – optional paid API worker; refuses to run unless explicitly
  enabled and given a budget ledger.
"""
from __future__ import annotations

import concurrent.futures as cf
import fnmatch
import json
import os
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .budget import BudgetLedger, InsufficientBudget, Usage
from .db import Store
from .decisions import ActionLedger, Decisions
from .tasks import TERMINAL, Tasks


class CancelToken:
    def __init__(self):
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._pause.set()  # set == running

    def cancel(self):
        self._cancel.set()
        self._pause.set()

    @property
    def canceled(self) -> bool:
        return self._cancel.is_set()

    def pause(self):
        self._pause.clear()

    def resume(self):
        self._pause.set()

    def checkpoint(self, timeout: float | None = None) -> None:
        """Workers call this between steps: blocks while paused, raises if canceled."""
        self._pause.wait(timeout)
        if self._cancel.is_set():
            raise Canceled()


class Canceled(Exception):
    pass


@dataclass
class WorkerContext:
    task: dict
    token: CancelToken
    actions: ActionLedger
    dependency_results: dict[str, Any]


@dataclass
class WorkerResult:
    output: Any
    changed_paths: list[str] = field(default_factory=list)
    usage: Usage | None = None
    acceptance: dict[str, tuple[bool, str]] = field(default_factory=dict)  # check_id -> (passed, evidence)


class Runner(Protocol):
    def estimate(self, task: dict) -> tuple[int, int]: ...  # (input_tokens, max_output_tokens)
    def run(self, ctx: WorkerContext) -> WorkerResult: ...


class FunctionRunner:
    def __init__(self, fn: Callable[[WorkerContext], WorkerResult], est: tuple[int, int] = (500, 500)):
        self.fn = fn
        self.est = est

    def estimate(self, task):
        return self.est

    def run(self, ctx):
        return self.fn(ctx)


class HostNativeRunner:
    """Produces instructions for the host's own subagents; Lucid only tracks state."""

    def estimate(self, task):
        return (0, 1)

    def run(self, ctx):
        t = ctx.task
        plan = {"delegate_to": "host-native subagent", "objective": t["objective"] or t["title"],
                "owned_paths": t["owned_paths"], "acceptance": [a["description"] for a in t["acceptance"]],
                "report_back": f"lucid task check {t['id']} <check_id> --passed|--failed --evidence '...'"}
        return WorkerResult(output=plan)


class AnthropicRunner:
    """Optional paid worker. Never runs implicitly."""

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 2048):
        if os.environ.get("LUCID_ENABLE_PAID_CALLS") != "1":
            raise PermissionError("paid model calls are disabled; set LUCID_ENABLE_PAID_CALLS=1 and configure a budget")
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise PermissionError("no ANTHROPIC_API_KEY configured")
        self.model, self.max_tokens = model, max_tokens

    def estimate(self, task):
        return (len((task["objective"] or task["title"])) // 3 + 200, self.max_tokens)

    def run(self, ctx):
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens,
                           "messages": [{"role": "user", "content": ctx.task["objective"] or ctx.task["title"]}]})
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body.encode(), method="POST",
                                     headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                              "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 (fixed https URL)
            data = json.loads(r.read())
        u = data.get("usage", {})
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return WorkerResult(output=text, usage=Usage(
            input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0),
            output_tokens=u.get("output_tokens", 0), cached_tokens=u.get("cache_read_input_tokens", 0)))


def paths_overlap(a: list[str], b: list[str]) -> bool:
    for x in a:
        for y in b:
            xs, ys = x.rstrip("/"), y.rstrip("/")
            if xs == ys or xs.startswith(ys + "/") or ys.startswith(xs + "/"):
                return True
            if fnmatch.fnmatch(xs, ys) or fnmatch.fnmatch(ys, xs):
                return True
            if any(ch in p for p in (xs, ys) for ch in "*?["):
                # Conservative for globs: overlapping literal prefixes are treated as overlap.
                px, py = _literal_prefix(xs), _literal_prefix(ys)
                if px.startswith(py) or py.startswith(px):
                    return True
    return False


def _literal_prefix(p: str) -> str:
    for i, ch in enumerate(p):
        if ch in "*?[":
            return p[:i]
    return p


def within(path: str, owned: list[str]) -> bool:
    p = path.rstrip("/")
    return any(p == o.rstrip("/") or p.startswith(o.rstrip("/") + "/") or fnmatch.fnmatch(p, o) for o in owned)


class Coordinator:
    def __init__(self, store: Store, *, concurrency: int = 3, max_depth: int = 2, max_retries: int = 1,
                 worker_timeout_s: float = 3600, decisions: Decisions | None = None):
        self.s = store
        self.tasks = Tasks(store)
        self.budget = BudgetLedger(store)
        self.actions = ActionLedger(store)
        self.decisions = decisions
        self.concurrency = concurrency
        self.max_depth = max_depth
        self.max_retries = max_retries
        self.worker_timeout_s = worker_timeout_s
        self.token = CancelToken()
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def _event(self, kind: str, **kw):
        with self._lock:
            self.events.append({"event": kind, "at": self.s.clock.iso(), **kw})
        self.s.log("coordinator", kind, "task", kw.get("task_id"), kw)

    def spawn(self, parent: dict | None, title: str, **kw) -> dict:
        depth = (parent["depth"] + 1) if parent else 0
        if depth > self.max_depth:
            raise RuntimeError(f"delegation depth {depth} exceeds limit {self.max_depth}")
        return self.tasks.create(title, parent_id=parent["id"] if parent else None, depth=depth, **kw)

    def cancel(self, root_id: str | None = None) -> None:
        self.token.cancel()
        for t in self.tasks.list():
            if root_id is not None and t["parent_id"] != root_id and t["id"] != root_id:
                continue
            if t["status"] in ("pending", "paused", "running"):
                if t["status"] != "running":  # running workers stop at their next token checkpoint
                    self.tasks.set_status(t["id"], "canceled")
                if self.decisions:
                    self.decisions.cancel_for_task(t["id"])
        self._event("cancel", task_id=root_id)

    def pause(self):
        self.token.pause()
        self._event("pause")

    def resume(self):
        self.token.resume()
        self._event("resume")

    def run(self, task_ids: list[str], runner: Runner | dict[str, Runner], *, ledger_id: str | None = None,
            verify: Callable[[dict[str, Any]], tuple[bool, str]] | None = None) -> dict:
        """Run tasks respecting dependencies, ownership, budget and concurrency."""
        runners = runner if isinstance(runner, dict) else {}
        default_runner = None if isinstance(runner, dict) else runner
        pending = set(task_ids)
        running: dict[cf.Future, dict] = {}
        results: dict[str, Any] = {}
        retries: dict[str, int] = {}
        with cf.ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="lucid-worker") as pool:
            while pending or running:
                if self.token.canceled:
                    for tid in list(pending):
                        self.tasks.set_status(tid, "canceled")
                        if self.decisions:
                            self.decisions.cancel_for_task(tid)
                    pending.clear()
                dispatched = False
                for tid in sorted(pending):
                    if len(running) >= self.concurrency:
                        break
                    t = self.tasks.get(tid)
                    deps = [self.tasks.get(d) for d in t["depends_on"]]
                    if any(d["status"] in TERMINAL and d["status"] != "completed" for d in deps):
                        self.tasks.set_status(tid, "blocked", result={"reason": "dependency did not complete"})
                        pending.discard(tid)
                        self._event("blocked", task_id=tid, reason="dependency failed")
                        continue
                    if any(d["status"] != "completed" for d in deps):
                        continue
                    busy = [p for rt in running.values() for p in rt["task"]["owned_paths"]]
                    if t["owned_paths"] and busy and paths_overlap(t["owned_paths"], busy):
                        continue  # serialize: wait for the owner to finish
                    r = runners.get(tid, default_runner)
                    if r is None:
                        raise ValueError(f"no runner for {tid}")
                    res_id = None
                    if ledger_id:
                        inp, out = r.estimate(t)
                        try:
                            res_id = self.budget.reserve(ledger_id, purpose=f"worker:{t['title']}",
                                                         input_tokens=inp, max_output_tokens=out, actor=tid)
                        except InsufficientBudget as e:
                            self.tasks.checkpoint(tid, {"not_dispatched": str(e)})
                            self.tasks.set_status(tid, "budget_exhausted", result={"reason": str(e)})
                            pending.discard(tid)
                            self._event("budget_exhausted", task_id=tid, reason=str(e))
                            continue
                    self.tasks.set_status(tid, "running")
                    ctx = WorkerContext(task=t, token=self.token, actions=self.actions,
                                        dependency_results={d["id"]: results.get(d["id"]) for d in deps})
                    fut = pool.submit(r.run, ctx)
                    running[fut] = {"task": t, "reservation": res_id}
                    pending.discard(tid)
                    dispatched = True
                    self._event("dispatch", task_id=tid, title=t["title"])
                if not running:
                    if pending and not dispatched:
                        for tid in pending:  # unsatisfiable (cycle or missing deps)
                            self.tasks.set_status(tid, "blocked", result={"reason": "unsatisfiable dependencies"})
                        pending.clear()
                    continue
                done, _ = cf.wait(list(running), timeout=self.worker_timeout_s, return_when=cf.FIRST_COMPLETED)
                if not done:
                    for fut, info in list(running.items()):
                        if info["reservation"]:
                            self.budget.mark_unknown(info["reservation"])
                        self.tasks.set_status(info["task"]["id"], "failed", result={"reason": "worker timeout"})
                        running.pop(fut)
                    continue
                for fut in done:
                    info = running.pop(fut)
                    t = info["task"]
                    tid = t["id"]
                    try:
                        wr: WorkerResult = fut.result()
                    except Canceled:
                        if info["reservation"]:
                            self.budget.mark_unknown(info["reservation"])
                        self.tasks.set_status(tid, "canceled")
                        self._event("canceled", task_id=tid)
                        continue
                    except Exception as e:
                        if info["reservation"]:
                            self.budget.mark_unknown(info["reservation"])
                        retries[tid] = retries.get(tid, 0) + 1
                        if retries[tid] <= self.max_retries and not self.token.canceled:
                            self.tasks.set_status(tid, "pending")
                            pending.add(tid)
                            self._event("retry", task_id=tid, error=str(e))
                        else:
                            self.tasks.set_status(tid, "failed", result={"error": f"{type(e).__name__}: {e}"})
                            self._event("failed", task_id=tid, error=str(e))
                        continue
                    if info["reservation"]:
                        if wr.usage:
                            self.budget.reconcile(info["reservation"], wr.usage)
                        else:
                            self.budget.mark_unknown(info["reservation"])
                    outside = [p for p in wr.changed_paths if not within(p, t["owned_paths"])]
                    if outside:
                        self.tasks.set_status(tid, "failed", result={"error": "edited files outside owned paths",
                                                                     "paths": outside})
                        self._event("ownership_violation", task_id=tid, paths=outside)
                        continue
                    for cid, (ok, ev) in wr.acceptance.items():
                        self.tasks.record_check(tid, cid, ok, ev)
                    results[tid] = wr.output
                    report = self.tasks.finish(tid)
                    self._event("finished", task_id=tid, status=report["status"])
        final = {"results": results, "statuses": {tid: self.tasks.get(tid)["status"] for tid in task_ids}}
        if verify and not self.token.canceled:
            res_id = None
            if ledger_id:
                try:
                    res_id = self.budget.reserve(ledger_id, purpose="final verification", input_tokens=200,
                                                 max_output_tokens=200, use_reserve=True)
                except InsufficientBudget as e:
                    final["verification"] = {"passed": False, "evidence": f"not run: {e}"}
            if "verification" not in final:
                ok, ev = verify(results)
                final["verification"] = {"passed": ok, "evidence": ev}
                if res_id:
                    self.budget.reconcile(res_id, Usage(input_tokens=0, output_tokens=0))
        return final
