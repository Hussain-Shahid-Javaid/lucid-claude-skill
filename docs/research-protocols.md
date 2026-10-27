# Research protocols

## Discovery mode

| step | requirement |
|---|---|
| Classify | missing information · new combination of known techniques · requires experimentation · apparently open · underspecified/infeasible |
| Specify | assumptions, constraints, outputs, resources, **independent** verification criteria |
| Search | primary literature and implementations; log queries, sources, dates. "Not found in our search" is not novelty |
| Generate | materially different approaches (decomposition, reduction, math, constraint solving, search, simulation, prototypes, program evolution where justified); parallel workers for distinct approaches |
| Experiment | small discriminating experiments first: counterexamples, boundary cases, property tests, reference implementations |
| Isolate | candidate generation never sees held-out data or evaluators; success criteria are fixed in advance |
| Ledger | hypothesis → prediction → experiment → result → criticism → revision (`Experiments`), with configs, artifacts, failures, costs |
| Report | verified within bounds · empirically supported · unverified candidate · disproven · unresolved · budget exhausted |

Finite tests do not prove universal claims. Use a proof assistant or a checkable certificate where feasible, and record its assumptions.

### Bundled controlled experiments (`src/lucid/research_modules/discovery.py`)

**A. Rediscovery, a positive control.**
- **Task:** find a recurrence for binary strings without "111".
- **Method:** fit on n ≤ 8, then evaluate held-out n = 9..22 by exhaustive enumeration.
- **Result:** `a(n) = a(n−1) + a(n−2) + a(n−3)`, labelled *empirically supported within n ≤ 22*. This result is known in the literature and is not a discovery.

**B. Falsification control.**
- **Hypothesis:** "n² + n + 41 is always prime."
- **Result:** disproven at n = 40 (1681 = 41²), and the counterexample can be checked independently.

## Consciousness-related research (optional module)

**Basis.** The indicator-property approach of Butlin, Long et al. (2023), *Consciousness in Artificial Intelligence: Insights from the Science of Consciousness* (arXiv:2308.08708). It draws on:
- global workspace theory (Baars; Dehaene & Changeux)
- recurrent processing theory (Lamme)
- higher-order theories (Rosenthal; Lau)

**Rules:**
- Test *functional* hypotheses about Lucid's own architecture with controlled removal, disruption or replacement.
- Distinguish application-level analogues from neural mechanisms.
- Never train or prompt the system to claim consciousness. Never treat self-reports as evidence. Never produce "consciousness percentages". Never equate memory, autonomy or fluency with experience.
- Activation-level experiments need genuine access to an open-weight model (`ActivationProbe`). Hosted models give no such access, and it is never simulated.

### GW-1: global availability (ablation + disruption)

- **Mechanism:** a capacity-limited broadcast buffer.
- **Prediction:**
  - Integration tasks need broadcast.
  - Local tasks do not.
  - Scrambled broadcast and an information-matched local-only control do not rescue integration.
- **Falsifiers:**
  - Integration works without broadcast, or
  - local tasks degrade equally.
- **Held-out result:**
  - Integration: 1.0 with broadcast, 0.0 without.
  - Local: 1.0 in both.
  - Matched control does not rescue integration.
  - Scrambling also broke local tasks, so the disruption control is **not specific**. This is reported as an observation.
- **Limitation:** the dissociation is largely guaranteed by construction, so this mainly validates the method.

### HO-1: higher-order monitoring

- **Mechanism:** a monitor estimates reliability from agreement between solver runs and abstains below a threshold.
- **Result:** Brier score 0.166 vs 0.174 without the monitor. Selective accuracy 0.935 at 34.5% coverage, against 0.777 overall.
- **Limitation:** the solver is simulated.

Neither experiment says anything about subjective experience.
