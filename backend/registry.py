"""
The registry — the one place a capability announces itself.

A capability in this app is never one thing. "Fed Watch" is a background fetcher,
an API route, a chat-agent tool, a flow node, a palette entry, a guide page and a
row of settings — seven registrations that must all appear together and all
disappear together. Today each lives in a different hardcoded list, which is why
adding a source means editing seven files and removing one is not really possible.

This is that list, once. Core registers through it and so do modules, so there is
one path rather than two, and `forget(module)` can undo exactly what a module
added — which is what makes uninstall a real operation instead of a claim.

    registry.tool("fedwatch", "CME rate-cut odds…", schema, handler, module="fedwatch")
    registry.node("fed-watch", "fedWatch", handler, palette={…}, module="fedwatch")
    registry.routes(my_router, module="fedwatch")
    registry.worker("fedwatch-fetcher", start_fn, module="fedwatch")
    registry.guide(manifest, module="fedwatch")

Registration happens at import/boot on one thread, so nothing here locks. If that
ever stops being true — hot-loading a module into a running app — this is the
file that needs a lock, and the reason will be right here.
"""
from __future__ import annotations

# name -> {"schema": dict, "handler": callable, "module": str|None}
_tools: dict = {}
# kind -> {"type", "handler", "palette", "opinion", "catalog", "module"}
_nodes: dict = {}
_routers: list = []          # [{"router": APIRouter, "module": str|None}]
_workers: list = []          # [{"name", "start", "module"}]
_guides: dict = {}           # guide id -> {"manifest": dict, "module": str|None}
_settings: dict = {}         # key -> {"spec": dict, "module": str|None}
_providers: dict = {}        # name -> {"obj": any, "module": str|None}  (see provider())
_notes: list = []            # [{"text": str, "module": str|None}]  (see system_note())
_assets: dict = {}           # module id -> Path of a directory it may serve from
_conn_types: list = []       # [{"spec": dict, "module": str|None}]  (see connection_type())

# The running FastAPI app, handed over by main. Starlette matches each request
# against `app.router.routes`, which is an ordinary list — so a router can be
# mounted and unmounted while the app is serving. That is the whole reason
# enabling and disabling a module does not need a restart.
_app = None


def bind_app(app):
    """Give the registry the live app, so routes can be mounted after boot."""
    global _app
    _app = app
    for r in _routers:                 # anything registered before the handover
        _mount(r["router"])


def _mount(router):
    if _app is None:
        return False
    _app.include_router(router)
    _app.openapi_schema = None         # the cached schema no longer describes it
    return True


def _unmount(router):
    """Remove a router's paths from the live app.

    Starlette has no `remove_router`, but it does not need one: the routes it
    matches against are a plain list, and a route removed from it stops matching
    on the very next request."""
    if _app is None:
        return 0
    # A router's prefix is applied when each route is ADDED to it, so
    # `route.path` is already the full path the app mounted. Prefixing again
    # would build a path that matches nothing, and silently unmount nothing.
    full = {p for p in (getattr(r, "path", None) for r in router.routes) if p}
    before = len(_app.router.routes)
    _app.router.routes[:] = [r for r in _app.router.routes
                             if getattr(r, "path", None) not in full]
    _app.openapi_schema = None
    return before - len(_app.router.routes)

# What a module contributed, so it can be taken away again exactly.
_by_module: dict = {}


def _own(module: str | None, kind: str, key):
    if not module:
        return
    _by_module.setdefault(module, []).append((kind, key))


# ── registering ────────────────────────────────────────────────────────────────
def tool(name: str, description: str, input_schema: dict, handler, *, module=None):
    """A chat-agent tool: what the model sees, and what runs when it calls it.

    Schema and handler are registered TOGETHER. They are two lists today — 40
    entries in TOOLS and 40 branches in execute_tool — which is one registry
    written twice, and the way a tool ends up advertised but not dispatchable."""
    _tools[name] = {"schema": {"name": name, "description": description,
                               "input_schema": input_schema},
                    "handler": handler, "module": module}
    _own(module, "tool", name)


def node(kind: str, type_: str, handler, *, palette: dict = None, opinion: bool = False,
         catalog: str = None, values: tuple = (), module=None):
    """A flow node: its engine handler, its canvas identity, and how the AI
    builder is told about it.

    `values` are the node's own setting keys, so the chat agent's authoring tools
    round-trip them instead of silently dropping what they do not recognise."""
    _nodes[kind] = {"type": type_, "handler": handler, "palette": palette or {},
                    "opinion": opinion, "catalog": catalog, "values": tuple(values),
                    "module": module}
    _own(module, "node", kind)


