import threading
import time

import pytest

from lucid.budget import InsufficientBudget, Usage
from lucid.core import Lucid
from lucid.decisions import DecisionError, DecisionScheduler
from lucid.memory import Scope
from lucid.util import Clock


# ----------------------------------------------------------------------------- budget
def test_reservations_atomic_under_contention(dbpath):
    """8 threads, each with its own connection (like separate processes), race for 10 slots."""
    main = Lucid(dbpath)
    lid = main.budget.create("race", token_limit=10_000, reserve_fraction=0.0, mode="hard")
    ok, denied = [], []
    barrier = threading.Barrier(8)

    def worker():
        lu = Lucid(dbpath)
        barrier.wait()
        for _ in range(5):
            try:
                ok.append(lu.budget.reserve(lid, purpose="w", input_tokens=500, max_output_tokens=500))
            except InsufficientBudget:
                denied.append(1)
        lu.close()
    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    st = main.budget.status(lid)
    assert len(ok) == 10 and len(denied) == 30
    assert st["committed_tokens"] == 10_000 and st["available_tokens"] == 0
    main.close()


def test_reserve_holdback_reconcile_unknown_and_late_report(L):
    lid = L.budget.create("task", token_limit=10_000, reserve_fraction=0.2)
    r1 = L.budget.reserve(lid, purpose="worker", input_tokens=3000, max_output_tokens=3000)
    with pytest.raises(InsufficientBudget):
        L.budget.reserve(lid, purpose="worker2", input_tokens=1500, max_output_tokens=1000)  # would eat the reserve
    v = L.budget.reserve(lid, purpose="final verification", input_tokens=1000, max_output_tokens=1000,
                         use_reserve=True)
    rep = L.budget.reconcile(r1, Usage(input_tokens=2500, output_tokens=400, cached_tokens=2000))
    assert rep["charged"] == 2900  # cached tokens are a subset of input: not double counted
    L.budget.mark_unknown(v)  # canceled / timed out: keep the conservative hold
    st = L.budget.status(lid)
    assert st["used_tokens"] == 2900 and st["unknown_held_tokens"] == 2000
    L.budget.reconcile(v, Usage(input_tokens=900, output_tokens=50))  # late usage report
    st = L.budget.status(lid)
    assert st["unknown_held_tokens"] == 0 and st["used_tokens"] == 3850
    assert "estimated" in st["coverage_note"]


def test_reasoning_tokens_and_context_window(L):
    lid = L.budget.create("r", token_limit=100_000)
    r = L.budget.reserve(lid, purpose="x", input_tokens=10, max_output_tokens=10)
    L.budget.reconcile(r, Usage(input_tokens=10, output_tokens=5, reasoning_tokens=7, reasoning_in_output=False))
    assert L.budget.status(lid)["used_tokens"] == 22
    with pytest.raises(InsufficientBudget, match="context window"):
        L.budget.reserve(lid, purpose="big", input_tokens=150_000, max_output_tokens=1, context_window=128_000)


def test_time_budget(L, clock):
    lid = L.budget.create("t", time_limit_s=60)
    clock.advance(61)
    with pytest.raises(InsufficientBudget, match="time"):
        L.budget.reserve(lid, purpose="x", input_tokens=1, max_output_tokens=1)


# ----------------------------------------------------------------------------- decisions
def test_timeout_applies_authorized_default_once(L, clock):
    applied = []
    L.decisions.on_default["internal_naming"] = lambda q: applied.append(q["default_option"]) or "renamed"
    L.decisions.authorize("internal_naming")
    q = L.decisions.ask("Name the helper module", ["util", "helpers"], category="internal_naming", default="util")
    assert "will be applied" in q["on_timeout"]
    assert q["deadline"] > clock.iso()  # default 120s
    clock.advance(119)
    assert L.decisions.tick() == []
    clock.advance(2)
    out = L.decisions.tick()
    assert out[0]["status"] == "defaulted" and applied == ["util"]
    assert L.decisions.tick() == [] and applied == ["util"]  # exactly once


def test_unauthorized_or_required_never_defaulted(L, clock):
    q1 = L.decisions.ask("Folder layout?", ["flat", "nested"], category="file_organization", default="flat",
                         timeout_s=5)
    q2 = L.decisions.ask("Spend $50 on API calls?", ["yes", "no"], category="spending", default="no", timeout_s=5)
    assert "blocked" in q1["on_timeout"] and q2["required_approval"] == 1
    with pytest.raises(DecisionError):
        L.decisions.authorize("spending")
    with pytest.raises(DecisionError):
        L.decisions.authorize("external_destination")
    clock.advance(10)
    st = {r["id"]: r["status"] for r in L.decisions.tick()}
    assert st == {q1["id"]: "blocked", q2["id"]: "blocked"}


def test_answer_before_deadline_wins_and_late_answer_reported(L, clock):
    L.decisions.authorize("presentation")
    q = L.decisions.ask("Chart style?", ["bar", "line"], category="presentation", default="bar", timeout_s=30)
    assert L.decisions.answer(q["id"], "line")["late"] is False
    clock.advance(31)
    assert L.decisions.tick() == []
    assert L.decisions.get(q["id"])["answer"] == "line"

    q2 = L.decisions.ask("Table style?", ["plain", "striped"], category="presentation", default="plain", timeout_s=30)
    clock.advance(31)
    L.decisions.tick()
    late = L.decisions.answer(q2["id"], "striped")
    assert late["late"] and late["applied"] == "plain" and "no reversal hook" in late["reversal"]
    assert L.decisions.get(q2["id"])["late_responses"][0]["answer"] == "striped"


