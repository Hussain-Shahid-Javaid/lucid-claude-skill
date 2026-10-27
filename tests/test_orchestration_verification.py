import threading
import time

import pytest

from lucid.budget import Usage
from lucid.orchestrator import Coordinator, FunctionRunner, HostNativeRunner, WorkerResult, paths_overlap
from lucid.tasks import NoProgress
from lucid.verification import CorrectionCycle, arithmetic_claims_check, python_tests_check


def _ok(ctx, **kw):
    return WorkerResult(output=ctx.task["title"], acceptance={"ac1": (True, "done")}, **kw)


def test_dependency_graph_concurrency_and_integration(L):
    co = Coordinator(L.store, concurrency=3)
    a = co.spawn(None, "A", acceptance=["a"], owned_paths=["src/a"])
    b = co.spawn(None, "B", acceptance=["b"], owned_paths=["src/b"])
    c = co.spawn(None, "C", acceptance=["c"], owned_paths=["src/c"], depends_on=[a["id"], b["id"]])
    live, peak, lock = [0], [0], threading.Lock()

    def work(ctx):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        if ctx.task["title"] == "C":
            assert set(ctx.dependency_results.values()) == {"A", "B"}
        return _ok(ctx, changed_paths=[f"src/{ctx.task['title'].lower()}/x.py"])
    res = co.run([a["id"], b["id"], c["id"]], FunctionRunner(work),
                 verify=lambda r: (set(r.values()) == {"A", "B", "C"}, "all results integrated"))
    assert res["statuses"] == {a["id"]: "completed", b["id"]: "completed", c["id"]: "completed"}
    assert res["verification"]["passed"] and peak[0] == 2


def test_file_ownership_serializes_and_rejects_violations(L):
    co = Coordinator(L.store, concurrency=3)
    t1 = co.spawn(None, "edit1", acceptance=["x"], owned_paths=["src/shared"])
    t2 = co.spawn(None, "edit2", acceptance=["x"], owned_paths=["src/shared/mod.py"])
    t3 = co.spawn(None, "rogue", acceptance=["x"], owned_paths=["docs"])
    active, overlap = set(), []

    def work(ctx):
        title = ctx.task["title"]
        if title.startswith("edit"):
            if active & {"edit1", "edit2"}:
                overlap.append(title)
            active.add(title)
            time.sleep(0.05)
            active.discard(title)
            return _ok(ctx, changed_paths=["src/shared/mod.py"])
        return _ok(ctx, changed_paths=["src/shared/mod.py"])  # outside owned 'docs'
    res = co.run([t1["id"], t2["id"], t3["id"]], FunctionRunner(work))
    assert overlap == []
    assert res["statuses"][t3["id"]] == "failed"
    assert L.tasks.get(t3["id"])["result"]["error"] == "edited files outside owned paths"
    assert paths_overlap(["src/*.py"], ["src/a.py"]) and not paths_overlap(["src/a"], ["src/ab"])


def test_budget_exhaustion_checkpoints_instead_of_starting(L):
    lid = L.budget.create("b", token_limit=3000, reserve_fraction=0.3, mode="hard")
    co = Coordinator(L.store, concurrency=1)
    ts = [co.spawn(None, f"T{i}", acceptance=["x"]) for i in range(3)]
    res = co.run([t["id"] for t in ts], FunctionRunner(
        lambda ctx: WorkerResult(output=1, usage=Usage(900, 100), acceptance={"ac1": (True, "ok")}),
        est=(500, 500)), ledger_id=lid, verify=lambda r: (True, "checked"))
    statuses = sorted(res["statuses"].values())
    assert statuses == ["budget_exhausted", "completed", "completed"]
    assert res["verification"]["passed"]  # verification still ran: it draws on the held-back reserve
    ex = [t for t in ts if L.tasks.get(t["id"])["status"] == "budget_exhausted"][0]
    assert "not_dispatched" in L.tasks.get(ex["id"])["checkpoint"]


def test_cancel_propagates_and_external_action_runs_once(L):
    co = Coordinator(L.store, concurrency=2)
    q_task = co.spawn(None, "slow", acceptance=["x"])
    L.decisions.authorize("ordering")
    q = L.decisions.ask("x?", ["a", "b"], category="ordering", default="a", task_id=q_task["id"])
    co.decisions = L.decisions
    later = co.spawn(None, "later", acceptance=["x"], depends_on=[q_task["id"]])
    sent = []

    def work(ctx):
        for _ in range(3):  # two "parallel" attempts to perform the same side effect
            ctx.actions.run_once("notify:build-green", lambda: sent.append(1) or "sent")
        co.cancel()
        ctx.token.checkpoint()
        return _ok(ctx)
    res = co.run([q_task["id"], later["id"]], FunctionRunner(work))
    assert sent == [1]
    assert res["statuses"][q_task["id"]] == "canceled"
    assert res["statuses"][later["id"]] in ("canceled", "blocked")
    assert L.decisions.get(q["id"])["status"] == "canceled"


