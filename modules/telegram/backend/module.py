"""
Telegram, as a module.

Three things, and the third is the one worth reading.

  1. SEND. An endpoint, an agent tool and a flow node. The node takes plain
     words — "send me the summary", "post the chart to the group" — and the
     model works out what that means from what the flow has already gathered.
     There is nothing to configure but the sentence.

  2. IMAGES. A chart the app drew is not a public URL, so it is uploaded as
     bytes rather than linked.

  3. RECEIVE. The bot is a way IN. A message to it runs the user's own chat
     agent — same tools, same accounts, same memory — and the answer comes back
     in Telegram. That makes the phone a full client without an app.
"""
import re
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import db  # noqa: E402  (core)
from deps import current_user  # noqa: E402  (core)
import telegram  # noqa: E402

router = APIRouter(prefix="/api/v1", tags=["telegram"])

CONNECTION = {
    "kind": "telegram",
    "name": "Telegram",
    "group": "messaging",
    "tone": "blue",
    "logo": "/api/modules/telegram/asset/icon.svg",
    "blurb": "Send messages and charts to Telegram, and talk to your agent from it. "
             "Create a bot with @BotFather, paste its token.",
    "docs": "https://t.me/BotFather",
    "docs_label": "Create a bot",
    "fields": [
        {"key": "api_key", "label": "Bot token", "secret": True, "required": True,
         "placeholder": "123456:ABC-DEF…"},
        {"key": "chat_id", "label": "Chat ID (optional)", "secret": False, "required": False,
         "placeholder": "Leave blank and it replies wherever you message the bot from"},
    ],
}


# ── whose bot, and which chat ──────────────────────────────────────────────────
def _bots(user_id) -> list:
    """This user's Telegram connections, oldest first. More than one is normal —
    a personal bot and one that posts to a group are different bots."""
    import connections
    return [c for c in connections.listing(user_id)
            if c["kind"] == "telegram" and c["enabled"]]


def _token(user_id, connection=None):
    """The bot token to send with.

    `connection` names one of the user's bots (its id, or the name they gave
    it); without one the first enabled connection is used, which is the same
    rule `connections.secret` follows."""
    import connections
    if connection:
        want = str(connection).strip().lower()
        for c in _bots(user_id):
            if want in (str(c["id"]).lower(), c["name"].strip().lower()):
                raw = connections.secret_of(user_id, c["id"], "api_key")
                if raw:
                    return raw
                break
    return connections.secret(user_id, "telegram", "api_key")


def _configured_chat(user_id, connection=None):
    """A Chat ID set on the connection itself — the chosen bot's, if one was
    chosen, since a second bot usually exists to talk somewhere else."""
    bots = _bots(user_id)
    if connection:
        want = str(connection).strip().lower()
        bots = [c for c in bots
                if want in (str(c["id"]).lower(), c["name"].strip().lower())] or bots
    for c in bots:
        if c["config"].get("chat_id"):
            return c["config"]["chat_id"]
    return None


def _remember_chat(user_id, chat: dict):
    """Record a chat the bot has been spoken to in, so it can be OFFERED later.

    Telegram has no 'list my chats' call — a bot only ever learns a chat when
    someone writes to it. Recording them here is what turns "paste a numeric
    chat id" into a menu."""
    cid = str((chat or {}).get("id") or "").strip()
    if not cid:
        return
    title = (chat.get("title")
             or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
             or chat.get("username") or "")
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO telegram_chats (user_id, chat_id, title, kind, last_seen) "
                "VALUES (%s,%s,%s,%s, now()) ON CONFLICT (user_id, chat_id) DO UPDATE SET "
                "title = COALESCE(NULLIF(EXCLUDED.title, ''), telegram_chats.title), "
                "kind = COALESCE(EXCLUDED.kind, telegram_chats.kind), last_seen = now()",
                (user_id, cid, title.strip()[:120], chat.get("type")))
            conn.commit()
    except Exception as e:
        print(f"[telegram] could not record chat: {e!r}", flush=True)


def _known_chats(user_id) -> list:
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT chat_id, title, kind FROM telegram_chats WHERE user_id = %s "
                "ORDER BY last_seen DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _last_chat(user_id):
    """Where they last spoke to the bot.

    A user who has messaged the bot has already told us where to reply, so
    making them find their numeric chat id would be asking for something they
    have effectively already given."""
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT last_chat FROM telegram_state WHERE user_id = %s",
                               (user_id,)).fetchone()
        return (row or {}).get("last_chat")
    except Exception:
        return None


