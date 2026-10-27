"""Small, dependency-free statistics for paired evaluations."""
from __future__ import annotations

import math
import random
from typing import Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def paired_bootstrap_ci(a: Sequence[float], b: Sequence[float], *, iters: int = 5000, alpha: float = 0.05,
                        seed: int = 0) -> tuple[float, float, float]:
    """Mean of (b - a) with a percentile bootstrap CI over paired items."""
    if len(a) != len(b) or not a:
        raise ValueError("paired samples must be non-empty and equal length")
    d = [y - x for x, y in zip(a, b)]
    rng = random.Random(seed)
    n = len(d)
    boots = sorted(mean([d[rng.randrange(n)] for _ in range(n)]) for _ in range(iters))
    lo = boots[int(alpha / 2 * iters)]
    hi = boots[int((1 - alpha / 2) * iters) - 1]
    return mean(d), lo, hi


def sign_test_p(a: Sequence[float], b: Sequence[float]) -> float:
    """Two-sided exact sign test on paired differences (ties dropped)."""
    pos = sum(1 for x, y in zip(a, b) if y > x)
    neg = sum(1 for x, y in zip(a, b) if y < x)
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))
