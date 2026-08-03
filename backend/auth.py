"""Password hashing, JWT sessions, and API-key generation."""
import time
import hashlib
import secrets

import bcrypt
import jwt

import config


# ── passwords (bcrypt) ─────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


# ── session tokens (JWT) ───────────────────────────────────────────────────────
def make_token(user_id, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + config.JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALG)


def decode_token(token: str) -> dict:
    return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALG])


# ── API keys ───────────────────────────────────────────────────────────────────
KEY_PREFIX = "ak_live_"


def generate_api_key() -> str:
    """Full plaintext key — shown to the user exactly once."""
    return KEY_PREFIX + secrets.token_urlsafe(28)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def key_display(raw: str):
    """(prefix_shown, last_four) for masked display in the UI."""
    return raw[: len(KEY_PREFIX) + 4], raw[-4:]


# ── symmetric encryption for stored secrets (Exness password) ───────────────────
from cryptography.fernet import Fernet

_fernet = Fernet(config.FERNET_KEY.encode()) if config.FERNET_KEY else None


def encrypt(plaintext: str) -> str:
    if not _fernet:
        raise RuntimeError("FERNET_KEY not configured")
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not _fernet:
        raise RuntimeError("FERNET_KEY not configured")
    return _fernet.decrypt(token.encode()).decode()
