# Research mode (unfamiliar or possibly unsolved problems)

1. Classify: missing information | new combination of known techniques | needs experimentation |
   apparently open question | underspecified/infeasible.
2. Specify assumptions, constraints, outputs, resources and *independent* acceptance criteria.
3. Search primary literature/implementations when tools allow; log queries, sources and dates.
   "Not found in our search" ≠ novel. You cannot enumerate your training data.
4. Generate materially different approaches (decomposition, reduction, math, constraint solving,
   search, simulation, prototypes). Use parallel workers for distinct approaches when worthwhile.
5. Prefer small discriminating experiments: counterexamples, boundary cases, property tests,
   reference implementations. Keep held-out evaluators out of the candidate generator's reach.
6. Ledger every step: `hypothesis → prediction → experiment → result → criticism → revision`
   (`lucid.research.Experiments`, `lucid eval run` for the bundled examples).
7. Report with restricted labels only: verified within bounds, empirically supported, unverified
   candidate, disproven, unresolved, budget exhausted. Finite tests never prove universal claims;
   use a proof assistant or checkable certificate when feasible and state its assumptions.

## Consciousness-related experiments (optional, separate)

Functional, application-level experiments on Lucid's own architecture (global availability,
monitoring), designed after the indicator-property approach of Butlin, Long et al. (2023,
arXiv:2308.08708). Each documents basis, mechanism, prediction, controls, falsifiers and limits.
Do not: train or prompt the system to claim consciousness, treat self-reports as evidence, convert
scores into "consciousness percentages", or equate memory/fluency/autonomy with experience.
Activation-level work needs real access to an open-weight model; hosted APIs don't provide it.
