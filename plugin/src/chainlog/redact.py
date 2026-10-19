"""Redact secrets from notes, titles and previews before they are stored or shown."""
from __future__ import annotations

import re

_PATTERNS = [
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}")),
    ("openai", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}")),
    ("github", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bearer", re.compile(r"(?i)(?<=\bbearer\s)[A-Za-z0-9._~+/=-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url-cred", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("kv", re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key)\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)")),
]


def redact(text):
    """Returns (redacted_text, count)."""
    if not text:
        return text, 0
    count = 0
    for kind, rx in _PATTERNS:
        if kind == "kv":
            def kv(m):
                return "%s%s[REDACTED:secret]" % (m.group(1), m.group(2))
            text, n = rx.subn(kv, text)
        else:
            text, n = rx.subn("[REDACTED:%s]" % kind, text)
        count += n
    return text, count


_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def clean(text, limit=160):
    """Redact, drop control characters (terminal escapes), collapse whitespace and cut to `limit`
    characters (for previews)."""
    if not text:
        return ""
    text = " ".join(_CTRL.sub(" ", str(text)).split())
    text, _ = redact(text)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text
