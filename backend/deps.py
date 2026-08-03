"""
Shared FastAPI dependencies — the ones a module needs and core must be able to
hand over without an import cycle.

`current_user` used to live in `main`, which meant a module wanting it had to
import the very file that was importing the module. Here it is importable by
anyone, at any point during startup, which is what a module needs.
"""
from fastapi import Header, HTTPException

import auth
import db


def current_user(authorization: str = Header(default="")) -> dict:
    """The signed-in user from a bearer token, or a clean 401."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = auth.decode_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, email, first_name, last_name, created_at FROM users WHERE id = %s",
            (claims["sub"],),
        ).fetchone()
    if not row:
        raise HTTPException(401, "User no longer exists")
    return row
