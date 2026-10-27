"""Correction-cycle simulation (deterministic, no model calls).

Question: does the regression guard in CorrectionCycle reduce *correct-answer
corruption* while keeping bug-fix success, when the reviser is noisy?

Setup: a pool of small functions, each with a correct version and seeded-bug
variants, and independent test suites (held out from the reviser). A simulated
reviser returns a uniformly random candidate from {correct, buggy variants} — a
deliberately crude model of an unreliable reviewer. Conditions:
  * no_review   – keep the draft
  * naive       – accept any revision the reviser proposes after a failed or
                  *partial* check (here: a style check that the correct code fails)
  * guarded     – Lucid's cycle: accept only revisions that fix ≥1 failing check
                  and break none.

This is a simulation of reviewer noise. It demonstrates the guard's mechanics; it is
not evidence about any particular model.
"""
from __future__ import annotations

import random

from ..verification import CorrectionCycle

TASKS = {
    "clamp": ("def f(x, lo, hi):\n    return max(lo, min(hi, x))\n",
              ["def f(x, lo, hi):\n    return min(lo, max(hi, x))\n", "def f(x, lo, hi):\n    return max(lo, x)\n"],
              [((5, 0, 3), 3), ((-1, 0, 3), 0), ((2, 0, 3), 2)]),
    "abs_diff": ("def f(a, b):\n    return abs(a - b)\n",
                 ["def f(a, b):\n    return a - b\n", "def f(a, b):\n    return abs(a) - abs(b)\n"],
                 [((1, 4), 3), ((4, 1), 3), ((-2, 2), 4)]),
    "is_leap": ("def f(y):\n    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n",
                ["def f(y):\n    return y % 4 == 0\n", "def f(y):\n    return y % 4 == 0 and y % 100 != 0\n"],
                [((2000,), True), ((1900,), False), ((2024,), True), ((2023,), False)]),
    "mid": ("def f(xs):\n    s = sorted(xs)\n    return s[len(s) // 2]\n",
            ["def f(xs):\n    return xs[len(xs) // 2]\n", "def f(xs):\n    s = sorted(xs)\n    return s[0]\n"],
            [(([3, 1, 2],), 2), (([9, 7, 8, 1, 5],), 7)]),
}


def _tests_pass(src: str, cases) -> bool:
    ns: dict = {}
    try:
        exec(compile(src, "<cand>", "exec"), ns)  # noqa: S102 — fixed, trusted task pool
        return all(ns["f"](*args) == want for args, want in cases)
    except Exception:
        return False


def run(seed: int, n: int) -> dict[str, dict[str, list[int]]]:
    rng = random.Random(seed)
    out = {c: {"final_correct": [], "corrupted": [], "fixed": []} for c in ("no_review", "naive", "guarded")}
    names = sorted(TASKS)
    for _ in range(n):
        name = rng.choice(names)
        good, bugs, cases = TASKS[name]
        start_correct = rng.random() < 0.5
        draft = good if start_correct else rng.choice(bugs)
        pool = [good] + bugs
        proposals = [rng.choice(pool) for _ in range(3)]
        tests = lambda s, c=cases: (_tests_pass(s, c), "independent tests")  # noqa: E731
        style = lambda s: ("# reviewed" in s, "reviewer wants a '# reviewed' marker")  # noqa: E731

        def reviser_factory():
            it = iter(proposals)
            return lambda cur, fb: "# reviewed\n" + next(it, cur)

        results = {
            "no_review": draft,
            "naive": "# reviewed\n" + proposals[0],  # naive: always take the first revision
            "guarded": CorrectionCycle(None, max_iterations=3).run(
                name, draft, {"tests": tests, "style": style}, reviser_factory()).final,
        }
        for cond, final in results.items():
            ok = _tests_pass(final, cases)
            out[cond]["final_correct"].append(int(ok))
            out[cond]["corrupted"].append(int(start_correct and not ok))
            out[cond]["fixed"].append(int((not start_correct) and ok))
    return out