_CHAT_RE = re.compile(r"^(-?\d{4,}|@[A-Za-z][A-Za-z0-9_]{4,})$")


def _real_chat(given):
    """A chat id Telegram could actually address, or nothing.

    Asked to write a message, a model will sometimes fill a `chat_id` it was
    told to omit — "trader", "user_007" — and a send to an invented name fails
    where a send to the user's own chat would have worked. A chat is a numeric
    id or an @username; anything else is discarded rather than attempted."""
    s = str(given or "").strip()
    return s if _CHAT_RE.match(s) else None


def _chats_for(user_id, given=None, connection=None) -> list:
    """Every chat this send should go to.

    `given` may be one chat or many. With none, it falls back the way it always
    has: the Chat ID on the connection, then wherever the bot was last spoken
    to — so the common case still needs no configuration at all."""
    asked = given if isinstance(given, (list, tuple, set)) else [given]
    out = [c for c in (_real_chat(x) for x in asked) if c]
    if out:
        return list(dict.fromkeys(out))
    fallback = _configured_chat(user_id, connection) or _last_chat(user_id)
    if not fallback:
        raise HTTPException(409, "No Telegram chat to send to. Message your bot once so it "
                                 "learns where you are, or set a Chat ID on the connection.")
    return [fallback]


_IMG_RE = re.compile(r"!?\[[^\]]*\]\((?:https?://[^)\s]*)?/api/v1/visuals/"
                     r"([0-9a-fA-F-]{36})\.png\)")


def _pull_images(text):
    """Take any drawn images OUT of a reply and hand back their ids.

    Telegram has no markdown image. An agent that drew a chart and wrote
    `![chart](/api/v1/visuals/…png)` — which is exactly what it is told to do,
    and right for the web chat — was sending that line as LITERAL TEXT, so the
    user got a URL where they had asked for a picture. Strip them out and send
    them as photos instead."""
    ids = _IMG_RE.findall(text or "")
    if not ids:
        return text, []
    return _IMG_RE.sub("", text or "").strip(), list(dict.fromkeys(ids))


def _visual_png(user_id, visual_id):
    """A picture the Visuals module already rendered.

    Asked through the registry, not imported: Telegram works perfectly well
    without Visuals installed, and saying so is the difference between an
    optional module and a hidden dependency."""
    import registry
    v = registry.get("visuals")
    if v is None:
        raise HTTPException(409, "Nothing has been drawn — the Visuals module is not installed.")
    png = v.png_of(user_id, visual_id)
    if not png:
        raise HTTPException(404, f"no image {visual_id}")
    return png


def _send(user_id, text, chat_id=None, image_b64=None, caption=None, connection=None,
          image_id=None):
    """Send to one chat or several.

    Delivery is per-chat: one bad chat id must not swallow the other three, so
    each is reported on its own and the caller is told what actually landed."""
    token = _token(user_id, connection)
    if not token:
        raise HTTPException(409, "No Telegram bot connected. Add one on the Connections page.")
    chats = _chats_for(user_id, chat_id, connection)
    raw = None
    if image_id:
        # By id, never by value. A 60 KB PNG is ~80k characters of base64, which
        # through a tool call would cost more than the analysis that made it.
        raw = _visual_png(user_id, image_id)
    elif image_b64:
        import base64
        raw = base64.b64decode(image_b64.split(",", 1)[-1])

    sent, failed = [], {}
    for chat in chats:
        try:
            if raw is not None:
                telegram.send_photo(token, chat, raw, caption or text or "")
            else:
                telegram.send_message(token, chat, text)
            sent.append(chat)
        except telegram.TelegramError as e:
            failed[chat] = str(e)
    if not sent:
        raise HTTPException(502, "; ".join(f"{c}: {e}" for c, e in failed.items())
                            or "nothing was sent")
    out = {"sent": True, "chats": sent, "chat_id": sent[0],
           "kind": "photo" if raw is not None else "message"}
    if failed:
        out["failed"] = failed
    return out


