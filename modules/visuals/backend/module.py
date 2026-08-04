"""
Visuals — the app draws, instead of describing.

"Show me the BTC chart" should produce a chart. "Explain the risk on this trade"
is often clearer as a card with the numbers on it than as a paragraph. And a
message to Telegram carrying a picture is worth several carrying prose.

Two ways in, one way out:

  · A CHART. Give it a symbol and it fetches the candles itself and draws them,
    with entry / stop / target ruled across if the trade is known.

  · ANYTHING ELSE. The model writes HTML and this screenshots it. That is the
    whole extensibility story — a risk breakdown, a comparison table, a summary
    card. No new node, no new endpoint, no release, just different markup.

The image is STORED and referred to by id. It is never handed back to the model
as base64: a 60 KB PNG is ~80 000 characters of token, which would cost more than
the analysis that produced it and evict the conversation to make room. The model
gets an id; the chat renders it from a URL; Telegram is handed the raw bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import db  # noqa: E402  (core)
from deps import current_user  # noqa: E402  (core)
import brand  # noqa: E402
import charts  # noqa: E402
import render  # noqa: E402

router = APIRouter(prefix="/api/v1", tags=["visuals"])

KEEP = 200          # per user; a gallery, not an archive
MAX_HTML = 200_000  # a document larger than this is a mistake, not a picture


# ── storage ────────────────────────────────────────────────────────────────────
def _save(user_id, png: bytes, *, kind="chart", title=None, width=None, height=None) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO visuals (user_id, kind, title, png, width, height) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id, created_at",
            (user_id, kind, (title or "")[:160] or None, png, width, height)).fetchone()
        # Trim here rather than on a timer: the only moment this user's list can
        # grow is the moment they add to it.
        conn.execute(
            "DELETE FROM visuals WHERE user_id = %s AND id NOT IN "
            "(SELECT id FROM visuals WHERE user_id = %s ORDER BY created_at DESC LIMIT %s)",
            (user_id, user_id, KEEP))
        conn.commit()
    return {"id": str(row["id"]), "url": f"/api/v1/visuals/{row['id']}.png",
            "kind": kind, "title": title, "bytes": len(png)}


def png_of(user_id, visual_id) -> bytes | None:
    """The raw image. This is what another module (Telegram) asks for, via the
    registry, so a picture can be attached without ever being serialised through
    the model."""
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT png FROM visuals WHERE id = %s AND user_id = %s",
                               (str(visual_id), user_id)).fetchone()
        return bytes(row["png"]) if row else None
    except Exception:
        return None


def listing(user_id, limit=30) -> list:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, title, width, height, octet_length(png) AS bytes, created_at "
            "FROM visuals WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, min(int(limit), 100))).fetchall()
    return [{**dict(r), "id": str(r["id"]), "url": f"/api/v1/visuals/{r['id']}.png"}
            for r in rows]


# ── drawing ────────────────────────────────────────────────────────────────────
def _candles(symbol, timeframe, count, account=None):
    import market
    data = market.candles(symbol, timeframe=timeframe, count=int(count), account=account)
    return data.get("candles") or [], (data.get("symbol") or symbol)


def make_chart(user_id, *, symbol, timeframe="M15", count=120, levels=None,
               title=None, note=None, width=1000, height=560, account=None) -> dict:
    rows, resolved = _candles(symbol, timeframe, count, account)
    html = charts.candles_html(rows, symbol=resolved, timeframe=timeframe, title=title,
                               note=note, levels=levels, width=width, height=height)
    # The chart's mark is drawn INSIDE the SVG (see charts.py) — appending a
    # footer to a fixed-height canvas would only push it off the bottom.
    png = render.html_to_png(html, width=width, height=height, scale=2)
    out = _save(user_id, png, kind="chart",
                title=title or f"{resolved} {timeframe}", width=width, height=height)
    return {**out, **_howto(out)}


def make_html(user_id, html, *, title=None, width=760, height=520, full_page=True) -> dict:
    if len(html or "") > MAX_HTML:
        raise HTTPException(400, "that document is too large to render")
    png = render.html_to_png(html, width=width, height=height, scale=2,
                             full_page=full_page, brand=brand.footer_html())
    out = _save(user_id, png, kind="html", title=title, width=width, height=height)
    return {**out, **_howto(out)}


# ── routes ─────────────────────────────────────────────────────────────────────
@router.get("/visuals/{visual_id}.png")
def visual_png(visual_id: str):
    """The image itself.

    UNAUTHENTICATED, like every other image route in this app, because an
    `<img src>` cannot carry a bearer token. The id is a random UUID and is the
    only way to reach it — a capability URL. Nothing is listed here and nothing
    is guessable; the listing route below does require a session."""
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT png FROM visuals WHERE id = %s", (visual_id,)).fetchone()
    except Exception:
        row = None
    if not row:
        raise HTTPException(404, "no such image")
    return Response(bytes(row["png"]), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/visuals")
def visuals_list(api_key: str = Query(...), limit: int = Query(30)):
    """The images this key's owner has made, newest first."""
    import trading_api
    row = trading_api.api_user(api_key)
    return {"visuals": listing(row["user_id"], limit)}


