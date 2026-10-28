#!/usr/bin/env python3
"""Small, repeatable retrieval benchmark: 2,000 memories, 200 searches. Prints p50/p95 latency.

It measures this machine only. Use it to catch regressions, not as a promise about other hardware.
"""
import os
import random
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from lucid.core import Lucid  # noqa: E402

WORDS = ("deploy cache budget retry storage handoff memory schema review release token index search export "
         "import branch conflict tombstone sync worker").split()


def main() -> int:
    rng = random.Random(7)
    with tempfile.TemporaryDirectory() as d:
        L = Lucid(os.path.join(d, "bench.db"))
        for i in range(2000):
            L.memory.add(" ".join(rng.choice(WORDS) for _ in range(12)) + f" item {i}", kind="note")
        times = []
        for _ in range(200):
            q = " ".join(rng.choice(WORDS) for _ in range(3))
            t0 = time.perf_counter()
            L.memory.search(q, budget_tokens=800)
            times.append((time.perf_counter() - t0) * 1000)
        L.close()
    times.sort()
    p95 = times[int(0.95 * len(times)) - 1]
    print(f"searches=200 memories=2000 p50_ms={statistics.median(times):.2f} p95_ms={p95:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
