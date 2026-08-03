"""
TradeLocker, as a module — the second broker, and the one that proves the seam
was worth building.

The adapter itself already existed and is unchanged: `TradeLockerTrader`
subclasses `broker_base.TraderBase` and implements the primitives, inheriting
the SL/TP maths, position sizing and bulk orchestration for free. What was
missing was the introduction — nothing told core this broker existed, so
`trader()` could not build it and an account on TradeLocker answered "no
tradelocker broker is installed".

Free, and bundled: a self-hosted instance with no paid module still gets a
broker, live prices and charts, because a TradeLocker login is the user's own
credentials against their own account.
"""
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, constr

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import auth  # noqa: E402  (core)
import db    # noqa: E402  (core)
from deps import current_user  # noqa: E402  (core owns authentication)

import tradelocker as tl  # noqa: E402

router = APIRouter(tags=["tradelocker"])


# ══ the broker provider ════════════════════════════════════════════════════════
def _is_archived(status) -> bool:
    """Anything TradeLocker does not call ACTIVE is not tradable.

    An unknown or missing status counts as ACTIVE on purpose: a status string we
    have never seen must not silently hide a working account. Only a value we
    can read AND that is not ACTIVE hides one."""
    return bool(status) and str(status).upper() != "ACTIVE"


class Provider:
    """What core is allowed to know about TradeLocker."""

    id = "tradelocker"
    name = "TradeLocker"
    logo = "icon.webp"          # served from this module's own assets directory

    @staticmethod
    def Trader(account, user_id=None):
        return tl.TradeLockerTrader(str(account), user_id)

    # No session to bind: each adapter loads its own login's tokens from the DB
    # when it is built, so there is nothing context-local to set up or tear down.

    @staticmethod
    def has_connection(user_id) -> bool:
        with db.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM tradelocker_user_sessions WHERE user_id = %s", (user_id,)
            ).fetchone() is not None

    @staticmethod
    def owns_account(user_id, account) -> bool:
        """Authoritative: an account is TradeLocker's only if it is in its table.

        This is also how core resolves which broker an account belongs to, so it
        must answer for the account NUMBER exactly as it was stored."""
        with db.connect() as conn:
            return conn.execute(
                "SELECT 1 FROM tradelocker_accounts WHERE user_id = %s AND account_id = %s",
                (user_id, str(account)),
            ).fetchone() is not None

    @staticmethod
    def login_accounts(user_id=None):
        if user_id is None:
            return []
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT account_id, status FROM tradelocker_accounts WHERE user_id = %s "
                "ORDER BY added_at", (user_id,)).fetchall()
        return [str(r["account_id"]) for r in rows if not _is_archived(r["status"])]

    @staticmethod
    def accounts_view(user_id) -> dict:
        """The Accounts page. A TradeLocker user may hold several logins at once
        — a demo and a live one — so accounts are grouped under the connection
        they came from rather than flattened."""
        with db.connect() as conn:
            sess = conn.execute(
                "SELECT id, tl_email, environment, server, connected_at "
                "FROM tradelocker_user_sessions WHERE user_id = %s ORDER BY connected_at",
                (user_id,)).fetchall()
            accs = conn.execute(
                "SELECT account_id, acc_num, environment, currency, name, session_id, "
                "       status, balance "
                "FROM tradelocker_accounts WHERE user_id = %s ORDER BY added_at",
                (user_id,)).fetchall()
        # An archived account cannot be traded, so it is not offered — not in the
        # connection listing, not in the canonical list, not in any picker.
        accs = [a for a in accs if not _is_archived(a["status"])]
        by_session = {}
        for a in accs:
            by_session.setdefault(str(a["session_id"]), []).append({
                "account_id": a["account_id"], "acc_num": a["acc_num"],
                "environment": a["environment"], "currency": a["currency"], "name": a["name"],
            })
        return {"connected": bool(sess),
                "connections": [{
                    "connection_id": str(c["id"]), "email": c["tl_email"],
                    "environment": c["environment"], "server": c["server"],
                    "connected_at": c["connected_at"],
                    "accounts": by_session.get(str(c["id"]), []),
                } for c in sess],
                # Canonical shape — the same keys Exness returns, so the agent and
                # anything else reading `accounts` never learns which broker it is.
                "accounts": [{
                    "account_number": a["account_id"],
                    "account_type": a["name"] or "TradeLocker",
                    "server": None,
                    "currency": a["currency"],
                    "balance": a["balance"],
                    "is_real": a["environment"] == "live",
                    "is_archived": False,          # archived ones never reach here
                    "platform": "tradelocker",
                    "acc_num": a["acc_num"],
                } for a in accs]}


# ══ routes ═════════════════════════════════════════════════════════════════════
class ConnectBody(BaseModel):
    email: EmailStr
    password: str
    server: constr(min_length=1)
    environment: str = "demo"


class DisconnectBody(BaseModel):
    connection_id: str | None = None    # None ⇒ disconnect ALL TradeLocker logins


