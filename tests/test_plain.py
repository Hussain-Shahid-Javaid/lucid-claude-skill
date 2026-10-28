"""Plain-language checker, and an audit of Lucid's own user-facing messages."""
import ast
import glob
import os
import re

from lucid import plain

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "lucid")


def kinds(text, **kw):
    return {f.kind for f in plain.check(text, **kw).findings}


def test_spec_example_is_flagged_and_safely_simplified():
    t = "It is recommended that you initiate an application restart subsequent to saving your modifications."
    rep = plain.check(t)
    texts = {f.text.lower() for f in rep.findings}
    assert {"it is recommended that you", "subsequent to", "initiate", "modifications"} <= texts
    assert rep.checklist["sounds_natural"]["pass"] is False
    new, info = plain.fix(t)
    assert new == "Start an application restart after saving your changes."
    assert all(info["preserved"].values())
    assert plain.check("Save your changes, then restart the app.").ok


def test_openers_closers_praise_and_jargon():
    assert "opener" in kinds("Great question! The file is in /tmp.")
    assert "opener" in kinds("Certainly! Here it is.")
    assert "closer" in kinds("Run it.\n\nIn summary, run it.")
    assert "jargon" in kinds("This seamless, best-in-class tool will streamline your work.")
    assert "wordy" in kinds("It is imperative to note that backups run nightly.")


def test_long_sentences_acronyms_headings_and_length():
    long = " ".join(["word"] * 35) + "."
    assert "long_sentence" in kinds(long)
    assert "acronym" in kinds("Check the RPO before the drill.")
    assert "acronym" not in kinds("Check the recovery point objective (RPO) before the drill. The RPO is 5 minutes.")
    assert "structure" in kinds("# Answer\n\nYes.")
    assert "length" in kinds("Is it on? " + "It is on. " * 60, question="Is it on?")


def test_code_numbers_and_hedges_are_never_changed():
    t = ("In order to utilize it, run `prior to=1 utilize()`; it may not work on 3.12.\n\n"
         "```\nin order to utilize\n```\n")
    new, info = plain.fix(t)
    assert "`prior to=1 utilize()`" in new and "```\nin order to utilize\n```" in new
    assert new.startswith("To use it")
    assert "may not" in new and "3.12" in new
    assert all(info["preserved"].values())
    # flags inside code are ignored
    assert plain.check("Run `utilize --prior-to` now.").ok


def test_tables_and_lists_are_not_long_sentences():
    table = "| a | b |\n|---|---|\n" + "".join(f"| row {i} | value {i} |\n" for i in range(20))
    bullets = "Do these:\n" + "".join(f"- step number {i} here\n" for i in range(15))
    assert "long_sentence" not in kinds(table + "\n" + bullets)


def test_plain_text_passes():
    assert plain.check("The backup ran at 02:00 and took 4 minutes. Nothing failed.").ok


def _user_facing_strings():
    """Error messages and CLI output text written in Lucid's own source."""
    out = []
    for f in glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True):
        tree = ast.parse(open(f, encoding="utf-8").read())
        for n in ast.walk(tree):
            target = None
            if isinstance(n, ast.Raise) and n.exc is not None:
                target = n.exc
            elif isinstance(n, ast.Call) and getattr(n.func, "id", None) in ("print", "_out"):
                target = n
            if target is None:
                continue
            for s in ast.walk(target):
                text = None
                if isinstance(s, ast.Constant) and isinstance(s.value, str):
                    text = s.value
                elif isinstance(s, ast.JoinedStr):
                    text = "".join(v.value if isinstance(v, ast.Constant) else "X" for v in s.values)
                if text and len(text.split()) >= 3:
                    out.append((os.path.relpath(f, SRC), n.lineno, text))
    return out


def test_lucids_own_messages_read_plainly():
    msgs = _user_facing_strings()
    assert len(msgs) > 150
    bad = []
    for f, line, text in msgs:
        found = [x for x in plain.check(text).findings if x.kind not in ("acronym",)]
        if found:
            bad.append(f"{f}:{line}: {text[:80]!r} {[x.text for x in found]}")
    assert not bad, "\n".join(bad)


def test_dashboard_text_reads_plainly():
    root = os.path.join(os.path.dirname(__file__), "..", "dashboard", "src")
    bad = []
    for f in glob.glob(os.path.join(root, "*.tsx")):
        src = open(f, encoding="utf-8").read()
        for m in re.finditer(r">([^<>{}]{20,})<|\"([A-Z][^\"]{20,})\"", src):
            text = m.group(1) or m.group(2)
            found = [x for x in plain.check(text).findings if x.kind not in ("acronym",)]
            if found:
                bad.append(f"{os.path.basename(f)}: {text[:80]!r} {[x.text for x in found]}")
    assert not bad, "\n".join(bad)