# ── routes ─────────────────────────────────────────────────────────────────────
@router.get("/telegram/send")
def telegram_send(api_key: str = Query(...), text: str = Query(...),
                  chat_id: str = Query(None), connection: str = Query(None),
                  image_id: str = Query(None)):
    """Send a message. `chat_id` may be several, comma-separated. Without one it
    goes to the connection's configured chat, or wherever you last messaged the
    bot from. `connection` picks which bot to send as."""
    import trading_api
    row = trading_api.api_user(api_key)
    chats = [c.strip() for c in (chat_id or "").split(",") if c.strip()]
    # An explicit chat_id that is not one is a caller's mistake, so say so rather
    # than quietly delivering their message somewhere else.
    bad = [c for c in chats if not _real_chat(c)]
    if bad:
        raise HTTPException(400, f"{', '.join(bad)} — not a chat id. Telegram wants a numeric "
                                 "id (negative for a group) or an @username.")
    return _send(row["user_id"], text, chats or None, connection=connection,
                 image_id=image_id)


# ── what the flow node's pickers offer ─────────────────────────────────────────
# Session-authed, unlike the documented API above: these serve the builder UI,
# where the caller is a signed-in person rather than a script with a key.
@router.get("/telegram/bots")
def telegram_bots(user: dict = Depends(current_user)):
    """The user's Telegram connections, to choose which bot sends."""
    return {"bots": [{"id": c["id"], "name": c["name"]} for c in _bots(user["id"])]}


@router.get("/telegram/chats")
def telegram_chats(user: dict = Depends(current_user)):
    """Every chat the bot has been spoken to in — the menu that replaces asking
    a trader to go and find a numeric chat id."""
    rows = _known_chats(user["id"])
    for r in rows:
        r["label"] = r["title"] or r["chat_id"]
        if r.get("kind") and r["kind"] != "private":
            r["label"] += f" · {r['kind']}"
    return {"chats": rows}


@router.get("/telegram/status")
def telegram_status(api_key: str = Query(...)):
    """Whether a bot is connected, who it is, and where a message would go."""
    import trading_api
    row = trading_api.api_user(api_key)
    token = _token(row["user_id"])
    if not token:
        return {"connected": False, "detail": "No Telegram bot connected."}
    try:
        who = telegram.me(token)
    except telegram.TelegramError as e:
        return {"connected": False, "error": str(e)}
    return {"connected": True, "bot": who.get("username"), "bot_name": who.get("first_name"),
            "chat_id": _configured_chat(row["user_id"]) or _last_chat(row["user_id"]),
            "listening": bool(_LISTENERS.get(str(row["user_id"])))}


# ── the agent tool ─────────────────────────────────────────────────────────────
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "The message. Markdown is supported."},
        "chat_id": {"type": "string",
                    "description": "Where to send it — a numeric chat id or @username, or "
                                   "several separated by commas. Omit to use the connected "
                                   "chat, which is almost always right."},
        "connection": {"type": "string",
                       "description": "Which bot to send as, by the name the user gave it. "
                                      "Only needed when they have more than one."},
        "image_id": {"type": "string",
                     "description": "The id of an image from create_chart or create_visual, to "
                                    "send as a photo with `text` as its caption. ALWAYS attach a "
                                    "picture this way — never paste image data into this call."},
    },
    "required": ["text"],
}


def _tool(args, user_id=None, **_kw):
    if not user_id:
        return {"error": "no user in context"}
    chats = [c.strip() for c in str(args.get("chat_id") or "").split(",") if c.strip()]
    try:
        return _send(user_id, args.get("text") or "", chats or None,
                     connection=args.get("connection"), image_id=args.get("image_id"))
    except HTTPException as e:
        return {"error": e.detail}


NODE_SYSTEM = (
    "You write a Telegram message for a trader, from inside a trading-analysis flow. "
    "Do EXACTLY what the node instruction asks — the instruction is WHAT TO SAY, so write "
    "the message itself, never a description of one and never a preamble like 'here is'. "
    "Use the gathered data and its real numbers; invent nothing. It is read on a phone: "
    "short lines, no headings, Markdown only for *bold* and bullets, and stop when the "
    "point is made. Answer ONLY with JSON: {\"text\": \"the message\", \"chat_id\": "
    "\"only if the instruction names a different chat, otherwise omit\"}.")