class DevKeyBody(BaseModel):
    key: str = ""


@router.get("/api/tradelocker/connection")
def connection(user: dict = Depends(current_user)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, tl_email, environment, server, connected_at "
            "FROM tradelocker_user_sessions WHERE user_id = %s ORDER BY connected_at",
            (user["id"],)).fetchall()
    return {"connected": bool(rows),
            "connections": [{"connection_id": str(r["id"]), "email": r["tl_email"],
                             "environment": r["environment"], "server": r["server"],
                             "connected_at": r["connected_at"]} for r in rows]}


@router.post("/api/tradelocker/connect")
def connect(body: ConnectBody, user: dict = Depends(current_user)):
    """Connect a TradeLocker login. The password is used once to obtain JWT
    tokens and is never stored. Every account under the login becomes available."""
    try:
        res = tl.login(body.email.lower(), body.password, body.server, body.environment)
    except Exception as e:
        raise HTTPException(400, f"Couldn’t connect your TradeLocker account: {e}")

    with db.connect() as conn:
        sess = conn.execute(
            """INSERT INTO tradelocker_user_sessions
                 (user_id, tl_email, environment, server, access_enc, refresh_enc)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, tl_email, environment, server) DO UPDATE SET
                 access_enc = EXCLUDED.access_enc, refresh_enc = EXCLUDED.refresh_enc,
                 updated_at = now()
               RETURNING id""",
            (user["id"], res["email"], res["environment"], res["server"],
             auth.encrypt(res["access"]), auth.encrypt(res["refresh"])),
        ).fetchone()
        sid = sess["id"]
        conn.execute("DELETE FROM tradelocker_accounts WHERE session_id = %s", (sid,))
        for a in res["accounts"]:
            conn.execute(
                """INSERT INTO tradelocker_accounts
                     (user_id, account_id, session_id, acc_num, environment, currency,
                      name, status, balance)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, account_id) DO UPDATE SET
                     session_id = EXCLUDED.session_id, acc_num = EXCLUDED.acc_num,
                     environment = EXCLUDED.environment, currency = EXCLUDED.currency,
                     name = EXCLUDED.name, status = EXCLUDED.status,
                     balance = EXCLUDED.balance""",
                (user["id"], a["account_id"], sid, a["acc_num"], a["environment"],
                 a.get("currency"), a.get("name"), a.get("status"), a.get("balance")),
            )
        conn.commit()

    # Connecting a broker and then being told "no active trading account" is the
    # app asking you to finish a job you thought you had finished.
    import brokers
    active = brokers.ensure_active_account(
        user["id"], "tradelocker", [a["account_id"] for a in res["accounts"]])
    return {"ok": True, "email": res["email"], "environment": res["environment"],
            "accounts": res["accounts"], "active": active.get("active")}


@router.post("/api/tradelocker/disconnect")
def disconnect(body: DisconnectBody, user: dict = Depends(current_user)):
    """Delete a TradeLocker login (or all of them) — tokens and account rows go
    entirely. If the active account was one of them, the pointer is cleared so
    nothing keeps trying to trade an account that is no longer connected."""
    with db.connect() as conn:
        if body.connection_id:
            conn.execute("DELETE FROM tradelocker_user_sessions WHERE id = %s AND user_id = %s",
                         (body.connection_id, user["id"]))
        else:
            conn.execute("DELETE FROM tradelocker_user_sessions WHERE user_id = %s", (user["id"],))
        # Only the ACCOUNT is cleared — `active_broker` is NOT NULL, and a user
        # with no account is between accounts, not between brokers.
        conn.execute(
            """UPDATE exness_settings SET active_account = NULL
               WHERE user_id = %s AND active_broker = 'tradelocker'
                 AND NOT EXISTS (SELECT 1 FROM tradelocker_accounts
                                 WHERE user_id = %s AND account_id = active_account::text)""",
            (user["id"], user["id"]),
        )
        conn.commit()
    return {"ok": True}


@router.get("/api/admin/tradelocker-key")
def get_dev_key(user: dict = Depends(current_user)):
    _require_admin(user)
    return {"has_key": tl.has_dev_key()}


@router.post("/api/admin/tradelocker-key")
def set_dev_key(body: DevKeyBody, user: dict = Depends(current_user)):
    """The app-level partner key. Optional — TradeLocker authenticates with the
    USER's own credentials, and the key only raises a rate limit."""
    _require_admin(user)
    return {"has_key": tl.set_dev_key(body.key.strip())}


def _require_admin(user):
    import admin_api
    if not admin_api._is_admin(user["email"]):
        raise HTTPException(403, "Owners only.")


def register(registry, module_id):
    registry.routes(router, module=module_id)
    registry.worker("tradelocker-dev-key-seed", tl.seed_dev_key_from_env,
                    stop=lambda: None, module=module_id)   # one-shot, nothing to stop
    registry.provider("broker:tradelocker", Provider, module=module_id)
