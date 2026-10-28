"""Professional quality as observable engineering: acceptance criteria, revision-bound
evidence, defects, independent review and release gates.

What this module promises, and what it does not:
* It never reports "verified" while a mandatory check has failed, has not run, or ran
  against an older version of the files it depends on.
* Evidence is bound to a content hash of exactly the files a check depends on, so a
  later edit makes the affected evidence stale and the check has to run again.
* It detects one common way of "passing" by weakening: a check that failed and later
  passed after its definition or its guarded files (e.g. tests/) changed. That pass
  is held until an independent review covers it.
* "No known blocking defects within the verified scope" is the target. It is not a
  claim of zero errors, and zero observed failures is reported as a bounded result.
"""
from __future__ import annotations

import builtins
import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from typing import Any, Iterable

from .db import Store
from .redact import redact
from .util import dumps, loads, new_id

CATEGORIES = ("correctness", "completeness", "maintainability", "usability_accessibility", "performance",
              "security_privacy", "communication", "conventions")
CHECK_KINDS = {"format", "lint", "typecheck", "unit", "integration", "boundary", "e2e", "security", "dependency",
               "performance", "build", "docs", "source_verify", "formula_verify", "render_inspect", "accessibility",
               "fidelity", "rollback", "manual"}
PROFILES: dict[str, dict[str, Any]] = {
    "trivial": {"required": ["lint"], "review": False,
                "about": "small, reversible change such as formatting or wording"},
    "standard": {"required": ["lint", "unit"], "review": False, "about": "ordinary code change"},
    "release": {"required": ["lint", "typecheck", "unit", "integration", "e2e", "security", "dependency", "build",
                             "docs"], "review": True, "about": "a version other people will install"},
    "production": {"required": ["lint", "typecheck", "unit", "integration", "e2e", "security", "dependency", "build",
                                "docs", "performance", "rollback"], "review": True,
                   "about": "a deployment that serves real users"},
}
SEVERITIES = ("blocker", "major", "minor", "trivial")
SCORE_KEYS = ("usefulness", "originality", "feasibility", "simplicity", "maintainability", "cost")
PRAISE = re.compile(r"\b(great|excellent|looks good|lgtm|perfect|amazing|clean|nice|well done|solid)\b", re.I)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hyperframes"}
OUTPUT_TAIL = 4000
GIT = shutil.which("git")


class QualityError(ValueError):
    pass


# ---------------------------------------------------------------- content hashing
def _files(base: str, patterns: Iterable[str]) -> builtins.list[str]:
    """Files under `base` matching the patterns (paths or globs). Respects .gitignore when git is available."""
    allp = list(patterns)
    pats = [p for p in allp if not p.startswith("!")] or ["."]
    excl = [p[1:].rstrip("/") for p in allp if p.startswith("!")]
    listed = None
    if GIT and os.path.isdir(os.path.join(base, ".git")):
        try:
            out = subprocess.run([GIT, "ls-files", "-co", "--exclude-standard", "-z"], cwd=base,
                                 capture_output=True, timeout=60, check=True).stdout
            listed = sorted(p for p in out.decode().split("\0") if p)
        except (OSError, subprocess.SubprocessError):
            listed = None
    if listed is None:
        listed = []
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                listed.append(os.path.relpath(os.path.join(root, f), base).replace(os.sep, "/"))
    out_files = []
    for rel in listed:
        if not os.path.isfile(os.path.join(base, rel)):
            continue  # deleted but still in the index
        if any(rel == e or rel.startswith(e + "/") or fnmatch.fnmatch(rel, e) for e in excl):
            continue
        for p in pats:
            p = p.rstrip("/")
            if p in (".", "") or rel == p or rel.startswith(p + "/") or fnmatch.fnmatch(rel, p):
                out_files.append(rel)
                break
    return out_files


