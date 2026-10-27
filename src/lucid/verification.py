"""Bounded correction cycle: draft → identify claims → verify → revise → recheck.

Key safeguard: a revision is accepted only if it fixes at least one failing check
and breaks none of the checks that were passing (regression guard). This is what
prevents "review" from damaging an initially correct answer.

Checks are callables returning (passed: bool, evidence: str). Use executable tests
for code, calculations for numbers, inspected sources for facts, and explicit
constraints for completion. Checks must not be edited by the reviser.
"""
from __future__ import annotations

import ast
import operator
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable

from .db import Store
from .redact import redact
from .util import dumps, new_id

Check = Callable[[Any], "tuple[bool, str]"]


@dataclass
class CycleResult:
    final: Any
    iterations: int
    passed_all: bool
    history: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    initially_correct: bool | None = None
    finally_correct: bool | None = None
    corrupted: bool = False


class CorrectionCycle:
    def __init__(self, store: Store | None = None, *, max_iterations: int = 3, max_tool_calls: int = 50):
        self.s = store
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls

    def run(self, subject: str, draft: Any, checks: dict[str, Check],
            revise: Callable[[Any, dict[str, str]], Any] | None, *, oracle: Callable[[Any], bool] | None = None,
            task_id: str | None = None) -> CycleResult:
        tool_calls = 0

        def evaluate(candidate):
            nonlocal tool_calls
            out = {}
            for name, fn in checks.items():
                tool_calls += 1
                try:
                    ok, ev = fn(candidate)
                except Exception as e:  # a crashing check is a failed check, never a pass
                    ok, ev = False, f"check raised {type(e).__name__}: {e}"
                out[name] = (bool(ok), str(ev))
            return out

        current = draft
        results = evaluate(current)
        history = [{"iteration": 0, "candidate": _short(current),
                    "checks": {k: {"passed": v[0], "evidence": v[1][:500]} for k, v in results.items()}}]
        init_ok = oracle(draft) if oracle else None
        stop = "all checks passed"
        seen_failures: set[frozenset] = set()
        it = 0
        while not all(v[0] for v in results.values()):
            failing = frozenset(k for k, v in results.items() if not v[0])
            if it >= self.max_iterations:
                stop = "iteration limit reached"
                break
            if tool_calls >= self.max_tool_calls:
                stop = "tool-call limit reached"
                break
            if failing in seen_failures and it > 0:
                stop = "no new evidence: same failures after revision"
                break
            seen_failures.add(failing)
            if revise is None:
                stop = "checks failed; no reviser available"
                break
            it += 1
            feedback = {k: results[k][1] for k in failing}
            candidate = revise(current, feedback)
            cand_results = evaluate(candidate)
            fixed = [k for k in failing if cand_results[k][0]]
            broke = [k for k, v in results.items() if v[0] and not cand_results[k][0]]
            accepted = bool(fixed) and not broke
            history.append({"iteration": it, "candidate": _short(candidate), "fixed": fixed, "broke": broke,
                            "accepted": accepted,
                            "checks": {k: {"passed": v[0], "evidence": v[1][:500]} for k, v in cand_results.items()}})
            if accepted:
                current, results = candidate, cand_results
            elif broke:
                stop = f"revision rejected: it broke passing checks {broke}"
                if it >= self.max_iterations:
                    break
        else:
            stop = "all checks passed" if it == 0 else f"all checks passed after {it} revision(s)"
        final_ok = oracle(current) if oracle else None
        res = CycleResult(final=current, iterations=it, passed_all=all(v[0] for v in results.values()),
                          history=history, stop_reason=stop, initially_correct=init_ok, finally_correct=final_ok,
                          corrupted=bool(init_ok and final_ok is False))
        if self.s:
            self.s.ex("INSERT INTO verifications (id, task_id, subject, iteration, original, revised, checks, changed,"
                      " initially_correct, finally_correct, remaining_uncertainty, stop_reason, created_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (new_id("ver"), task_id, subject, it, redact(_short(draft, 4000)), redact(_short(current, 4000)),
                       redact(dumps(history)), int(current != draft), _b(init_ok), _b(final_ok),
                       None if res.passed_all else "some checks still failing", stop, self.s.clock.iso()))
        return res


def _b(x):
    return None if x is None else int(bool(x))


def _short(x: Any, n: int = 800) -> str:
    s = x if isinstance(x, str) else repr(x)
    return s if len(s) <= n else s[:n] + "…"


# ----------------------------------------------------------------- checkers
def python_tests_check(test_code: str, timeout: int = 60) -> Check:
    """Run `candidate` (python source) together with independent tests in a subprocess."""
    def check(candidate: str):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/run.py"
            with open(path, "w") as fh:
                fh.write(candidate + "\n\n" + test_code + "\n")
            try:
                p = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True, timeout=timeout, cwd=d)
            except subprocess.TimeoutExpired:
                return False, "timeout"
            return p.returncode == 0, (p.stdout + p.stderr)[-1500:] or "ok"
    return check


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg}


def safe_eval(expr: str) -> float:
    """Arithmetic only — no names, calls or attributes."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 64:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr, mode="eval"))


def arithmetic_claims_check(candidate: str):
    """Find 'a op b = c' claims in text and recompute them."""
    import re
    bad, n = [], 0
    for m in re.finditer(r"([\d\.\s\+\-\*/\(\)%]+?)\s*=\s*(-?\d+(?:\.\d+)?)", candidate):
        expr = m.group(1).strip()
        if not re.search(r"\d\s*[\+\-\*/%]\s*\d", expr):
            continue
        n += 1
        try:
            val = safe_eval(expr)
        except Exception:
            continue
        if abs(val - float(m.group(2))) > 1e-9 * max(1, abs(val)):
            bad.append(f"{expr} = {m.group(2)} (actual {val:g})")
    return (not bad), (f"{n} arithmetic claims checked" if not bad else "wrong: " + "; ".join(bad))
