"""
Economic Calendar — the module's single entry point.

Everything this capability contributes is registered here, in one place: the
fetcher, three API routes, the chat-agent tool, the flow node, and the provider
other code consumes it through. When this module is not installed, none of that
exists — which is the whole point, and why core may never `import econ` directly.

The fetcher itself is unchanged from when it lived in core; it moved file, not
behaviour.
"""
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))                       # so `import econ` finds ours

import econ                                          # noqa: E402  the fetcher


# ── API ────────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/v1", tags=["calendar"])


def _api_user(api_key: str):
    """Core owns authentication; the module borrows it rather than inventing its
    own. A module that could bypass auth would be a module that must be trusted."""
    import trading_api
    return trading_api.api_user(api_key)


@router.get("/calendar")
def calendar(
    api_key: str = Query(...),
    symbol: str = Query(None),
    currency: str = Query(None),
    impact: str = Query(None),
    range: str = Query(None),
    hours: int = Query(0),
    days: int = Query(0),
    since: str = Query(None),
    until: str = Query(None),
    limit: int = Query(50),
):
    """Scheduled economic releases and the instruments they move."""
    _api_user(api_key)
    res = econ.query(symbol=symbol, currency=currency, impact=impact, range=range,
                     hours=hours, days=days, since=since, until=until, limit=limit)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@router.get("/calendar/next")
def calendar_next(api_key: str = Query(...), symbol: str = Query(None),
                  currency: str = Query(None), limit: int = Query(5)):
    """The next releases due, soonest first."""
    _api_user(api_key)
    return econ.next_events(symbol=symbol, currency=currency, limit=limit)


@router.get("/calendar/status")
def calendar_status(api_key: str = Query(...)):
    """Is the calendar still arriving?"""
    _api_user(api_key)
    return econ.status()


# ── chat-agent tool ────────────────────────────────────────────────────────────
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Instrument whose events to return, e.g. 'GBPUSD', 'gold'."},
        "currency": {"type": "string", "description": "Filter by currency, e.g. 'USD,EUR'."},
        "impact": {"type": "string", "description": "high | moderate | low (comma-separated)."},
        "range": {"type": "string", "description": "today | tomorrow | this_week."},
        "hours": {"type": "integer", "description": "The next N hours."},
        "days": {"type": "integer", "description": "The next N days."},
        "limit": {"type": "integer"},
    },
    "required": [],
}


def _tool(args, **_kw):
    return econ.query(symbol=args.get("symbol"), currency=args.get("currency"),
                      impact=args.get("impact"), range=args.get("range"),
                      hours=args.get("hours", 0), days=args.get("days", 0),
                      limit=args.get("limit", 50))


# ── flow node ──────────────────────────────────────────────────────────────────
def _node(text, context, ctx, nv):
    """The node reads the same query the tool does; an LLM turns the node's free
    text into its parameters, exactly as core's data nodes do."""
    import analysis_agent as eng
    p = eng._params(ctx, nv, "economic calendar",
                    "Fields: symbol (optional); currency (e.g. USD,EUR); impact "
                    "(high|moderate|low); range (today|tomorrow|this_week); hours (int); "
                    "days (int); limit (int).",
                    text, context, {"range": "today"})
    return econ.query(symbol=p.get("symbol"), currency=p.get("currency"),
                      impact=p.get("impact"), range=p.get("range"),
                      hours=int(p.get("hours") or 0), days=int(p.get("days") or 0),
                      limit=int(p.get("limit") or 50))


CATALOG = ("economic-calendar — scheduled events; values.text (e.g. \"today's high-impact "
           "USD events\").\n")


# ── registration ───────────────────────────────────────────────────────────────
def register(registry, module_id):
    """Called once by the loader. Everything this module adds, added here."""
    registry.routes(router, module=module_id)

    registry.tool(
        "economic_calendar",
        "Scheduled economic releases (Forex Factory + Investing) with the instruments each "
        "event moves. Ask for a symbol, a currency, an impact level or a window — 'today', "
        "'this_week', the next N hours. Use it before trading around a print.",
        TOOL_SCHEMA, _tool, module=module_id)

    registry.node(
        "economic-calendar", "economicCalendar", _node,
        palette={"label": "Economic Calendar", "sub": "Read scheduled economic events",
                 "icon": "CalendarClock", "tone": "calendar", "model": True,
                 "args": [{"name": "text", "type": "text", "required": True}],
                           "api_keys": "symbol · currency (USD,EUR) · impact (high|moderate|low) · range (today|tomorrow|this_week) · hours · days · limit",
                           "api_example": "currency=USD&impact=high&range=today",
                           "api_doc": [{"key": "symbol", "values": ["XAUUSD", "EURUSD"]}, {"key": "currency", "values": ["USD", "EUR", "GBP", "USD,EUR"]}, {"key": "impact", "values": ["high", "moderate", "low"]}, {"key": "range", "values": ["today", "tomorrow", "this_week"]}, {"key": "hours", "values": ["6", "24"]}, {"key": "days", "values": ["1", "7"]}, {"key": "limit", "values": ["10", "50"]}]},
        opinion=True, catalog=CATALOG, values=("text",), module=module_id)

    registry.worker("economic-calendar-fetcher", econ.start_worker,
                    stop=econ.stop_fetcher, module=module_id)

    # How CORE consumes this module: by asking, never by importing.
    registry.provider("calendar", econ, module=module_id)
