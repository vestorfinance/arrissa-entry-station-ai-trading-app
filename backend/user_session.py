"""
Per-user broker session binding — the isolation layer, so every call in a request
acts on THAT user's own broker session and never on another user's.

Core does not know how any broker stores a session. It knows only that a broker
module can be asked to bind one for a user and to unbind it afterwards, which is
what `brokers.bind_sessions()` does across every installed broker. What used to
be Exness-specific here now lives in the Exness module, and this file is the same
size it always was with none of the knowledge.

`bind()` also sets the active (broker, account, user) for the current
context/thread, which is what lets `trading_api.trader()` build the right adapter
with no argument at all.
"""
import contextlib

import db
import brokers


class NotConnected(brokers.SessionExpired):
    """This user hasn't connected a broker account yet."""


def has_connection(user_id) -> bool:
    """Is this user connected to any installed broker?"""
    return brokers.has_connection(user_id)


def _active_account_for(user_id):
    """(broker, account_ref) for the user's active account."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT active_account, active_broker FROM exness_settings WHERE user_id = %s",
            (user_id,),
        ).fetchone()
    if not row or not row["active_account"]:
        return (None, None)
    return (row.get("active_broker") or brokers.default_broker() or "exness",
            row["active_account"])


def bind(user_id):
    """Bind every installed broker's session provider for this user and set their
    active (broker, account) + user for the current context/thread. Returns a
    token bundle for reset()."""
    import trading_api
    broker, acct = _active_account_for(user_id)
    stoks = brokers.bind_sessions(user_id)
    atok = trading_api._active_ctx.set(acct)
    abtok = trading_api._active_broker_ctx.set(broker)
    autok = trading_api._active_user_ctx.set(user_id)
    return (stoks, atok, abtok, autok)


def reset(tokens):
    if not tokens:
        return
    stoks, atok, abtok, autok = tokens
    brokers.unbind_sessions(stoks)
    try:
        import trading_api
        trading_api._active_ctx.reset(atok)
        trading_api._active_broker_ctx.reset(abtok)
        trading_api._active_user_ctx.reset(autok)
    except Exception:
        pass


@contextlib.contextmanager
def as_user(user_id):
    """`with as_user(uid):` — every broker call inside runs on that user's session
    and defaults to that user's active account."""
    tokens = bind(user_id)
    try:
        yield
    finally:
        reset(tokens)
