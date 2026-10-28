# Original ideas and prior-art checks

Lucid helps you generate ideas and check them against existing work. It does not prove an idea is new.
Every status is limited to what was actually searched.

## 1. Define the target

```bash
lucid.py invent target "Clients overload a service when they all retry after an outage" \
  --users "API client authors" --constraint "no server changes" \
  --existing "exponential backoff" --improvement "lower peak load after recovery" \
  --level mechanism --allow local:reference --allow host:web --confidential "AcmeBank"
```

Levels: `mechanism`, `implementation`, `application`, `combination`. Say which one you are aiming for.

Three kinds of "new" are kept apart:

| label | meaning |
|---|---|
| new to the user | not in this user's Lucid records |
| new within the searched collection | no equivalent in the sources that were searched |
| potentially new more broadly | no equivalent across a documented search that covered papers, products and repos |

## 2. Generate candidates that differ in substance

Use different routes: challenge an assumption, combine fields, change a constraint, reverse a process,
remove a dependency, or a new method. Each candidate states how it works and what exactly is different.

```bash
lucid.py invent candidate <study> --title "..." --mechanism "how it works" \
  --difference "what is specifically different" --method change_constraint \
  --advantage "why it is better" [--component A --component B] [--domain irrigation]
```

Lucid flags cosmetic novelty: no mechanism, no stated difference, "AI-powered" with no model doing
anything, a combination with no stated advantage, and candidates that are near-copies of each other.

## 3. Check Lucid's own records first

`invent check` compares the candidate's mechanism (not its title, so a new name changes nothing) with
earlier candidates, including rejected ones, and with the user's memories. Very close matches are
treated as the same idea. Close-but-uncertain matches are listed as possible repeats and block an
originality claim until someone compares them:

```bash
lucid.py invent not-a-repeat <cand> <earlier_id> --reason "the earlier one was for solo practice"
```

To revisit an earlier idea on purpose, add it with `--revisit-of <id> --revisit-reason "what changed"`.

Similarity scores are retrieval aids for deciding what to read. They are not judgments of novelty.

## 4. Search existing work (when authorized)

Lucid searches the bundled reference collection itself. It cannot browse. If you can, search the web
yourself and log it. Cover:

- the problem, in the user's words and in other fields' words
- the mechanism and its synonyms
- adjacent industries
- each component, and the components together

`lucid.py invent plan <cand>` prints suggested queries, with confidential terms already removed.

```bash
lucid.py invent search-log <cand> --provider host:web --kind mechanism --status ran \
  --query "..." --results 8 --covers papers,repos
lucid.py invent search-log <cand> --provider scholar --kind problem --status unavailable --note "rate limited"
lucid.py invent add-source <cand> --provider host:web --kind papers --ref "https://doi.org/..." \
  --title "..." --summary "what it does" --inspected --relation different --differences "..."
```

Rules:

- Log failed and skipped searches too. A failed search is not evidence of absence.
- Mark `--inspected` only if you read the source itself, not just a search snippet.
- Never cite a source you could not open.
- Keep private details out of external queries. Send queries only to providers the user allowed.

## 5. Compare and assign a status

For the closest sources Lucid records what exists, what is borrowed, what is materially different, the
practical advantage and any risky similarities. Then one of:

| status | when |
|---|---|
| known or substantially equivalent | an inspected source, or the comparison, shows the same mechanism, or Lucid already has it |
| adaptation or incremental | a known mechanism applied elsewhere, extended, or combined |
| potentially original within the documented search scope | searches ran (problem, mechanism, synonyms, and components if any), the closest sources were inspected, a difference and an advantage are stated, and nothing is flagged |
| originality unresolved | searches failed, coverage is incomplete, close sources are unread, or the comparison is too weak |
| not assessed | no authorized search ran |

The claim text always says what was searched and that this is not proof nobody has thought of it.

## 6. Original ideas only

```bash
lucid.py invent gate <study>                         # keeps only qualifying ideas
lucid.py invent gate <study> --include-incremental   # also lists adaptations, labelled
```

If nothing qualifies, say so. Do not relabel incremental ideas as original.

## 7. Test value separately

Novelty and usefulness are different questions. Test feasibility, correctness, usefulness, cost and
the claimed advantage with prototypes, experiments, calculations or review:

```bash
lucid.py invent value <cand> --check "prototype=python3 bench.py --compare baseline" \
  --feasibility "runs on a laptop" --resources "1 day"
```

A failed check marks the idea invalid, however original it looks.

## 8. Parallel workers

`lucid.py invent workers <study>` prints briefs for three workers: ideation, prior-art research and a
skeptical reviewer. They share the user's budget and permissions. Prior-art and skeptic work from the
candidates only and must not see each other's verdicts. Record each verdict with
`invent assess <cand> --role <role> --verdict ... --rationale ...`. Agreement between workers is not
evidence; the status comes from the searches, sources and checks.

## 9. Invention record

`lucid.py invent record <cand> --markdown` shows the idea, problem, mechanism, closest existing work
with references, differences, status and scope, feasibility evidence, open questions and the next
experiment. `--save` stores it as an `invention` memory with its sources as provenance, so a later
repeat is caught.

## How well does it work?

See `reports/evaluation-heldout.md`. On a small held-out probe set against the bundled collection,
Lucid made no false originality claims but missed some known equivalents, which it left as
"unresolved". The probes and collection were written by the same developer; the numbers measure the
mechanism, not real-world prior-art search.
