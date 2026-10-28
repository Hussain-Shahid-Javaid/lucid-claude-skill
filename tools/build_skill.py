#!/usr/bin/env python3
"""Build the installable skill: vendor the Lucid library into skill/lucid/scripts/lib and zip it.

Output: dist/lucid-skill.zip (folder `lucid/` at the zip root, as Agent Skills hosts expect).
"""
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skill", "lucid")
LIB = os.path.join(SKILL, "scripts", "lib")


def build(zip_out: bool = True) -> str:
    if os.path.exists(LIB):
        shutil.rmtree(LIB)
    shutil.copytree(os.path.join(ROOT, "src", "lucid"), os.path.join(LIB, "lucid"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from validate_skill import validate
    problems = validate(SKILL)
    if problems:
        raise SystemExit("skill validation failed:\n- " + "\n- ".join(problems))
    if not zip_out:
        return SKILL
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "lucid-skill.zip")
    # Reproducible zip: sorted entries and a fixed timestamp, so the same sources give the same bytes.
    entries = []
    for base, dirs, files in os.walk(SKILL):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        entries += [os.path.join(base, f) for f in files if not f.endswith(".pyc")]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(entries):
            info = zipfile.ZipInfo(os.path.relpath(p, os.path.dirname(SKILL)).replace(os.sep, "/"),
                                   date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if os.access(p, os.X_OK) else 0o644) << 16
            with open(p, "rb") as fh:
                z.writestr(info, fh.read())
    return out


if __name__ == "__main__":
    print(build())
