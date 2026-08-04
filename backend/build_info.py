"""What this build actually IS, taken from the commit it was built from.

The problem this replaces
-------------------------
`CORE_VERSION` was a constant somebody had to remember to raise. Sixteen
commits shipped once while it sat still, so every instance compared 1.2.0
against 1.2.0 and correctly concluded there was nothing to do. That happened
twice. The failure mode of a step a person has to remember is not that they
disagree with it, so a firmer reminder was never going to be the fix.

Here the version is not a decision. It is the commit: a sha to identify the
build and a commit DATE to order two of them. Push, and the thing that decides
whether an instance is behind has already changed, because it is the push.

Where the stamp comes from
--------------------------
`.git` is not in the image and should not be — it is the repository, not the
program. So the build reads it once and writes `build.json`, and everything
afterwards reads that file. A build made outside git (a tarball, an unpacked
ZIP) has no stamp and says so, rather than inventing one: an instance that
cannot prove what it is running should not claim to be current.

`CORE_VERSION` survives as the human-readable name of a release, for release
notes and for support conversations. It is no longer what decides an update,
which is the whole point: nothing anybody types decides that now.
"""
import json
import os
from pathlib import Path

_STAMP = Path(__file__).parent / "build.json"
_cached = None


def stamp() -> dict:
    """{sha, date, ref} for this build, or {} when it was not built from a repo."""
    global _cached
    if _cached is not None:
        return _cached
    out = {}
    # The environment wins, so a deploy that is not a Docker build can stamp
    # itself without writing into the source tree it just copied.
    for key, env in (("sha", "ENTRYSTATION_BUILD_SHA"),
                     ("date", "ENTRYSTATION_BUILD_DATE"),
                     ("ref", "ENTRYSTATION_BUILD_REF")):
        v = (os.getenv(env) or "").strip()
        if v:
            out[key] = v
    if not out.get("date"):
        try:
            out = {**json.loads(_STAMP.read_text()), **out}
        except Exception:
            pass
    _cached = out
    return out


def describe() -> dict:
    """Everything an update check needs, in one shape both ends understand."""
    import modules as module_system
    s = stamp()
    return {
        "version": module_system.CORE_VERSION,   # the name of the release
        "sha": (s.get("sha") or "")[:12],
        "date": s.get("date") or "",             # ISO 8601, and the thing compared
        "ref": s.get("ref") or "",
    }


def newer_than(mine: dict, theirs: dict) -> bool:
    """Is `theirs` a later build than `mine`?

    Commit dates, compared as strings, because ISO 8601 in UTC sorts correctly
    and parsing gains nothing. Equal shas are the same build whatever the dates
    say — a rebuild of one commit is not an update, and treating it as one would
    hand every instance a permanent notification it could never clear.

    Unknown either side is FALSE. An instance that cannot tell should say
    nothing, not cry update once an hour for ever.
    """
    a, b = (mine or {}), (theirs or {})
    if not b.get("date") or not a.get("date"):
        return False
    if a.get("sha") and a["sha"] == b.get("sha"):
        return False
    return b["date"] > a["date"]
