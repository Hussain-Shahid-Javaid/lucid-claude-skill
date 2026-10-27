"""Controlled discovery experiments.

Pipeline: hypothesis → prediction → experiment → result → criticism → revision,
with candidate generation separated from evaluation:

* The candidate generator sees ONLY the development range.
* The evaluator (independent brute-force counter) is a separate function the
  generator never calls; held-out range is evaluated once, after candidates freeze.
* Outcomes use restricted labels; finite checks are never called proofs.

Experiment A (rediscovery, used as a positive control for the pipeline):
  Count binary strings of length n with no three consecutive 1s. Search over
  integer linear recurrences of order ≤ 4 with small coefficients using n ≤ 8, then
  test on held-out 9..22 against exhaustive enumeration. (The answer — a
  tribonacci-type recurrence — is known in the literature; this is not a new result.)

Experiment B (falsification control):
  Hypothesis "n² + n + 41 is prime for every n ≥ 0" (Euler's polynomial). Counterexample
  search must find n = 40.
"""
from __future__ import annotations

import itertools
from typing import Callable


# ---------------------------------------------------------------- independent evaluators (held out)
def _brute_no_three_ones(n: int) -> int:
    return sum(1 for bits in itertools.product("01", repeat=n) if "111" not in "".join(bits))


def _is_prime(k: int) -> bool:
    if k < 2:
        return False
    i = 2
    while i * i <= k:
        if k % i == 0:
            return False
        i += 1
    return True


# ---------------------------------------------------------------- candidate generation (dev data only)
def search_recurrences(dev: list[int], max_order: int = 4, coef_range: range = range(-2, 3)) -> list[dict]:
    """All integer linear recurrences a(n)=Σ c_i a(n-i) consistent with the dev sequence, simplest first."""
    found = []
    for order in range(1, max_order + 1):
        for coefs in itertools.product(coef_range, repeat=order):
            if all(c == 0 for c in coefs):
                continue
            ok = all(dev[n] == sum(c * dev[n - 1 - i] for i, c in enumerate(coefs)) for n in range(order, len(dev)))
            if ok and len(dev) - order >= 3:  # require ≥3 constraint checks to avoid trivial fits
                found.append({"order": order, "coefs": list(coefs), "complexity": order + sum(map(abs, coefs))})
    return sorted(found, key=lambda c: (c["complexity"], c["order"]))


def extend(seq: list[int], coefs: list[int], upto: int) -> list[int]:
    s = list(seq)
    while len(s) < upto:
        s.append(sum(c * s[-1 - i] for i, c in enumerate(coefs)))
    return s


def run_experiment_a(experiments, *, dev_max: int = 8, heldout_max: int = 22) -> dict:
    eid = experiments.plan("discovery", "Recurrence for binary strings without '111'",
                           hypothesis="The count obeys a low-order integer linear recurrence",
                           prediction=f"A recurrence fitted on n≤{dev_max} matches exhaustive counts for "
                                      f"n={dev_max + 1}..{heldout_max}",
                           config={"dev_range": [0, dev_max], "heldout_range": [dev_max + 1, heldout_max],
                                   "search": "order≤4, coefficients in [-2,2]", "evaluator": "exhaustive enumeration",
                                   "literature_note": "known result (tribonacci-type); positive control"})
    dev = [_brute_no_three_ones(n) for n in range(dev_max + 1)]
    cands = search_recurrences(dev)
    if not cands:
        return experiments.record(eid, status="unresolved", result={"candidates": 0})
    best = cands[0]
    # Freeze candidate, then evaluate held-out once.
    predicted = extend(dev, best["coefs"], heldout_max + 1)
    truth = {n: _brute_no_three_ones(n) for n in range(dev_max + 1, heldout_max + 1)}
    mismatches = {n: (predicted[n], t) for n, t in truth.items() if predicted[n] != t}
    alternatives_failing = 0
    for c in cands[1:20]:
        p = extend(dev, c["coefs"], heldout_max + 1)
        alternatives_failing += any(p[n] != t for n, t in truth.items())
    status = "empirically_supported" if not mismatches else "disproven"
    return experiments.record(eid, status=status, result={
        "selected": best, "n_candidates_consistent_with_dev": len(cands),
        "alternatives_failing_heldout": alternatives_failing, "heldout_mismatches": mismatches,
        "claim_bounds": f"agreement verified exhaustively for n ≤ {heldout_max}; no proof for all n",
        "dev_sequence": dev}, criticism=(
        "Finite agreement is not a proof. A combinatorial proof exists in the literature (transfer-matrix / "
        "last-run argument) but was not machine-checked here. Simplicity ordering is a bias, not evidence."))


def run_experiment_b(experiments, *, limit: int = 1000) -> dict:
    eid = experiments.plan("discovery", "Euler polynomial primality (falsification control)",
                           hypothesis="n^2+n+41 is prime for all n ≥ 0",
                           prediction=f"No counterexample for n ≤ {limit}",
                           config={"method": "exhaustive counterexample search", "limit": limit})
    for n in range(limit + 1):
        v = n * n + n + 41
        if not _is_prime(v):
            return experiments.record(eid, status="disproven", result={
                "counterexample": {"n": n, "value": v, "factor": next(d for d in range(2, v) if v % d == 0)},
                "independently_checkable": True}, criticism="A single checkable counterexample refutes the claim.")
    return experiments.record(eid, status="empirically_supported", result={"checked_upto": limit})


def run_all(experiments) -> list[dict]:
    return [run_experiment_a(experiments), run_experiment_b(experiments)]


Evaluator = Callable[[int], int]