def routes(router, *, module=None):
    """A FastAPI router. Mounted straight away if the app is already running,
    otherwise at boot when `bind_app` hands the app over."""
    _routers.append({"router": router, "module": module})
    _own(module, "router", router)
    _mount(router)


def worker(name: str, start, *, stop=None, module=None):
    """A background worker. `start` is called once and must return immediately —
    spawn your own thread, as every fetcher does.

    `stop` is how a module gets switched off without a restart. Without one, the
    thread outlives a disable: everything the module REGISTERED goes, so nothing
    reads what it writes, but it keeps writing. Provide one."""
    _workers.append({"name": name, "start": start, "stop": stop, "module": module})
    _own(module, "worker", name)
    if _started_workers:               # switched on after boot → start it now
        _start_one(_workers[-1])


_started_workers = False


def _start_one(w) -> bool:
    try:
        w["start"]()
        return True
    except Exception as e:
        print(f"[modules] worker {w['name']} failed to start: {e!r}", flush=True)
        return False


def start_workers() -> int:
    """Start everything registered so far. Called once, from the app's startup."""
    global _started_workers
    _started_workers = True
    return sum(1 for w in _workers if _start_one(w))


# The vocabulary a guide may use. Closed on purpose: a module names a widget,
# a column format or an icon, and core draws it. An open-ended list would mean a
# module could only be rendered by a frontend that already knew about it, which
# is the exact thing the format exists to avoid. See MODULE-GUIDES.md.
# "status" is for an endpoint that answers with ONE object rather than a list —
# a health check, a connection's state, the result of an action. Without it a
# module had to invent a one-row array to say a single thing.
GUIDE_SECTION_TYPES = {"table", "list", "status"}
GUIDE_FORMATS = {"text", "code", "number", "signed", "percent", "list", "time", "badge"}
GUIDE_GROUPS = {"trade", "analysis", "modules"}


def validate_guide(g: dict) -> dict:
    """Check a guide manifest and say exactly what is wrong with it.

    A guide that renders blank is the worst outcome for a module author: nothing
    failed, nothing appeared, and there is nowhere to look. So it is checked when
    it loads, and a mistake becomes a module that reports a specific error."""
    def bad(msg):
        raise ValueError(f"guide {g.get('id') or '(no id)'}: {msg}")

    if not isinstance(g, dict):
        bad("must be a JSON object")
    if not (g.get("id") or "").strip():
        bad("needs an `id` (it becomes the page URL, /module/<id>)")
    if not (g.get("title") or g.get("heading")):
        bad("needs a `title` (shown in the browser tab and page header)")

    nav = g.get("nav") or {}
    if not isinstance(nav, dict):
        bad("`nav` must be an object")
    if nav and not nav.get("label"):
        bad("`nav.label` is required when `nav` is present — it is the menu entry")
    grp = nav.get("group", "modules")
    if grp not in GUIDE_GROUPS:
        bad(f"`nav.group` must be one of {sorted(GUIDE_GROUPS)}, not {grp!r}")

    def check_actions(items, where):
        for i, a in enumerate(items or []):
            if not a.get("label"):
                bad(f"{where}[{i}] needs a `label`")
            if not (a.get("to") or a.get("href")):
                bad(f"{where}[{i}] ({a['label']}) needs `to` (a page in this app) "
                    f"or `href` (an external link)")
            if a.get("to") and not str(a["to"]).startswith("/"):
                bad(f"{where}[{i}] `to` must be an in-app path starting with /")

    check_actions(g.get("actions"), "actions")

    for i, ep in enumerate(g.get("endpoints") or []):
        where = f"endpoints[{i}]"
        if not ep.get("path"):
            bad(f"{where} needs a `path`")
        if not ep.get("title"):
            bad(f"{where} ({ep['path']}) needs a `title`")
        for j, prm in enumerate(ep.get("params") or []):
            if not prm.get("name"):
                bad(f"{where}.params[{j}] needs a `name`")
            if prm.get("level") not in (None, "required", "optional"):
                bad(f"{where}.params[{j}] `level` must be 'required' or 'optional'")

    for i, sec in enumerate(g.get("sections") or []):
        where = f"sections[{i}]"
        typ = sec.get("type", "table")
        if typ not in GUIDE_SECTION_TYPES:
            bad(f"{where} type {typ!r} is not one of {sorted(GUIDE_SECTION_TYPES)}")
        if not sec.get("endpoint"):
            bad(f"{where} needs an `endpoint` to call")
        if not sec.get("title"):
            bad(f"{where} needs a `title`")
        if typ == "table":
            cols = sec.get("columns") or []
            if not cols:
                bad(f"{where} is a table and needs `columns`")
            for j, c in enumerate(cols):
                if not c.get("key"):
                    bad(f"{where}.columns[{j}] needs a `key`")
                if c.get("format", "text") not in GUIDE_FORMATS:
                    bad(f"{where}.columns[{j}] format {c.get('format')!r} is not one of "
                        f"{sorted(GUIDE_FORMATS)}")
        if typ == "list" and not (sec.get("item") or {}).get("title"):
            bad(f"{where} is a list and needs `item.title`")
        if typ == "status" and not (sec.get("lead") or sec.get("footer")):
            bad(f"{where} is a status and needs a `lead` or `footer` to say what it found")
        for j, inp in enumerate(sec.get("inputs") or []):
            if not inp.get("name"):
                bad(f"{where}.inputs[{j}] needs a `name` — it becomes the query parameter")
        check_actions([sec["action"]] if sec.get("action") else None, f"{where}.action")
    return g


