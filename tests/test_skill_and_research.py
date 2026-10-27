import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))


def test_skill_validates_and_runs_isolated(tmp_path):
    from build_skill import build
    from validate_skill import validate
    out = build()
    assert validate(os.path.join(ROOT, "skill", "lucid")) == []
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "lucid/SKILL.md" in names and "lucid/scripts/lib/lucid/memory.py" in names
    assert not any(n.endswith(".db") or "token" in os.path.basename(n) for n in names)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LUCID_HOME": str(tmp_path / "data")}
    script = os.path.join(ROOT, "skill", "lucid", "scripts", "lucid.py")
    p = subprocess.run([sys.executable, "-I", script, "--json", "memory", "add", "isolated run"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["content"] == "isolated run"
    assert oct(os.stat(tmp_path / "data" / "lucid.db").st_mode & 0o777) == "0o600"


def test_skill_rejects_nonstandard_frontmatter(tmp_path):
    from validate_skill import validate
    d = tmp_path / "lucid"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: lucid\ndescription: x\nargument-hint: foo\n---\nbody\n")
    assert any("non-standard" in p for p in validate(str(d)))


def test_research_experiments_record_bounded_outcomes(L):
    from lucid.research_modules import consciousness, discovery
    a, b = discovery.run_all(L.experiments)
    assert a["status"] == "empirically_supported" and a["result"]["selected"]["coefs"] == [1, 1, 1]
    assert "no proof" in a["result"]["claim_bounds"]
    assert b["status"] == "disproven" and b["result"]["counterexample"]["n"] == 40
    gw = consciousness.run_gw1(L.experiments, n=120)
    assert gw["result"]["accuracy"]["no_broadcast"]["local"] == 1.0
    assert "not evidence of experience" in gw["result"]["claim_bounds"]
    blob = json.dumps(L.experiments.list()).lower()
    assert "consciousness score" not in blob and "% conscious" not in blob
    import pytest
    with pytest.raises(RuntimeError, match="activation access unavailable"):
        consciousness.ActivationProbe(None)


def test_eval_suites_small_runs():
    from lucid.evals import correction_suite, memory_suite
    m = memory_suite.run(3, 15, 150)
    assert set(m) == set(memory_suite.CONDITIONS) and all(len(v) == 15 for v in m.values())
    c = correction_suite.run(3, 30)
    assert sum(c["guarded"]["corrupted"]) == 0
