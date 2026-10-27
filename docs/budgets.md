# Budgets

These quantities are distinct and never conflated:

- **Cumulative tokens consumed.** This is what the ledger tracks.
- **Per-call context capacity.** This is `context_window`, checked separately. It is not your remaining allowance.
- **Monetary cost.** Tracked via `est_cost` and `actual_cost`.
- **Elapsed time.** Tracked via `time_limit_s`.
- **Provider rate limits.** Handled by the outbox and worker backoff, not by the ledger.

## Accounting convention

`charged = input_tokens + output_tokens (+ reasoning_tokens only if the provider reports them separately from output)`

`cached_tokens` are recorded for cost, but they are a subset of input and are never added again. A late usage report for an `unknown` reservation replaces the conservative hold with the actual numbers. Reconciliation is idempotent.

## Lifecycle

1. **`reserve`** (atomic, using `BEGIN IMMEDIATE`, so it is safe across threads and processes):
   - `need = input_estimate + max_output_tokens`.
   - The reservation is refused if `need > limit − verification_reserve − (used + in-flight + unknown)`.
   - Calls flagged `use_reserve=True` may also draw on the verification reserve. Use this for integration, verification, checkpointing and the final report.
2. **`reconcile`** replaces the hold with actual usage.
3. **`release`** applies when the call was never dispatched.
4. **`mark_unknown`** applies when a call was canceled or timed out after dispatch. The full reservation stays counted until a late report arrives.

## Hard versus estimated

- **`mode="hard"`** is appropriate only when Lucid makes every relevant call itself, for example `AnthropicRunner` workers in the orchestrator. Each call is reserved with a bounded `max_tokens`, so the ceiling holds.
- **`estimated`** applies to everything else. In particular, a skill running inside a host chat cannot see or limit the host's own usage. The dashboard and `budget status` say exactly what is and is not covered.

## When the budget runs out

The coordinator does not start work that cannot fit:
- The task is checkpointed with `not_dispatched` and marked `budget_exhausted`.
- Final verification still runs from the held-back reserve.
- `task finish` reports the completed checks, the remaining requirements and a continuation plan.
