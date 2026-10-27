#!/usr/bin/env python3
"""Validate a skill folder against the Agent Skills spec (agentskills.io/specification, checked 2026-10-27).

Checks: SKILL.md present; YAML frontmatter with only the standard fields (name, description, license,
compatibility, metadata, allowed-tools) so the skill also uploads to claude.ai; name rules; length limits;
body under 500 lines; referenced files exist.
"""
import os
import re
import sys

STANDARD = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = text.index("\n---", 4)
    raw, body = text[4:end], text[end + 4:]
    try:
        import yaml  # type: ignore
        return yaml.safe_load(raw), body
    except ImportError:  # minimal fallback parser for flat keys
        out, cur = {}, None
        for line in raw.splitlines():
            if line.startswith("  ") and cur:
                k, _, v = line.strip().partition(":")
                out.setdefault(cur, {})[k] = v.strip().strip('"')
            else:
                k, _, v = line.partition(":")
                cur = k.strip()
                out[cur] = v.strip() or {}
        return out, body


def validate(folder: str) -> list[str]:
    p = []
    path = os.path.join(folder, "SKILL.md")
    if not os.path.isfile(path):
        return ["SKILL.md missing"]
    fm, body = _frontmatter(open(path, encoding="utf-8").read())
    extra = set(fm) - STANDARD
    if extra:
        p.append(f"non-standard frontmatter fields (rejected by claude.ai upload): {sorted(extra)}")
    name = str(fm.get("name", ""))
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
        p.append("name must be 1-64 chars of lowercase letters, digits and single hyphens")
    if name != os.path.basename(os.path.normpath(folder)):
        p.append("name must match the folder name")
    d = str(fm.get("description", ""))
    if not 1 <= len(d) <= 1024:
        p.append("description must be 1-1024 characters")
    if "compatibility" in fm and not 1 <= len(str(fm["compatibility"])) <= 500:
        p.append("compatibility must be 1-500 characters")
    if "metadata" in fm and not all(isinstance(v, str) for v in (fm["metadata"] or {}).values()):
        p.append("metadata values must be strings")
    if len(body.splitlines()) > 500:
        p.append("SKILL.md body should stay under 500 lines")
    for ref in re.findall(r"`(references/[\w./-]+)`", body):
        if not os.path.exists(os.path.join(folder, ref)):
            p.append(f"referenced file missing: {ref}")
    return p


if __name__ == "__main__":
    probs = validate(sys.argv[1] if len(sys.argv) > 1 else "skill/lucid")
    print("\n".join(probs) or "ok")
    sys.exit(1 if probs else 0)
