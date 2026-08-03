"""
Whose AI key, and which model — the one place both questions are answered.

Two editions want opposite things here, and pretending otherwise is what makes
this messy:

  · **cloud** sells a product. The user picks `arrissa-chat` or `arrissa-pro`,
    the app runs it on OUR key, and the real provider is never exposed — the
    branding IS the offering, and it is what the credit price is quoted against.

  · **community** sells nothing. The user brings their own OpenAI, Anthropic or
    DeepSeek key and picks whatever models that key can reach. Branding someone
    else's model as "arrissa-pro" on their own machine would be dressing up
    their purchase as ours.

So `resolve()` returns (provider, model, key) and each edition answers it its
own way. Nothing else in the app needs to know which edition it is in.

The model list is NOT hardcoded. It is fetched from the provider, live, with the
user's own key — because a list compiled today is wrong by the next release, and
a user who pays for a model they cannot select has been told a small lie by
their own software.
"""
from __future__ import annotations

import auth
import db
import edition

# Anthropic has its own wire format. EVERY other provider speaks OpenAI's, so
# what distinguishes them is a base URL and nothing else — which is why adding
# one is a line in this table rather than a branch in five files. It was five:
# run_agent, the analysis engine's _llm, the memory extractor, the model list
# and the settings page each asked "which provider is this" separately.
OPENAI_WIRE = {
    "openai":     "https://api.openai.com/v1",
    "deepseek":   "https://api.deepseek.com",
    "gemini":     "https://generativelanguage.googleapis.com/v1beta/openai",
    "grok":       "https://api.x.ai/v1",
    "groq":       "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

PROVIDERS = ("openai", "anthropic", *(p for p in OPENAI_WIRE if p != "openai"))

# The three that predate connections and still have a column on ai_settings.
# A newer provider has no column and does not need one — a connection is where
# a key lives now, and `user_key` reads that first.
_COL = {"openai": "openai_key_enc", "anthropic": "anthropic_key_enc",
        "deepseek": "deepseek_key_enc"}


# How long to wait on a model before giving up.
#
# The OpenAI SDK's default is 600 seconds. A flow makes many calls, so one stalled
# provider turned a 90-second agent run into something that never came back — which
# is exactly what "it just says running" looks like from the outside. Failing in a
# minute and a half with a reason beats hanging for ten with none.
LLM_TIMEOUT = 90


def base_url(provider) -> str | None:
    """Where this provider's OpenAI-compatible API lives, or None if it is not
    one (i.e. Anthropic, which the caller must handle itself)."""
    return OPENAI_WIRE.get(provider)


def speaks_openai(provider) -> bool:
    return provider in OPENAI_WIRE


# Where each provider publishes what it can run. Every OpenAI-compatible one
# answers on {base}/models with a bearer token; Anthropic is the exception.
_bearer = lambda k: {"Authorization": f"Bearer {k}"}          # noqa: E731
_LIST = {p: (f"{b}/models", _bearer) for p, b in OPENAI_WIRE.items()}
_LIST["anthropic"] = ("https://api.anthropic.com/v1/models",
                      lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"})


# ── storage ────────────────────────────────────────────────────────────────────
def _row(user_id):
    with db.connect() as conn:
        return conn.execute(
            "SELECT openai_key_enc, anthropic_key_enc, deepseek_key_enc, selected_models "
            "FROM ai_settings WHERE user_id = %s", (user_id,)).fetchone()


def user_key(user_id, provider) -> str | None:
    """This user's key for a provider.

    A named CONNECTION wins, because that is where keys are managed now. The old
    single column is still read behind it so nobody who had entered a key before
    connections existed has to enter it again."""
    import connections
    k = connections.secret(user_id, provider, "api_key")
    if k:
        return k

    col = _COL.get(provider)
    row = _row(user_id) if col else None
    if not row or not row[col]:
        return None
    try:
        return auth.decrypt(row[col])
    except Exception:
        return None


def own_key(user_id, provider) -> str | None:
    """A key the user DELIBERATELY CONNECTED. Connections only.

    `user_key` also reads the legacy ai_settings column, which is right for
    running a call — a community user who entered a key before the Connections
    page existed should not have to enter it again. It is wrong for deciding
    whose key it is: a value that has been sitting in that column since before
    the page existed is not a deliberate act, and now that "their key" changes
    the bill and the model list, the difference is money. A user with one
    DeepSeek connection was being offered two OpenAI models and billed the BYOK
    rate for them."""
    import connections
    return connections.secret(user_id, provider, "api_key")


def set_user_key(user_id, provider, raw: str) -> bool:
    col = _COL.get(provider)
    if not col:
        raise ValueError(f"unknown provider {provider}")
    enc = auth.encrypt(raw.strip()) if raw and raw.strip() else None
    with db.connect() as conn:
        conn.execute(
            f"INSERT INTO ai_settings (user_id, {col}) VALUES (%s, %s) "
            f"ON CONFLICT (user_id) DO UPDATE SET {col} = EXCLUDED.{col}, updated_at = now()",
            (user_id, enc))
        conn.commit()
    return bool(enc)


def admin_key(provider) -> str | None:
    """The app-level key — what the cloud edition runs everything on."""
    col = _COL.get(provider)
    if not col:
        return None
    with db.connect() as conn:
        row = conn.execute(f"SELECT {col} AS k FROM admin_settings WHERE id = 1").fetchone()
    if not row or not row["k"]:
        return None
    try:
        return auth.decrypt(row["k"])
    except Exception:
        return None


# ── bring your own key, on a metered edition ──────────────────────────────────
#
# A cloud user may connect their own provider key. We then spend nothing on
# tokens, and charge a markup on what the request WOULD have cost instead of the
# cost itself — the model is theirs, everything around it is still ours.
#
# Both knobs live in admin_settings so the operator sets them, not the code.
BYOK_DEFAULT_ENABLED = True
BYOK_DEFAULT_MARKUP_PCT = 40

_byok_cache: tuple = (0.0, None)


def byok_policy() -> dict:
    """{'enabled', 'markup_pct'} — admin-set, memoised 10s like the other
    admin-tunable numbers. Meaningless where nothing is metered."""
    global _byok_cache
    import time as _t
    now = _t.time()
    if _byok_cache[0] > now and _byok_cache[1]:
        return _byok_cache[1]
    out = {"enabled": BYOK_DEFAULT_ENABLED, "markup_pct": BYOK_DEFAULT_MARKUP_PCT}
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT byok_enabled, byok_markup_pct FROM admin_settings "
                               "WHERE id = 1").fetchone()
        if row:
            if row["byok_enabled"] is not None:
                out["enabled"] = bool(row["byok_enabled"])
            if row["byok_markup_pct"] is not None:
                out["markup_pct"] = max(0, min(500, int(row["byok_markup_pct"])))
    except Exception:
        pass
    _byok_cache = (now + 10, out)
    return out


def save_byok_policy(enabled=None, markup_pct=None) -> dict:
    global _byok_cache
    cur = byok_policy()
    e = cur["enabled"] if enabled is None else bool(enabled)
    p = cur["markup_pct"] if markup_pct is None else int(markup_pct)
    if not (0 <= p <= 500):
        raise ValueError("the markup must be between 0 and 500 percent")
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO admin_settings (id, byok_enabled, byok_markup_pct)
               VALUES (1, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 byok_enabled    = EXCLUDED.byok_enabled,
                 byok_markup_pct = EXCLUDED.byok_markup_pct""", (e, p))
        conn.commit()
    _byok_cache = (0.0, None)
    return byok_policy()


def on_own_key(user_id, provider) -> bool:
    """Is this call going out on the USER's key rather than ours?

    Asked separately from `key_for` rather than returned beside it, because the
    answer must be the same for the caller who runs the request and the caller
    who prices it, and those are different places."""
    if edition.byok():
        return True                       # nothing is metered there anyway
    if not byok_policy()["enabled"]:
        return False
    return bool(own_key(user_id, provider))


def key_for(user_id, provider) -> str | None:
    """The key this call should run on.

    Community takes the user's own and stops there — falling back to an app key
    would mean an operator silently spending the vendor's money, or a key that
    is not there on a self-hosted box anyway.

    On a metered edition the user's own key WINS when they have connected one and
    the operator allows it. They are then billed a markup on what the tokens
    would have cost us rather than the cost itself."""
    if edition.byok():
        return user_key(user_id, provider)
    if byok_policy()["enabled"]:
        own = own_key(user_id, provider)
        if own:
            return own
    return admin_key(provider)


def selected(user_id) -> list:
    row = _row(user_id)
    return (row["selected_models"] if row else None) or []


def set_selected(user_id, models: list) -> list:
    from psycopg.types.json import Json
    clean = [{"provider": m["provider"], "model": m["model"]}
             for m in models if m.get("provider") in PROVIDERS and m.get("model")]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO ai_settings (user_id, selected_models) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET selected_models = EXCLUDED.selected_models, "
            "updated_at = now()", (user_id, Json(clean)))
        conn.commit()
    return clean


# ── what a provider can actually run, asked at the time ────────────────────────
def _provider_message(r) -> str:
    """Whatever the provider said, out of whichever envelope it used.

    Seven providers, three shapes: {"error": {"message": …}}, {"error": …} and
    {"message": …}. Their own words beat ours — "API key not valid" is a fix,
    "rejected" is a shrug."""
    try:
        b = r.json() or {}
    except Exception:
        return ""
    err = b.get("error", b)
    if isinstance(err, dict):
        err = err.get("message") or err.get("code") or ""
    return str(err or "").strip()[:120]



def list_models(user_id, provider) -> dict:
    """Every model this key can reach, from the provider itself.

    Fetched each time rather than cached or compiled in: providers ship models
    weekly, and a stale list is a user unable to select the thing they are
    paying for."""
    if provider not in _LIST:
        return {"error": f"unknown provider {provider}", "models": []}
    # The user's OWN key first, in either edition. The question this answers is
    # "what can this key run", and the key being asked about is the one they
    # just typed into the form — falling straight through to the app key would
    # show a cloud user a list their key had no part in.
    key = user_key(user_id, provider) or key_for(user_id, provider)
    if not key:
        return {"error": "no key set for this provider", "models": []}
    url, headers = _LIST[provider]
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, headers=headers(key), impersonate="chrome", timeout=20)
        # 401/403 is the usual "bad key", but Gemini and xAI answer 400 — and on
        # a GET whose only input IS the key, every one of those means the same
        # thing. Reporting it as "could not reach" would send someone to check
        # their network over a typo in a key.
        if r.status_code in (400, 401, 403):
            return {"error": f"that key was rejected by {provider}"
                             + (f" — {_provider_message(r)}" if _provider_message(r) else ""),
                    "models": []}
        r.raise_for_status()
        body = r.json() or {}
    except Exception as e:
        return {"error": f"could not reach {provider}: {e}", "models": []}

    raw = body.get("data") if isinstance(body, dict) else None
    if raw is None:
        raw = body if isinstance(body, list) else []
    out = []
    for m in raw:
        mid = m.get("id") if isinstance(m, dict) else str(m)
        if not mid:
            continue
        out.append({"provider": provider, "model": mid,
                    "name": (m.get("display_name") if isinstance(m, dict) else None) or mid})
    out.sort(key=lambda x: x["model"])

    # The provider has just told us what it can run, so this is the one moment we
    # can tell a stale pick from a live one. A selection is stored per
    # (provider, model) and outlives the model itself — deepseek-chat was
    # retired and went on being offered in the picker, because nothing ever
    # asked. Pruned ONLY on a successful listing: a transient failure must never
    # look like "the provider dropped everything".
    dropped = []
    try:
        live = {m["model"] for m in out}
        if live:
            picks = selected(user_id)
            keep = [m for m in picks
                    if m.get("provider") != provider or m.get("model") in live]
            if len(keep) != len(picks):
                dropped = [m["model"] for m in picks if m not in keep]
                set_selected(user_id, keep)
    except Exception:
        dropped = []

    return {"provider": provider, "models": out, "dropped": dropped}


# ── which model a call runs on ─────────────────────────────────────────────────
def resolve(user_id, alias: str):
    """(provider, model, key) for this call.

    Cloud maps a branded alias through billing. Community takes a real
    `provider:model` the user chose, defaulting to the first they enabled."""
    import billing
    if not edition.byok():
        # A branded tier and a provider:model can now BOTH appear in the picker
        # here, so the colon is what tells them apart. Sending "deepseek:x" to
        # the alias mapper would quietly hand back the default tier instead.
        if alias and ":" in alias:
            p, m = alias.split(":", 1)
            if p in PROVIDERS:
                return p, m, own_key(user_id, p) or key_for(user_id, p)
        provider, model = billing.resolve_model(alias)
        # key_for, not admin_key: a cloud user who has connected their own key
        # runs on it, and is billed a markup instead of the token cost.
        return provider, model, key_for(user_id, provider)

    picks = selected(user_id)
    chosen = None
    if alias and ":" in alias:
        p, m = alias.split(":", 1)
        chosen = next((x for x in picks if x["provider"] == p and x["model"] == m), None) \
            or ({"provider": p, "model": m} if p in PROVIDERS else None)
    elif alias:
        chosen = next((x for x in picks if x["model"] == alias), None)
    if chosen is None:
        chosen = picks[0] if picks else None
    if chosen is None:
        return None, None, None
    return chosen["provider"], chosen["model"], user_key(user_id, chosen["provider"])


def may_bring_own(user_id=None) -> bool:
    """May this instance's users connect their own provider keys and pick models
    from them? Always on a community box; on a metered one only while the
    operator allows it."""
    return bool(edition.byok() or byok_policy()["enabled"])


def _own_models(user_id) -> list:
    """The user's picks, MINUS any whose provider they no longer have a key for.

    A pick is stored per (provider, model) and survives the key being removed —
    so a stale row kept offering models from a provider the user had never
    connected. Selecting a model you cannot run is not a choice, it is a trap.
    On a metered edition only a connected key counts; on a community box the
    legacy column still does, because that is where those keys legitimately
    live."""
    have = (own_key if not edition.byok() else user_key)
    keys = {p: bool(have(user_id, p)) for p in PROVIDERS}
    return [{"key": f"{m['provider']}:{m['model']}", "name": m["model"],
             "tagline": m["provider"], "available": True, "own": True}
            for m in selected(user_id) if keys.get(m["provider"])]


def config(user_id) -> dict:
    """What the model picker should offer, and what Settings should show.

    On a metered edition it offers BOTH: the branded tiers that run on our key,
    and whatever the user has chosen from a key of their own. Offering only the
    first meant a user could connect DeepSeek, be shown no models at all, and
    have no way to reach the thing they had just paid to connect."""
    own_ok = may_bring_own(user_id)
    if not edition.byok():
        import billing
        avail = {p: bool(admin_key(p)) for p in PROVIDERS}
        models = [{"key": m["key"], "name": m["name"], "tagline": m["tagline"],
                   "available": avail.get(m["provider"], False), "own": False}
                  for m in billing.MODELS.values()]
        if own_ok:
            models += _own_models(user_id)
        return {"byok": False, "own_keys": own_ok,
                "markup_pct": byok_policy()["markup_pct"],
                "providers": [{"provider": p, "has_key": bool(own_key(user_id, p))}
                              for p in PROVIDERS] if own_ok else [],
                "models": models}

    keys = {p: bool(user_key(user_id, p)) for p in PROVIDERS}
    return {"byok": True, "own_keys": True,
            "providers": [{"provider": p, "has_key": keys[p]} for p in PROVIDERS],
            "models": _own_models(user_id)}
