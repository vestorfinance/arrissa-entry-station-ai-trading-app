"""Load backend/.env into the environment and expose config."""
import os
from pathlib import Path

_envfile = Path(__file__).parent / ".env"
if _envfile.exists():
    for line in _envfile.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://davidrichchild@localhost:5432/arrissa"
)
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
JWT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

FERNET_KEY = os.environ.get("FERNET_KEY", "")

# TradeLocker app-level developer/partner API key. Seeded once into admin_settings
# on startup if that row has none yet (see tradelocker.seed_dev_key_from_env).
TRADELOCKER_DEV_KEY = os.environ.get("TRADELOCKER_DEV_KEY", "")
