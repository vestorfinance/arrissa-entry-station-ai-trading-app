"""
TradeLocker broker adapter.

Public surface:
  - dev-key management (app-level partner key, stored on admin_settings)
  - login()            — connect a user's TradeLocker account (returns tokens+accounts)
  - TradeLockerTrader  — the canonical Trader adapter (native TL ⇄ canonical shape)

The adapter is the ONLY place that knows TradeLocker's native wire format; the
rest of the app speaks the canonical trading shape (see broker_base.TraderBase).
"""
import auth
import db


# ── app-level developer/partner API key (admin_settings) ────────────────────────
def set_dev_key(raw: str) -> bool:
    enc = auth.encrypt(raw) if raw else None
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO admin_settings (id, tradelocker_dev_key_enc, updated_at) "
            "VALUES (1, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET "
            "tradelocker_dev_key_enc = EXCLUDED.tradelocker_dev_key_enc, updated_at = now()",
            (enc,),
        )
        conn.commit()
    return bool(enc)


def dev_key() -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT tradelocker_dev_key_enc FROM admin_settings WHERE id = 1"
        ).fetchone()
    if not row or not row["tradelocker_dev_key_enc"]:
        return None
    try:
        return auth.decrypt(row["tradelocker_dev_key_enc"])
    except Exception:
        return None


def has_dev_key() -> bool:
    return dev_key() is not None


def seed_dev_key_from_env() -> None:
    import config
    env_key = getattr(config, "TRADELOCKER_DEV_KEY", "") or ""
    if env_key and not has_dev_key():
        try:
            set_dev_key(env_key.strip())
        except Exception:
            pass


# ── lazy re-exports (avoid importing curl/adapter at module load) ───────────────
def login(*args, **kwargs):
    from .connect import login as _login
    return _login(*args, **kwargs)


def TradeLockerTrader(*args, **kwargs):
    from .trader import TradeLockerTrader as _T
    return _T(*args, **kwargs)


ENVIRONMENTS = ("demo", "live")
