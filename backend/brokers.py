"""
The broker seam — how core trades without knowing which broker it is trading with.

Core has exactly two things to say about brokers, and this file holds both.

1. The EXCEPTIONS. A session that has expired and an account that refuses us are
   facts about trading, not facts about Exness, so core names them here and every
   broker raises these (or a subclass). That is what lets `main.py` turn an
   expired session into a clean 401 without importing the broker that raised it.

2. The LOOKUP. A broker registers itself as the provider `broker:<id>`, and core
   asks for one by name. Core never imports a broker module; if none is
   installed, `adapter()` says so in a sentence a person can act on rather than
   raising ImportError at some unrelated depth.

A broker provider is any object exposing:

    Trader(account, user_id)      -> the adapter (broker_base.TraderBase-shaped)
    bind_session(user_id)         -> token, or None            [optional]
    reset_session(token)                                       [optional]
    has_connection(user_id)       -> bool                      [optional]
    login_accounts(user_id)       -> [account_ref, ...]        [optional]
    owns_account(user_id, account)-> bool                      [optional]
    accounts_view(user_id)        -> dict                      [optional]
    is_refusal(exc)               -> bool                      [optional]

Plus one flag: `owns_any_account = True` says this broker cannot tell you an
account is NOT its own, so it is only ever consulted as the fallback when
resolving which broker an account belongs to.

Only `Trader` is required. Everything else has a sensible default here, so a
broker as simple as an API key implements one function and works.
"""
from __future__ import annotations

import registry


class SessionExpired(RuntimeError):
    """The broker session is no longer valid — the user must reconnect."""


class AccountRefused(RuntimeError):
    """The broker accepted us but refused THIS account (archived, wrong server,
    read-only). A different account of the same user may work perfectly well."""


class NoBroker(RuntimeError):
    """No broker is installed, or not the one this account belongs to."""


class NoAccount(RuntimeError):
    """A broker is installed but the user has not connected or activated an
    account on it yet — the ordinary state of a brand-new install, and not an
    error in the sense that a 500 implies."""


def providers() -> dict:
    """{broker_id: provider} for every installed broker."""
    out = {}
    for name in registry.providers():
        if name.startswith("broker:"):
            out[name[len("broker:"):]] = registry.get(name)
    return out


def get(broker_id: str):
    """The provider for one broker, or None if it is not installed."""
    return registry.get(f"broker:{broker_id}")


def adapter(broker_id: str, account, user_id=None):
    """The Trader for `account` on `broker_id`.

    Raises NoBroker with a sentence worth reading, because "no module named
    exness_trading" is a true statement that helps nobody."""
    p = get(broker_id)
    if p is None:
        installed = sorted(providers())
        have = ", ".join(installed) if installed else "none"
        raise NoBroker(
            f"This account is on {broker_id}, but no {broker_id} broker is installed "
            f"(installed: {have}). Install the {broker_id} module to trade it."
        )
    return p.Trader(account, user_id)


def default_broker() -> str | None:
    """The broker to assume when nothing says otherwise: the only one installed,
    or None when there are none or several — in which case the caller must know."""
    names = list(providers())
    return names[0] if len(names) == 1 else None


# ── session binding, across every installed broker ─────────────────────────────
def bind_sessions(user_id) -> list:
    """Bind each installed broker's per-user session. Returns tokens for unbind.

    Every broker is bound, not just the active one: a request may act on the
    active account and then read prices from another broker's account, and a
    binding that is never used costs nothing."""
    tokens = []
    for bid, p in providers().items():
        fn = getattr(p, "bind_session", None)
        if not fn:
            continue
        try:
            tokens.append((bid, fn(user_id)))
        except Exception:
            pass
    return tokens


def unbind_sessions(tokens) -> None:
    for bid, tok in (tokens or []):
        p = get(bid)
        fn = getattr(p, "reset_session", None) if p else None
        if not fn:
            continue
        try:
            fn(tok)
        except Exception:
            pass


def has_connection(user_id, broker_id=None) -> bool:
    """Is this user connected to `broker_id` — or to any broker at all?"""
    items = ([(broker_id, get(broker_id))] if broker_id else list(providers().items()))
    for _bid, p in items:
        fn = getattr(p, "has_connection", None) if p else None
        try:
            if fn and fn(user_id):
                return True
        except Exception:
            pass
    return False


