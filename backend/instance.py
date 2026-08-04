"""This installation's own name, decided once and kept for ever.

Why a box needs a name of its own
---------------------------------
A licence binds to an INSTANCE, and until now the instance was the hostname the
buyer's browser happened to be using. That works for entrystation.com and for
anyone self-hosting on a real domain, and it fails completely for the case most
people start with: `localhost`.

It fails twice over. `localhost` is not unique — every install in the world
answers to it, so a licence bound to it is bound to all of them. And it is not
reachable, so the store cannot call back to prove the claim, which is the error
that sent people to type a key by hand:

    localhost is not a routable host, so there is nowhere to check the claim

So the box names ITSELF, once, at first boot, and keeps that name in its own
database. A domain can change, a container can move, a reverse proxy can put
three names on one machine — the id underneath does not move.

What the id is
--------------
A bearer credential, and treated as one. There is nothing for the store to call
back to on a laptop behind NAT, so possession of the id IS the proof: whoever
presents it gets the licence bound to it. That is only safe because it carries
256 bits of randomness and is never published — it is not derived from the
hostname, the MAC, the install path or anything else guessable.

It is not a secret from the OPERATOR, though. They have to hand it to the store
when they buy, so it is shown on the Module Store page with a copy button — and
the Buy link carries it automatically, so in the ordinary case nobody types it.

The `es-` prefix is what the store already uses to tell a generated id from a
domain, so `normalise_instance` leaves it alone rather than stripping a port off
it.
"""
import secrets

_cached: str | None = None


def _read() -> str | None:
    import db
    with db.connect() as conn:
        row = conn.execute("SELECT instance_id FROM admin_settings WHERE id = 1").fetchone()
    return (row["instance_id"] if row else None) or None


def ident() -> str:
    """This installation's id, generating it on first call.

    Written with ON CONFLICT DO NOTHING and then read back, so two workers
    starting at once cannot end up with two different ids for one box — the
    loser of the race adopts the winner's id rather than overwriting it. A
    licence bound to an id that changed the next morning would be a licence lost.
    """
    global _cached
    if _cached:
        return _cached
    have = _read()
    if have:
        _cached = have
        return have

    import db
    mine = "es-" + secrets.token_hex(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO admin_settings (id, instance_id, updated_at) VALUES (1, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET instance_id = COALESCE(admin_settings.instance_id, "
            "EXCLUDED.instance_id), updated_at = now()",
            (mine,))
        conn.commit()
    _cached = _read() or mine
    return _cached


def short() -> str:
    """Enough of it to recognise in a support conversation, and useless on its own."""
    i = ident()
    return f"{i[:11]}…{i[-4:]}" if len(i) > 20 else i
