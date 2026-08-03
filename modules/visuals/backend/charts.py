"""
A price chart, written as markup.

No charting library. A library would have to be loaded from somewhere at render
time, and the renderer deliberately has no network — an image of your account's
positions should not require a CDN to be up. Candlesticks are rectangles and
lines, which SVG does natively, so the whole chart is a string.

The style is the app's: dark ground, the same greens and reds, price on the right
where the platforms put it.
"""
from __future__ import annotations

import html as _html
from datetime import datetime

import brand
import fonts

# The app's own palette, so an image posted to Telegram looks like it came from
# the same product as the screen it was made on.
INK = "#e7e7e7"
MUTED = "#8b8b8b"
BG = "#111111"
PANEL = "#181818"
GRID = "#242424"
UP = "#22c55e"
DOWN = "#ef4444"
ENTRY = "#3b82f6"
STOP = "#ef4444"
TARGET = "#22c55e"


def _num(v, dp):
    try:
        return f"{float(v):,.{dp}f}"
    except Exception:
        return str(v)


def _dp(price) -> int:
    """Decimals to quote in, from the size of the number.

    Counting the digits in the data seems more honest and is not: a float that
    arrived as 2371.6844550900002 is noise, not precision, and quoting it turned
    a gold chart's axis into eleven meaningless digits that ran off the edge.
    Magnitude gives every instrument the convention its traders actually use —
    gold and indices 2, the yen crosses 3, the majors 5."""
    p = abs(float(price or 0))
    if p >= 1000:
        return 2
    if p >= 100:
        return 3
    if p >= 10:
        return 4
    return 5


def _rows(candles) -> list:
    """Accept whatever the market API gave us — dicts with o/h/l/c or open/high/…"""
    out = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        g = lambda *k: next((c[x] for x in k if c.get(x) is not None), None)  # noqa: E731
        o, h, low, cl = g("o", "open"), g("h", "high"), g("l", "low"), g("c", "close")
        if None in (o, h, low, cl):
            continue
        out.append({"t": g("t", "time", "timestamp", "date"),
                    "o": float(o), "h": float(h), "l": float(low), "c": float(cl),
                    "v": float(g("v", "volume", "tick_volume") or 0)})
    return out


def _time_label(t) -> str:
    if t is None:
        return ""
    try:
        if isinstance(t, (int, float)):
            return datetime.utcfromtimestamp(t if t < 1e11 else t / 1000).strftime("%d %b %H:%M")
        s = str(t).replace("Z", "").replace("T", " ")
        return datetime.fromisoformat(s[:19]).strftime("%d %b %H:%M")
    except Exception:
        return str(t)[:16]


