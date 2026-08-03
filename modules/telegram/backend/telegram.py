"""
Telegram bot — the thin layer over the Bot API.

Everything here is one HTTP call to api.telegram.org with the user's own bot
token. No SDK: the three calls this needs (sendMessage, sendPhoto, getUpdates)
are simple, and a dependency for them would be more code than they are.

Nothing in this file decides WHAT to say — that is the module's job, and the
agent's. This only knows how to say it.
"""
from __future__ import annotations

import html as _html
import re
import uuid

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


def _upload(url, fields: dict, filename: str, content_type: str, blob: bytes, timeout):
    """POST a file the way this HTTP client wants it.

    curl_cffi dropped requests-style `files=` for its own CurlMime, and says so
    in the error rather than doing it — so use CurlMime where it exists, and
    fall back to a hand-built multipart body where it does not. The body is a
    dozen lines and it works on every client, which is worth more here than
    elegance: a self-hosted box may be running a different version entirely."""
    from curl_cffi import requests as creq
    try:
        from curl_cffi import CurlMime
    except Exception:
        CurlMime = None

    if CurlMime is not None:
        mp = None
        try:
            mp = CurlMime()
            for k, v in fields.items():
                mp.addpart(name=k, data=str(v).encode())
            mp.addpart(name="photo", filename=filename, content_type=content_type, data=blob)
            return creq.post(url, multipart=mp, timeout=timeout)
        except (TypeError, AttributeError, ValueError):
            # The mime API changed shape under us. Fall through and build the
            # body by hand — but ONLY for errors that mean the request could not
            # be CONSTRUCTED. A network failure must not be retried here, or a
            # photo that did go out gets sent twice.
            pass
        finally:
            try:
                if mp is not None:
                    mp.close()
            except Exception:
                pass

    boundary = "----EntryStation" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v}\r\n").encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode()
    out += blob + f"\r\n--{boundary}--\r\n".encode()
    return creq.post(url, data=bytes(out), timeout=timeout,
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


def _call(token: str, method: str, timeout=30, upload=None, **params):
    from curl_cffi import requests as creq
    url = API.format(token=token, method=method)
    params = {k: v for k, v in params.items() if v is not None}
    try:
        if upload:
            r = _upload(url, params, *upload, timeout=timeout)
        else:
            r = creq.post(url, json=params, timeout=timeout)
        body = r.json()
    except Exception as e:
        raise TelegramError(f"could not reach Telegram: {e}")
    if not body.get("ok"):
        # Telegram's own words are better than ours: "chat not found", "bot was
        # blocked by the user", "Unauthorized" all say exactly what to fix.
        raise TelegramError(body.get("description") or f"Telegram refused {method}")
    return body.get("result")


def me(token: str) -> dict:
    """Who this token belongs to — the check that a token is real."""
    return _call(token, "getMe", timeout=15)


LIMIT = 4000        # Telegram's own ceiling is 4096; leave room for a stray entity.


def _chunks(text: str) -> list:
    """Split a long message where a reader would have paused anyway.

    Telegram rejects anything over 4096 characters OUTRIGHT, so a long analysis
    would arrive as no message at all. Paragraphs first, then lines, then a hard
    cut — a mid-word break is ugly, but losing the end of the answer is worse."""
    out, cur = [], ""
    for para in str(text or "").split("\n\n"):
        while len(para) > LIMIT:                      # a wall of text with no blank line
            cut = para.rfind("\n", 0, LIMIT)
            if cut < LIMIT // 2:
                cut = LIMIT
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:cut])
            para = para[cut:].lstrip("\n")
        if len(cur) + len(para) + 2 > LIMIT:
            if cur:
                out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    out.append(cur or "(empty)")
    return out


# ── the model writes GitHub markdown; Telegram does not speak it ──────────────
#
# Telegram's "Markdown" mode is the 2015 one: bold is *one* asterisk, so the
# **bold** every model produces arrives with the asterisks showing. Its
# MarkdownV2 fixes that and demands 18 characters be backslash-escaped, which on
# prose full of prices and dashes fails constantly and refuses the whole message.
#
# Its HTML mode is the one that behaves: a short, fixed tag list and only three
# characters to escape. So convert once, here, and send HTML.
_FENCE = re.compile(r"```(?:[\w+-]*)\n?(.*?)```", re.S)
_TICK = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"(?<!\w)(?:\*\*|__)(?=\S)(.+?)(?<=\S)(?:\*\*|__)(?!\w)", re.S)
_ITAL = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=\S)\*(?![\w*])")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", re.M)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.M)
_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.M)