def _node(text, context, ctx, nv):
    """Say it in plain words; the model writes what actually gets sent.

    A flow node for a notifier usually means a template and a pile of fields.
    Here the node has the whole run in front of it, so "tell me if anything
    looks worth trading" is the entire configuration — it composes the message
    from what the flow found."""
    import analysis_agent as eng
    uid = (ctx or {}).get("user_id")
    if not uid:
        return {"error": "this flow has no user to send as"}

    # A message stated outright is not a thing to ask a model about. The field
    # was offered on this node and read by nothing — so `text=Done` paid for a
    # model call and then discarded what it wrote.
    stated = eng._explicit_params(nv, (context or {}).get("vars")) or {}
    if stated.get("text"):
        out = {"text": stated["text"], "chat_id": stated.get("chat_id")}
    else:
        user = (f"The user's request (what is being analysed): {context.get('request') or '(none)'}\n\n"
                f"Node instruction: {text or 'Summarise what the flow found.'}\n\n"
                f"Flow context: {eng._ctx_json(context)}")
        out = eng._llm(ctx, nv, NODE_SYSTEM, user, want_json=True)
    # A chat picked in the node is a decision the user has already made, so it
    # beats anything the model volunteers. Only when they picked none does the
    # instruction get to name one.
    picked = nv.get("chats") or stated.get("chat_id")
    if isinstance(picked, str):
        picked = [c.strip() for c in picked.split(",") if c.strip()]
    body = (out or {}).get("text") if isinstance(out, dict) else None
    if not body:
        # No model, or a reply that would not parse. Send the finding rather than
        # nothing — a flow that reached this node did so to say something — but
        # say plainly that it is unwritten, so a silent model is not mistaken for
        # a quiet market.
        why = ctx.get("_llm_error")
        last = context.get("last")
        body = "⚠️ Could not write this message" + (f" ({why})" if why else "") + "."
        body += f"\n\n{eng._short(last)}" if last else " Nothing was gathered to report."
    where = picked or ((out or {}).get("chat_id") if isinstance(out, dict) else None)
    try:
        return _send(uid, body, where, connection=nv.get("connection"))
    except HTTPException as e:
        return {"error": e.detail}


CATALOG = ("telegram — send a message to Telegram; values.text says WHAT to send in plain words "
           "(e.g. 'send me a short summary of the setup and why') and the node writes it from "
           "what the flow gathered. values.chats is an optional list of chat ids to send to and "
           "values.connection an optional bot name — leave both out unless the user named a "
           "specific chat or bot, since the default is their own chat.\n")


# ── receiving: the bot as a way in ─────────────────────────────────────────────
_LISTENERS: dict = {}
_STOP = threading.Event()


HISTORY_TURNS = 12          # 6 exchanges — enough for "and enter" to mean something

CHANNEL_NOTE = (
    "\n\nYou are replying over TELEGRAM, not in the web app. Anything that renders only in the "
    "app — show_chart's live interactive chart, one-tap trade cards — cannot be seen here. If a "
    "picture is wanted, draw a real one with create_chart or create_visual and include the "
    "markdown line it gives you; it will be delivered as a photo. Keep replies short enough to "
    "read on a phone.")


def _remember(user_id, update_id, chat_id):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO telegram_state (user_id, last_update, last_chat, updated_at) "
            "VALUES (%s,%s,%s, now()) ON CONFLICT (user_id) DO UPDATE SET "
            "last_update = EXCLUDED.last_update, last_chat = COALESCE(EXCLUDED.last_chat, "
            "telegram_state.last_chat), updated_at = now()",
            (user_id, update_id, str(chat_id) if chat_id else None))
        conn.commit()


def _state(user_id) -> dict:
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT last_update, history FROM telegram_state "
                               "WHERE user_id = %s", (user_id,)).fetchone()
        return dict(row or {})
    except Exception:
        return {}


def _offset(user_id) -> int:
    return int(_state(user_id).get("last_update") or 0)


def _save_history(user_id, history):
    from psycopg.types.json import Json
    with db.connect() as conn:
        conn.execute("INSERT INTO telegram_state (user_id, history, updated_at) "
                     "VALUES (%s,%s, now()) ON CONFLICT (user_id) DO UPDATE SET "
                     "history = EXCLUDED.history, updated_at = now()",
                     (user_id, Json(history[-HISTORY_TURNS:])))
        conn.commit()


