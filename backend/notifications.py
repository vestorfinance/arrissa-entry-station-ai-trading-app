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
        # underlying thing at all, so it offers the way to get one.
        if t.get("signup_url"):
            item["link"] = t["signup_url"]
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


def for_user(user_id, *, is_operator: bool = False) -> dict:
    """Everything this person can act on, worst first."""
    items = []
    items += _connection_gaps(user_id)
    items += _broker_gaps(user_id)
    # Updates are the operator's business. Showing "a module update is ready" to
    # somebody who cannot install one is telling them off for a thing they are
    # not allowed to fix.
    if is_operator:
        items += _update_items()

    rank = {"blocked": 0, "todo": 1, "info": 2}
    items.sort(key=lambda i: rank.get(i.get("severity"), 3))
    return {
        "items": items,
        "count": len(items),
        "blocked": len([i for i in items if i.get("severity") == "blocked"]),
    }
