"""
TradeLocker connect flow — mirrors the intent of exness_connect.login, but far
simpler: TradeLocker offers a direct credential login (no browser / reCAPTCHA).

Returns everything the caller needs to persist a connection:
  {access, refresh, expire_date, accounts:[{account_id, acc_num, currency, name, environment}]}

Security stance (same as Exness): the password is used once here and never stored.
"""
from . import api


def login(email: str, password: str, server: str, environment: str = "demo") -> dict:
    email = (email or "").strip()
    server = (server or "").strip()
    environment = (environment or "demo").strip().lower()
    if environment not in ("demo", "live"):
        raise api.TradeLockerError("environment must be 'demo' or 'live'")
    if not (email and password and server):
        raise api.TradeLockerError("email, password and server are all required.")

    tokens = api.login_tokens(email, password, server, environment)
    accounts = api.all_accounts(tokens["accessToken"], environment)
    if not accounts:
        raise api.TradeLockerError(
            "Logged in, but no TradeLocker accounts were found for this login.")
    return {
        "access": tokens["accessToken"],
        "refresh": tokens["refreshToken"],
        "expire_date": tokens.get("expireDate"),
        "environment": environment,
        "server": server,
        "email": email,
        "accounts": accounts,
    }
