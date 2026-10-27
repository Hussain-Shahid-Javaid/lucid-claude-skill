# Verification, self-monitoring and correction

Record what you are relying on — these are explicit notes, not hidden-thought logs:
```bash
lucid.py monitor note assumption "Input CSV is UTF-8" --task <id>
lucid.py monitor note claim "The API returns at most 100 rows" --task <id>
lucid.py monitor predict "All tests will pass after this patch" 0.7 --task <id>   # BEFORE running them
lucid.py monitor outcome <pred_id> true|false
lucid.py monitor calibration
```
Check types: executable tests for code; recomputation for numbers; opened sources for facts; explicit
constraints for "done". Evaluate claims on evidence, not on who asserted them, how confident they
sound, how insistently they are repeated, or how many agents agree. Apply the same standard to
competing views. Avoid automatic agreement, automatic disagreement and false balance.

Correction loop limits: ≤3 iterations, stop when a round produces no new evidence, never edit tests or
answer keys to pass, never report a check you did not run. Keep the original if a "fix" breaks a
passing check.