def guide(manifest: dict, *, module=None):
    """A declarative guide page — see ModuleGuide on the frontend. Data, not code,
    so a module can add a page to a bundle that was built without it."""
    gid = manifest.get("id") or module
    if not gid:
        raise ValueError("a guide needs an id")
    manifest.setdefault("id", gid)
    validate_guide(manifest)
    _guides[gid] = {"manifest": manifest, "module": module}
    _own(module, "guide", gid)


def setting(key: str, spec: dict, *, module=None):
    """One configurable value, surfaced in Settings."""
    _settings[key] = {"spec": spec, "module": module}
    _own(module, "setting", key)


def provider(name: str, obj, *, module=None):
    """A named capability other code may CONSUME — `registry.get("calendar")`.

    This is the rule that makes extraction real: CORE MAY NEVER IMPORT A MODULE.
    Seven core files did `import econ` directly, and the watch list did it outside
    its own try — so an uninstalled calendar would not have degraded, it would
    have crashed the daily build.

    Asking the registry means core states its dependency as optional and handles
    the absence, which is the only honest way for a feature to be removable."""
    _providers[name] = {"obj": obj, "module": module}
    _own(module, "provider", name)


def assets(directory, *, module=None):
    """A directory of static files the module may serve — a logo, an image in
    its guide. One directory per module, resolved once here so nothing later can
    be talked into serving a path outside it."""
    from pathlib import Path
    _assets[module] = Path(directory).resolve()
    _own(module, "assets", module)


def asset_dir(module: str):
    return _assets.get(module)


def connection_type(spec: dict, *, module=None):
    """A kind of thing the user can connect to — see backend/connections.py.

    A module that talks to an outside service needs somewhere to keep the
    credential, and the Connections page is that somewhere. Declaring the shape
    rather than shipping a form means a new integration is a dict."""
    _conn_types.append({"spec": spec, "module": module})
    _own(module, "conn_type", spec.get("kind"))


def connection_types() -> list:
    """Each spec, stamped with the module that registered it.

    The registry already knows which module a connection belongs to, and was
    keeping it to itself — so a module-provided kind arrived with no
    `requires_module` and nothing could tell that Telegram unconnected meant the
    Telegram module was sitting there doing nothing. Asking every module to
    repeat what the registration already says is a rule somebody eventually
    forgets; taking it from the registration is a rule nobody can."""
    return [{**c["spec"], **({"requires_module": c["module"]} if c.get("module") else {})}
            for c in _conn_types if c]


def system_note(text: str, *, module=None):
    """A line for the assistant's system prompt, contributed by a module.

    Core must not describe a capability it may not have — telling the model about
    a tool that isn't installed produces confident references to something that
    cannot be called. So the module that provides the capability also provides
    the sentence that explains it, and both appear or neither does."""
    _notes.append({"text": text, "module": module})
    _own(module, "note", len(_notes) - 1)


def notes() -> str:
    """Every module's system-prompt contribution, ready to append."""
    return "".join(" " + n["text"] for n in _notes if n)


def get(name: str):
    """A registered provider, or None when that module is not installed.

    Returning None rather than raising is deliberate: the caller has to write the
    degraded path, and a missing optional capability is not an error."""
    p = _providers.get(name)
    return p["obj"] if p else None


def has(name: str) -> bool:
    return name in _providers


def providers() -> list:
    return sorted(_providers)


# ── reading ────────────────────────────────────────────────────────────────────
def tool_schemas() -> list:
    """Every registered tool, in the shape the model is given."""
    return [t["schema"] for t in _tools.values()]