# ── which accounts the user lets this app touch ────────────────────────────────
def available(user_id, broker_id):
    """Account ids the user has made available on this broker, or None when they
    have not chosen — which means all of them.

    None and [] are DIFFERENT: none-chosen is "I have not been asked", empty is
    "I looked and picked nothing". Collapsing them would either opt a new user
    out of their own accounts or silently re-opt them in."""
    import db
    with db.connect() as conn:
        row = conn.execute("SELECT available_accounts, selected_accounts FROM exness_settings "
                           "WHERE user_id = %s", (user_id,)).fetchone()
    if not row:
        return None
    picks = (row["available_accounts"] or {}).get(broker_id)
    if picks is None and broker_id == "exness":
        # Exness had this before it was a shared idea; keep those choices.
        legacy = row["selected_accounts"]
        return [str(a) for a in legacy] if legacy else None
    return None if picks is None else [str(a) for a in picks]


def set_available(user_id, broker_id, accounts) -> list:
    import db
    from psycopg.types.json import Json
    picks = [str(a) for a in (accounts or [])]
    with db.connect() as conn:
        conn.execute("INSERT INTO exness_settings (user_id) VALUES (%s) "
                     "ON CONFLICT (user_id) DO NOTHING", (user_id,))
        conn.execute(
            # Both parameters need an explicit type: Postgres cannot infer them
            # inside jsonb_build_object and refuses with IndeterminateDatatype.
            "UPDATE exness_settings SET available_accounts = "
            "  COALESCE(available_accounts, '{}'::jsonb) || jsonb_build_object(%s::text, %s::jsonb), "
            "  updated_at = now() WHERE user_id = %s",
            (broker_id, Json(picks), user_id))
        # Exness's own copy stays in step, so its selection endpoint and the
        # market-data shortlist keep agreeing with this.
        if broker_id == "exness":
            conn.execute("UPDATE exness_settings SET selected_accounts = %s WHERE user_id = %s",
                         (Json(picks), user_id))
        conn.commit()
    _drop_active_if_unavailable(user_id, broker_id, picks)
    return picks


def is_available(user_id, broker_id, account) -> bool:
    picks = available(user_id, broker_id)
    return picks is None or str(account) in picks


def _drop_active_if_unavailable(user_id, broker_id, picks):
    """Taking away the account the app is acting on has to move it, or the next
    call trades somewhere the user just said no to."""
    import db
    with db.connect() as conn:
        row = conn.execute("SELECT active_account, active_broker FROM exness_settings "
                           "WHERE user_id = %s", (user_id,)).fetchone()
        if not row or row["active_broker"] != broker_id or not row["active_account"]:
            return
        if str(row["active_account"]) in picks:
            return
        conn.execute("UPDATE exness_settings SET active_account = %s WHERE user_id = %s",
                     (picks[0] if picks else None, user_id))
        conn.commit()


def ensure_active_account(user_id, broker_id, accounts) -> dict:
    """Point the user at an account, if they are not pointed at a working one.

    Connecting a broker and then being told "no active trading account" is the
    app asking you to finish a job you thought you had finished. So the first
    account a broker brings becomes the active one — unless a valid choice is
    already in place, because silently moving a live app onto a different
    account is the one thing worse than not choosing for them.

    `accounts` is whatever the broker just connected, most-preferred first.
    """
    import db
    picks = available(user_id, broker_id)
    if picks is not None:
        accounts = [a for a in accounts if str(a) in picks]
    if not accounts:
        return {"changed": False, "reason": "no account of this broker is available to the app"}

    with db.connect() as conn:
        conn.execute("INSERT INTO exness_settings (user_id) VALUES (%s) "
                     "ON CONFLICT (user_id) DO NOTHING", (user_id,))
        row = conn.execute("SELECT active_account, active_broker FROM exness_settings "
                           "WHERE user_id = %s", (user_id,)).fetchone()
        conn.commit()

    cur = (row or {}).get("active_account")
    cur_broker = (row or {}).get("active_broker")   # NOT NULL: always a name, even with no account
    if cur:
        # Still a real account on an installed broker? Then leave it alone.
        p = get(cur_broker) if cur_broker else None
        owns = getattr(p, "owns_account", None) if p else None
        try:
            if p is not None and (owns is None or owns(user_id, cur)):
                return {"changed": False, "reason": "an account is already active",
                        "active": {"broker": cur_broker, "account": cur}}
        except Exception:
            pass                     # could not verify → treat as stale, choose again

    pick = str(accounts[0])
    with db.connect() as conn:
        conn.execute("UPDATE exness_settings SET active_account = %s, active_broker = %s, "
                     "updated_at = now() WHERE user_id = %s", (pick, broker_id, user_id))
        conn.commit()
    return {"changed": True, "active": {"broker": broker_id, "account": pick}}


def is_refusal(exc, broker_id=None) -> bool:
    """A broker saying "not this account", as opposed to a real failure."""
    if isinstance(exc, AccountRefused):
        return True
    for _bid, p in ([(broker_id, get(broker_id))] if broker_id else list(providers().items())):
        fn = getattr(p, "is_refusal", None) if p else None
        try:
            if fn and fn(exc):
                return True
        except Exception:
            pass
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 403)
