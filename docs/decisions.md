# Timed questions, standing policies and completion-first autonomy

## Standing policies (authorizations)

```bash
lucid decide authorize internal_naming            # also: file_organization, equivalent_implementation,
lucid decide authorize presentation --expires 86400 #       presentation, ordering, optional_preference
```

Some categories can **never** be resolved by silence: `credentials`, `essential_fact`, `spending`, `scope_expansion`, `external_destination`, `destructive_action` and `publish`. Trying to authorize them is refused.

A specific external action needs its own authorization with an explicit narrow scope and limits, for example `authorize_action("post_slack", scope="channel:#builds", limits={"max_count": 3})`.

## Asking

```bash
lucid decide ask "Name for the helper module?" --options util,helpers --default util \
  --category internal_naming --timeout 120 --task <task_id> --consequence "affects import paths only"
```

**Before asking,** Lucid checks remembered preferences (`--pref-key`). If one matches, the question resolves immediately with `answered_from_preference`.

Each question records:
- ID, decision, options and default
- Deadline (default **120 s**, configurable in the `decision_timeout_s` setting)
- Scope and consequence
- The authorization that permits a default
- Its `on_timeout` behaviour, shown to the user

## Resolution rules

- **Exactly one resolution.** The atomic `UPDATE … WHERE status='open'` means only one of these wins: a user answer, a timeout default or a cancel.
- **Answers win if they arrive first.** An answer that arrives before the default has been applied takes precedence, even if it lands slightly after the nominal deadline.
- **Late answers are recorded.** A later answer goes into `late_responses`, and the response says what was applied. If a reversal hook is registered for the category, it runs once.
- **On expiry,** the authorization is re-checked. A revoked or expired authorization means the question becomes `blocked`, not `defaulted`.
- **Default side effects run at most once.** They go through `ActionLedger.run_once("default:<qid>")`, so a restart or a concurrent timer never runs a completed or in-progress action again.
  - If the action **raises**, it is marked `failed` and retried on a later tick.
  - If the process **dies mid-action**, the outcome is unknown. The claim stays `claimed`, is listed by `ActionLedger.unresolved()`, and is not re-run automatically.
  - If the process dies **after** the question was marked `defaulted` but **before** the action was claimed, the next tick runs it.
- **Blocked questions can still be answered.** A blocked question was never applied, so a later user answer resolves it normally.
- **Authorization scopes match exactly.** A scope matches exactly unless it ends with `*`, which makes it a prefix match.
- **Controls.** Answer, extend, accept default, answer and remember (stored as a preference), pause/resume, and cancel. Canceling a task cancels its open questions.

## Timers

`DecisionScheduler` is a runtime thread inside `lucid serve`. It sleeps until the next deadline, with no model polling. On start it immediately reconciles deadlines that passed while it was down.

A Markdown skill alone cannot keep executing after its host stops. Without the service, `lucid decide tick` performs the same reconciliation.

## Completion-first tasks

1. Define acceptance checks before executing. Mark checks `essential: false` for polish items.
2. Produce usable work early. Record checks with evidence. `task check --cmd …` runs an executable check.
3. Use `record_attempt(approach, new_evidence)` to detect repeats. Repeating the same approach without new evidence raises `NoProgress`; when that happens, replan with a materially different approach.
4. Run `finish` to get the status:
   - `completed`, only when every essential check passed
   - `partially_completed`
   - `blocked`
   - `budget_exhausted`
   - `canceled`
   - `failed`

   The report lists completed checks, remaining requirements, artifacts, decisions and a continuation plan. A task with no acceptance checks can never be marked `completed`.