def has_tool(name: str) -> bool:
    return name in _tools


def dispatch(name: str, args: dict, **kw):
    """Run a registered tool. Raises KeyError if it is not registered — callers
    check `has_tool` first, so reaching here with an unknown name is a bug worth
    hearing about rather than a silent None."""
    return _tools[name]["handler"](args, **kw)


def nodes() -> dict:
    return dict(_nodes)


def node_handler(kind: str):
    n = _nodes.get(kind)
    return n["handler"] if n else None


def node_types() -> dict:
    """kind -> React Flow type, for the authoring round-trip."""
    return {k: n["type"] for k, n in _nodes.items()}


def node_values() -> tuple:
    """Every node setting key any registered node uses."""
    out = []
    for n in _nodes.values():
        out.extend(n["values"])
    return tuple(dict.fromkeys(out))


def opinion_kinds() -> set:
    return {k for k, n in _nodes.items() if n["opinion"]}


def palette() -> list:
    """Palette entries for the canvas.

    `type` travels with each entry because the canvas picks a node component by
    type — an entry without one could be listed and never drawn."""
    return [{"key": k, "type": n["type"], **n["palette"]}
            for k, n in _nodes.items() if n["palette"]]


def catalog() -> str:
    """The AI flow-builder's description of every registered node."""
    return "".join(n["catalog"] for n in _nodes.values() if n.get("catalog"))


def routers() -> list:
    return [r["router"] for r in _routers]


def workers() -> list:
    return list(_workers)


def guides() -> list:
    return [g["manifest"] for g in _guides.values()]


def settings() -> dict:
    return {k: v["spec"] for k, v in _settings.items()}


def provided_by(module: str) -> list:
    """Everything this module registered — the uninstall manifest, as strings.

    Strings, not the objects themselves: a router key IS the live APIRouter, and
    handing that to a JSON response recurses until the encoder gives up. Callers
    want it readable anyway."""
    out = []
    for kind, key in _by_module.get(module, []):
        if kind == "router":
            n = len(getattr(key, "routes", []) or [])
            out.append(f"router:{n} endpoint" + ("" if n == 1 else "s"))
        else:
            out.append(f"{kind}:{key}")
    return out


def modules() -> list:
    return sorted(_by_module)


# ── unregistering ──────────────────────────────────────────────────────────────
def forget(module: str) -> int:
    """Remove everything a module registered.

    Routers are the exception: FastAPI has no supported way to unmount one, so a
    module that added routes needs a restart to fully leave. Saying that plainly
    is better than pretending otherwise — the installer tells the user."""
    removed = 0
    for kind, key in _by_module.pop(module, []):
        if kind == "tool" and _tools.pop(key, None) is not None:
            removed += 1
        elif kind == "node" and _nodes.pop(key, None) is not None:
            removed += 1
        elif kind == "guide" and _guides.pop(key, None) is not None:
            removed += 1
        elif kind == "setting" and _settings.pop(key, None) is not None:
            removed += 1
        elif kind == "conn_type":
            before = len(_conn_types)
            _conn_types[:] = [c for c in _conn_types
                              if not (c["module"] == module and c["spec"].get("kind") == key)]
            removed += before - len(_conn_types)
        elif kind == "assets" and _assets.pop(key, None) is not None:
            removed += 1
        elif kind == "note":
            if 0 <= key < len(_notes) and _notes[key] is not None:
                _notes[key] = None      # index-stable: other modules' notes keep theirs
                removed += 1
        elif kind == "provider" and _providers.pop(key, None) is not None:
            removed += 1
        elif kind == "worker":
            for w in [w for w in _workers if w["module"] == module and w["name"] == key]:
                if w.get("stop"):
                    try:
                        w["stop"]()
                    except Exception as e:
                        print(f"[modules] worker {w['name']} would not stop: {e!r}", flush=True)
                _workers.remove(w)
                removed += 1
        elif kind == "router":
            _unmount(key)
            before = len(_routers)
            _routers[:] = [r for r in _routers if r["router"] is not key]
            removed += before - len(_routers)
    return removed


def unstoppable_workers(module: str) -> list:
    """Workers this module registered with no way to stop them.

    Everything else comes off cleanly while the app serves. A worker without a
    `stop` is the one thing a disable cannot fully undo — it stops being READ
    immediately, but it keeps running until the next restart, and the UI says so
    rather than pretending."""
    names = {key for kind, key in _by_module.get(module, []) if kind == "worker"}
    return sorted(w["name"] for w in _workers
                  if w["module"] == module and w["name"] in names and not w.get("stop"))
