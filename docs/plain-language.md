# Plain-language replies

Lucid asks the assistant to write like a knowledgeable colleague: answer first, familiar words, direct
sentences, brief answers to simple questions, and structure only when it helps. Facts, numbers,
caveats, code and technical distinctions stay exactly as they are. An explicit style request from the
user wins.

The rules live in the skill (`skill/lucid/SKILL.md`, "How to write every reply"). The host model
applies them as it writes, so they cost no extra model call.

## The checker

`lucid plain check` is a local, deterministic helper (`src/lucid/plain.py`). It does not call a model.

```bash
lucid plain check reply.md                 # findings plus the five-question checklist
echo "..." | lucid plain check --question "Is it on?"
lucid plain check reply.md --fix           # safe word swaps only
lucid plain check reply.md --strict        # exit 1 if anything is flagged (for CI)
```

It flags:

| kind | examples |
|---|---|
| wordy / filler | "in order to", "due to the fact that", "it is important to note that" |
| stiff | "utilize", "prior to", "subsequent to", "initiate", "modifications" |
| noun instead of a verb | "perform an analysis", "initiate an application restart" |
| opener / praise | "Great question!", "Certainly!", "I'd be happy to help" |
| closer / repeated summary | "In summary, ...", "Let me know if you have any questions" |
| jargon | "seamless", "best-in-class", "streamline", "synergy" |
| long sentence | more than 30 words |
| acronym | an all-caps term not spelled out and not on the common list |
| length | more than 150 words for a question of 12 words or fewer |
| structure | headings in an answer under 120 words |

The checklist answers four of the five pre-send questions from these signals:
- main point first
- reader understands
- nothing to cut
- sounds natural

The fifth question, whether the facts and caveats were kept, cannot be checked by the tool. `--fix` reports instead whether any numbers, code or hedge words (not, may, only, unless…) changed. They never should.

`--fix` does not restructure sentences. The spec example shows the limit:

| | |
|---|---|
| original | It is recommended that you initiate an application restart subsequent to saving your modifications. |
| `--fix` | Start an application restart after saving your changes. |
| a person's rewrite | Save your changes, then restart the app. |

## Lucid's own text

`tests/test_plain.py` runs the checker on Lucid's own text and fails if any of it is flagged:
- every error message and CLI output string in `src/lucid`
- the explanatory text in the dashboard

Many error messages were rewritten for v0.2.0. For example:

| before | after |
|---|---|
| "no sharing rule authorises destination 'x'" | "nothing is shared with 'x' yet. Choose what it may see with `lucid share add`…" |
| "approach repeated 2x without new evidence; replan with a materially different approach" | "…has been tried 2 times with nothing new learned. Try something clearly different" |

## Limits

These are heuristics. A clean report does not make text clear, and a flag is a prompt to reread, not a rule. The tool cannot judge accuracy, tone for a specific reader, or whether an answer actually answers the question.