def candles_html(candles, *, symbol="", timeframe="", title=None, note=None,
                 levels=None, width=1000, height=560) -> str:
    """A candlestick chart, optionally with entry / stop / target lines drawn on.

    `levels` is {label: price} — the point of the picture is usually the trade,
    not the candles, so the levels are labelled ON the price axis where a trader
    reads them."""
    rows = _rows(candles)
    if not rows:
        return card_html("Nothing to chart",
                         "No candles came back for this instrument and timeframe.")

    levels = {k: float(v) for k, v in (levels or {}).items() if v not in (None, "")}
    dp = _dp(rows[-1]["c"])

    # The right margin has to FIT the widest thing that goes in it — an axis
    # price, or a level badge like "ENTRY 2,412.00". Guessing a constant is how
    # the first version clipped every label on a four-figure instrument.
    CH = 6.6                                      # Inter tabular digit at 11.5px
    axis_w = max(len(_num(r, dp)) for r in
                 (max(r["h"] for r in rows), min(r["l"] for r in rows))) * CH
    badge_w = max((len(f"{k.upper()[:5]} {_num(v, dp)}") * CH + 14
                   for k, v in (levels or {}).items()), default=0)
    pad_l, pad_t, pad_b = 14, 54, 58        # pad_b carries the brand strip
    pad_r = int(max(axis_w + 18, badge_w + 8)) + 8
    vol_h = 62
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b - vol_h - 10

    lo = min(r["l"] for r in rows)
    hi = max(r["h"] for r in rows)
    for p in levels.values():                 # a target off-screen is a useless chart
        lo, hi = min(lo, p), max(hi, p)
    span = (hi - lo) or (hi * 0.001 or 1)
    lo -= span * 0.06
    hi += span * 0.06
    span = hi - lo

    n = len(rows)
    step = plot_w / n
    body = max(1.0, min(step * 0.68, 26))

    def y(price):
        return pad_t + (hi - float(price)) / span * plot_h

    parts = []

    # horizontal grid + the price axis on the right
    for i in range(5):
        gy = pad_t + plot_h * i / 4
        price = hi - span * i / 4
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" y2="{gy:.1f}" '
                     f'stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{width - 8}" y="{gy + 4:.1f}" fill="{MUTED}" font-size="12" '
                     f'text-anchor="end" font-family="Inter" font-variant-numeric="tabular-nums">'
                     f'{_num(price, dp)}</text>')

    vmax = max((r["v"] for r in rows), default=0) or 1
    vol_top = pad_t + plot_h + 10

    for i, r in enumerate(rows):
        cx = pad_l + step * (i + 0.5)
        up = r["c"] >= r["o"]
        col = UP if up else DOWN
        parts.append(f'<line x1="{cx:.1f}" y1="{y(r["h"]):.1f}" x2="{cx:.1f}" '
                     f'y2="{y(r["l"]):.1f}" stroke="{col}" stroke-width="1.1"/>')
        top, bot = y(max(r["o"], r["c"])), y(min(r["o"], r["c"]))
        parts.append(f'<rect x="{cx - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" '
                     f'height="{max(1.0, bot - top):.1f}" fill="{col}" rx="0.5"/>')
        if r["v"]:
            vh = r["v"] / vmax * vol_h
            parts.append(f'<rect x="{cx - body / 2:.1f}" y="{vol_top + vol_h - vh:.1f}" '
                         f'width="{body:.1f}" height="{vh:.1f}" fill="{col}" opacity="0.28"/>')

    # the trade, drawn over the candles
    for label, price in levels.items():
        key = label.lower()
        col = STOP if key.startswith(("sl", "stop")) else \
            TARGET if key.startswith(("tp", "target", "take")) else ENTRY
        ly = y(price)
        parts.append(f'<line x1="{pad_l}" y1="{ly:.1f}" x2="{pad_l + plot_w}" y2="{ly:.1f}" '
                     f'stroke="{col}" stroke-width="1.4" stroke-dasharray="6 4" opacity="0.95"/>')
        txt = f"{label.upper()[:5]} {_num(price, dp)}"
        bw = len(txt) * CH + 12
        bx = width - bw - 4
        parts.append(f'<rect x="{bx:.1f}" y="{ly - 9:.1f}" width="{bw:.1f}" height="18" '
                     f'rx="4" fill="{col}"/>')
        parts.append(f'<text x="{bx + 6:.1f}" y="{ly + 4:.1f}" fill="#0b0b0b" '
                     f'font-size="11.5" font-weight="700" '
                     f'font-family="Inter" font-variant-numeric="tabular-nums">{_html.escape(txt)}</text>')

    last = rows[-1]
    ly = y(last["c"])
    last_col = UP if last["c"] >= last["o"] else DOWN
    parts.append(f'<line x1="{pad_l}" y1="{ly:.1f}" x2="{pad_l + plot_w}" y2="{ly:.1f}" '
                 f'stroke="{last_col}" stroke-width="1" opacity="0.5"/>')

    for i, anchor in ((0, "start"), (n // 2, "middle"), (n - 1, "end")):
        x = pad_l + step * (i + 0.5)
        x = pad_l if anchor == "start" else (pad_l + plot_w if anchor == "end" else x)
        parts.append(f'<text x="{x:.1f}" y="{height - 32}" fill="{MUTED}" font-size="11" '
                     f'text-anchor="{anchor}" font-family="Inter" font-variant-numeric="tabular-nums">'
                     f'{_html.escape(_time_label(rows[i]["t"]))}</text>')

    change = (last["c"] - rows[0]["o"]) / (rows[0]["o"] or 1) * 100
    head = title or f"{symbol.upper()} · {timeframe.upper()}"
    sub = f'{_num(last["c"], dp)}  <tspan fill="{UP if change >= 0 else DOWN}">' \
          f'{change:+.2f}%</tspan>  <tspan fill="{MUTED}">· {n} candles</tspan>'

    brand_y = height - 13
    parts.append(
        f'<image href="{brand.MARK}" x="{pad_l}" y="{brand_y - 12:.0f}" width="16" height="16" '
        f'opacity="0.95"/>'
        f'<text x="{pad_l + 22}" y="{brand_y:.0f}" fill="{brand.INK}" font-size="13" '
        f'font-weight="500">{brand.WORDS}</text>'
        f'<text x="{width - 8}" y="{brand_y:.0f}" fill="{brand.INK}" font-size="13" '
        f'text-anchor="end" opacity="0.85">{brand.SITE}</text>')

    return f"""<!doctype html><html><head>{fonts.style_tag()}</head>
<body style="margin:0;background:{BG};font-family:{fonts.STACK}">
<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
     xmlns="http://www.w3.org/2000/svg" style="display:block">
  <rect width="{width}" height="{height}" fill="{BG}"/>
  <text x="{pad_l}" y="26" fill="{INK}" font-size="17" font-weight="650"
        font-family="Inter">{_html.escape(head)}</text>
  <text x="{pad_l}" y="45" font-size="13"
        font-family="Inter" font-variant-numeric="tabular-nums" fill="{INK}">{sub}</text>
  {''.join(parts)}
  {f'<text x="{width - 14}" y="26" fill="{MUTED}" font-size="12" text-anchor="end" font-family="Inter">{_html.escape(note)}</text>' if note else ''}
</svg></body></html>"""


def card_html(title, body="", rows=None, tone="neutral", width=760) -> str:
    """A plain information card — the fallback shape when there is something to
    say and no series to draw it from."""
    accent = {"good": UP, "bad": DOWN, "neutral": ENTRY}.get(tone, ENTRY)
    line = "".join(
        f'<div style="display:flex;justify-content:space-between;gap:20px;padding:9px 0;'
        f'border-top:1px solid {GRID}"><span style="color:{MUTED}">{_html.escape(str(k))}</span>'
        f'<span style="font-family:{fonts.STACK};{fonts.TABULAR}">{_html.escape(str(v))}</span></div>'
        for k, v in (rows or {}).items())
    return f"""<!doctype html><html><head>{fonts.style_tag()}</head>
<body style="margin:0;background:{BG};font-family:{fonts.STACK};color:{INK}">
<div style="width:{width}px;box-sizing:border-box;background:{PANEL};padding:26px 28px;
     border-left:4px solid {accent}">
  <h1 style="margin:0 0 6px;font-size:21px;font-weight:650">{_html.escape(str(title))}</h1>
  {f'<p style="margin:0 0 14px;color:{MUTED};font-size:14px;line-height:1.6">{_html.escape(str(body))}</p>' if body else ''}
  {line}
  {brand.footer_html(tone=MUTED)}
</div></body></html>"""


# What the model is told when it writes its own markup. Constraints first,
# because the ones that matter are the ones that make a render FAIL.
HTML_RULES = (
    "Write a complete standalone HTML document. It is screenshotted by a headless "
    "browser with NO NETWORK, so: no external stylesheets, scripts, fonts or images — "
    "inline everything, use system fonts, and draw shapes with CSS or inline SVG. "
    f"Set everything in {fonts.STACK} — it is embedded and ready. "
    f"Match the app: dark ground {BG}, panels {PANEL}, text {INK}, muted {MUTED}, "
    f"hairlines {GRID}, up/positive {UP}, down/negative {DOWN}, accent {ENTRY}. "
    "Set an explicit pixel width on the outer element, let the height follow the "
    "content. Numbers line up with font-variant-numeric:tabular-nums — do NOT reach for a "
    "monospace family, the whole app is set in Inter. "
    "It will be read on a phone: big type, few words, no decoration that carries no "
    "information."
)
