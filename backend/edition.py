"""
Which edition this deployment is — the one place the difference is expressible.

Two editions, one codebase:

  · **cloud** — entrystation.com. Many tenants on one instance, metered by
    subscription and credits. The operator is us; a user is a customer.

  · **community** — someone's own box. One person, who is simultaneously the
    user, the operator and the owner. Nobody bills them, because there is nobody
    to bill: they are paying for their own server and their own AI keys.

Every gate in the app that asks "may this person do this" resolves through here.
That matters because the gates were written for the cloud, and read as a
subscription check — which in Community would lock the owner out of their own
software. A Community user with no plan is not a freeloader; there is no plan.

Set with the ENTRYSTATION_EDITION environment variable. Default is `cloud`, so
the hosted deployment keeps behaving exactly as it does today and only a
deliberate act changes it.
"""
from __future__ import annotations

import os

NAME = (os.getenv("ENTRYSTATION_EDITION") or "cloud").strip().lower()
if NAME not in ("cloud", "community"):
    NAME = "cloud"


def is_community() -> bool:
    return NAME == "community"


def metered() -> bool:
    """Does anyone pay per use here? Cloud: yes. Community: no.

    This is the flag that stops a Community user being 402'd out of chat, test
    runs, agent builds, voice and cron suggestions — none of which cost US
    anything on their machine."""
    return not is_community()


def byok() -> bool:
    """Does the user bring their own AI keys?

    Cloud runs everything on the app's keys and sells the result by the credit.
    Community has no app keys to run on — the operator holds their own OpenAI or
    Anthropic account, and it is their bill."""
    return is_community()


def multi_tenant() -> bool:
    """Are there other people's accounts on this instance to protect?"""
    return not is_community()


def everyone_is_owner() -> bool:
    """In Community the single user IS the operator. There is no admin to
    escalate to and nobody else to keep out, so the owner-only surfaces — the
    Modules page above all — belong to them."""
    return is_community()


def _active_modules() -> list:
    """Which modules are loaded and serving RIGHT NOW.

    The frontend needs this to stop calling endpoints that do not exist. It used
    to ask for /api/exness/accounts on every page load whether or not the Exness
    module was installed, which is a 404 in the console on every single load and
    a real request the server has to refuse."""
    try:
        import registry
        return sorted(registry.modules())
    except Exception:
        return []


def has_admin_console() -> bool:
    """Is there an admin CONSOLE here?

    Not the same question as "is this person the owner". The console exists to
    manage OTHER PEOPLE — users, plans, credits, suspensions, who may register.
    On a single-user box there is nobody else, so it has nothing to administer,
    and the handful of settings on it that DO still matter belong in that
    person's own Settings rather than behind a door marked Admin."""
    return multi_tenant()


def capabilities(user_email: str, billing_state: dict | None = None) -> dict:
    """What this user may actually reach, resolved once and sent to the frontend.

    The frontend used to ask `billing.developer` for the API guides and `admin`
    for the Modules page. Both are cloud questions. Asking them directly is what
    left a Community owner staring at an empty menu on software they installed
    themselves."""
    import admin_api

    owner = everyone_is_owner() or admin_api._is_admin(user_email)
    b = billing_state or {}
    subscribed = bool(b.get("active"))
    installed = _active_modules()

    if is_community():
        # Their machine, their keys, their data. The only thing that can stop
        # them is a capability the instance genuinely does not have.
        return {"edition": NAME, "owner": True, "metered": False,
                "chat": True, "guides": True, "modules": True, "byok": True,
                # `owner` is true and `admin` is false on purpose: they own the
                # instance, and there is no console because there is nobody else
                # on it. What the console held that still applies to them moves
                # into Settings.
                "billing": False, "admin": False, "active_modules": installed}

    return {"edition": NAME, "owner": owner, "metered": True,
            "chat": subscribed,
            "guides": bool(b.get("developer")),
            # A module is arbitrary code in this process. On a shared instance
            # that can only ever be the operator's call, never a tenant's.
            "modules": owner, "byok": False,
            "billing": True, "admin": owner, "active_modules": installed}
