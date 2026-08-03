"""
The mark that goes on every picture.

WHITE, not grey. These images are drawn on the app's near-black ground, and a
mid-grey logo on black reads as a smudge — the light version is the one that is
legible, which is the whole point of putting it there.

Inlined as a data URI because the renderer has no network: a footer that
depended on fetching a logo would be a footer that vanished the first time the
box was offline.
"""
import fonts

SITE = "www.entrystation.com"
WORDS = "Powered by EntryStation"

# entry-station-mark.png — ink lifted by luminance (the alpha channel includes
# the white plate behind the glyph, so tinting by it yields a blob), painted
# white, trimmed to the crosshair.
MARK_B64 = (
    "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"
    "fwGEXFoOBr28KwAAAABJRU5ErkJggg=="
)
MARK = "data:image/png;base64," + MARK_B64

INK = "#c9c9c9"          # legible on black without competing with the content


def footer_html(width=None, tone=INK) -> str:
    """The strip that closes a card. `width` pins it to the content above; left
    to itself it fills whatever it is dropped into."""
    w = f"width:{width}px;" if width else ""
    return (
        f'<div data-brand="1" style="{w}box-sizing:border-box;display:flex;'
        f'align-items:center;gap:8px;padding:12px 16px 13px;'
        f'font:500 13px/1 {fonts.STACK};color:{tone};letter-spacing:.005em">'
        f'<img src="{MARK}" width="17" height="17" style="display:block;opacity:.95" alt="">'
        f'<span>{WORDS}</span>'
        f'<span style="margin-left:auto;opacity:.85">{SITE}</span>'
        f'</div>')
