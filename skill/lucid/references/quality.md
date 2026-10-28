# Quality plans and release gates

The aim is **no known blocking defects within the verified scope**. That is something you can check. "Perfect" or "zero errors" is not something anyone can promise.

## How it works

1. **Define quality first.** A plan lists acceptance criteria. The categories are correctness, completeness, maintainability, usability and accessibility, performance, security and privacy, communication, and project conventions. Each criterion is either mandatory or optional polish.
2. **Pick a proportionate profile.** Each profile requires certain kinds of check:

   | profile | for | required check kinds |
   |---|---|---|
   | `trivial` | a small, reversible change such as formatting | lint |
   | `standard` | an ordinary code change | lint, unit |
   | `release` | a version other people install | lint, typecheck, unit, integration, e2e, security, dependency, build, docs, plus an independent review |
   | `production` | a live deployment | release, plus performance and rollback |

   A required kind that does not apply can be marked not applicable, with a reason. The reason is listed as a limitation.
3. **Bind evidence to the files.** Each check names the files it depends on. A result is stored with a content hash of exactly those files.
   - If you edit them afterwards, the result becomes **stale** and the check must run again.
   - Edits to unrelated files leave the result valid.
   - If the files change while a check is running, the result is thrown away.
4. **Catch weakened tests.** A check can name **guarded** files, such as `tests/`. Suppose a check failed, and later passed only after its definition or its guarded files changed. That pass is held until an independent review covers it.
   - A mandatory check cannot be made optional by editing it.
5. **Make reviews concrete.** A review must list what it examined. Each finding needs a severity, a title, and a location or detail. Findings become defects. Praise with nothing examined is rejected.
6. **Track defects.**
   - Severities are blocker, major, minor and trivial.
   - Blocker and major fixes must name a check that passed on the current files.
   - Only you can accept a known defect, and a blocker can never be accepted.
   - A defect found after release is recorded as *escaped*.
7. **Run the gate.** The gate gives one of three results:

   | result | when |
   |---|---|
   | `verified` | every mandatory check passed on the current files, there are no open blocker or major defects, and the required review covers the current files |
   | `failed` | a mandatory check failed, or a blocker is open |
   | `verification_pending` | anything else; the exact blockers are listed |

   The report covers:
   - the verified scope
   - the checks performed, with their file revisions and durations
   - known defects by severity
   - checks not run
   - limitations
8. **Spend time on required checks first.** With a time budget, builds and mandatory checks run first, and optional polish is skipped first. A mandatory check that doesn't fit the budget leaves the gate pending. It never passes by default.
9. **Compare alternatives.** When different approaches could change the outcome, `lucid.py quality compare` scores them on usefulness, originality, feasibility, simplicity, maintainability and cost.
   - The originality score comes from the originality check when a candidate id is given.
   - It warns when the chosen option is beaten on every score.
   - It also warns when the choice adds complexity without adding usefulness.
10. **Measure.** `lucid.py quality metrics` reports:
    - plans verified, with a Wilson interval
    - plans verified on the first gate
    - escaped defects
    - regressions (a check that passed and then failed)

    Zero escaped defects over *n* verified plans is reported as a bound: below about 3/*n* at 95%. It is not proof that none exist.

## Commands

```bash
lucid quality init quality.json            # Lucid's own release plan is in quality.json
lucid quality run <plan> [--time-budget 900] [--no-optional] [--only unit]
lucid quality record <plan> layout --passed --evidence "checked the page at 375px and 1280px; nothing overlaps"
lucid quality review <plan> --file review.json
lucid quality defect add <plan> --severity major --title "..." --evidence "..."
lucid quality gate <plan> --markdown --out ../quality-gate.md   # exit 0 verified, 1 failed, 3 pending
```

## Lucid's own release gate

`quality.json` defines Lucid's release plan. The table shows what each check runs.

| check | runs |
|---|---|
| lint | ruff with the bugbear and security rule sets |
| type checks | mypy, and the TypeScript compiler for the dashboard |
| unit, integration and end-to-end tests | split across three pytest runs |
| security lint | ruff's security (bandit) rules |
| secret scan | a scan of every tracked file for credentials |
| dependency audit | pip-audit and npm audit |
| builds | the skill zip and the dashboard, both reproducible |
| docs | one version everywhere, working links, the right test count, plain language |
| performance | a benchmark (optional) |

Write the report outside the files the checks read. A report saved inside them changes those files, so the evidence becomes stale and the next gate says `verification_pending`. Lucid's own gate report for each release is attached to its GitHub release instead of being committed.