def test_delegation_depth_bounded_and_host_native_plan(L):
    co = Coordinator(L.store, max_depth=1)
    root = co.spawn(None, "root")
    child = co.spawn(root, "child")
    with pytest.raises(RuntimeError):
        co.spawn(child, "grandchild")
    t = co.spawn(None, "delegate", objective="refactor X", owned_paths=["src/x"], acceptance=["tests pass"])
    res = co.run([t["id"]], HostNativeRunner())
    assert res["results"][t["id"]]["delegate_to"] == "host-native subagent"
    assert res["statuses"][t["id"]] == "partially_completed"  # plan emitted, nothing verified yet


def test_completion_rules_and_no_progress(L):
    t = L.tasks.create("ship", acceptance=["tests pass", {"description": "docs polish", "essential": False}])
    L.tasks.record_check(t["id"], "ac1", True, "pytest: 10 passed")
    assert L.tasks.finish(t["id"])["status"] == "completed"
    t2 = L.tasks.create("vague")
    assert L.tasks.finish(t2["id"])["status"] == "partially_completed"
    t3 = L.tasks.create("loop", acceptance=["x"])
    L.tasks.record_attempt(t3["id"], "retry same patch", False)
    with pytest.raises(NoProgress):
        L.tasks.record_attempt(t3["id"], "retry same patch", False)
    rep = L.tasks.finish(t3["id"], budget_exhausted=True)
    assert rep["status"] == "budget_exhausted" and rep["continuation_plan"]


# ----------------------------------------------------------------------------- correction cycle
BUGGY = "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs)//2]\n"
FIXED = ("def median(xs):\n    xs = sorted(xs)\n    n = len(xs)\n    m = n // 2\n"
         "    return xs[m] if n % 2 else (xs[m-1] + xs[m]) / 2\n")
TESTS = "assert median([3,1,2]) == 2\nassert median([4,1,2,3]) == 2.5\nassert median([5]) == 5\n"


def test_seeded_bug_corrected_by_independent_tests(L):
    cyc = CorrectionCycle(L.store, max_iterations=3)
    res = cyc.run("median", BUGGY, {"tests": python_tests_check(TESTS)}, lambda cur, fb: FIXED,
                  oracle=lambda src: python_tests_check(TESTS)(src)[0])
    assert res.passed_all and res.final == FIXED and res.initially_correct is False and res.finally_correct
    row = L.store.one("SELECT * FROM verifications")
    assert row["changed"] == 1 and row["finally_correct"] == 1


def test_correct_answer_preserved_against_harmful_review(L):
    cyc = CorrectionCycle(L.store)
    # A reviewer that "improves" the answer by breaking it is rejected by the regression guard.
    res = cyc.run("median", FIXED, {"tests": python_tests_check(TESTS)}, lambda cur, fb: BUGGY,
                  oracle=lambda s: python_tests_check(TESTS)(s)[0])
    assert res.iterations == 0 and res.final == FIXED and not res.corrupted
    # Partial check set: style check fails, the "fix" breaks behaviour -> rejected, original kept.
    style = lambda s: ("# reviewed" in s, "needs review marker")
    res2 = cyc.run("median", FIXED, {"tests": python_tests_check(TESTS), "style": style},
                   lambda cur, fb: "# reviewed\n" + BUGGY, oracle=lambda s: python_tests_check(TESTS)(s)[0])
    assert res2.final == FIXED and not res2.corrupted and "broke" in res2.stop_reason


def test_bounded_loop_stops_without_new_evidence(L):
    calls = []
    cyc = CorrectionCycle(L.store, max_iterations=10)
    res = cyc.run("x", "1 + 1 = 3", {"math": arithmetic_claims_check}, lambda c, fb: calls.append(1) or c)
    assert not res.passed_all and len(calls) == 1 and "no new evidence" in res.stop_reason
    fixed = cyc.run("x", "1 + 1 = 3", {"math": arithmetic_claims_check}, lambda c, fb: "1 + 1 = 2")
    assert fixed.passed_all


def test_monitor_calibration(L):
    m = L.monitor
    ids = [m.predict(f"claim {i}", c) for i, c in enumerate([0.9, 0.9, 0.2, 0.6])]
    for pid, o in zip(ids, [True, False, False, True]):
        m.resolve_prediction(pid, o)
    with pytest.raises(ValueError):
        m.resolve_prediction(ids[0], False)  # write-once outcomes
    cal = m.calibration()
    assert cal["n"] == 4 and cal["brier"] == round((0.01 + 0.81 + 0.04 + 0.16) / 4, 4)