@router.get("/visuals/chart")
def visuals_chart(api_key: str = Query(...), symbol: str = Query(...),
                  timeframe: str = Query("M15"), count: int = Query(120),
                  entry: float = Query(None), sl: float = Query(None), tp: float = Query(None),
                  title: str = Query(None), note: str = Query(None),
                  width: int = Query(1000), height: int = Query(560),
                  account: int = Query(None)):
    """Draw a price chart and return where to find it."""
    import trading_api
    row = trading_api.api_user(api_key)
    levels = {k: v for k, v in (("entry", entry), ("sl", sl), ("tp", tp)) if v is not None}
    try:
        return make_chart(row["user_id"], symbol=symbol, timeframe=timeframe, count=count,
                          levels=levels, title=title, note=note,
                          width=_clamp(width, 320, 2000), height=_clamp(height, 200, 1400),
                          account=account)
    except render.RenderError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/visuals/html")
def visuals_html(api_key: str = Query(...), body: dict = Body(...)):
    """Screenshot your own markup. `html` is required; `width`, `height`, `title`
    and `full_page` are not."""
    import trading_api
    row = trading_api.api_user(api_key)
    try:
        return make_html(row["user_id"], body.get("html") or "", title=body.get("title"),
                         width=_clamp(body.get("width", 760), 200, 2000),
                         height=_clamp(body.get("height", 520), 100, 4000),
                         full_page=body.get("full_page", True))
    except render.RenderError as e:
        raise HTTPException(503, str(e))


@router.get("/visuals/status")
def visuals_status(api_key: str = Query(...)):
    """Whether anything can be drawn on this machine, proven by drawing."""
    import trading_api
    trading_api.api_user(api_key)
    ok, detail = render.available()
    return {"healthy": ok, "renderer": "chromium (headless)", "last_error": None if ok else detail}


@router.get("/visuals/mine")
def visuals_mine(user: dict = Depends(current_user), limit: int = 30):
    """The signed-in user's gallery — what the app's own pages read."""
    return {"visuals": listing(user["id"], limit)}


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return lo


# ── the agent's hands ──────────────────────────────────────────────────────────
CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "Instrument. Forgiving: gold, BTC, cable."},
        "timeframe": {"type": "string", "description": "M1 M5 M15 M30 H1 H4 D1 W1. Default M15."},
        "count": {"type": "integer", "description": "How many candles. Default 120."},
        "entry": {"type": "number", "description": "Rule an entry line across the chart."},
        "sl": {"type": "number", "description": "Rule a stop line across the chart."},
        "tp": {"type": "number", "description": "Rule a target line across the chart."},
        "title": {"type": "string"},
        "note": {"type": "string", "description": "Small text in the top-right corner."},
    },
    "required": ["symbol"],
}

HTML_SCHEMA = {
    "type": "object",
    "properties": {
        "html": {"type": "string", "description": "A complete standalone HTML document. "
                                                  + charts.HTML_RULES},
        "title": {"type": "string"},
        "width": {"type": "integer", "description": "CSS pixels. Default 760."},
    },
    "required": ["html"],
}


