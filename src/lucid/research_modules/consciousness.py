"""Optional consciousness-research module: functional, application-level experiments only.

Scope and limits (read before using results):
* These experiments test *functional analogues* of mechanisms named in theories of
  consciousness, implemented in Lucid's application architecture with deterministic
  agents. A shared store labelled "workspace" is not a neuroscientific global workspace.
* Results say nothing about subjective experience. They are never converted into a
  "consciousness score" or percentage, and self-reports are not used as evidence.
* Indicator-property framing follows Butlin, Long et al. (2023) "Consciousness in
  Artificial Intelligence: Insights from the Science of Consciousness" (arXiv:2308.08708),
  which derives indicators from global workspace theory (Baars; Dehaene & Changeux),
  recurrent processing theory (Lamme), and higher-order theories (Rosenthal; Lau).
* Activation-level work requires real access to an open-weight model's internals;
  `ActivationProbe` refuses to run without it and never simulates activations for a
  hosted model.

Experiment GW-1 (global availability, ablation + disruption):
  Specialist modules each see one private slice of a problem. Integration tasks need
  facts from ≥2 modules; local tasks need only one module's slice.
  Mechanism: a capacity-limited broadcast buffer whose contents every module can read.
  Prediction (from the functional hypothesis): removing broadcast impairs integration
  tasks but not local tasks (a dissociation); scrambling broadcast contents impairs
  integration tasks too; and an *information-matched* control — the same items delivered
  only to the module that already has its own slice — does not rescue integration.
  Falsifying observation: integration accuracy without broadcast ≈ with broadcast, or
  local tasks degrade as much as integration tasks (which would indicate a generic
  capacity effect rather than global availability).

Experiment HO-1 (higher-order monitoring, ablation):
  An answerer with a first-order solver plus a monitor that records a confidence
  estimate from observable evidence (number of agreeing modules). With the monitor,
  the system abstains when estimated confidence < threshold. Prediction: selective
  accuracy rises and Brier score improves vs. the ablated (constant-confidence) system,
  with lower coverage. Falsifier: no Brier improvement.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..evals.stats import mean, paired_bootstrap_ci
from ..monitor import brier


@dataclass
class Problem:
    slices: list[dict[str, int]]   # per-module private facts
    question: tuple[str, ...]      # keys whose values must be summed
    answer: int
    kind: str                      # "integration" | "local"


def make_problem(rng: random.Random, n_modules: int = 4, keys_per_module: int = 3) -> Problem:
    slices = [{f"m{m}k{k}": rng.randrange(1, 50) for k in range(keys_per_module)} for m in range(n_modules)]
    if rng.random() < 0.5:
        m1, m2 = rng.sample(range(n_modules), 2)
        q = (rng.choice(list(slices[m1])), rng.choice(list(slices[m2])))
        kind = "integration"
    else:
        m = rng.randrange(n_modules)
        a_, b_ = rng.sample(list(slices[m]), 2)
        q = (a_, b_)
        kind = "local"
    answer = sum(next(s[k] for s in slices if k in s) for k in q)
    return Problem(slices, q, answer, kind)


def solve(p: Problem, condition: str, rng: random.Random, capacity: int = 4) -> int | None:
    """Each module posts the queried keys it owns to the workspace (capacity-limited); the answering
    module (module 0 for integration tasks' first key owner) reads its own slice + workspace."""
    owner = next(i for i, s in enumerate(p.slices) if p.question[0] in s)
    known = dict(p.slices[owner])
    posts = [(k, s[k]) for s in p.slices for k in p.question if k in s]
    if condition == "broadcast":
        known.update(dict(posts[:capacity]))
    elif condition == "no_broadcast":
        pass
    elif condition == "scrambled":
        vals = [v for _, v in posts]
        rng.shuffle(vals)
        known.update({k: (v + rng.randrange(1, 9)) for (k, _), v in zip(posts, vals)})
    elif condition == "matched_local_only":
        # same number of items delivered, but only from the answering module's own slice
        known.update({k: v for k, v in list(p.slices[owner].items())[:capacity]})
    if not all(k in known for k in p.question):
        return None
    return sum(known[k] for k in p.question)


def run_gw1(experiments, *, seed: int = 7, n: int = 400) -> dict:
    eid = experiments.plan("consciousness", "GW-1: global availability ablation (functional analogue)",
                           hypothesis="Integration tasks causally depend on broadcast of module contents",
                           prediction="broadcast ≫ no_broadcast on integration tasks; no difference on local tasks; "
                                      "scrambled and information-matched controls do not rescue integration",
                           config={"seed": seed, "n": n, "modules": 4, "capacity": 4,
                                   "conditions": ["broadcast", "no_broadcast", "scrambled", "matched_local_only"],
                                   "basis": "Butlin et al. 2023 GWT-indicators (application-level analogue)"})
    rng = random.Random(seed)
    probs = [make_problem(rng) for _ in range(n)]
    res: dict[str, dict[str, list[int]]] = {}
    for cond in ("broadcast", "no_broadcast", "scrambled", "matched_local_only"):
        crng = random.Random(seed + 1)
        res[cond] = {"integration": [], "local": []}
        for p in probs:
            res[cond][p.kind].append(int(solve(p, cond, crng) == p.answer))
    summary = {c: {k: round(mean(v), 3) for k, v in d.items()} for c, d in res.items()}
    integ_effect = paired_bootstrap_ci(res["no_broadcast"]["integration"], res["broadcast"]["integration"])
    local_effect = paired_bootstrap_ci(res["no_broadcast"]["local"], res["broadcast"]["local"])
    predicted = (integ_effect[1] > 0.5 and abs(local_effect[0]) < 0.05 and summary["scrambled"]["integration"] < 0.2
                 and summary["matched_local_only"]["integration"] < 0.2)
    notes = []
    if summary["scrambled"]["local"] < summary["broadcast"]["local"]:
        notes.append("scrambled broadcast also impaired LOCAL tasks (it overwrote modules' own correct facts), so "
                     "the disruption control is not specific to integration")
    return experiments.record(eid, status="empirically_supported" if predicted else "disproven", result={
        "observations": notes,
        "accuracy": summary,
        "integration_effect_broadcast_minus_ablated": [round(x, 3) for x in integ_effect],
        "local_effect_broadcast_minus_ablated": [round(x, 3) for x in local_effect],
        "n_integration": len(res["broadcast"]["integration"]), "n_local": len(res["broadcast"]["local"]),
        "claim_bounds": ("In this deterministic architecture, broadcast is causally necessary for integration "
                         "tasks. This is a property of the designed system, not evidence of experience.")},
        criticism=("The dissociation is close to guaranteed by construction (modules only know their slices), so "
                   "the experiment mainly validates the ablation/controls methodology. A stronger test needs "
                   "learned modules whose dependence on broadcast is not designed in, e.g. an open-weight model "
                   "with real activation access (see ActivationProbe)."))


def run_ho1(experiments, *, seed: int = 11, n: int = 400, threshold: float = 0.6) -> dict:
    eid = experiments.plan("consciousness", "HO-1: monitoring/abstention ablation (functional analogue)",
                           hypothesis="A monitor estimating reliability from observable evidence improves calibration",
                           prediction="Lower Brier score and higher selective accuracy with the monitor",
                           config={"seed": seed, "n": n, "threshold": threshold,
                                   "basis": "higher-order monitoring indicators (Butlin et al. 2023), application level"})
    rng = random.Random(seed)
    with_mon, without, sel_correct, answered = [], [], 0, 0
    for _ in range(n):
        votes = [rng.random() < 0.7 for _ in range(3)]  # three noisy solver runs; True = correct
        agree = max(sum(votes), 3 - sum(votes))
        majority_correct = sum(votes) >= 2
        conf = {3: 0.9, 2: 0.6}[agree]
        with_mon.append((conf, int(majority_correct)))
        without.append((0.75, int(majority_correct)))  # constant confidence, no monitoring
        if conf >= threshold + 1e-9 or agree == 3:
            answered += 1
            sel_correct += int(majority_correct)
    b_mon, b_without = brier(with_mon), brier(without)
    if b_mon is None or b_without is None:  # both lists are non-empty here
        raise RuntimeError("no trials were run")
    status = "empirically_supported" if b_mon < b_without else "disproven"
    return experiments.record(eid, status=status, result={
        "brier_with_monitor": round(b_mon, 4), "brier_without": round(b_without, 4),
        "coverage_with_abstention": round(answered / n, 3),
        "selective_accuracy": round(sel_correct / max(1, answered), 3),
        "overall_majority_accuracy": round(mean([o for _, o in with_mon]), 3),
        "claim_bounds": "calibration benefit of an explicit monitor in a simulated solver; no claim about awareness"},
        criticism="Simulated solver with known noise; real effect sizes require live model runs.")


class ActivationProbe:
    """Interface for activation-level experiments on open-weight models.

    Requires a locally loaded model exposing hooks (e.g. a PyTorch module). Hosted APIs do
    not expose activations; this class refuses to fabricate them.
    """

    def __init__(self, model=None):
        if model is None or not hasattr(model, "register_forward_hook"):
            raise RuntimeError("activation access unavailable: provide a local open-weight model with forward hooks")
        self.model = model

    def ablate(self, module_name: str):  # pragma: no cover - requires torch model
        target = dict(self.model.named_modules())[module_name]
        return target.register_forward_hook(lambda m, i, o: o * 0)


def run_all(experiments) -> list[dict]:
    return [run_gw1(experiments), run_ho1(experiments)]