def content_hash(base: str, patterns: Iterable[str] = ()) -> str:
    """Hash of the paths and bytes of every matching file. Any edit to them changes it."""
    if os.path.isfile(base):
        with open(base, "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    h = hashlib.sha256()
    for rel in _files(base, patterns):
        h.update(rel.encode() + b"\0")
        with open(os.path.join(base, rel), "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    return "sha256:" + h.hexdigest()


def git_commit(base: str) -> str | None:
    if not GIT or not os.path.isdir(os.path.join(base, ".git")):
        return None
    try:
        head = subprocess.run([GIT, "rev-parse", "HEAD"], cwd=base, capture_output=True, text=True, timeout=30,
                              check=True).stdout.strip()
        dirty = subprocess.run([GIT, "status", "--porcelain"], cwd=base, capture_output=True, text=True,
                               timeout=30, check=True).stdout.strip()
        return head + ("+uncommitted" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - m), min(1.0, c + m))


# ---------------------------------------------------------------- main API
class Quality:
    def __init__(self, store: Store):
        self.s = store

    # ---------------------------------------------------------- A. plan and criteria
    def create_plan(self, subject: str, artifact: str, *, profile: str = "standard",
                    criteria: Iterable[dict] = (), limitations: Iterable[str] = (),
                    not_applicable: dict[str, str] | None = None, scope: Iterable[str] = (),
                    author: str = "lucid") -> dict:
        """`scope` lists the paths that make up the deliverable (prefix with ! to exclude, e.g. generated
        reports). Its content hash is the artifact revision that reviews and gates are bound to."""
        if profile not in PROFILES:
            raise QualityError(f"profile must be one of {sorted(PROFILES)}")
        if not os.path.exists(artifact):
            raise QualityError(f"artifact not found: {artifact}")
        crit = []
        for i, c in enumerate(criteria):
            c = dict(c)
            if c.get("category") not in CATEGORIES:
                raise QualityError(f"criterion category must be one of {list(CATEGORIES)}")
            if not c.get("text"):
                raise QualityError("each criterion needs text")
            c.setdefault("id", f"c{i + 1}")
            c["mandatory"] = bool(c.get("mandatory", True))
            crit.append(c)
        na = dict(not_applicable or {})
        bad = [k for k in na if k not in PROFILES[profile]["required"]]
        if bad:
            raise QualityError(f"only required check kinds can be marked not applicable: {bad}")
        if any(not str(v).strip() for v in na.values()):
            raise QualityError("say why each check kind does not apply")
        pid = new_id("qp")
        now = self.s.clock.iso()
        lim = list(limitations) + [f"{k} not applicable: {v}" for k, v in na.items()]
        self.s.ex("INSERT INTO quality_plans (id, subject, artifact, profile, criteria, limitations, created_at,"
                  " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                  (pid, subject, os.path.abspath(artifact), profile, dumps({"items": crit, "not_applicable": na, "scope": list(scope), "author": author}),
                   dumps(lim), now, now))
        return self.plan(pid)

    def plan(self, pid: str) -> dict:
        r = self.s.one("SELECT * FROM quality_plans WHERE id=?", (pid,))
        if not r:
            raise KeyError(pid)
        d = dict(r)
        crit = loads(d["criteria"], {})
        d["criteria"] = crit.get("items", [])
        d["not_applicable"] = crit.get("not_applicable", {})
        d["scope"] = crit.get("scope", [])
        d["author"] = crit.get("author", "lucid")
        d["limitations"] = loads(d["limitations"], [])
        d["checks"] = [self._check(x[0]) for x in self.s.q(
            "SELECT id FROM quality_checks WHERE plan_id=? ORDER BY mandatory DESC, created_at, rowid", (pid,))]
        return d

    def load_plan(self, path: str, artifact: str | None = None) -> dict:
        """Create a plan and its checks from a JSON file (see quality.json in the Lucid repo)."""
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        base = artifact or os.path.dirname(os.path.abspath(path))
        p = self.create_plan(cfg["subject"], base, profile=cfg.get("profile", "standard"),
                             criteria=cfg.get("criteria", []), limitations=cfg.get("limitations", []),
                             not_applicable=cfg.get("not_applicable"), scope=cfg.get("scope", []),
                             author=cfg.get("author", "lucid"))
        for c in cfg.get("checks", []):
            self.add_check(p["id"], **c)
        return self.plan(p["id"])

    def add_check(self, pid: str, key: str, kind: str, description: str, *, mandatory: bool = True,
                  command: builtins.list[str] | None = None, inputs: Iterable[str] = (),
                  guards: Iterable[str] = (), metric: dict | None = None, est_seconds: float | None = None,
                  criteria: Iterable[str] = ()) -> dict:
        if kind not in CHECK_KINDS:
            raise QualityError(f"check kind must be one of {sorted(CHECK_KINDS)}")
        if command is not None and (not isinstance(command, list) or not command):
            raise QualityError("command must be a non-empty argument list (no shell strings)")
        if metric:
            if not {"regex", "op", "threshold"} <= set(metric) or metric["op"] not in ("<=", ">=", "<", ">"):
                raise QualityError("metric needs regex, op (<=, >=, <, >) and threshold")
        p = self.plan(pid)
        for pat in inputs:
            if not pat.startswith("!") and not _files(p["artifact"], [pat]):
                raise QualityError(f"input {pat!r} matches no files in {p['artifact']}")
        defn = {"kind": kind, "command": command, "metric": metric, "inputs": sorted(inputs),
                "guards": sorted(guards), "mandatory": mandatory}
        cid = new_id("qc")
        self.s.ex("INSERT INTO quality_checks (id, plan_id, key, kind, description, mandatory, command, inputs, guards,"
                  " metric, definition_hash, est_seconds, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, pid, key, kind, description + (f" [criteria: {', '.join(criteria)}]" if criteria else ""),
                   int(mandatory), dumps(command) if command else None, dumps(sorted(inputs)), dumps(sorted(guards)),
                   dumps(metric) if metric else None, _defn_hash(defn), est_seconds, self.s.clock.iso()))
        return self._check(cid)

    def update_check(self, pid: str, key: str, *, reason: str, **changes: Any) -> dict:
        """Change a check's definition. The change is recorded; if the check had failed, its next pass is
        held until an independent review covers it."""
        if not reason.strip():
            raise QualityError("say why the check is changing")
        c = self._check_by_key(pid, key)
        allowed = {"command", "metric", "inputs", "guards", "mandatory", "description", "est_seconds"}
        bad = set(changes) - allowed
        if bad:
            raise QualityError(f"cannot change {sorted(bad)}")
        if c["command"] and "command" in changes and not changes["command"]:
            raise QualityError("an automated check cannot be turned into a manual one; define a new manual check")
        if c["mandatory"] and changes.get("mandatory") is False:
            raise QualityError("a mandatory check cannot be made optional here; change the plan's profile or "
                               "record the reason as a limitation in a new plan")
        merged = {**c, **changes}
        defn = {"kind": merged["kind"], "command": merged["command"], "metric": merged["metric"],
                "inputs": sorted(merged["inputs"]), "guards": sorted(merged["guards"]),
                "mandatory": merged["mandatory"]}
        self.s.ex("UPDATE quality_checks SET description=?, mandatory=?, command=?, inputs=?, guards=?, metric=?,"
                  " definition_hash=?, est_seconds=? WHERE id=?",
                  (merged["description"], int(merged["mandatory"]),
                   dumps(merged["command"]) if merged["command"] else None, dumps(sorted(merged["inputs"])),
                   dumps(sorted(merged["guards"])), dumps(merged["metric"]) if merged["metric"] else None,
                   _defn_hash(defn), merged["est_seconds"], c["id"]))
        self.s.log("user", "quality.check.changed", "quality_check", c["id"], {"reason": reason,
                                                                              "fields": sorted(changes)})
        return self._check(c["id"])

    def _check(self, cid: str) -> dict:
        r = self.s.one("SELECT * FROM quality_checks WHERE id=?", (cid,))
        if not r:
            raise KeyError(cid)
        d = dict(r)
        d["mandatory"] = bool(d["mandatory"])
        d["command"] = loads(d["command"], None)
        d["inputs"] = loads(d["inputs"], [])
        d["guards"] = loads(d["guards"], [])
        d["metric"] = loads(d["metric"], None)
        return d

    def _check_by_key(self, pid: str, key: str) -> dict:
        r = self.s.one("SELECT id FROM quality_checks WHERE plan_id=? AND key=?", (pid, key))
        if not r:
            raise KeyError(f"no check {key!r} in plan {pid}")
        return self._check(r[0])

    # ---------------------------------------------------------- B. evidence bound to revisions
    def revisions(self, pid: str, check: dict | None = None) -> dict:
        p = self.plan(pid)
        base = p["artifact"]
        if check is not None and check["inputs"] and os.path.isdir(base) and not _files(base, check["inputs"]):
            raise QualityError(f"{check['key']}: its inputs match no files any more; fix the check's inputs")
        out = {"artifact": content_hash(base, p["scope"]), "git": git_commit(base) if os.path.isdir(base) else None}
        if check is not None:
            out["inputs"] = content_hash(base, check["inputs"]) if check["inputs"] else out["artifact"]
            out["guards"] = content_hash(base, check["guards"]) if check["guards"] else ""
        return out

    def run_check(self, pid: str, key: str, *, timeout_s: int = 1800) -> dict:
        p = self.plan(pid)
        c = self._check_by_key(pid, key)
        if not c["command"]:
            raise QualityError(f"{key} is a manual check; record its result with record_result")
        rev = self.revisions(pid, c)
        cwd = p["artifact"] if os.path.isdir(p["artifact"]) else os.path.dirname(p["artifact"])
        t0 = time.monotonic()
        try:
            proc = subprocess.run(c["command"], cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
            out = proc.stdout + proc.stderr
            outcome = "passed" if proc.returncode == 0 else "failed"
            evidence = f"$ {' '.join(c['command'])}\nexit={proc.returncode}\n{out[-OUTPUT_TAIL:]}"
        except subprocess.TimeoutExpired as e:
            out, outcome = str(e.stdout or ""), "error"
            evidence = f"$ {' '.join(c['command'])}\ntimed out after {timeout_s}s"
        except OSError as e:
            out, outcome = "", "error"
            evidence = f"$ {' '.join(c['command'])}\ncould not start: {e}"
        dur = time.monotonic() - t0
        measured = None
        if c["metric"] and outcome == "passed":
            measured, ok, note = _measure(c["metric"], out)
            evidence += f"\nmetric: {note}"
            if not ok:
                outcome = "failed"
        after = self.revisions(pid, c)
        if after["inputs"] != rev["inputs"]:
            outcome = "error"
            evidence += "\nfiles changed while the check ran; result discarded"
        return self._store_run(c, rev, outcome, evidence, measured, dur, "lucid")

    def record_result(self, pid: str, key: str, passed: bool, evidence: str, *, actor: str = "user",
                      measured: float | None = None) -> dict:
        """Record a manual check (e.g. a rendered layout inspected, a source verified). Evidence is required."""
        if not evidence or len(evidence.strip()) < 10:
            raise QualityError("describe what was checked and what was seen")
        c = self._check_by_key(pid, key)
        if c["command"]:
            raise QualityError(f"{key} is an automated check; run it, a typed result cannot replace it")
        return self._store_run(c, self.revisions(pid, c), "passed" if passed else "failed", evidence, measured,
                               None, actor)

    def _store_run(self, c: dict, rev: dict, outcome: str, evidence: str, measured: float | None,
                   dur: float | None, actor: str) -> dict:
        rid = new_id("qr")
        self.s.ex("INSERT INTO quality_runs (id, check_id, revision, artifact_revision, definition_hash, guard_hash,"
                  " outcome, evidence, measured, duration_s, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, c["id"], rev["inputs"], rev["artifact"], c["definition_hash"], rev.get("guards", ""),
                   outcome, redact(evidence), measured, dur, actor, self.s.clock.iso()))
        if dur is not None:
            self.s.ex("UPDATE quality_checks SET est_seconds=? WHERE id=?", (round(dur, 2), c["id"]))
        return self.run(rid)

    def run(self, rid: str) -> dict:
        r = self.s.one("SELECT * FROM quality_runs WHERE id=?", (rid,))
        if not r:
            raise KeyError(rid)
        return dict(r)

    def runs(self, check_id: str) -> builtins.list[dict]:
        return [dict(r) for r in self.s.q("SELECT * FROM quality_runs WHERE check_id=? ORDER BY created_at, rowid",
                                          (check_id,))]

    def check_state(self, pid: str, c: dict, rev: dict | None = None) -> dict:
        """passed / failed / error at the current revision, stale (evidence is for older files), or unrun."""
        rev = rev or self.revisions(pid, c)
        runs = self.runs(c["id"])
        cur = [r for r in runs if r["revision"] == rev["inputs"] and r["definition_hash"] == c["definition_hash"]]
        if cur:
            last = cur[-1]
            return {"state": last["outcome"], "run": last}
        if runs:
            return {"state": "stale", "run": runs[-1]}
        return {"state": "unrun", "run": None}

    def run_all(self, pid: str, *, time_budget_s: float | None = None, include_optional: bool = True,
                only: Iterable[str] | None = None) -> dict:
        """Run what is needed: mandatory checks first, optional polish only with time left over."""
        p = self.plan(pid)
        only_set = set(only or [])
        todo = [c for c in p["checks"] if c["command"] and (not only_set or c["key"] in only_set)]
        # Builds first (later checks may read their output), then required checks, then optional polish.
        todo.sort(key=lambda c: (c["kind"] != "build", not c["mandatory"], c["est_seconds"] or 0))
        left = time_budget_s
        done, skipped = [], []
        for c in todo:
            st = self.check_state(pid, c)["state"]
            if st == "passed":
                done.append({"key": c["key"], "outcome": "passed", "reused": True})
                continue
            if not c["mandatory"] and not include_optional:
                skipped.append({"key": c["key"], "reason": "optional checks not requested"})
                continue
            est = c["est_seconds"] or 0
            if left is not None and est > left:
                why = (f"time budget too small for mandatory check (needs about {est:.0f}s, {left:.0f}s left)"
                       if c["mandatory"] else "optional; skipped to keep time for required checks")
                skipped.append({"key": c["key"], "reason": why})
                continue
            t0 = time.monotonic()
            r = self.run_check(pid, c["key"])
            if left is not None:
                left -= time.monotonic() - t0
            done.append({"key": c["key"], "outcome": r["outcome"], "reused": False})
        return {"ran": done, "skipped": skipped}

    # ---------------------------------------------------------- defects
    def add_defect(self, pid: str, severity: str, title: str, evidence: str, *, source: str = "user",
                   check_key: str | None = None) -> dict:
        if severity not in SEVERITIES:
            raise QualityError(f"severity must be one of {list(SEVERITIES)}")
        if source not in ("check", "review", "user", "escaped"):
            raise QualityError("source must be check, review, user or escaped")
        cid = self._check_by_key(pid, check_key)["id"] if check_key else None
        did = new_id("qd")
        now = self.s.clock.iso()
        escaped = int(source == "escaped")
        self.s.ex("INSERT INTO quality_defects (id, plan_id, severity, title, evidence, source, check_id,"
                  " found_revision, escaped, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (did, pid, severity, title, redact(evidence), source, cid, self.revisions(pid)["artifact"],
                   escaped, now, now))
        return self.defect(did)

    def report_escaped(self, pid: str, severity: str, title: str, evidence: str) -> dict:
        """A defect found after the plan was marked verified. Counts toward escaped-defect metrics."""
        if not self.s.one("SELECT 1 FROM quality_gates WHERE plan_id=? AND status='verified'", (pid,)):
            raise QualityError("this plan was never marked verified; report it as an ordinary defect")
        return self.add_defect(pid, severity, title, evidence, source="escaped")

    def defect(self, did: str) -> dict:
        r = self.s.one("SELECT * FROM quality_defects WHERE id=?", (did,))
        if not r:
            raise KeyError(did)
        return dict(r)

    def defects(self, pid: str, status: str | None = None) -> builtins.list[dict]:
        sql = "SELECT * FROM quality_defects WHERE plan_id=?" + (" AND status=?" if status else "")  # noqa: S608
        return [dict(r) for r in self.s.q(sql + " ORDER BY created_at, rowid", (pid, status) if status else (pid,))]

    def resolve_defect(self, did: str, *, resolution: str, verified_by: str | None = None) -> dict:
        """Close a defect as fixed. `verified_by` names a check that must have passed on the current files."""
        d = self.defect(did)
        if not resolution.strip():
            raise QualityError("describe the fix")
        if d["severity"] in ("blocker", "major") and not verified_by:
            raise QualityError("blocker and major fixes need a check that demonstrates the fix (verified_by)")
        if verified_by:
            c = self._check_by_key(d["plan_id"], verified_by)
            if self.check_state(d["plan_id"], c)["state"] != "passed":
                raise QualityError(f"{verified_by} has not passed on the current files")
        rev = self.revisions(d["plan_id"])["artifact"]
        self.s.ex("UPDATE quality_defects SET status='fixed', resolution=?, fixed_revision=?, updated_at=?"
                  " WHERE id=?", (resolution + (f" (verified by {verified_by})" if verified_by else ""), rev,
                                  self.s.clock.iso(), did))
        return self.defect(did)

    def accept_defect(self, did: str, *, reason: str, actor: str) -> dict:
        """The user knowingly ships with a major, minor or trivial defect. Blockers cannot be accepted."""
        d = self.defect(did)
        if d["severity"] == "blocker":
            raise QualityError("a blocker cannot be accepted; fix it or keep the release pending")
        if actor != "user":
            raise QualityError("only the user can accept a known defect")
        if not reason.strip():
            raise QualityError("say why it is acceptable")
        self.s.ex("UPDATE quality_defects SET status='accepted', resolution=?, updated_at=? WHERE id=?",
                  (reason, self.s.clock.iso(), did))
        return self.defect(did)

    # ---------------------------------------------------------- C. review and alternatives
    def add_review(self, pid: str, reviewer: str, *, independent: bool, examined: Iterable[str],
                   findings: Iterable[dict], summary: str = "") -> dict:
        """Findings must be concrete (severity, title, location, detail). Praise alone is not a review."""
        ex = [e for e in examined if str(e).strip()]
        fs = [dict(f) for f in findings]
        if not ex:
            raise QualityError("list what the review examined (files, features, scenarios)")
        for f in fs:
            if f.get("severity") not in SEVERITIES or not f.get("title") or not (f.get("location") or f.get("detail")):
                raise QualityError("each finding needs severity, title, and a location or detail")
        author = self.plan(pid).get("author", "lucid").lower()
        who = reviewer.strip().lower()
        if independent and (not who or who in ("author", "self") or author in who or who in author):
            raise QualityError(f"an independent review needs a reviewer other than the author ({author})")
        if not fs:
            substance = [w for w in re.findall(r"[a-z]{3,}", PRAISE.sub(" ", summary.lower()))]
            if len(substance) < 6:
                raise QualityError("a review with no findings must say what was checked (at least a sentence), "
                                   "not only praise")
        rev = self.revisions(pid)["artifact"]
        rid = new_id("qrev")
        self.s.ex("INSERT INTO quality_reviews (id, plan_id, reviewer, independent, examined, findings,"
                  " artifact_revision, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (rid, pid, reviewer, int(independent), dumps(ex), dumps({"items": fs, "summary": summary}), rev,
                   self.s.clock.iso()))
        for f in fs:
            self.add_defect(pid, f["severity"], f["title"],
                            f"{f.get('location', '')}: {f.get('detail', '')} (review {rid} by {reviewer})",
                            source="review")
        return self.review(rid)

    def review(self, rid: str) -> dict:
        r = self.s.one("SELECT * FROM quality_reviews WHERE id=?", (rid,))
        if not r:
            raise KeyError(rid)
        d = dict(r)
        d["independent"] = bool(d["independent"])
        d["examined"] = loads(d["examined"], [])
        f = loads(d["findings"], {})
        d["findings"], d["summary"] = f.get("items", []), f.get("summary", "")
        return d

    def reviews(self, pid: str) -> builtins.list[dict]:
        return [self.review(r[0]) for r in self.s.q(
            "SELECT id FROM quality_reviews WHERE plan_id=? ORDER BY created_at, rowid", (pid,))]

    def compare(self, pid: str, options: Iterable[dict], *, chosen: str, rationale: str,
                originality: Any = None) -> dict:
        """Compare distinct approaches on usefulness, originality, feasibility, simplicity, maintainability
        and cost (1-5, higher is better; for cost, higher means cheaper)."""
        opts = []
        for o in options:
            o = dict(o)
            scores = dict(o.get("scores", {}))
            if set(scores) - set(SCORE_KEYS):
                raise QualityError(f"scores may only use {list(SCORE_KEYS)}")
            for v in scores.values():
                if v is not None and not 1 <= float(v) <= 5:
                    raise QualityError("scores are 1 to 5")
            cand = o.get("originality_candidate")
            if cand and originality is not None:
                st = originality.candidate(cand)["status"]
                derived = {"known": 1, "adaptation": 2, "potentially_original": 4}.get(st)
                o["originality_status"] = st
                if scores.get("originality") is not None and derived is not None and \
                        abs(float(scores["originality"]) - derived) >= 2:
                    o.setdefault("notes", []).append(f"originality score replaced: the originality check says {st}")
                scores["originality"] = derived  # None when unresolved or not assessed
            o["scores"] = scores
            opts.append(o)
        names = [o["name"] for o in opts]
        if len(set(names)) != len(names) or chosen not in names:
            raise QualityError("options need unique names and `chosen` must be one of them")
        if len(opts) < 2:
            raise QualityError("compare at least two distinct approaches")
        if not rationale.strip():
            raise QualityError("give the reason for the choice")
        ch = next(o for o in opts if o["name"] == chosen)
        warnings = []
        for o in opts:
            if o is ch:
                continue
            a, b = ch["scores"], o["scores"]
            common = [k for k in SCORE_KEYS if a.get(k) is not None and b.get(k) is not None]
            if common and all(b[k] >= a[k] for k in common) and any(b[k] > a[k] for k in common):
                warnings.append(f"{o['name']} is at least as good on every scored criterion")
            if a.get("simplicity") is not None and b.get("simplicity") is not None and a["simplicity"] < b["simplicity"] \
                    and (a.get("usefulness") or 0) <= (b.get("usefulness") or 0):
                warnings.append(f"more complex than {o['name']} with no gain in usefulness")
        aid = new_id("qa")
        self.s.ex("INSERT INTO quality_alternatives (id, plan_id, options, chosen, rationale, warnings, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (aid, pid, dumps(opts), chosen, rationale, dumps(warnings),
                                              self.s.clock.iso()))
        return {"id": aid, "options": opts, "chosen": chosen, "rationale": rationale, "warnings": warnings}

    # ---------------------------------------------------------- D. the gate
    def gate(self, pid: str, *, record: bool = True) -> dict:
        p = self.plan(pid)
        prof = PROFILES[p["profile"]]
        base_rev = self.revisions(pid)
        performed, unrun, blockers, failed = [], [], [], []
        weakening = []
        kinds_covered = set()
        for c in p["checks"]:
            rev = self.revisions(pid, c)
            st = self.check_state(pid, c, rev)
            state = st["state"]
            if st["run"] and state != "stale":
                r = st["run"]
                performed.append({"key": c["key"], "kind": c["kind"], "mandatory": c["mandatory"],
                                  "outcome": state, "revision": r["revision"], "at": r["created_at"],
                                  "actor": r["actor"], "measured": r["measured"],
                                  "duration_s": None if r["duration_s"] is None else round(r["duration_s"], 1)})
            if c["mandatory"] and state == "passed":
                kinds_covered.add(c["kind"])
            if state in ("unrun", "stale"):
                unrun.append({"key": c["key"], "mandatory": c["mandatory"],
                              "reason": "files changed since it last ran" if state == "stale" else "not run"})
                if c["mandatory"]:
                    blockers.append(f"{c['key']}: {'evidence is for older files' if state == 'stale' else 'not run'}")
            elif state in ("failed", "error") and c["mandatory"]:
                failed.append(c["key"])
                blockers.append(f"{c['key']}: {state} on the current files")
            if state == "passed":
                w = self._weakening(c, st["run"], p)
                if w:
                    weakening.append({"key": c["key"], "detail": w})
                    blockers.append(f"{c['key']}: {w}")
        for kind in prof["required"]:
            if kind in p["not_applicable"]:
                continue
            if kind not in {c["kind"] for c in p["checks"] if c["mandatory"]}:
                blockers.append(f"no mandatory {kind} check is defined for a {p['profile']} plan")
        defects = self.defects(pid)
        open_def = [d for d in defects if d["status"] == "open"]
        by_sev = {s: [{"id": d["id"], "title": d["title"], "status": d["status"], "source": d["source"]}
                      for d in defects if d["severity"] == s and d["status"] in ("open", "accepted")]
                  for s in SEVERITIES}
        blocking_def = [d for d in open_def if d["severity"] in ("blocker", "major")]
        for d in blocking_def:
            blockers.append(f"open {d['severity']} defect: {d['title']}")
        review_ok = True
        if prof["review"]:
            cur = [r for r in self.reviews(pid) if r["independent"] and r["artifact_revision"] == base_rev["artifact"]]
            if not cur:
                review_ok = False
                older = [r for r in self.reviews(pid) if r["independent"]]
                blockers.append("independent review " + ("covers older files; re-review the changes" if older
                                                         else "not done"))
        mand_crit = [c for c in p["criteria"] if c["mandatory"]]
        if failed or any(d["severity"] == "blocker" for d in open_def):
            status = "failed"
        elif blockers:
            status = "verification_pending"
        else:
            status = "verified"
        report = {
            "plan": pid, "subject": p["subject"], "profile": p["profile"], "status": status,
            "artifact_revision": base_rev["artifact"], "git": base_rev["git"],
            "verified_scope": {"criteria": [c["text"] for c in mand_crit] if status == "verified" else [],
                               "checks_passed": [x["key"] for x in performed if x["outcome"] == "passed"],
                               "statement": ("No known blocking defects within the verified scope." if status ==
                                             "verified" else "Not verified: see blockers.")},
            "checks_performed": performed, "known_defects": by_sev, "unrun_checks": unrun,
            "blockers": blockers, "weakening_flags": weakening, "independent_review_current": review_ok,
            "reviews": [{"reviewer": r["reviewer"], "independent": r["independent"], "findings": len(r["findings"]),
                         "current": r["artifact_revision"] == base_rev["artifact"]} for r in self.reviews(pid)],
            "limitations": p["limitations"] + [
                "Reviewer independence is declared by the reviewer and shown here; Lucid cannot verify it.",
                "Passing checks show the absence of the failures they test for, not the absence of all defects.",
                "Test coverage percentages are not used as proof of correctness."],
            "optional_criteria": [c["text"] for c in p["criteria"] if not c["mandatory"]],
        }
        if record:
            self.s.ex("INSERT INTO quality_gates (id, plan_id, artifact_revision, status, report, created_at)"
                      " VALUES (?,?,?,?,?,?)", (new_id("qg"), pid, base_rev["artifact"], status, dumps(report),
                                               self.s.clock.iso()))
        return report

    def _weakening(self, c: dict, passing: dict, plan: dict) -> str | None:
        """A pass that followed a failure after the check or its guarded files changed needs an independent review."""
        runs = self.runs(c["id"])
        earlier = [r for r in runs if r["created_at"] <= passing["created_at"] and r["id"] != passing["id"]
                   and r["outcome"] in ("failed", "error")]
        changed = set()
        for f in earlier:  # any earlier failure counts, not just the latest one
            if f["definition_hash"] != passing["definition_hash"]:
                changed.add("the check's definition")
            if c["guards"] and f["guard_hash"] != passing["guard_hash"]:
                changed.add("guarded files (" + ", ".join(c["guards"]) + ")")
        if not changed:
            return None
        what = sorted(changed)
        covered = [r for r in self.reviews(plan["id"]) if r["independent"] and r["created_at"] >= passing["created_at"]
                   and any(_covers(e, c) for e in r["examined"])]
        if covered:
            return None
        return ("passed only after " + " and ".join(what) + " changed; an independent review must confirm "
                "the check was not weakened")

    def render(self, report: dict) -> str:
        L = [f"# Quality gate: {report['subject']}", "",
             f"**Status:** {report['status'].replace('_', ' ')} ({report['profile']} profile)", "",
             f"Files checked: `{report['artifact_revision'][:23]}…`" + (f" · git `{report['git']}`" if report['git']
                                                                          else ""), "",
             report["verified_scope"]["statement"], ""]
        if report["blockers"]:
            L += ["## Blockers", ""] + [f"- {b}" for b in report["blockers"]] + [""]
        L += ["## Checks performed", "", "| check | kind | required | result | time | input revision |",
              "|---|---|---|---|---|---|"]
        for x in report["checks_performed"]:
            L.append(f"| {x['key']} | {x['kind']} | {'yes' if x['mandatory'] else 'no'} | {x['outcome']} | "
                     f"{'' if x['duration_s'] is None else str(x['duration_s']) + 's'} | `{x['revision'][7:19]}` |")
        L += ["", "## Known defects", ""]
        any_def = False
        for s in SEVERITIES:
            for d in report["known_defects"][s]:
                any_def = True
                L.append(f"- **{s}** ({d['status']}): {d['title']}")
        if not any_def:
            L.append("- None open or accepted.")
        L += ["", "## Not run", ""]
        L += [f"- {u['key']} ({'required' if u['mandatory'] else 'optional'}): {u['reason']}"
              for u in report["unrun_checks"]] or ["- Nothing."]
        L += ["", "## Limitations", ""] + [f"- {x}" for x in report["limitations"]] + [""]
        return "\n".join(L)

    # ---------------------------------------------------------- metrics
    def metrics(self) -> dict:
        """Task success, escaped defects and regressions, with sample sizes and uncertainty."""
        plans = [r[0] for r in self.s.q("SELECT id FROM quality_plans")]
        gated = verified = first_try = 0
        for pid in plans:
            gates = [r[0] for r in self.s.q("SELECT status FROM quality_gates WHERE plan_id=? ORDER BY created_at,"
                                            " rowid", (pid,))]
            if not gates:
                continue
            gated += 1
            if "verified" in gates:
                verified += 1
                if gates[0] == "verified":
                    first_try += 1
        escaped = self.s.one("SELECT COUNT(*) FROM quality_defects WHERE escaped=1")[0]  # type: ignore[index]
        regressions = 0
        checks_with_history = 0
        for (cid,) in self.s.q("SELECT id FROM quality_checks"):
            outs = [r["outcome"] for r in self.runs(cid)]
            if len(outs) > 1:
                checks_with_history += 1
            regressions += sum(1 for a, b in zip(outs, outs[1:]) if a == "passed" and b == "failed")
        lo, hi = wilson(verified, gated)
        upper_escape = None
        if verified:
            upper_escape = round(3 / verified, 3) if escaped == 0 else None
        return {
            "plans_gated": gated, "plans_verified": verified, "verified_rate_wilson95": [round(lo, 3), round(hi, 3)],
            "verified_on_first_gate": first_try, "escaped_defects": escaped,
            "escaped_per_verified_plan": None if not verified else round(escaped / verified, 3),
            "escaped_rate_upper95_if_zero": upper_escape,
            "regressions_passed_then_failed": regressions, "checks_with_history": checks_with_history,
            "note": ("Zero observed escaped defects over n verified plans bounds the rate below about 3/n (95%); "
                     "it is not proof that none exist."),
        }


def _covers(examined: str, c: dict) -> bool:
    e = examined.strip().rstrip("/")
    if e == c["key"] or e == f"check:{c['key']}":
        return True
    return any(e == g.rstrip("/") or e.startswith(g.rstrip("/") + "/") for g in c["guards"])


def _defn_hash(defn: dict) -> str:
    return "sha256:" + hashlib.sha256(dumps(defn).encode()).hexdigest()


def _measure(metric: dict, output: str) -> tuple[float | None, bool, str]:
    m = re.search(metric["regex"], output)
    if not m:
        return None, False, f"metric pattern {metric['regex']!r} not found in output"
    v = float(m.group(1))
    t = float(metric["threshold"])
    ok = {"<=": v <= t, ">=": v >= t, "<": v < t, ">": v > t}[metric["op"]]
    unit = metric.get("unit", "")
    return v, ok, f"{v}{unit} {metric['op']} {t}{unit}: {'ok' if ok else 'not met'}"
