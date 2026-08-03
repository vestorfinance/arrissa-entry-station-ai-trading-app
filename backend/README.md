# Arrissa Exness API — Backend

FastAPI + PostgreSQL. Auth (login, change password) and API-key management.

## Setup

Postgres must be running and the `arrissa` database must exist:

```bash
createdb arrissa                        # once
python3 -m pip install "psycopg[binary]" bcrypt email-validator pyjwt fastapi uvicorn
python3 db.py                           # create tables
python3 create_user.py you@example.com yourpassword
```

Config is in `.env` (`DATABASE_URL`, `JWT_SECRET`).

## Run

```bash
python3 -m uvicorn main:app --reload --port 8000
```

The frontend dev server proxies `/api` → `:8000` (see `frontend/vite.config.js`).

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/auth/login` | — | email+password → `{ token, user }` |
| GET | `/api/me` | ✓ | current user |
| POST | `/api/me/password` | ✓ | change password (`current_password`, `new_password`) |
| GET | `/api/keys` | ✓ | list API keys (masked) |
| POST | `/api/keys` | ✓ | generate key (full key returned **once**) |
| DELETE | `/api/keys/{id}` | ✓ | revoke a key |

## Security notes

- Passwords: **bcrypt**. API keys: only a **sha256 hash** + prefix/last-4 are
  stored — the plaintext key is shown exactly once at creation.
- Sessions: JWT (HS256), 7-day expiry, secret in `.env`.
- `.env` holds the DB URL + JWT secret — keep it out of version control.