def _answer(user_id, chat_id, text, token):
    """Run the user's own chat agent on a Telegram message and reply with it.

    Their agent — their tools, their accounts, their memory, their broker
    session. A second, lesser assistant that could only answer questions would
    be a worse product than the one they already have, so this is the same
    `run_agent` the web chat calls, metered the same way."""
    import agent, ai_keys, billing, edition, user_session

    if text.lower().lstrip("/") in ("reset", "clear", "new"):
        _save_history(user_id, [])
        telegram.send_message(token, chat_id, "Cleared. Starting fresh.")
        return
    if text.lower().lstrip("/") == "start":
        telegram.send_message(
            token, chat_id,
            "Connected. Ask me anything you would ask in the app — "
            "“how are my positions”, “analyse gold”, "
            "“close EURUSD”. Send /reset to start a new conversation.")
        return

    if edition.metered():
        state = billing.get_state(user_id)
        if not state["active"] or state["credits"] <= 0:
            telegram.send_message(token, chat_id,
                                  "You are out of credits. Top up in the app to keep going.")
            return

    provider, model, key = ai_keys.resolve(user_id, billing.DEFAULT_MODEL)
    if not key:
        telegram.send_message(token, chat_id,
                              "No AI model is configured for this account yet — "
                              "add a provider key on the Connections page.")
        return

    messages = list(_state(user_id).get("history") or []) + \
        [{"role": "user", "content": text}]
    # Which CHANNEL this is answering on changes what an answer can even be. A
    # live interactive chart is the best reply in the web app and nothing at all
    # here, so the agent has to be told where it is standing.
    memory = (agent.read_memory(user_id) or "") + CHANNEL_NOTE
    meter = {}
    reply = []
    with user_session.as_user(user_id):        # this user's own broker accounts only
        try:
            accounts = agent._accounts_context() or []
        except Exception:
            accounts = []
        for ev in agent.run_agent(provider, model, key, messages, accounts,
                                  memory, user_id, meter=meter):
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "text":
                reply.append(ev.get("text") or "")
            elif ev.get("type") == "error":
                reply.append(f"\n\n_{ev.get('error')}_")

    answer = "".join(reply).strip() or "(no answer)"
    # A picture the agent drew has to arrive AS a picture here.
    caption, images = _pull_images(answer)
    if images:
        # The words ride with the first image as its caption so a chart and its
        # explanation arrive as one message — but a photo caption is capped at
        # 1024 characters where a message is 4096, and a real analysis is longer
        # than that. Too long to caption means picture first, words after.
        ride = caption if len(caption) <= telegram.CAPTION_LIMIT else ""
        for i, vid in enumerate(images):
            try:
                png = _visual_png(user_id, vid)
            except Exception:
                continue
            telegram.send_photo(token, chat_id, png, ride if i == 0 else "")
            if i == 0 and ride:
                caption = ""
        if caption:
            telegram.send_message(token, chat_id, caption)
    else:
        telegram.send_message(token, chat_id, answer)
    _save_history(user_id, messages + [{"role": "assistant", "content": answer}])

    try:
        cost = billing.cost_of(meter, model) + float(meter.get("extra_cost_usd", 0))
        billing.charge_cost(user_id, cost, "telegram", provider=provider)
    except Exception:
        pass
    try:
        agent.extract_memory(provider, key, text, answer, memory, user_id, model)
    except Exception:
        pass


def _listen(user_id):
    """One long-poll loop per connected user. 25s per call — Telegram holds the
    connection open, so this is one request a minute at rest, not a spin."""
    uid = str(user_id)
    while not _STOP.is_set():
        token = _token(user_id)
        if not token:
            _LISTENERS.pop(uid, None)
            return
        try:
            started = time.monotonic()
            updates = telegram.poll(token, _offset(user_id) + 1, timeout=25)
            for u in updates:
                msg = u.get("message") or {}
                chat_obj = msg.get("chat") or {}
                chat = chat_obj.get("id")
                text = (msg.get("text") or "").strip()
                _remember_chat(user_id, chat_obj)
                # The cursor advances FIRST, and for every update — including ones
                # this bot cannot answer. An update that is never acknowledged is
                # redelivered forever, so a photo with no caption would become an
                # infinite loop rather than something quietly skipped.
                _remember(user_id, u.get("update_id", 0), chat)
                if not text or not chat:
                    continue
                try:
                    _answer(user_id, chat, text, token)
                except Exception as e:
                    try:
                        telegram.send_message(token, chat, f"Sorry — that failed: {e}")
                    except Exception:
                        pass
            # Telegram holds the call open for the full timeout when idle. If it
            # ever answers instantly with nothing, this would become a hot loop
            # against someone else's API, so make it impossible rather than
            # unlikely.
            if not updates and time.monotonic() - started < 1:
                _STOP.wait(5)
        except telegram.TelegramError as e:
            print(f"[telegram] {uid}: {e}", flush=True)
            _STOP.wait(30)
        except Exception as e:
            print(f"[telegram] {uid}: {e!r}", flush=True)
            _STOP.wait(30)