def _tool_chart(args, user_id=None, **_kw):
    if not user_id:
        return {"error": "no user in context"}
    levels = {k: args[k] for k in ("entry", "sl", "tp") if args.get(k) is not None}
    try:
        out = make_chart(user_id, symbol=args.get("symbol") or "",
                         timeframe=args.get("timeframe") or "M15",
                         count=_clamp(args.get("count", 120), 10, 600),
                         levels=levels, title=args.get("title"), note=args.get("note"))
    except Exception as e:
        return {"error": str(e)}
    return out


def _tool_html(args, user_id=None, **_kw):
    if not user_id:
        return {"error": "no user in context"}
    try:
        out = make_html(user_id, args.get("html") or "", title=args.get("title"),
                        width=_clamp(args.get("width", 760), 200, 2000))
    except HTTPException as e:
        return {"error": e.detail}
    except Exception as e:
        return {"error": str(e)}
    return out


def _howto(out) -> dict:
    """Handed back with every image.

    The markdown line is given READY TO COPY rather than as a rule to follow — a
    model told "use ![](url)" writes the placeholder surprisingly often. And an
    id it does not know what to do with is worse than no picture at all: it will
    describe the image instead of showing it."""
    return {
        "show_it": "Include this line VERBATIM so the user sees the image — the url is already "
                   "complete, never put a domain in front of it: "
                   f"![{out.get('title') or 'chart'}]({out['url']})",
        "send_it": f"To put it on Telegram, call send_telegram with image_id={out['id']}. "
                   "Never paste image data into a tool call.",
    }


# ── the flow node ──────────────────────────────────────────────────────────────
NODE_SYSTEM = (
    "You turn a trading-analysis flow's findings into ONE image, inside a flow. "
    "Decide which kind:\n"
    "  · a price chart — when the point is what price did. Answer "
    "{\"kind\":\"chart\",\"symbol\":\"…\",\"timeframe\":\"…\",\"count\":120,"
    "\"entry\":…,\"sl\":…,\"tp\":…,\"title\":\"…\"} — include entry/sl/tp ONLY if the flow "
    "actually produced them, never invented.\n"
    "  · anything else — a summary, a risk breakdown, a comparison. Answer "
    "{\"kind\":\"html\",\"html\":\"<!doctype html>…\",\"title\":\"…\",\"width\":760} with the "
    "real numbers from the flow written into it.\n"
    + charts.HTML_RULES)


def _node(text, context, ctx, nv):
    """Plain words in, a picture out — the node decides which kind is right."""
    import analysis_agent as eng
    uid = (ctx or {}).get("user_id")
    if not uid:
        return {"error": "this flow has no user to draw for"}
    # Stated wins, and skips the model entirely. This node was asking an LLM
    # which symbol to chart even when the node said `symbol=XAUUSD` — a guess
    # bought every fifteen minutes at something that was never in question.
    # The field was on the node the whole time and silently ignored here.
    stated = eng._explicit_params(nv, (context or {}).get("vars"))
    if stated and (stated.get("symbol") or stated.get("html")):
        spec = dict(stated)
        for k in ("count", "width", "height"):
            if spec.get(k) is not None:
                try:
                    spec[k] = int(float(spec[k]))
                except (TypeError, ValueError):
                    spec.pop(k)
        for k in ("entry", "sl", "tp"):
            if spec.get(k) is not None:
                try:
                    spec[k] = float(spec[k])
                except (TypeError, ValueError):
                    spec.pop(k)
    else:
        user = (f"The user's request: {context.get('request') or '(none)'}\n\n"
                f"Node instruction: {text or 'Show what this flow found.'}\n\n"
                f"Flow context: {eng._ctx_json(context)}")
        spec = eng._llm(ctx, nv, NODE_SYSTEM, user, want_json=True)
    if not isinstance(spec, dict):
        return {"error": "could not decide what to draw"
                         + (f" ({ctx.get('_llm_error')})" if ctx.get("_llm_error") else "")}
    try:
        if (spec.get("kind") or "chart") == "chart" and spec.get("symbol"):
            levels = {k: spec[k] for k in ("entry", "sl", "tp") if spec.get(k) is not None}
            return make_chart(uid, symbol=spec["symbol"],
                              timeframe=spec.get("timeframe") or "M15",
                              count=_clamp(spec.get("count", 120), 10, 600),
                              levels=levels, title=spec.get("title"),
                              note=spec.get("note"),
                              width=_clamp(spec.get("width", 1000), 320, 2000),
                              height=_clamp(spec.get("height", 560), 200, 1400))
        if spec.get("html"):
            return make_html(uid, spec["html"], title=spec.get("title"),
                             width=_clamp(spec.get("width", 760), 200, 2000))
    except Exception as e:
        return {"error": str(e)}
    return {"error": "nothing to draw — no symbol and no markup"}


