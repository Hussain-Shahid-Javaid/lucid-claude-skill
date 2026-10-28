#!/usr/bin/env python3
"""Scan every tracked or new (not ignored) file for credentials using Lucid's own redaction patterns.

Strings that are clearly marked as fake (containing EXAMPLE) are allowed, because the redaction
tests need realistic-looking fake keys. The generic "password = ..." pattern is not used here: in
source code it matches ordinary variable assignments. Exit 1 if anything else matches.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from lucid.redact import PATTERNS  # noqa: E402

SKIP_EXT = {".png", ".jpg", ".gif", ".mp4", ".zip", ".db", ".woff2", ".ico", ".wav", ".mp3"}


def main() -> int:
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,  # noqa: S607
                           text=True, check=True).stdout.split("\n")
    hits = []
    for rel in filter(None, files):
        if os.path.splitext(rel)[1].lower() in SKIP_EXT or not os.path.isfile(os.path.join(ROOT, rel)):
            continue
        try:
            text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for name, pat in PATTERNS:
            for m in pat.finditer(text):
                if re.search(r"EXAMPLE|example", m.group(0)) or name == "password_assignment":
                    continue
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{rel}:{line}: {name}")
    print(f"scanned {len([f for f in files if f])} files; {len(hits)} possible secrets")
    for h in hits:
        print("  " + h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
