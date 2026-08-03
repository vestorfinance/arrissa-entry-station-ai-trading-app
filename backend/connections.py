"""
Connections — a named, editable link to something outside this app.

The AI provider keys were the first of these and they were stored as three
columns on a settings row: one OpenAI key, one Anthropic, one DeepSeek, none of
them nameable and none of them editable except by overwriting. That is fine
until someone has a personal key and a work key, or wants to know which of two
keys the agent actually used.

So a connection is a ROW: a kind, a name the user chose, and the parameters that
kind needs. Add a second OpenAI key and it is a second connection with its own
name. The parameters are declared per kind — `TYPES` below — so a new
integration is a dict, not a form.

Secrets never come back out. A field marked `secret` is encrypted on the way in
and reported only as "set" on the way out; editing one means replacing it, and
leaving it blank means keeping what is there.
"""
from __future__ import annotations

import auth
import db

# What can be connected, and what each one needs. `logo` names an asset the
# frontend may have; where it does not, `mark` (or the first initial) in a
# coloured circle is the placeholder — deliberately, so a kind can be added
# before its artwork exists.
TYPES = [
    {
        "kind": "openai", "name": "OpenAI", "group": "ai", "tone": "teal",
        "logo": "/logos/openai.webp",
        "blurb": "GPT models for chat and analysis. Your key, your bill.",
        "docs": "https://platform.openai.com/api-keys",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "sk-…"},
        ],
    },
    {
        "kind": "anthropic", "name": "Anthropic", "group": "ai", "tone": "orange",
        "logo": "/logos/anthropic.webp",
        "blurb": "Claude models for chat and analysis. Your key, your bill.",
        "docs": "https://console.anthropic.com/settings/keys",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "sk-ant-…"},
        ],
    },
    {
        "kind": "deepseek", "name": "DeepSeek", "group": "ai", "tone": "blue",
        "logo": "/logos/deepseek.webp",
        "blurb": "DeepSeek models — fast and inexpensive. Your key, your bill.",
        "docs": "https://platform.deepseek.com/api_keys",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "sk-…"},
        ],
    },
    # Gemini, Grok and Groq all begin with G, so the first-initial fallback would
    # draw three identical circles. `mark` is the short code used when the logo
    # is missing or fails to load.
    {
        "kind": "gemini", "name": "Google Gemini", "group": "ai", "tone": "indigo",
        "logo": "/logos/gemini.webp",
        "mark": "Ge",
        "blurb": "Gemini models, including the free tier. Your key, your bill.",
        "docs": "https://aistudio.google.com/apikey",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "AIza…"},
        ],
    },
    {
        "kind": "grok", "name": "xAI Grok", "group": "ai", "tone": "slate",
        "logo": "/logos/grok.webp",
        "mark": "Gr",
        "blurb": "Grok models from xAI. Your key, your bill.",
        "docs": "https://console.x.ai",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "xai-…"},
        ],
    },
    {
        "kind": "groq", "name": "Groq", "group": "ai", "tone": "orange",
        "logo": "/logos/groq.webp",
        "mark": "Gq",
        "blurb": "Open models run on Groq's own hardware — the fast one. Your key, your bill.",
        "docs": "https://console.groq.com/keys",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "gsk_…"},
        ],
    },
    {
        "kind": "openrouter", "name": "OpenRouter", "group": "ai", "tone": "pink",
        "logo": "/logos/openrouter.webp",
        "mark": "OR",
        "blurb": "One key, every provider's models — hundreds of them. Your key, your bill.",
        "docs": "https://openrouter.ai/keys",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True,
             "placeholder": "sk-or-…"},
        ],
    },
]



def types() -> list:
    """Core's kinds, plus anything an installed module adds."""
    import registry
    return TYPES + registry.connection_types()


def _spec(kind):
    return next((t for t in types() if t["kind"] == kind), None)


def _ensure_table():
    with db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS connections (
                id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind       TEXT NOT NULL,
                name       TEXT NOT NULL,
                config     JSONB NOT NULL DEFAULT '{}'::jsonb,
                enabled    BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_connections_user "
                     "ON connections(user_id, kind)")
        conn.commit()


def _public(row) -> dict:
    """A connection as the UI may see it — every secret reduced to whether it is
    set. A key that can be read back is a key that can be taken."""
    spec = _spec(row["kind"]) or {}
    cfg = row["config"] or {}
    out = {}
    for f in spec.get("fields", []):
        if f.get("secret"):
            out[f["key"]] = "set" if cfg.get(f["key"]) else ""
        else:
            out[f["key"]] = cfg.get(f["key"], "")
    return {"id": str(row["id"]), "kind": row["kind"], "name": row["name"],
            "enabled": row["enabled"], "config": out,
            "created_at": row["created_at"]}