CATALOG = ("visual — draw ONE image from what the flow found: a price chart, or a card/table "
           "the AI writes as HTML. values.text says what to show in plain words (e.g. 'chart "
           "gold with the entry, stop and target on it' or 'a card with the risk numbers'). "
           "Pair it with a telegram node to send the picture.\n")


def register(registry, module_id):
    registry.routes(router, module=module_id)
    registry.tool(
        "create_chart",
        "Draw a price chart as an IMAGE, for somewhere the app cannot reach. Use it when the "
        "chart is LEAVING this app — going to Telegram, being saved, or shown to someone who is "
        "not here. Inside the app chat, use show_chart instead: it is live, interactive, and "
        "costs nothing to draw, where this renders a picture in a browser and stores it. "
        "Rendering an image for a user who is looking at the app is work nobody asked for. "
        "It returns a ready-made markdown line in `show_it`; put that line in your reply "
        "verbatim and the user sees the chart.",
        CHART_SCHEMA, _tool_chart, module=module_id)
    registry.tool(
        "create_visual",
        "Turn HTML you write into an image — for anything that is not a price chart: a risk "
        "breakdown, a position summary, a comparison table, a scorecard. Use it when a table "
        "or a set of numbers would read better as a picture, especially when it is going to "
        "Telegram. Returns an id and a url; show it with ![](url).",
        HTML_SCHEMA, _tool_html, module=module_id)
    registry.node("visual", "visual", _node,
                  palette={"label": "Visual", "sub": "Draw a chart or a card from the findings",
                           "icon": "Image", "tone": "respond", "model": True,
                           "args": [{"name": "text", "type": "text", "required": True}],
                           "api_keys": "symbol · timeframe · count · entry · sl · tp · title · width · height, or html",
                           "api_example": "symbol=XAUUSD&timeframe=H1&count=200",
                           "api_doc": [
                               {"key": "symbol", "values": ["XAUUSD", "EURUSD", "US30"],
                                "note": "draws a price chart; no model is used"},
                               {"key": "timeframe", "values": ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]},
                               {"key": "count", "values": ["60", "120", "300"], "note": "candles, 10-600"},
                               {"key": "entry", "values": ["2415.50"], "note": "ruled on the chart"},
                               {"key": "sl", "values": ["2402.00"]},
                               {"key": "tp", "values": ["2440.00"]},
                               {"key": "title", "values": ["Gold H1"]},
                               {"key": "note", "values": ["London open"], "note": "caption under the chart"},
                               {"key": "width", "values": ["760", "1000", "1400"], "note": "px, 320-2000"},
                               {"key": "height", "values": ["420", "560", "800"], "note": "px, 200-1400"},
                               {"key": "html", "values": ["<div>…</div>"],
                                "note": "a card instead of a chart — your own markup, no model used"},
                           ]},
                  catalog=CATALOG, values=("text",), module=module_id)
    registry.system_note(
        "You can DRAW REAL IMAGES. create_chart makes a candlestick chart of an instrument with "
        "entry/stop/target ruled on it; create_visual screenshots HTML you write, for anything "
        "that is not a price chart — risk breakdowns, tables, summaries. "
        "IN THE APP, show_chart is the right one: live, interactive, and free to draw. Reach for "
        "create_chart only when the picture has to LEAVE the app — Telegram, a file, a message to "
        "someone not here — because that is the only thing an image can do that the live chart "
        "cannot. Do not make an image for a user who is looking at the app. "
        "NEVER write an image link you were not given: each of these tools hands back a "
        "ready-made markdown line, and you include that line exactly. Do not invent a url, do not "
        "write a placeholder like ${chart_url}, and do not describe a picture instead of showing "
        "it. To put one on Telegram, pass the returned id to send_telegram as image_id.",
        module=module_id)
    registry.provider("visuals", sys.modules[__name__], module=module_id)
