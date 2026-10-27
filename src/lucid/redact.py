"""Secret detection and redaction.

Applied before anything is written to memories, archives, logs, handoffs or
API responses. Detection is pattern-based: it reduces risk, it does not
guarantee that every secret format is caught.
"""
from __future__ import annotations

import logging
import re

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{20,}=*")),
    ("password_assignment", re.compile(r"(?i)\b(pass(?:word|wd)?|secret|api[_-]?key|token)\b(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3")),
]


def find_secrets(text: str) -> list[str]:
    if not text:
        return []
    return [name for name, pat in PATTERNS if pat.search(text)]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for name, pat in PATTERNS:
        if name == "password_assignment":
            out = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:{name}]", out)
        else:
            out = pat.sub(f"[REDACTED:{name}]", out)
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def install_log_redaction(logger: logging.Logger | None = None) -> None:
    lg = logger or logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in lg.filters):
        lg.addFilter(RedactingFilter())
    for h in lg.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
