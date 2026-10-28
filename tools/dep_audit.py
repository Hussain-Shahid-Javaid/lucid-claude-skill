#!/usr/bin/env python3
"""Dependency vulnerability audit: Python runtime extras with pip-audit, dashboard packages with npm audit.

Exit 0 when both audits ran and found nothing; 1 when a vulnerability was reported; 2 when an audit
could not run (that is a blocker, not a pass).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        proj = tomllib.load(fh)["project"]
    reqs = sorted(set(proj.get("dependencies", [])) |
                  {r for k, v in proj.get("optional-dependencies", {}).items() if k != "dev" for r in v})
    status = 0
    pa = shutil.which("pip-audit")
    if not pa:
        print("pip-audit is not installed: Python dependencies were NOT audited")
        status = 2
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(reqs) + "\n")
        p = subprocess.run([pa, "-r", fh.name, "--progress-spinner", "off"], capture_output=True, text=True)
        os.unlink(fh.name)
        print(f"pip-audit on {len(reqs)} runtime requirements: exit {p.returncode}\n{(p.stdout + p.stderr)[-2000:]}")
        if p.returncode != 0:
            status = max(status, 1 if "Found" in p.stdout else 2)
    npm = shutil.which("npm")
    if not npm:
        print("npm is not installed: dashboard dependencies were NOT audited")
        status = 2
    else:
        p = subprocess.run([npm, "audit", "--omit=dev"], cwd=os.path.join(ROOT, "dashboard"), capture_output=True,
                           text=True)
        print(f"npm audit --omit=dev (dashboard): exit {p.returncode}\n{(p.stdout + p.stderr)[-1500:]}")
        if p.returncode != 0:
            status = max(status, 1 if "vulnerabilit" in p.stdout else 2)
    return status


if __name__ == "__main__":
    sys.exit(main())
