"""Harness adapters. Adding a harness = one Source here + one Integration + fixtures."""
from chainlog.sources.claude import ClaudeSource
from chainlog.sources.codex import CodexSource


def all_sources():
    return [ClaudeSource(), CodexSource()]


def by_name(name):
    for s in all_sources():
        if s.name == name:
            return s
    raise KeyError(name)