def _supervise():
    """Start a listener for every user who has a bot, and pick up new ones.

    Polling per user rather than one webhook because a self-hosted box may have
    no public URL at all — long-polling works from behind anything."""
    import connections
    while not _STOP.is_set():
        try:
            for user_id in connections.users_with("telegram"):
                uid = str(user_id)
                t = _LISTENERS.get(uid)
                if t and t.is_alive():
                    continue
                t = threading.Thread(target=_listen, args=(user_id,),
                                     daemon=True, name=f"telegram-{uid[:8]}")
                _LISTENERS[uid] = t
                t.start()
        except Exception as e:
            print(f"[telegram] supervisor: {e!r}", flush=True)
        _STOP.wait(20)


def _start():
    _STOP.clear()
    threading.Thread(target=_supervise, daemon=True, name="telegram-supervisor").start()


def _stop():
    _STOP.set()
    _LISTENERS.clear()


def register(registry, module_id):
    registry.connection_type(CONNECTION, module=module_id)
    registry.routes(router, module=module_id)
    registry.tool("send_telegram",
                  "Send a message to the user's Telegram. Use it when they ask to be told, "
                  "messaged, pinged or notified — 'text me when', 'send that to Telegram', "
                  "'let me know on my phone'. Supports Markdown, and can attach an image made "
                  "by create_chart or create_visual — pass its id as image_id.",
                  TOOL_SCHEMA, _tool, module=module_id)
    registry.node("telegram", "telegram", _node,
                  palette={"label": "Telegram", "sub": "Send a message or chart to Telegram",
                           "icon": "Send", "tone": "respond", "model": True,
                           "args": [
                               {"name": "text", "type": "text", "required": True},
                               # Both pickers read their options from this module.
                               # Leaving either alone is the ordinary case: one
                               # bot, one chat, nothing to choose.
                               {"name": "connection", "type": "select", "required": False,
                                "label": "Send as",
                                "source": "/api/v1/telegram/bots", "rows": "bots",
                                "value": "id", "text": "name",
                                "empty": "The bot you added first",
                                "none": "You have no Telegram bot connected yet."},
                               {"name": "chats", "type": "multiselect", "required": False,
                                "label": "Send to",
                                "source": "/api/v1/telegram/chats", "rows": "chats",
                                "value": "chat_id", "text": "label", "free": True,
                                "empty": "Wherever you last messaged the bot from",
                                "none": "No chats known yet — message your bot once, or type "
                                        "a chat id below."},
                           ],
                           "api_keys": "text (the exact message — no model used) · chat_id",
                           "api_example": "text=Flow finished&chat_id=123456789",
                           "api_doc": [
                               {"key": "text", "values": ["Flow finished", "{{symbol}} setup found"],
                                "note": "sent word for word; no model is used to write it"},
                               {"key": "chat_id", "values": ["123456789", "-1001234567890"],
                                "note": "overridden by the picker above if you used it"},
                           ]},
                  catalog=CATALOG, values=("text", "connection", "chats"), module=module_id)
    registry.worker("telegram-listener", _start, stop=_stop, module=module_id)
    registry.system_note(
        "Telegram is available. When the user asks to be messaged, told, pinged or notified "
        "about something — 'text me when', 'send that to my phone' — use send_telegram as "
        "well as answering here. If no bot is connected the tool says so; relay that rather "
        "than pretending it sent.",
        module=module_id)
    registry.provider("telegram", telegram, module=module_id)
