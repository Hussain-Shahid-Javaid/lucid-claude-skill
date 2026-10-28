#!/usr/bin/env python3
"""Documentation consistency check for a release.

* One version everywhere: pyproject, lucid.__version__, SKILL.md metadata, top CHANGELOG entry,
  and a release-notes file for it.
* Every relative link in the Markdown docs points at a file that exists.
* The test count quoted in the README matches what pytest collects.
* Release notes and README pass the plain-language checker (no filler, stiff phrases or openers).
"""
import os
import re
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import lucid  # noqa: E402
from lucid import plain  # noqa: E402

DOCS = ["README.md", "CHANGELOG.md", "skill/lucid/SKILL.md"]


def main() -> int:
    problems = []
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        ver = tomllib.load(fh)["project"]["version"]
    skill = open(os.path.join(ROOT, "skill/lucid/SKILL.md"), encoding="utf-8").read()
    m = re.search(r'version:\s*"([^"]+)"', skill)
    top = re.search(r"^## \[([^\]]+)\]", open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read(), re.M)
    found = {"pyproject": ver, "lucid.__version__": lucid.__version__, "SKILL.md": m and m.group(1),
             "CHANGELOG top": top and top.group(1)}
    if len(set(found.values())) != 1:
        problems.append(f"versions disagree: {found}")
    notes = os.path.join(ROOT, "docs", "releases", f"v{ver}.md")
    if not os.path.exists(notes):
        problems.append(f"missing release notes {os.path.relpath(notes, ROOT)}")
    files = DOCS + [os.path.join("docs", f) for f in os.listdir(os.path.join(ROOT, "docs")) if f.endswith(".md")]
    files += [os.path.join("docs", "releases", f) for f in os.listdir(os.path.join(ROOT, "docs", "releases"))]
    files += [os.path.join("skill/lucid/references", f) for f in os.listdir(os.path.join(ROOT, "skill/lucid/references"))]
    links = 0
    for rel in files:
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:src|href)="([^"]+)"', text):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            links += 1
            path = os.path.normpath(os.path.join(ROOT, os.path.dirname(rel), target.split("#")[0]))
            if not os.path.exists(path):
                problems.append(f"{rel}: broken link {target}")
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "not live"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    n = len([ln for ln in out.splitlines() if "::" in ln]) or sum(
        int(m.group(1)) for m in re.finditer(r"^tests/\S+\.py: (\d+)$", out, re.M))
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    quoted = {int(x) for x in re.findall(r"(\d+) tests", readme)}
    if quoted != {n}:
        problems.append(f"README quotes {sorted(quoted)} tests; pytest collects {n}")
    for rel in ("README.md", os.path.relpath(notes, ROOT)):
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue
        rep = plain.check(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        bad = [f for f in rep.findings if f.kind in ("wordy", "stiff", "opener", "closer", "jargon")]
        for f in bad:
            problems.append(f"{rel}: [{f.kind}] {f.text}")
    print(f"version {ver}; {len(files)} docs, {links} relative links checked; pytest collects {n} tests")
    for p in problems:
        print("  problem: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
