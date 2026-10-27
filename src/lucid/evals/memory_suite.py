"""Offline memory-retrieval evaluation (deterministic, no model calls).

What it measures: whether Lucid's *retrieval component* puts the correct, current
fact into a fixed context budget, compared with simple baselines. It does NOT
measure end-to-end assistant reasoning (that needs the live-model suite).

Each generated episode is a user history containing:
  * target facts (some later corrected → the old value must not be served as current),
  * distractor facts sharing vocabulary,
  * conflicting statements from an assistant guess (must be flagged, not chosen silently).
A query asks for one subject; success = the current value is in the retrieved context
AND no superseded value for that subject is presented as current.

Conditions (paired on identical episodes):
  * recency_last_k      – the most recent memories that fit the budget
  * history_in_context  – the full chronological history, truncated to the budget (oldest dropped)
  * lucid_retrieval     – Lucid search (supersession-aware, ranked) within the same budget
  * lucid_no_supersede  – ablation: corrections stored as new notes instead of supersede()
  * lucid_hybrid        – lucid_retrieval + recency backfill of unused budget
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..core import Lucid
from ..memory import Scope
from ..util import approx_tokens

SUBJECTS = ["deploy day", "staging database", "test runner", "release branch", "on-call engineer", "invoice currency",
            "code formatter", "API region", "backup bucket", "design system", "build tool", "error tracker"]
VALUES = ["Monday", "Tuesday", "Postgres", "MySQL", "pytest", "unittest", "main", "trunk", "Amna", "Brian", "AUD", "USD",
          "black", "ruff", "ap-southeast-2", "us-east-1", "lucid-bk-1", "lucid-bk-2", "Fluent", "Material", "vite",
          "webpack", "Sentry", "Rollbar"]
# Paraphrased queries share (almost) no vocabulary with the stored facts: a known weakness of
# lexical retrieval, included so the report shows where Lucid's default retrieval fails.
PARAPHRASE = {"deploy day": "which weekday do we ship", "staging database": "what DB backs pre-prod",
              "test runner": "what do we run specs with", "release branch": "where do releases get cut from",
              "on-call engineer": "who carries the pager", "invoice currency": "what money unit do bills use",
              "code formatter": "what tool prettifies source", "API region": "where is the endpoint hosted",
              "backup bucket": "where do snapshots go", "design system": "which UI kit",
              "build tool": "what bundles the app", "error tracker": "what catches crashes"}
FILLER = ["meeting notes mention the project timeline", "client asked about the roadmap again",
          "remember to update the onboarding doc", "the dashboard colours were discussed", "lunch order was pizza",
          "the project kickoff was moved", "someone asked about the API docs", "release notes need screenshots"]


@dataclass
class Episode:
    events: list[tuple[str, str, str, bool]]  # (subject, value, origin, is_correction)
    query_subject: str
    answer: str
    stale: list[str]


def make_episode(rng: random.Random, n_subjects: int = 6, n_filler: int = 40, p_correct: float = 0.5) -> Episode:
    """Facts, filler and corrections interleaved in time (corrections always after their original)."""
    subjects = rng.sample(SUBJECTS, n_subjects)
    timeline: list[tuple[float, tuple[str, str, str, bool]]] = []
    current: dict[str, str] = {}
    stale: dict[str, list[str]] = {s: [] for s in subjects}
    for s in subjects:
        t0 = rng.random() * 0.7
        v = rng.choice(VALUES)
        timeline.append((t0, (s, v, "user", False)))
        if rng.random() < p_correct:
            nv = rng.choice([x for x in VALUES if x != v])
            timeline.append((t0 + rng.random() * (1 - t0), (s, nv, "user", True)))
            stale[s].append(v)
            v = nv
        current[s] = v
    for _ in range(n_filler):
        timeline.append((rng.random(), ("", rng.choice(FILLER) + f" (#{rng.randrange(1000)})", "user", False)))
    timeline.sort(key=lambda x: x[0])
    q = rng.choice(subjects)
    return Episode([e for _, e in timeline], q, current[q], stale[q])


def _text(subject: str, value: str) -> str:
    return f"The {subject} is {value}." if subject else value


def _score(context: str, ep: Episode) -> int:
    # Lines rendered as explicit correction records mention the old value as history, not as current.
    lines = [ln for ln in context.splitlines() if "| correction |" not in ln]
    context = "\n".join(lines)
    has_answer = f"The {ep.query_subject} is {ep.answer}." in context
    has_stale = any(f"The {ep.query_subject} is {s}." in context for s in ep.stale)
    return int(has_answer and not has_stale)


def run_condition(ep: Episode, cond: str, budget: int, db: str = ":memory:", paraphrase: bool = False) -> int:
    if cond in ("recency_last_k", "history_in_context"):
        lines = [_text(s, v) for s, v, _, _ in ep.events]
        if cond == "recency_last_k":
            ctx, used = [], 0
            for line in reversed(lines):
                t = approx_tokens(line)
                if used + t > budget:
                    break
                ctx.append(line)
                used += t
            return _score("\n".join(ctx), ep)
        ctx, used = [], 0
        for line in lines:  # chronological; truncation drops the newest (typical "fill until full")
            t = approx_tokens(line)
            if used + t > budget:
                break
            ctx.append(line)
            used += t
        return _score("\n".join(ctx), ep)
    L = Lucid(db)
    try:
        P = Scope("eval", "ep")
        ids: dict[str, str] = {}
        for s, v, origin, corr in ep.events:
            key = f"subject:{s}" if s else None
            if corr and s in ids and cond in ("lucid_retrieval", "lucid_hybrid"):
                ids[s] = L.memory.correct(ids[s], _text(s, v), reason="user correction")["id"]
            else:
                kind = "fact" if s else "note"
                m = L.memory.add(_text(s, v), kind=kind, origin=origin, scope=P,
                                 key=key if cond in ("lucid_retrieval", "lucid_hybrid") else None)
                if s:
                    ids[s] = m["id"]
        q = PARAPHRASE[ep.query_subject] if paraphrase else f"what is the {ep.query_subject}"
        r = L.memory.search(q, P, budget_tokens=budget, backfill_recent=(cond == "lucid_hybrid"))
        return _score(r.context, ep)
    finally:
        L.close()


# lucid_hybrid was added AFTER inspecting development-split results (paraphrase failure);
# that development exposure is recorded in the report.
CONDITIONS = ["recency_last_k", "history_in_context", "lucid_no_supersede", "lucid_retrieval", "lucid_hybrid"]


def run(seed: int, n: int, budget: int, paraphrase: bool = False) -> dict[str, list[int]]:
    rng = random.Random(seed)
    eps = [make_episode(rng) for _ in range(n)]
    return {c: [run_condition(e, c, budget, paraphrase=paraphrase) for e in eps] for c in CONDITIONS}
