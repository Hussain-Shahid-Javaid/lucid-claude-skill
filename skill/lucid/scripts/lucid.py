#!/usr/bin/env python3
"""Skill entry point: runs the bundled Lucid library (scripts/lib) or an installed `lucid` package."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "lib")
if os.path.isdir(os.path.join(LIB, "lucid")):
    sys.path.insert(0, LIB)

try:
    from lucid.cli import main
except ImportError:  # pragma: no cover
    sys.stderr.write("Lucid library not found. Rebuild the skill (python3 tools/build_skill.py) "
                     "or `pip install lucid-skill`.\n")
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
