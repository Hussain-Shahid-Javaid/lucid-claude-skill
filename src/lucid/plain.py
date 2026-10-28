"""Plain-language check for text Lucid (or the host) is about to send.

Deterministic and local: no model call, no network. It flags stiff or wordy phrasing,
canned openers and closers, praise, corporate jargon, long sentences and unexplained
acronyms, and answers the readability checklist where a signal exists.

`fix()` only makes safe phrase swaps outside code. It never touches code, numbers,
negations or hedges, and it reports anything it could not keep identical. Rewriting
a sentence's structure is left to the writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# (pattern, replacement or None for "flag only", why). Replacement "" deletes the phrase.
WORDY: list[tuple[str, str | None, str]] = [
    (r"it is imperative to note that\s*", "", "filler"),
    (r"it is important to note that\s*", "", "filler"),
    (r"it should be noted that\s*", "", "filler"),
    (r"please be advised that\s*", "", "filler"),
    (r"please note that\s*", "", "filler"),
    (r"it is recommended that you\s+", "", "say it directly"),
    (r"you may wish to\s+", "", "say it directly"),
    (r"in order to\b", "to", "wordy"),
    (r"due to the fact that\b", "because", "wordy"),
    (r"in the event that\b", "if", "wordy"),
    (r"at this point in time\b", "now", "wordy"),
    (r"for the purpose of\b", "for", "wordy"),
    (r"with regard to\b", "about", "wordy"),
    (r"in regard to\b", "about", "wordy"),
    (r"a large number of\b", "many", "wordy"),
    (r"is able to\b", "can", "wordy"),
    (r"are able to\b", "can", "wordy"),
    (r"prior to\b", "before", "stiff"),
    (r"subsequent to\b", "after", "stiff"),
    (r"utili[sz]e\b", "use", "stiff"),
    (r"utili[sz]es\b", "uses", "stiff"),
    (r"utili[sz]ed\b", "used", "stiff"),
    (r"utili[sz]ing\b", "using", "stiff"),
    (r"leverage\b", "use", "jargon"),
    (r"initiate\b", "start", "stiff"),
    (r"initiates\b", "starts", "stiff"),
    (r"commence\b", "start", "stiff"),
    (r"terminate\b", "stop", "stiff"),
    (r"facilitate\b", "help", "stiff"),
    (r"ascertain\b", "find out", "stiff"),
    (r"endeavou?r to\b", "try to", "stiff"),
    (r"modifications\b", "changes", "stiff"),
    (r"approximately\b", "about", "stiff"),
    (r"numerous\b", "many", "stiff"),
    (r"in addition to\b", None, "often 'besides' or 'and'"),
    (r"(?:perform|conduct|carry out|initiate|execute) an? (\w+)", None, "use the verb instead of a noun"),
]
OPENERS = [r"great question", r"what a great", r"certainly[!,.]", r"absolutely[!,.]", r"sure thing",
           r"i'?d be (?:happy|glad) to help", r"happy to help", r"as an ai\b", r"i hope this (?:helps|email finds)",
           r"thanks for (?:asking|reaching out)", r"excellent question", r"you'?re absolutely right"]
CLOSERS = [r"^in (?:summary|conclusion)\b", r"^to (?:summari[sz]e|sum up)\b", r"^overall,", r"let me know if you have any (?:other )?questions",
           r"i hope this helps", r"feel free to (?:ask|reach out)"]
JARGON = ["synergy", "best-in-class", "seamless", "seamlessly", "cutting-edge", "game-changer", "game changer",
          "paradigm", "holistic", "delve", "streamline", "robust solution", "empower", "unlock the power",
          "world-class", "next-generation", "revolutionize", "revolutionise"]
KNOWN_ACRONYMS = {"API", "URL", "CLI", "MCP", "JSON", "HTML", "CSS", "SQL", "AI", "UI", "OK", "PDF", "ID", "IDS", "USB",
                  "HTTP", "HTTPS", "AWS", "S3", "CSV", "UTC", "FAQ", "PR", "GPU", "CPU", "RAM", "MB", "GB", "KB",
                  "TB", "USD", "ISO", "SSH", "TLS", "DNS", "IP", "OS", "LLM", "MP4", "PNG", "SVG", "JS", "TS", "NO", "MIT"}
LONG_SENTENCE = 30
HEDGES = r"\b(?:not|no|never|may|might|could|likely|unlikely|probably|unless|only|except|without|cannot|can't|won't|don't|doesn't|isn't|aren't)\b"


@dataclass
class Finding:
    kind: str
    text: str
    suggestion: str | None = None
    why: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "suggestion": self.suggestion, "why": self.why}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checklist: dict[str, dict] = field(default_factory=dict)
    words: int = 0
    sentences: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {"ok": self.ok, "words": self.words, "sentences": self.sentences,
                "findings": [f.as_dict() for f in self.findings], "checklist": self.checklist}


_CODE = re.compile(r"```.*?```|`[^`\n]+`", re.S)


def _prose(text: str) -> str:
    """Text with code blocks and inline code blanked out (same length, so offsets still line up)."""
    return _CODE.sub(lambda m: " " * len(m.group(0)), text)


def _sentences(prose: str) -> list[str]:
    lines = [ln for ln in prose.splitlines() if not ln.lstrip().startswith("|")]  # table rows are not sentences
    text = "\n".join(lines)
    parts = re.split(r"(?<=[.!?:])\s+|\n{2,}|\n(?=\s*(?:#|\d+\.\s|[-*•]\s))", text)
    out = []
    for p in parts:
        p = re.sub(r"^\s*(?:#+|\d+\.|[-*•])\s+", "", p).strip()
        if p and re.search(r"[A-Za-z]", p):
            out.append(p)
    return out


def check(text: str, *, question: str | None = None) -> Report:
    """Readability check. Signals are heuristics; they point at places to reread, not rules."""
    prose = _prose(text)
    low = prose.lower()
    rep = Report()
    sents = _sentences(prose)
    rep.sentences = len(sents)
    rep.words = len(re.findall(r"[A-Za-z][\w'’-]*", prose))

    for pat, repl, why in WORDY:
        for m in re.finditer(r"\b" + pat, low):
            original = prose[m.start():m.end()].strip()
            sug = None if repl is None else (repl or "(cut it)")
            rep.findings.append(Finding("wordy" if why in ("wordy", "filler", "say it directly") else "stiff",
                                        original, sug, why))
    first = sents[0].lower() if sents else ""
    for pat in OPENERS:
        if re.search(pat, first) or (pat.startswith("you") and re.search(pat, low)):
            rep.findings.append(Finding("opener", sents[0][:80], "start with the answer", "canned opener or praise"))
            break
    paras = [p.strip() for p in re.split(r"\n{2,}", prose) if p.strip()]
    if paras:
        last = paras[-1].lower()
        for pat in CLOSERS:
            if re.search(pat, last, re.M):
                rep.findings.append(Finding("closer", paras[-1][:80], "drop it if it repeats the answer",
                                            "canned closer or repeated summary"))
                break
    for j in JARGON:
        if re.search(r"\b" + re.escape(j) + r"\b", low):
            rep.findings.append(Finding("jargon", j, "say what it does", "corporate language"))
    for s in sents:
        n = len(s.split())
        if n > LONG_SENTENCE:
            rep.findings.append(Finding("long_sentence", s[:80] + ("…" if len(s) > 80 else ""),
                                        "split it", f"{n} words"))
    defined = set(re.findall(r"\(([A-Z][A-Z0-9]{1,5})\)", prose))
    seen = set()
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,5})s?\b", prose):
        a = m.group(1)
        if a in KNOWN_ACRONYMS or a in defined or a in seen or not re.search(r"[A-Z].*[A-Z]", a):
            continue
        seen.add(a)
        rep.findings.append(Finding("acronym", a, f"spell out {a} the first time", "may be unfamiliar"))
    if question is not None and len(question.split()) <= 12 and rep.words > 150:
        rep.findings.append(Finding("length", f"{rep.words} words", "answer a short question briefly",
                                    "long answer to a short question"))
    if rep.words < 120 and re.search(r"^\s*#{1,6}\s", text, re.M):
        rep.findings.append(Finding("structure", "headings", "drop headings in a short answer",
                                    "headings in a short answer"))

    kinds = {f.kind for f in rep.findings}
    rep.checklist = {
        "main_point_first": {"pass": "opener" not in kinds, "signal": "first sentence is not a canned opener"},
        "reader_understands": {"pass": not ({"jargon", "acronym"} & kinds), "signal": "no jargon or unexplained acronyms"},
        "nothing_to_cut": {"pass": not ({"wordy", "closer", "length"} & kinds),
                           "signal": "no filler, wordy phrases or repeated summary"},
        "sounds_natural": {"pass": not ({"stiff", "long_sentence"} & kinds),
                           "signal": f"no stiff phrases or sentences over {LONG_SENTENCE} words"},
        "facts_and_caveats_kept": {"pass": None, "signal": "not checkable here: compare with your source. "
                                                         "fix() reports any change to numbers, code or hedges."},
    }
    return rep


def _case_like(src: str, repl: str) -> str:
    if src[:1].isupper() and repl:
        return repl[0].upper() + repl[1:]
    return repl


def fix(text: str) -> tuple[str, dict]:
    """Apply only the safe phrase swaps, outside code. Returns (new_text, info)."""
    out, last, changes = [], 0, []
    for m in _CODE.finditer(text):
        out.append(_fix_prose(text[last:m.start()], changes))
        out.append(m.group(0))
        last = m.end()
    out.append(_fix_prose(text[last:], changes))
    new = "".join(out)
    kept = preserved(text, new)
    return new, {"changes": changes, "preserved": kept}


def _fix_prose(s: str, changes: list) -> str:
    for pat, repl, _ in WORDY:
        if repl is None:
            continue

        def sub(m, repl=repl):
            r = _case_like(m.group(0), repl)
            changes.append({"from": m.group(0).strip(), "to": r or "(removed)"})
            return r
        s = re.sub(r"\b" + pat, sub, s, flags=re.I)
    # Recapitalise a sentence start left lower-case by a deletion.
    s = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
    s = re.sub(r"  +", " ", s)
    return s


def preserved(before: str, after: str) -> dict:
    """What must not change when wording is simplified."""
    def nums(t):
        return sorted(re.findall(r"\d+(?:[.,]\d+)*", _prose(t)))

    def code(t):
        return _CODE.findall(t)

    def hedges(t):
        return sorted(w.lower() for w in re.findall(HEDGES, _prose(t), re.I))
    return {"numbers": nums(before) == nums(after), "code": code(before) == code(after),
            "hedges_and_negations": hedges(before) == hedges(after)}


def render(rep: Report) -> str:
    if rep.ok:
        return f"reads plainly ({rep.words} words, {rep.sentences} sentences)"
    lines = [f"{len(rep.findings)} things to look at ({rep.words} words):"]
    for f in rep.findings:
        sug = f" -> {f.suggestion}" if f.suggestion else ""
        lines.append(f"  [{f.kind}] {f.text}{sug}" + (f"  ({f.why})" if f.why else ""))
    for name, c in rep.checklist.items():
        mark = "?" if c["pass"] is None else ("ok" if c["pass"] else "no")
        lines.append(f"  {mark:>2}  {name.replace('_', ' ')}")
    return "\n".join(lines)
