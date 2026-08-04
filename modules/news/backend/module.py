"""Market news — impact-scored headlines, as a module."""
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import news  # noqa: E402

router = APIRouter(prefix="/api/v1", tags=["news"])


def _api_user(api_key: str):
    import trading_api
    return trading_api.api_user(api_key)


@router.get("/news")
def news_query(api_key: str = Query(...), symbol: str = Query(None), impact: str = Query(None),
               min_score: int = Query(None), source: str = Query(None),
               category: str = Query(None), q: str = Query(None), range: str = Query(None),
               minutes: int = Query(0), hours: int = Query(0), days: int = Query(0),
               since: str = Query(None), until: str = Query(None), full: bool = Query(False),
               limit: int = Query(50), order: str = Query("desc")):
    """Impact-scored market news, read from the database — this never fetches."""
    _api_user(api_key)
    res = news.query(symbol=symbol, impact=impact, min_score=min_score, source=source,
                     category=category, q=q, range=range, minutes=minutes, hours=hours,
                     days=days, since=since, until=until, full=full, limit=limit, order=order)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


@router.get("/news/latest")
def news_latest(api_key: str = Query(...), symbol: str = Query(None),
                impact: str = Query(None), limit: int = Query(10)):
    """The most recent articles, newest first."""
    _api_user(api_key)
    return news.query(symbol=symbol, impact=impact, limit=limit)


@router.get("/news/status")
def news_status(api_key: str = Query(...)):
    """Is news still arriving, and from which sources?"""
    _api_user(api_key)
    return news.status()


@router.get("/news/impact")
def news_impact(api_key: str = Query(...), symbol: str = Query(None),
                impact: str = Query(None), min_score: int = Query(None),
                source: str = Query(None), category: str = Query(None), q: str = Query(None),
                range: str = Query(None), minutes: int = Query(0), hours: int = Query(0),
                days: int = Query(0), since: str = Query(None), until: str = Query(None),
                compare: bool = Query(True)):
    """How heavy the news is over a period, and how that compares with the period
    immediately before it.

    Counting articles cannot answer "is today busier than yesterday" — ten filler
    pieces are not louder than one central-bank decision. The headline number is
    the SUM of impact scores, with the count and the average beside it so a spike
    from one huge story is distinguishable from a spike from fifty small ones.

    The comparison window is the same LENGTH as the one asked for, ending where
    it begins. Comparing "today" against a fixed 24 hours would call every
    morning quiet."""
    _api_user(api_key)
    return news.impact(symbol=symbol, impact=impact, min_score=min_score, source=source,
                       category=category, q=q, range=range, minutes=minutes, hours=hours,
                       days=days, since=since, until=until, compare=compare)


TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Instrument the article is about, e.g. XAUUSD."},
        "impact": {"type": "string", "description": "high | medium | low (comma-separated)."},
        "min_score": {"type": "integer", "description": "Floor on the 0–100 impact score."},
        "hours": {"type": "integer"}, "days": {"type": "integer"},
        "range": {"type": "string", "description": "today | yesterday | this_week | last_7_days."},
        "q": {"type": "string", "description": "Text search over title, description and body."},
        "limit": {"type": "integer"},
    },
    "required": [],
}


def _tool(args, **_kw):
    return news.query(symbol=args.get("symbol"), impact=args.get("impact"),
                      min_score=args.get("min_score"), q=args.get("q"),
                      range=args.get("range"), hours=args.get("hours", 0),
                      days=args.get("days", 0), limit=args.get("limit", 50))


IMPACT_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Only news about this instrument."},
        "impact": {"type": "string", "description": "high | medium | low."},
        "range": {"type": "string", "description": "today | yesterday | this_week | last_7_days."},
        "hours": {"type": "integer", "description": "The last N hours instead of a range."},
        "days": {"type": "integer", "description": "The last N days instead of a range."},
        "q": {"type": "string", "description": "Text search over title, description and body."},
        "compare": {"type": "boolean",
                    "description": "Compare with the preceding window of the same length "
                                   "(default true) — this is what answers 'busier than yesterday'."},
    },
    "required": [],
}


