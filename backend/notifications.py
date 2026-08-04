"""Everything outstanding, in one place, so nothing needs to be discovered.

The app can be half-configured in ways that are completely silent. Sentiment
installed with no Myfxbook connection returns nothing and says nothing. The
Exness module loaded with no account attached looks exactly like one that is
working until a trade is attempted. A module update sits there until somebody
happens to open the store. None of that is an error, so none of it shows up
anywhere an error would, and the result is a person who thinks the product is
quiet when it is actually waiting on them.

So this asks the same question of everything: is there something the operator
has to do before this works? A module that needs a connection nobody made, a
broker with no account behind it, a version newer than the one installed.

Nothing here is hard-coded per module. The kinds come from `connections.types()`,
which already merges what each installed module declares and hides what is not
loaded — so a module published next year appears in this list without core
learning anything about it. What a broker calls its sign-up page is likewise
declared by the kind, because core does not know what an Exness is.

Severity is honest: `blocked` means something installed cannot work at all,
`todo` means it would work better, `info` means there is nothing wrong. A bell
that cries wolf gets switched off.
"""


def _connection_gaps(user_id) -> list:
    """Kinds this instance offers that this user has not connected.

    Only what an installed module actually needs. Every AI provider being
    unconnected is not a problem — the app ships its own keys — but a module
    whose entire job is reading one service, with that service unconnected, is
    installed and inert."""
    import connections

    try:
        kinds = connections.types()
        mine = {c["kind"] for c in connections.listing(user_id)}
    except Exception:
        return []

    out = []
    for t in kinds:
        kind = t.get("kind")
        if kind in mine:
            continue
        # A `managed_by` kind is NOT a row in the connections table. A broker
        # login is deliberately not stored there — the password is used once for
        # a session and thrown away — so asking that table whether Exness is
        # connected returns "no" for an account that is connected and trading.
        # The broker seam is the only thing that knows.
        if t.get("managed_by"):
            try:
                import brokers
                if brokers.has_connection(user_id, kind):
                    continue
            except Exception:
                continue          # cannot tell: say nothing rather than nag
        # A kind that exists only because a module is loaded is a kind that
        # module is waiting on. That is the whole signal: nobody installs a
        # module in order to leave it unconnected.
        needs = t.get("requires_module")
        if not needs:
            continue
        item = {
            "id": f"connect:{kind}",
            "kind": "connection",
            "severity": "blocked",
            # The MODULE is installed; the connection is the thing missing.
            # "Myfxbook is installed" is not true of anything and reads as a
            # confusion about what was bought. The blurb below names the module
            # it serves, which is the part somebody needs to understand why
            # they are being asked.
            "title": f"{t['name']} is not connected",
            "body": t.get("blurb") or f"{t['name']} cannot do anything until it is connected.",
            "action": "Connect",
            "to": "/connections",
            "logo": t.get("logo"),
            "mark": t.get("mark"),
            "tone": t.get("tone"),
        }
        # A broker is the one kind that can be blocked by not having the
        # underlying thing at all, so it offers the way to get one. TradeLocker
        # is a platform rather than a broker, so it offers several.
        if t.get("signup_url"):
            item["link"] = t["signup_url"]
            item["link_label"] = t.get("signup_label") or "Open an account"
        if t.get("signup_options"):
            item["links"] = t["signup_options"]
            item["link_label"] = t.get("signup_label") or "Open an account"
        out.append(item)
    return out


def _update_items() -> list:
    """Versions newer than what is installed, and why one cannot be taken."""
    try:
        import catalog
        view = catalog.view()
    except Exception:
        return []

    out = []
    core = view.get("core") or {}
    if core.get("update_available"):
        out.append({
            "id": f"core:{core.get('latest')}",
            "kind": "update", "severity": "info",
            "title": f"EntryStation {core.get('latest')} is available",
            "body": f"This instance is on {core.get('version')}.",
            "action": "See what changed", "to": "/modules",
        })

    waiting = [m for m in view.get("modules", []) if m.get("update_available")]
    takeable = [m for m in waiting if m.get("can_update")]
    blocked = [m for m in waiting if not m.get("can_update")]
    if takeable:
        names = ", ".join(m["name"] for m in takeable[:3])
        more = f" and {len(takeable) - 3} more" if len(takeable) > 3 else ""
        out.append({
            "id": "updates:" + ",".join(sorted(m["id"] + m["version"] for m in takeable)),
            "kind": "update", "severity": "info",
            "title": f"{len(takeable)} module update{'s' if len(takeable) != 1 else ''} ready",
            "body": f"{names}{more}.",
            "action": "Update", "to": "/modules",
        })
    for m in blocked:
        # Its own row rather than a count: a lapsed subscription is a decision
        # to make about one module, not a number to glance at.
        out.append({
            "id": f"lapsed:{m['id']}",
            "kind": "licence", "severity": "todo",
            "title": f"{m['name']} update needs a live subscription",
            "body": m.get("update_blocked") or "",
            "action": "Renew", "to": "/modules",
        })
    return out


