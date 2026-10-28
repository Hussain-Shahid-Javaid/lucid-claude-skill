"""Quality plans, revision-bound evidence, weakening detection, reviews and release gates."""
import json
import os
import subprocess
import sys

import pytest

from lucid.quality import QualityError, content_hash

PY = sys.executable


@pytest.fixture
def Q(L):
    return L.quality


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "proj"
    (d / "src").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "docs").mkdir()
    (d / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (d / "tests" / "check.py").write_text("import sys\nsys.path.insert(0, 'src')\nfrom app import add\n"
                                          "assert add(2, 2) == 4\n")
    (d / "docs" / "README.md").write_text("# app\n")
    return d


def lint_cmd():
    return [PY, "-c", "import ast,sys; ast.parse(open('src/app.py').read())"]


def unit_cmd():
    return [PY, "tests/check.py"]


def standard(Q, proj, **kw):
    p = Q.create_plan("demo", str(proj), profile="standard", criteria=[
        {"category": "correctness", "text": "add() returns the sum"},
        {"category": "communication", "text": "README explains usage", "mandatory": False}], **kw)
    Q.add_check(p["id"], "lint", "lint", "parses", command=lint_cmd(), inputs=["src"])
    Q.add_check(p["id"], "unit", "unit", "unit tests", command=unit_cmd(), inputs=["src", "tests"], guards=["tests"])
    return p["id"]


def test_pending_until_run_then_verified(Q, proj):
    pid = standard(Q, proj)
    g = Q.gate(pid)
    assert g["status"] == "verification_pending"
    assert {"lint: not run", "unit: not run"} <= set(g["blockers"])
    Q.run_all(pid)
    g = Q.gate(pid)
    assert g["status"] == "verified" and g["blockers"] == []
    assert g["verified_scope"]["statement"] == "No known blocking defects within the verified scope."
    assert g["verified_scope"]["criteria"] == ["add() returns the sum"]
    assert all(x["revision"].startswith("sha256:") for x in g["checks_performed"])


def test_failing_mandatory_check_fails_the_gate(Q, proj):
    pid = standard(Q, proj)
    (proj / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    Q.run_all(pid)
    g = Q.gate(pid)
    assert g["status"] == "failed" and "unit: failed on the current files" in g["blockers"]


def test_optional_failure_does_not_block(Q, proj):
    pid = standard(Q, proj)
    Q.add_check(pid, "style", "format", "optional polish", mandatory=False, command=[PY, "-c", "raise SystemExit(1)"])
    Q.run_all(pid)
    assert Q.gate(pid)["status"] == "verified"


def test_edits_make_only_affected_evidence_stale(Q, proj):
    pid = standard(Q, proj)
    Q.run_all(pid)
    (proj / "docs" / "README.md").write_text("# app\nmore docs\n")  # outside both checks' inputs
    assert Q.gate(pid)["status"] == "verified"
    (proj / "tests" / "check.py").write_text((proj / "tests" / "check.py").read_text() + "assert add(1, 1) == 2\n")
    g = Q.gate(pid)
    assert g["status"] == "verification_pending"
    assert "unit: evidence is for older files" in g["blockers"] and not any(b.startswith("lint") for b in g["blockers"])
    res = Q.run_all(pid)
    assert {r["key"]: r["reused"] for r in res["ran"]} == {"lint": True, "unit": False}
    assert Q.gate(pid)["status"] == "verified"


def test_pass_after_weakened_tests_is_held_for_review(Q, proj):
    pid = standard(Q, proj)
    (proj / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    Q.run_all(pid)
    # "fix" by weakening the test instead of the code
    (proj / "tests" / "check.py").write_text("import sys\nsys.path.insert(0, 'src')\nfrom app import add\n")
    Q.run_all(pid)
    g = Q.gate(pid)
    assert g["status"] == "verification_pending"
    assert g["weakening_flags"] and "guarded files (tests)" in g["weakening_flags"][0]["detail"]
    Q.add_review(pid, "reviewer-2", independent=True, examined=["tests/check.py", "src/app.py"],
                 findings=[{"severity": "blocker", "title": "test assertion removed to make it pass",
                            "location": "tests/check.py"}])
    g = Q.gate(pid)
    assert g["status"] == "failed" and any("test assertion removed" in b for b in g["blockers"])


def test_changing_a_check_after_failure_is_flagged(Q, proj):
    pid = standard(Q, proj)
    (proj / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    Q.run_all(pid)
    with pytest.raises(QualityError):
        Q.update_check(pid, "unit", reason="flaky", mandatory=False)
    Q.update_check(pid, "unit", reason="switch to a looser check", command=[PY, "-c", "pass"])
    Q.run_all(pid)
    g = Q.gate(pid)
    assert any("the check's definition" in w["detail"] for w in g["weakening_flags"])
    assert g["status"] == "verification_pending"


def test_release_profile_requires_kinds_and_independent_review(Q, proj):
    with pytest.raises(QualityError):
        Q.create_plan("r", str(proj), profile="release", not_applicable={"e2e": ""})
    p = Q.create_plan("r", str(proj), profile="release", not_applicable={"e2e": "library with no UI"})
    pid = p["id"]
    Q.add_check(pid, "unit", "unit", "unit", command=unit_cmd())
    Q.run_all(pid)
    g = Q.gate(pid)
    assert g["status"] == "verification_pending"
    assert "no mandatory typecheck check is defined for a release plan" in g["blockers"]
    assert not any("e2e" in b for b in g["blockers"])
    assert "independent review not done" in g["blockers"]
    assert "e2e not applicable: library with no UI" in g["limitations"]


def test_reviews_must_be_concrete(Q, proj):
    pid = standard(Q, proj)
    with pytest.raises(QualityError):
        Q.add_review(pid, "r", independent=True, examined=[], findings=[])
    with pytest.raises(QualityError):
        Q.add_review(pid, "r", independent=True, examined=["src"], findings=[], summary="Looks great, LGTM!")
    with pytest.raises(QualityError):
        Q.add_review(pid, "r", independent=True, examined=["src"], findings=[{"severity": "major", "title": "x"}])
    r = Q.add_review(pid, "r", independent=True, examined=["src/app.py", "tests/check.py"], findings=[],
                     summary="checked add() for negative and float inputs; no issues found")
    assert r["findings"] == []


def test_defects_block_until_fixed_with_evidence(Q, proj):
    pid = standard(Q, proj)
    Q.run_all(pid)
    d = Q.add_defect(pid, "major", "add() overflows on huge ints", "repro: ...", check_key="unit")
    assert Q.gate(pid)["status"] == "verification_pending"
    with pytest.raises(QualityError):
        Q.resolve_defect(d["id"], resolution="fixed")  # no demonstrating check
    Q.resolve_defect(d["id"], resolution="bounded inputs", verified_by="unit")
    assert Q.gate(pid)["status"] == "verified"
    b = Q.add_defect(pid, "blocker", "data loss", "repro")
    with pytest.raises(QualityError):
        Q.accept_defect(b["id"], reason="ship it", actor="user")
    assert Q.gate(pid)["status"] == "failed"
    m = Q.add_defect(pid, "major", "slow on big files", "2s")
    with pytest.raises(QualityError):
        Q.accept_defect(m["id"], reason="ok", actor="lucid")
    Q.accept_defect(m["id"], reason="known; tracked for next release", actor="user")
    g = Q.gate(pid)
    assert [x["title"] for x in g["known_defects"]["major"]] == ["slow on big files"]


def test_time_budget_keeps_required_checks_first(Q, proj):
    pid = standard(Q, proj)
    Q.add_check(pid, "polish", "format", "optional", mandatory=False, command=[PY, "-c", "pass"], est_seconds=999)
    res = Q.run_all(pid, time_budget_s=60)
    assert {r["key"] for r in res["ran"]} == {"lint", "unit"}
    assert res["skipped"][0]["key"] == "polish" and "optional" in res["skipped"][0]["reason"]
    Q.add_check(pid, "slow", "integration", "required", command=[PY, "-c", "pass"], est_seconds=500)
    res = Q.run_all(pid, time_budget_s=10)
    assert any(s["key"] == "slow" and "mandatory" in s["reason"] for s in res["skipped"])
    assert "slow: not run" in Q.gate(pid)["blockers"]


def test_performance_metric_threshold(Q, proj):
    pid = standard(Q, proj)
    Q.add_check(pid, "perf", "performance", "p95 under 20ms", command=[PY, "-c", "print('p95_ms=12.5')"],
                metric={"regex": r"p95_ms=([\d.]+)", "op": "<=", "threshold": 20, "unit": "ms"})
    Q.add_check(pid, "perf2", "performance", "p95 under 10ms", command=[PY, "-c", "print('p95_ms=12.5')"],
                metric={"regex": r"p95_ms=([\d.]+)", "op": "<=", "threshold": 10, "unit": "ms"})
    Q.run_all(pid)
    states = {c["key"]: Q.check_state(pid, c)["state"] for c in Q.plan(pid)["checks"]}
    assert states["perf"] == "passed" and states["perf2"] == "failed"


def test_result_discarded_if_files_change_during_run(Q, proj):
    pid = standard(Q, proj)
    Q.add_check(pid, "touchy", "unit", "edits its own input", inputs=["src"],
                command=[PY, "-c", "open('src/app.py','a').write('# x\\n')"])
    r = Q.run_check(pid, "touchy")
    assert r["outcome"] == "error" and "result discarded" in r["evidence"]


def test_manual_checks_need_evidence(Q, proj):
    pid = standard(Q, proj)
    Q.add_check(pid, "layout", "render_inspect", "README renders")
    with pytest.raises(QualityError):
        Q.record_result(pid, "layout", True, "ok")
    r = Q.record_result(pid, "layout", True, "Opened the rendered README at 375px and 1280px; headings intact")
    assert r["outcome"] == "passed" and r["actor"] == "user"


def test_compare_alternatives_and_originality(Q, L, proj):
    pid = standard(Q, proj)
    O = L.originality
    sid = O.create_study("dedupe incoming events")["id"]
    known = O.add_candidate(sid, "Bit sieve", "Hash each item with several hash functions into a bit array; a lookup "
                            "checks the bits. Occasionally wrong with false positives.", "x", "new_method",
                            advantage="fast")["id"]
    O.check(known, [__import__("lucid.originality", fromlist=["x"]).LocalCollectionProvider()])
    r = Q.compare(pid, [
        {"name": "fancy", "scores": {"usefulness": 3, "originality": 5, "simplicity": 2, "cost": 2},
         "originality_candidate": known},
        {"name": "plain", "scores": {"usefulness": 3, "originality": 2, "simplicity": 5, "cost": 5}},
    ], chosen="fancy", rationale="it sounds advanced", originality=O)
    fancy = r["options"][0]
    assert fancy["scores"]["originality"] == 1 and fancy["originality_status"] == "known"
    assert any("at least as good on every scored criterion" in w for w in r["warnings"])
    assert any("more complex than plain with no gain in usefulness" in w for w in r["warnings"])
    with pytest.raises(QualityError):
        Q.compare(pid, [{"name": "only"}], chosen="only", rationale="x")


def test_metrics_report_bounded_results(Q, proj):
    pid = standard(Q, proj)
    with pytest.raises(QualityError):
        Q.report_escaped(pid, "minor", "typo", "x")
    Q.run_all(pid)
    Q.gate(pid)
    m = Q.metrics()
    assert m["plans_verified"] == 1 and m["escaped_defects"] == 0
    assert m["escaped_rate_upper95_if_zero"] == 3.0 and "not proof" in m["note"]
    Q.report_escaped(pid, "minor", "doc typo found by a user", "README line 2")
    assert Q.metrics()["escaped_defects"] == 1


def test_content_hash_ignores_excluded_paths(proj):
    a = content_hash(str(proj), [".", "!docs"])
    (proj / "docs" / "README.md").write_text("changed")
    assert content_hash(str(proj), [".", "!docs"]) == a
    assert content_hash(str(proj)) != a


def test_render_and_cli_exit_codes(Q, proj, tmp_path):
    cfg = {"subject": "demo", "profile": "standard",
           "criteria": [{"category": "correctness", "text": "sum is right"}],
           "checks": [{"key": "lint", "kind": "lint", "description": "parses", "command": lint_cmd(),
                       "inputs": ["src"]},
                      {"key": "unit", "kind": "unit", "description": "tests", "command": unit_cmd(),
                       "inputs": ["src", "tests"]}]}
    (proj / "quality.json").write_text(json.dumps(cfg))
    env = dict(os.environ, LUCID_HOME=str(tmp_path / "h"),
               PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))

    def run(*a):
        return subprocess.run([PY, "-m", "lucid.cli", *a], capture_output=True, text=True, env=env, timeout=120)
    pid = json.loads(run("--json", "quality", "init", str(proj / "quality.json")).stdout)["id"]
    assert run("quality", "gate", pid).returncode == 3
    assert run("quality", "run", pid).returncode == 0
    g = run("quality", "gate", pid, "--markdown")
    assert g.returncode == 0 and "## Checks performed" in g.stdout and "No known blocking defects" in g.stdout
