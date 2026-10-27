# Budgets

- **Tokens consumed** (cumulative) ≠ **context window** (per call) ≠ **cost** ≠ **wall time** ≠ **rate limits**.
- Accounting: charged = input + output (+ reasoning only when reported separately). Cached input is a
  subset of input and is not added twice.
- Reserve before every controlled call: `budget reserve <ledger> --purpose ... --input N --max-output M`.
  Then `budget reconcile <reservation> --input a --output b`. If a call was canceled/timed out with
  unknown usage: `budget unknown <reservation>` (the reservation stays counted).
- A fraction (default 15%) is held back for integration, verification, checkpointing and the final
  report; only `--use-reserve` reservations may use it.
- `mode=hard` only when every relevant call goes through the ledger (e.g. Lucid's own API workers).
  The host chat's own usage is invisible to a skill, so ledgers covering it are `estimated`.
- When the budget cannot fit the next step: checkpoint, `task finish --budget-exhausted`, and report the
  continuation plan. Never call the task completed because the budget ran out.
