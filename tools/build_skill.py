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
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(SKILL):
            for f in files:
                if f.endswith(".pyc"):
                    continue
                p = os.path.join(base, f)
                z.write(p, os.path.relpath(p, os.path.dirname(SKILL)))
    return out


if __name__ == "__main__":
    print(build())
