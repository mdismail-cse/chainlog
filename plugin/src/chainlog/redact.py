"""Redact secrets from notes, titles and previews before they are stored or shown.

Everything here runs on every note, preview and summary excerpt, so it must be linear in the text: no pattern
may backtrack over a long run of ordinary characters. The "is this name a secret?" decision is therefore made
in code (`_is_secret_name`), and the regular expressions only find candidates.
"""
from __future__ import annotations

import re

# --- names that hold secrets ----------------------------------------------------------------------------------
_WORDS = {"password", "passwd", "pwd", "token", "secret", "apikey", "accesskey", "privatekey", "userpass"}
_AFTER = {"key", "id", "access", "value", "string"}  # may follow one: SECRET_ACCESS_KEY, secretKey, token_id
_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")  # the parts of DB_PASSWORD, db.password, dbPassword


def _is_secret_name(name):
    """DB_PASSWORD, AWS_SECRET_ACCESS_KEY, client_secret, X-Api-Key, accessToken: yes.
    max_tokens, token_count, password_reset_url, bypass: no."""
    parts = [p.lower() for p in _PART.findall(name)]
    end = -1
    for i, p in enumerate(parts):
        if p in _WORDS:
            end = max(end, i)
        elif i + 1 < len(parts) and p + parts[i + 1] in _WORDS:  # api + key, access + key, private + key, user + pass
            end = max(end, i + 1)
    return end >= 0 and all(p in _AFTER for p in parts[end + 1:])


_NAME_RUN = r"[A-Za-z0-9_.-]+"
_VALUE = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'|[^\s'\"]+")
# NAME=value, NAME: value, "name": "value". Only the start is matched here; the value is read once the name is known.
_KEY = re.compile(r"(?<![A-Za-z0-9_.-])(" + _NAME_RUN + r")([\"']?[ \t]*[:=][ \t]*)")
# --password hunter2 (a space instead of =)
_FLAG = re.compile(r"(?<![A-Za-z0-9_.-])(--[A-Za-z][A-Za-z0-9_-]*)([ \t]+)")


def _redact_names(text):
    """Secret-named assignments and flags. One left-to-right scan; a non-secret name is skipped without
    reading its value, so `foo=password=x` still finds the second one."""
    out, pos, n = [], 0, 0
    km = fm = None
    km_done = fm_done = False  # no further match exists: never search again (that would be quadratic)
    while pos < len(text):
        if not km_done and (km is None or km.start() < pos):
            km = _KEY.search(text, pos)
            km_done = km is None
        if not fm_done and (fm is None or fm.start() < pos):
            fm = _FLAG.search(text, pos)
            fm_done = fm is None
        m = min((x for x in (km, fm) if x), key=lambda x: x.start(), default=None)
        if m is None:
            break
        is_flag = m is fm and m is not km
        value = _VALUE.match(text, m.end()) if _is_secret_name(m.group(1)) else None  # read a value only for a secret name
        take = (value is not None and "[REDACTED" not in value.group(0)
                and not (is_flag and value.group(0)[0] in "-$`"))  # `--key $(cat file)` is not a literal secret
        if take:
            out.append(text[pos:m.end()] + "[REDACTED:secret]")
            pos = value.end()
            n += 1
        else:
            out.append(text[pos:m.end()])
            pos = m.end()
    out.append(text[pos:])
    return "".join(out), n


def _sub(rx, text, repl):
    """Like rx.subn, but a replacement that returns None leaves the match alone and is not counted."""
    n = 0

    def f(m):
        nonlocal n
        out = repl(m)
        if out is None:
            return m.group(0)
        n += 1
        return out
    return rx.sub(f, text), n


def _mark(kind):
    return lambda m: "[REDACTED:%s]" % kind


def _after_prefix(m):  # keep what came before the value, hide the value; skip if an earlier pattern did it
    return None if "[REDACTED" in m.group(2) else m.group(1) + "[REDACTED:secret]"


# (regex, replacement function), applied in this order. Each is linear; none backtracks over ordinary text.
_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), _mark("pem")),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), _mark("anthropic")),
    (re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}"), _mark("openai")),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), _mark("github")),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), _mark("aws")),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), _mark("slack")),
    (re.compile(r"(?i)(?<=\bbearer\s)[A-Za-z0-9._~+/=-]{16,}"), _mark("bearer")),
    # Basic credentials only in a header ("basic" is also an ordinary English word)
    (re.compile(r"(?i)(\bauthorization[ \t]*[:=][ \t]*basic[ \t]+)([A-Za-z0-9+/=]{8,})"), lambda m: m.group(1) + "[REDACTED:bearer]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), _mark("jwt")),
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"), _mark("url-cred")),
    # the two command lines that take a password as a short flag
    (re.compile(r"(?i)(\bsshpass[ \t]+-p[ \t]*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s'\"]+)"), _after_prefix),
    (re.compile(r"(\bmysql(?:dump|admin)?\b[^\n|;&]{0,200}?\s-p)(?=[^\s'\"-])([^\s'\"]+)"), _after_prefix),
]


def redact(text):
    """Returns (redacted_text, count)."""
    if not text:
        return text, 0
    count = 0
    for rx, repl in _PATTERNS:
        text, n = _sub(rx, text, repl)
        count += n
    text, n = _redact_names(text)
    return text, count + n


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
