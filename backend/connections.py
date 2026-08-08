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
    # Not a model provider — a data source, and the only connection here that
    # takes a login rather than a key. Retail Sentiment reads Myfxbook's
    # community outlook, and the richer of its two sources (volumes, positions,
    # the full symbol list) requires an account.
    #
    # It lived in MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD, which meant shell access, a
    # file edit and a restart to change a password — and a plaintext secret
    # sitting beside the code. Here it is encrypted like every other credential
    # and changed from a form. The env vars still work, so nobody's existing
    # install breaks.
    {
        "kind": "myfxbook", "name": "Myfxbook", "group": "data", "tone": "blue",
        "logo": "/logos/myfxbook.webp",
        "mark": "Mf",
        # It exists FOR Retail Sentiment and does nothing without it, so it is
        # hidden until that module is installed — and, once installed, an
        # unconnected Myfxbook is a module that returns nothing and says
        # nothing. Naming the module it serves is what lets that be noticed.
        "requires_module": "sentiment",
        "blurb": "Your Myfxbook login, for Retail Sentiment. The free account is enough — "
                 "it is read-only community positioning, never your own accounts.",
        "docs": "https://www.myfxbook.com/login",
        "docs_label": "Create account",
        "fields": [
            {"key": "email", "label": "Myfxbook email", "required": True,
             "placeholder": "you@example.com"},
            {"key": "password", "label": "Myfxbook password", "secret": True, "required": True},
        ],
    },

    # ── the brokers ───────────────────────────────────────────────────────────
    #
    # These are connections in every sense a user cares about, and in no sense
    # this table understands. A broker login is NOT stored here and must not be:
    # the password is used once to obtain a session and then thrown away, which
    # is the promise the whole product rests on. Writing it into `config`
    # alongside an API key would quietly break that.
    #
    # So they are `managed_by` their own module. The page shows them, collects
    # the fields and posts them to the module's own endpoint, which does the
    # login and keeps only what it should. Nothing broker-shaped reaches this
    # table, and `secret: True` never gets to mean "stored, encrypted" for a
    # password that should not exist a second later.
    #
    # `requires_module` keeps them out of sight until the module is installed —
    # offering to connect a broker the instance cannot talk to is a dead end
    # dressed as an option.
    {
        "kind": "exness", "name": "Exness", "group": "broker", "tone": "amber",
        "logo": "/logos/exness.png",
        "mark": "Ex",
        "requires_module": "exness",
        "managed_by": {"connect": "/api/exness/connect",
                       "status": "/api/exness/connection",
                       "disconnect": "/api/exness/disconnect"},
        "blurb": "Your own Exness account. The password is used once to obtain a session "
                 "and never stored.",
        "docs": "https://my.exness.com/accounts/sign-in",
        "docs_label": "Sign in",
        # Where somebody with NO account goes. A broker connection is the one
        # kind that can be blocked by not having the underlying thing at all,
        # and "Get a key" pointing at a sign-in form is no use to a person who
        # has nothing to sign in with. Declared per kind so any broker can name
        # its own, and core never learns what an Exness is.
        "signup_url": "https://one.exnessonelink.com/a/entrystati",
        "signup_label": "Create account",
        "fields": [
            {"key": "exness_email", "label": "Exness email", "required": True,
             "placeholder": "you@example.com"},
            {"key": "exness_password", "label": "Exness password", "secret": True,
             "required": True, "transient": True},
        ],
    },
    {
        "kind": "tradelocker", "name": "TradeLocker", "group": "broker", "tone": "blue",
        # TradeLocker is a platform, not a broker: an account is opened with a
        # broker who runs on it. So this kind names several, where Exness names
        # one, and the seam carries a list rather than a single URL.
        "signup_label": "Create account",
        "signup_options": [
            {"name": "GatesFX", "url": "https://secure.gatesfx.com/links/go/3646"},
            {"name": "HeroFX", "url": "https://secure.gatesfx.com/links/go/3646"},
        ],
        "logo": "/logos/tradelocker.webp",
        "mark": "TL",
        "requires_module": "tradelocker",
        "managed_by": {"connect": "/api/tradelocker/connect",
                       "status": "/api/tradelocker/connection",
                       "disconnect": "/api/tradelocker/disconnect"},
        "blurb": "A TradeLocker login. The password is used once for tokens and never "
                 "stored. Every account under the login becomes available.",
        # No `docs`, so no "Get a key" on this card. There is no key to get:
        # tradelocker.com is a product page, and the credential is the login the
        # BROKER issues — which is what signup_options is for.
        "fields": [
            {"key": "email", "label": "TradeLocker email", "required": True,
             "placeholder": "you@example.com"},
            {"key": "password", "label": "Password", "secret": True, "required": True,
             "transient": True},
            {"key": "server", "label": "Server", "required": True,
             "placeholder": "the server name your broker gave you"},
            {"key": "environment", "label": "Environment", "required": True,
             "placeholder": "live or demo"},
        ],
    },
]



def types() -> list:
    """Core's kinds, plus anything an installed module adds.

    A kind that names `requires_module` is hidden until that module is actually
    loaded. Offering to connect a broker this instance has no code for is a dead
    end dressed as an option — and worse, its connect endpoint would not exist,
    so the form would fail with a 404 rather than an explanation."""
    import registry
    loaded = set(registry.modules())
    mine = [t for t in TYPES
            if not t.get("requires_module") or t["requires_module"] in loaded]
    return mine + registry.connection_types()


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


def value(user_id, kind, field) -> str | None:
    """A NON-secret field, as stored.

    `secret()` decrypts, which is right for a key and wrong for a username: a
    field the spec did not mark secret was never encrypted, so decrypting it
    throws and the caller sees None — a credential pair silently becoming half a
    pair. Same lookup rule as `secret()`: first enabled connection of that kind,
    oldest first."""
    _ensure_table()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT config FROM connections WHERE user_id = %s AND kind = %s AND enabled "
            "ORDER BY created_at", (user_id, kind)).fetchall()
    for r in rows:
        v = (r["config"] or {}).get(field)
        if v:
            return v
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