def test_deadline_race_resolves_exactly_once(dbpath, clock):
    for _ in range(20):
        a = Lucid(dbpath, clock)
        b = Lucid(dbpath, clock)
        a.decisions.authorize("ordering")
        q = a.decisions.ask("Order?", ["x", "y"], category="ordering", default="x", timeout_s=1)
        clock.advance(1)
        results = {}
        t1 = threading.Thread(target=lambda: results.setdefault("tick", a.decisions.tick()))
        t2 = threading.Thread(target=lambda: results.setdefault("ans", b.decisions.answer(q["id"], "y")))
        t1.start(); t2.start(); t1.join(); t2.join()
        final = a.decisions.get(q["id"])
        assert final["status"] in ("answered", "defaulted")
        if final["status"] == "answered":
            assert results["ans"]["late"] is False and results["tick"] == []
        else:
            assert results["ans"]["late"] is True
        a.close(); b.close()


def test_restart_does_not_repeat_default_action(dbpath, clock):
    effects = []
    a = Lucid(dbpath, clock)
    a.decisions.authorize("ordering")
    a.decisions.on_default["ordering"] = lambda q: effects.append(q["id"])
    a.decisions.ask("Order?", ["x", "y"], category="ordering", default="x", timeout_s=1)
    clock.advance(2)
    a.decisions.tick()
    a.close()
    b = Lucid(dbpath, clock)
    b.decisions.on_default["ordering"] = lambda q: effects.append(q["id"])
    b.decisions.tick()
    assert len(effects) == 1
    b.close()


def test_remembered_preference_skips_question(L):
    q = L.decisions.ask("Test runner?", ["pytest", "unittest"], category="equivalent_implementation", default="pytest",
                        pref_key="decision:test-runner")
    L.decisions.answer(q["id"], "unittest", remember=True, pref_key="decision:test-runner")
    again = L.decisions.ask("Test runner?", ["pytest", "unittest"], category="equivalent_implementation",
                            default="pytest", pref_key="decision:test-runner")
    assert again["status"] == "answered_from_preference" and again["answer"] == "unittest"


def test_controls_extend_pause_cancel(L, clock):
    L.decisions.authorize("ordering")
    q = L.decisions.ask("Order?", ["a", "b"], category="ordering", default="a", timeout_s=10)
    L.decisions.extend(q["id"], 100)
    clock.advance(50)
    assert L.decisions.tick() == []
    assert L.decisions.pause(q["id"])
    clock.advance(500)
    assert L.decisions.tick() == []  # paused questions are not defaulted
    assert L.decisions.cancel(q["id"]) and L.decisions.get(q["id"])["status"] == "canceled"


def test_scheduler_uses_runtime_timer(dbpath):
    lu = Lucid(dbpath, Clock())
    lu.decisions.authorize("ordering")
    q = lu.decisions.ask("Order?", ["a", "b"], category="ordering", default="a", timeout_s=0.3)
    sch = DecisionScheduler(lu.decisions, max_sleep_s=5)
    sch.start()
    t0 = time.time()
    while lu.decisions.get(q["id"])["status"] == "open" and time.time() - t0 < 5:
        time.sleep(0.05)
    sch.stop()
    assert lu.decisions.get(q["id"])["status"] == "defaulted"
    assert time.time() - t0 < 3
    lu.close()


def test_external_action_authorization_needs_scope(L):
    with pytest.raises(DecisionError):
        L.decisions.authorize_action("post_slack", scope="*", limits={"max_count": 1})
    a = L.decisions.authorize_action("post_slack", scope="channel:#builds", limits={"max_count": 3})
    assert a["category"] == "action:post_slack"


def test_review_regressions_decisions_and_budget(L, clock):
    # memory op ids cannot collide with action keys
    L.decisions.authorize("ordering")
    ran = []
    L.decisions.on_default["ordering"] = lambda q: ran.append(1) or "ok"
    q = L.decisions.ask("x?", ["a", "b"], category="ordering", default="a", timeout_s=1)
    L.memory.add("collide", op_id=f"default:{q['id']}")
    clock.advance(2)
    L.decisions.tick()
    assert ran == [1]
    # blocked questions can still be answered
    qb = L.decisions.ask("Spend?", ["yes", "no"], category="spending", timeout_s=1)
    clock.advance(2)
    L.decisions.tick()
    r = L.decisions.answer(qb["id"], "no")
    assert r["status"] == "answered" and r["late"] is False
    # scope without '*' is exact
    L.decisions.authorize("file_organization", scope="repo/a")
    assert L.decisions.find_authorization("file_organization", "repo/abc-secret") is None
    assert L.decisions.find_authorization("file_organization", "repo/a")
    # failed default actions are retried on a later tick; replay returns the original result
    calls = []

    def flaky(q):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("network")
        return "applied"
    L.decisions.authorize("presentation")
    L.decisions.on_default["presentation"] = flaky
    qf = L.decisions.ask("Theme?", ["light", "dark"], category="presentation", default="light", timeout_s=1)
    clock.advance(2)
    L.decisions.tick()
    L.decisions.tick()
    assert len(calls) == 2 and L.decisions.actions.state(f"default:{qf['id']}")["state"] == "done"
    assert L.decisions.actions.run_once(f"default:{qf['id']}", lambda: "again") == (False, "applied")
    # cost limit holds after an overrun even without per-call estimates
    lid = L.budget.create("c", cost_limit=1.0)
    r1 = L.budget.reserve(lid, purpose="x", input_tokens=1, max_output_tokens=1, est_cost=0.5)
    L.budget.reconcile(r1, Usage(input_tokens=1, output_tokens=1, cost=5.0))
    with pytest.raises(InsufficientBudget):
        L.budget.reserve(lid, purpose="y", input_tokens=1, max_output_tokens=1)