def _impact_tool(args, **_kw):
    return news.impact(symbol=args.get("symbol"), impact=args.get("impact"),
                       range=args.get("range"), hours=int(args.get("hours") or 0),
                       days=int(args.get("days") or 0), q=args.get("q"),
                       compare=args.get("compare", True))


def _node(text, context, ctx, nv):
    import analysis_agent as eng
    p = eng._params(ctx, nv, "market news",
                    "Fields: symbol (optional); impact (high|medium|low); min_score (0-100); "
                    "hours (int); days (int); range (today|yesterday|this_week); q (text search); "
                    "limit. ALSO: measure (true when the ask is about how HEAVY or BUSY the news "
                    "is, or a comparison with an earlier period — 'more news than yesterday', "
                    "'is today quiet' — rather than for the headlines themselves).",
                    text, context, {"hours": 12})
    # One node, two questions. Asking for the headlines and asking how heavy the
    # day is are the same source and the same filters; splitting them into two
    # nodes would make a flow author choose between them before they knew which
    # they wanted.
    if p.get("measure"):
        return _impact_tool(p)
    return _tool(p)


CATALOG = ("news — impact-scored headlines, AND how heavy the news is over a period versus the "
           "period before it; values.text (e.g. 'high-impact gold news, last 12h', or 'is there "
           "more news today than yesterday' for the comparison).\n")


def register(registry, module_id):
    registry.routes(router, module=module_id)
    registry.tool("news_impact",
                  "How HEAVY the news is over a period, and how that compares with the period "
                  "before it — the answer to 'is there more news today than yesterday', 'is it "
                  "quiet', 'did the flow pick up'. Returns the SUM of impact scores (volume and "
                  "severity in one number), the counts by level, the busiest instruments, and a "
                  "change against the preceding window of the same length with a plain "
                  "heavier/lighter/similar verdict. Use this instead of counting articles "
                  "yourself: ten filler pieces are not louder than one central-bank decision.",
                  IMPACT_SCHEMA, _impact_tool, module=module_id)
    registry.tool("news",
                  "Market news, impact-scored, with the instruments each article concerns. Filter "
                  "by symbol, impact, score or a time window. Reads the stored feed — it never "
                  "blocks on a fetch.",
                  TOOL_SCHEMA, _tool, module=module_id)
    registry.node("news", "news", _node,
                  palette={"label": "News", "sub": "Interact with the News API",
                           "icon": "Newspaper", "tone": "news", "model": True,
                           "args": [{"name": "text", "type": "text", "required": True}],
                           "api_keys": "symbol · impact (high|medium|low) · min_score (0-100) · hours · days · range (today|yesterday|this_week) · q · limit",
                           "api_example": "symbol=XAUUSD&impact=high&hours=24&limit=20",
                           "api_doc": [{"key": "symbol", "values": ["XAUUSD", "EURUSD", "US30"], "note": "instrument to filter to"}, {"key": "impact", "values": ["high", "medium", "low"]}, {"key": "min_score", "values": ["50", "70", "90"], "note": "0-100"}, {"key": "hours", "values": ["6", "12", "24"]}, {"key": "days", "values": ["1", "3", "7"]}, {"key": "range", "values": ["today", "yesterday", "this_week"]}, {"key": "q", "values": ["tariff", "rate cut"], "note": "free text search"}, {"key": "limit", "values": ["10", "20", "50"]}, {"key": "measure", "values": ["true"], "note": "how heavy the news is, not the headlines"}]},
                  opinion=True, catalog=CATALOG, values=("text",), module=module_id)
    registry.worker("news-fetcher", news.start_fetcher,
                    stop=news.stop_fetcher, module=module_id)
    registry.provider("news", news, module=module_id)
