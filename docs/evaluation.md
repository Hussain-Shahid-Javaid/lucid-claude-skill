# Evaluation methodology

## Five kinds of evidence, kept separate

1. **Unit tests** (`pytest`).
2. **Integration and contract tests:** moto S3, the fake Google Docs client, MCP stdio, the service API, and Playwright for the dashboard and extension.
3. **Live-provider validation** (`pytest -m live`, needs configuration). This was **not run** for S3 or Google Docs. The MCP handshake was live-checked with Claude Code 2.1.311.
4. **Model-performance evaluations.** These need a live model, and **none has been run**. The runner records the planned conditions and requirements.
5. **Research experiments:** `lucid eval run` records them in an experiment ledger.

Passing (1) and (2) says nothing about whether reasoning improved.

## Offline mechanism suites (`src/lucid/evals/`)

### Memory retrieval

- **Data:** generated episodes of interleaved facts, filler and corrections.
- **Query:** one subject per episode.
- **Success:** the current value appears in a fixed 150-token context, and no superseded value appears as current.
- **Conditions:**
  - `recency_last_k`
  - `history_in_context` (full chronological history truncated to the budget)
  - `lucid_no_supersede` (ablation)
  - `lucid_retrieval`
  - `lucid_hybrid` (recency backfill)
- **Query styles:** keyword and paraphrase.

### Correction cycle

- **Data:** a fixed pool of functions with seeded bugs and independent tests.
- **Reviser:** a random proposal generator standing in for reviewer noise.
- **Conditions:** `no_review`, `naive` (accept revision) and `guarded` (Lucid regression guard).
- **Metrics:** correct-answer corruption, fix rate and final correctness.

## Protocol

- Primary metrics, splits and comparisons are pre-registered in `evals/preregistration.json`.
- **Development split** (seed 1) is used while building. **Held-out split** uses different seeds and larger n.
- **Development exposure is disclosed.** The paraphrase style and the `lucid_hybrid` condition were added after the development results were seen.
- All conditions run on identical episodes. Reports give:
  - Wilson intervals for proportions
  - paired bootstrap intervals for differences
  - an exact sign test
- Generators, evaluators and answer keys are separate functions. Candidates and revisers never see the held-out tests.
- Runs are deterministic, with seeds recorded. Cost is recorded: 0 model calls.

## Results (held-out; full tables in `reports/evaluation-heldout.md`)

| suite | finding |
|---|---|
| memory, keyword queries | `lucid_retrieval` 1.00 vs `history_in_context` 0.24 (diff +0.76, 95% CI 0.71–0.81). This is a ceiling result on a task built around vocabulary overlap. |
| memory, **paraphrase queries** | `lucid_retrieval` **0.077** vs `recency_last_k` 0.257 and `history_in_context` 0.24 (diff −0.163, CI −0.22 to −0.11). **This is a regression.** Lexical retrieval fails without shared vocabulary, and recency backfill (`lucid_hybrid` 0.097) does not fix it. |
| correction | Corruption of initially-correct answers: naive 0.644 → guarded **0.0** (diff −0.644, CI −0.71 to −0.58). Fix rate: guarded 0.568 vs naive 0.365. |

## Live-model suite (planned, not run)

The live suite would compare these conditions on paired tasks, with a frozen model version, matched tools and an explicit budget:

| condition | what changes |
|---|---|
| baseline assistant | no Lucid |
| full Lucid | all mechanisms on |
| no memory | memory removed |
| no self-monitoring | monitor removed |
| no independent review | review removed |
| no correction | correction cycle removed |
| relevant-history-in-context | history supplied directly in context |
| sequential vs parallel | orchestration mode varied |

Comparisons would be reported both resource-matched and resource-disclosed.

**Metrics:**
- completion and correctness
- unsupported claims and citation support
- resistance to misleading prompts
- calibration (Brier score), with confidence recorded before outcomes
- correction success and corruption
- cross-tool continuation
- automatic-decision quality
- latency, tokens and cost

**Rules:**
- Reset memory between independent tasks.
- Disclose model-judge limits and blind judges to condition.
- Do not use AI-detector scores.

**Requirements:** `LUCID_ENABLE_PAID_CALLS=1`, an API key, and a budget ledger.
