"""
Comparing versions, in one place.

This existed as `a != b` and that is not the same question. `dist/` held
tradelocker-1.0.0 while 1.1.0 was installed, so "different" read as "newer" and
the store offered every user a downgrade, labelled as an update.

Semantic versions compare numerically per part — 1.10.0 is above 1.9.0, which
string comparison gets backwards — and a pre-release (1.2.0-beta.1) ranks BELOW
the release it leads to, which is the rule that stops a beta looking like an
upgrade from the stable build.
"""
from __future__ import annotations

import re

_PART = re.compile(r"^(\d+)(.*)$")


def parse(v) -> tuple:
    """(1, 2, 3, pre) — missing parts are zero, so "1.2" sorts with "1.2.0"."""
    s = str(v or "0").strip().lstrip("vV")
    core, _, pre = s.partition("-")
    core = core.partition("+")[0]                 # build metadata is not ordering
    nums = []
    for bit in core.split(".")[:4]:
        m = _PART.match(bit.strip())
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3]), pre.strip()


def _key(v):
    core, pre = parse(v)
    # No pre-release sorts ABOVE any pre-release of the same core, so
    # 1.2.0 > 1.2.0-rc.1. The 1/0 flag is what encodes that.
    return core + (1 if not pre else 0,) + (_pre_key(pre),)


def _pre_key(pre: str) -> tuple:
    out = []
    for bit in (pre or "").split("."):
        m = _PART.match(bit)
        out.append((0, int(m.group(1)), "") if m and not m.group(2) else (1, 0, bit))
    return tuple(out)


def newer(candidate, installed) -> bool:
    """Is `candidate` strictly newer than what is installed?

    Strictly: an equal version is not an update, and an older one is a
    downgrade — neither should be offered as one."""
    if not candidate or not installed:
        return False
    try:
        return _key(candidate) > _key(installed)
    except Exception:
        return False


def compare(a, b) -> int:
    ka, kb = _key(a), _key(b)
    return (ka > kb) - (ka < kb)