def _broker_gaps(user_id) -> list:
    """A broker connected, but with no account behind it.

    Connecting is not the same as having something to trade. Asked of each
    broker through the seam, so core never names one."""
    out = []
    try:
        import brokers
        providers = brokers.providers()
    except Exception:
        return out

    for bid, p in providers.items():
        try:
            if not getattr(p, "has_connection", None) or not p.has_connection(user_id):
                continue                      # not connected: the other check has it
            view = getattr(p, "accounts_view", None)
            if not view:
                continue
            accounts = (view(user_id) or {}).get("accounts") or []
            if accounts:
                continue
        except Exception:
            continue
        out.append({
            "id": f"accounts:{bid}",
            "kind": "connection", "severity": "blocked",
            "title": f"{getattr(p, 'name', bid.title())} has no accounts",
            "body": "The connection worked but no trading accounts came back.",
            "action": "Open accounts", "to": "/accounts",
        })
    return out


def _ai_gap(user_id) -> list:
    """No model the app can actually call.

    The chat says so when you open it, the agents say so when they run, and
    neither is anywhere you look BEFORE trying. On a Community box this is the
    single most common reason the product appears to do nothing: nothing is
    broken, there is simply no key behind any model."""
    try:
        import ai_keys
        cfg = ai_keys.config(user_id)
    except Exception:
        return []

    models = cfg.get("models") or []
    ready = [m for m in models if m.get("available", True)]
    if ready:
        return []

    # Where the key is actually entered. This used to be Settings and moved to
    # Connections; the chat's own warning still names the old page, and sending
    # somebody to a screen that no longer has the thing is worse than saying
    # nothing.
    return [{
        "id": "ai:none",
        "kind": "config", "severity": "blocked",
        "title": "No AI model is configured",
        "body": "Arrissa and every analysis agent need a model to call. Connect a "
                "provider and pick a model.",
        "action": "Connect a provider", "to": "/connections",
    }]


def _module_faults() -> list:
    """Installed, and not running. A module that failed to load is invisible
    everywhere except a log nobody is reading."""
    out = []
    try:
        import modules as module_system
        rows = (module_system.status() or {}).get("modules") or []
    except Exception:
        return out
    for m in rows:
        if m.get("status") == "error" or m.get("error"):
            out.append({
                "id": f"module-error:{m['id']}",
                "kind": "module", "severity": "blocked",
                "title": f"{m.get('name') or m['id']} failed to load",
                "body": (m.get("error") or "")[:180],
                "action": "Open the store", "to": "/modules",
            })
        elif m.get("status") == "disabled":
            out.append({
                "id": f"module-off:{m['id']}",
                "kind": "module", "severity": "todo",
                "title": f"{m.get('name') or m['id']} is switched off",
                "body": "It is installed but not running, so nothing uses it.",
                "action": "Turn it on", "to": "/modules",
            })
    return out


def _mail_gap() -> list:
    """No SMTP, on an edition that actually sends email.

    Community never does. Sign-up returns the verification code on screen when
    there is no mail server, because the one person signing up is the person
    standing there; licence receipts come from the store, not from the box that
    bought something. So a self-hosted install has nothing to send and nobody to
    send it to, and telling its owner to configure a mail server is inventing a
    chore to complete a checklist."""
    try:
        import edition
        if edition.is_community():
            return []
    except Exception:
        pass
    try:
        import mailer
        mailer._smtp()               # noqa: SLF001 — raises when unconfigured
        return []
    except RuntimeError:
        pass                         # unconfigured: that is the whole point
    except Exception:
        return []                    # no database, no opinion
    return [{
        "id": "smtp:none",
        "kind": "config", "severity": "todo",
        "title": "No mail server configured",
        "body": "Licence receipts and verification emails cannot be sent. A "
                "single-user install works without one.",
        "action": "Set it up", "to": "/settings",
    }]


def for_user(user_id, *, is_operator: bool = False) -> dict:
    """Everything this person can act on, worst first."""
    items = []
    items += _connection_gaps(user_id)
    items += _broker_gaps(user_id)
    items += _ai_gap(user_id)
    # Updates are the operator's business. Showing "a module update is ready" to
    # somebody who cannot install one is telling them off for a thing they are
    # not allowed to fix.
    if is_operator:
        items += _update_items()
        items += _module_faults()
        items += _mail_gap()

    rank = {"blocked": 0, "todo": 1, "info": 2}
    items.sort(key=lambda i: rank.get(i.get("severity"), 3))
    return {
        "items": items,
        "count": len(items),
        "blocked": len([i for i in items if i.get("severity") == "blocked"]),
    }