def to_html(text: str) -> str:
    """GitHub-ish markdown → the small HTML Telegram accepts.

    Code is lifted out FIRST and put back last, so a price inside backticks is
    never mistaken for emphasis; everything else is HTML-escaped before a single
    tag is inserted, so the model cannot emit markup by writing `<b>`."""
    src = str(text or "")
    vault = []

    def stash(html):
        vault.append(html)
        return f"\x00{len(vault) - 1}\x00"

    src = _FENCE.sub(lambda m: stash(f"<pre>{_html.escape(m.group(1).rstrip())}</pre>"), src)
    src = _TICK.sub(lambda m: stash(f"<code>{_html.escape(m.group(1))}</code>"), src)

    src = _html.escape(src, quote=False)

    src = _RULE.sub("", src)
    src = _HEAD.sub(lambda m: f"<b>{m.group(1)}</b>", src)
    # The url has already been through the escape pass above; only the quote that
    # would close the attribute is still outstanding. Escaping again turns a
    # &amp; into &amp;amp; and breaks the link.
    src = _LINK.sub(lambda m: f'<a href="{m.group(2).replace(chr(34), "&quot;")}">'
                              f'{m.group(1)}</a>', src)
    src = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", src)
    src = _ITAL.sub(lambda m: f"<i>{m.group(1)}</i>", src)
    src = _BULLET.sub(lambda m: f"{m.group(1)}• ", src)

    src = re.sub(r"\x00(\d+)\x00", lambda m: vault[int(m.group(1))], src)
    return re.sub(r"\n{3,}", "\n\n", src).strip()


def send_message(token: str, chat_id, text: str, markdown=True) -> dict:
    out = None
    for part in _chunks(to_html(text) if markdown else str(text or "")):
        try:
            out = _call(token, "sendMessage", chat_id=str(chat_id), text=part,
                        parse_mode="HTML" if markdown else None,
                        disable_web_page_preview=True)
        except TelegramError as e:
            # A tag split across a chunk boundary, or something the converter did
            # not foresee: Telegram refuses the WHOLE message rather than render
            # it plainly. Delivering it unformatted beats not delivering it.
            if markdown and "parse" in str(e).lower():
                out = _call(token, "sendMessage", chat_id=str(chat_id),
                            text=_html.unescape(re.sub(r"<[^>]+>", "", part)),
                            disable_web_page_preview=True)
            else:
                raise
    return out


CAPTION_LIMIT = 1024        # Telegram's own; a photo caption is shorter than a message


def send_photo(token: str, chat_id, image: bytes, caption: str = "") -> dict:
    """An image with an optional caption. `image` is raw bytes — a chart the app
    drew, not a URL, because the app's own charts are not public."""
    # The caption had NO parse_mode at all, so every ** in it showed literally.
    body = to_html(caption or "")
    try:
        return _call(token, "sendPhoto", timeout=60,
                     upload=("chart.png", "image/png", image),
                     chat_id=str(chat_id), caption=body[:CAPTION_LIMIT], parse_mode="HTML")
    except TelegramError as e:
        if "parse" not in str(e).lower():
            raise
        plain = _html.unescape(re.sub(r"<[^>]+>", "", body))
        return _call(token, "sendPhoto", timeout=60,
                     upload=("chart.png", "image/png", image),
                     chat_id=str(chat_id), caption=plain[:CAPTION_LIMIT])


def poll(token: str, offset: int = 0, timeout: int = 25) -> list:
    from curl_cffi import requests as creq
    url = API.format(token=token, method="getUpdates")
    params = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        r = creq.post(url, json=params, timeout=timeout + 15)
        body = r.json()
    except Exception as e:
        raise TelegramError(f"could not reach Telegram: {e}")
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram refused getUpdates")
    return body.get("result") or []