def listing(user_id) -> list:
    _ensure_table()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, name, config, enabled, created_at FROM connections "
            "WHERE user_id = %s ORDER BY created_at", (user_id,)).fetchall()
    return [_public(r) for r in rows]


def _merge_config(kind, existing, incoming) -> dict:
    """New values over old, EXCEPT a blank secret — which means "leave it".

    Otherwise opening a connection to rename it would wipe the key, because the
    form cannot show what it is not allowed to read."""
    spec = _spec(kind) or {}
    cfg = dict(existing or {})
    for f in spec.get("fields", []):
        k = f["key"]
        if k not in (incoming or {}):
            continue
        v = (incoming.get(k) or "").strip()
        if f.get("secret"):
            if v:
                cfg[k] = auth.encrypt(v)
        else:
            cfg[k] = v
    return cfg


def create(user_id, kind, name, config) -> dict:
    from psycopg.types.json import Json
    spec = _spec(kind)
    if not spec:
        raise ValueError(f"unknown connection type {kind}")
    _ensure_table()
    cfg = _merge_config(kind, {}, config)
    for f in spec["fields"]:
        if f.get("required") and not cfg.get(f["key"]):
            raise ValueError(f"{f['label']} is required")
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO connections (user_id, kind, name, config) VALUES (%s,%s,%s,%s) "
            "RETURNING id, kind, name, config, enabled, created_at",
            (user_id, kind, (name or spec["name"]).strip()[:60], Json(cfg))).fetchone()
        conn.commit()
    return _public(row)


def update(user_id, cid, name=None, config=None, enabled=None) -> dict | None:
    from psycopg.types.json import Json
    _ensure_table()
    with db.connect() as conn:
        cur = conn.execute("SELECT id, kind, name, config, enabled, created_at FROM connections "
                           "WHERE id = %s AND user_id = %s", (cid, user_id)).fetchone()
        if not cur:
            return None
        cfg = _merge_config(cur["kind"], cur["config"], config) if config is not None \
            else cur["config"]
        row = conn.execute(
            "UPDATE connections SET name = %s, config = %s, enabled = %s, updated_at = now() "
            "WHERE id = %s AND user_id = %s "
            "RETURNING id, kind, name, config, enabled, created_at",
            ((name if name is not None else cur["name"]).strip()[:60] or cur["name"],
             Json(cfg), cur["enabled"] if enabled is None else bool(enabled),
             cid, user_id)).fetchone()
        conn.commit()
    return _public(row)


def delete(user_id, cid) -> bool:
    _ensure_table()
    with db.connect() as conn:
        n = conn.execute("DELETE FROM connections WHERE id = %s AND user_id = %s",
                         (cid, user_id)).rowcount
        conn.commit()
    return bool(n)


def users_with(kind) -> list:
    """Everyone who has this kind connected and switched on.

    A module that needs to run something PER connected user — a listener, a
    poller — asks here rather than writing SQL against this table, so the table
    stays this file's business and the module keeps working if it changes."""
    _ensure_table()
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT user_id FROM connections "
                            "WHERE kind = %s AND enabled", (kind,)).fetchall()
    return [r["user_id"] for r in rows]


def secret_of(user_id, cid, field="api_key") -> str | None:
    """One SPECIFIC connection's secret.

    `secret()` answers "the key for this kind", which is right when a kind has
    one meaningful key. It is not enough once the user can pick — a second
    Telegram bot exists precisely because it talks somewhere the first does
    not, so the thing choosing it must be able to name it."""
    _ensure_table()
    with db.connect() as conn:
        row = conn.execute("SELECT config FROM connections "
                           "WHERE id = %s AND user_id = %s AND enabled",
                           (cid, user_id)).fetchone()
    v = (row or {}).get("config", {}).get(field) if row else None
    if not v:
        return None
    try:
        return auth.decrypt(v)
    except Exception:
        return None


def secret(user_id, kind, field="api_key") -> str | None:
    """The value the app should actually USE for this kind.

    The first ENABLED connection of that kind, oldest first — so adding a second
    key never silently changes which one is in use. Switching means disabling the
    one in front of it, which is a visible act."""
    _ensure_table()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT config FROM connections WHERE user_id = %s AND kind = %s AND enabled "
            "ORDER BY created_at", (user_id, kind)).fetchall()
    for r in rows:
        v = (r["config"] or {}).get(field)
        if v:
            try:
                return auth.decrypt(v)
            except Exception:
                continue
    return None
