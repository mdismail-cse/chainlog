"""Redact secrets from notes, titles and previews before they are stored or shown."""
from __future__ import annotations

import re

# A name that holds a secret: DB_PASSWORD, AWS_SECRET_ACCESS_KEY, client_secret, X-Api-Key, "password".
# Every part is joined by _ . or -, so max_tokens and bypass are not secrets but my_api_token is.
_SECRET_NAME = (r"(?:[A-Za-z0-9]+[_.-])*"
                r"(?:password|passwd|pwd|user[_-]?pass|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)"
                r"(?:[_.-][A-Za-z0-9]+)*")
_VALUE = r"(\"[^\"]*\"|'[^']*'|[^\s'\"]+)"  # a quoted string (may hold spaces) or one bare word
_NOT_DONE = r"(?!\[REDACTED)"  # already redacted by an earlier pattern: do not count it twice

# (kind, regex, replacement). Patterns with groups keep the name and the separator.
_PATTERNS = [
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
     "[REDACTED:pem]"),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), "[REDACTED:anthropic]"),
    ("openai", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}"), "[REDACTED:openai]"),
    ("github", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
     "[REDACTED:github]"),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws]"),
    ("slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack]"),
    ("bearer", re.compile(r"(?i)(?:(?<=\bbearer\s)|(?<=\bbasic\s))[A-Za-z0-9._~+/=-]{16,}"), "[REDACTED:bearer]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    ("url-cred", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"), "[REDACTED:url-cred]"),
    # NAME=value, NAME: value, "name": "value"
    ("kv", re.compile(r"(?i)(?<![A-Za-z0-9])(" + _SECRET_NAME + r")([\"']?\s*[:=]\s*)" + _NOT_DONE + _VALUE),
     r"\g<1>\g<2>[REDACTED:secret]"),
    # --password hunter2, --api-key abc (a space instead of =)
    ("flag", re.compile(r"(?i)(?<![A-Za-z0-9-])(--?(?:[a-z0-9]+-)*(?:password|passwd|token|secret|api-?key)\s+)(?!-)"
                        + _NOT_DONE + _VALUE),
     r"\g<1>[REDACTED:secret]"),
    # the two command lines that take a password as a short flag
    ("sshpass", re.compile(r"(?i)(\bsshpass\s+-p\s*)" + _NOT_DONE + _VALUE), r"\g<1>[REDACTED:secret]"),
    ("mysql", re.compile(r"(\bmysql(?:dump|admin)?\b[^\n|;&]*?\s-p)(?=[^\s'\"-])([^\s'\"]+)"),
     r"\g<1>[REDACTED:secret]"),
]


def redact(text):
    """Returns (redacted_text, count)."""
    if not text:
        return text, 0
    count = 0
    for _kind, rx, repl in _PATTERNS:
        text, n = rx.subn(repl, text)
        count += n
    return text, count


_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ‎‏‪-‮⁦-⁩]")


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
