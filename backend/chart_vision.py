"""Read the chart the user is actually looking at, drawings and all.

Why this cannot be done on the server
-------------------------------------
The chart is drawn in the browser by lightweight-charts, and the lines somebody
draws on it live in THAT page's localStorage. Nothing here has ever seen them.
So a server-side redraw would produce a different picture — same candles, none
of the reasoning the person drew on top — and then answer confidently about it.

The browser therefore takes the picture, with `chart.takeScreenshot()`, at the
moment the question is asked, and posts it here. What gets analysed is the image
that was on screen: their trendline, their zone, their timeframe.

Picking a model
---------------
Their own default, when it can see. Most cannot: DeepSeek runs the whole hosted
product and has no vision at all, so silently sending it an image would produce
an answer about nothing, which is worse than refusing. When the default is
blind, the first vision-capable model they hold a key for is used instead and
the reply says which — an analysis from a different model than the one they
chose is a fact they are entitled to.
"""
import base64

# Vision, by provider and model. Deliberately a list of what IS known to see
# rather than a list of exceptions: a new model that cannot see and is assumed
# to is a confident answer about an image the model never received.
VISION = {
    "openai": ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "o1", "o3", "o4", "chatgpt-4o"),
    "anthropic": ("claude-",),          # every Claude 3 and later reads images
    "gemini": ("gemini-",),
    "grok": ("grok-2-vision", "grok-3", "grok-4", "grok-vision"),
    "groq": ("llama-3.2-11b-vision", "llama-3.2-90b-vision", "llama-4", "maverick", "scout"),
    # deepseek and openrouter are absent on purpose: DeepSeek's chat models are
    # text-only, and an OpenRouter alias can be anything, so neither can be
    # assumed. OpenRouter still works when the user names a model we recognise.
}


def can_see(provider: str, model: str) -> bool:
    marks = VISION.get((provider or "").lower())
    if not marks:
        return False
    m = (model or "").lower()
    return any(mark in m for mark in marks)


def pick(user_id, preferred=None):
    """(provider, model, key, note). The user's own model when it can see.

    `note` is not decoration: it is what the answer will say about why the
    analysis came from somewhere other than the model they picked."""
    import ai_keys
    import billing

    alias = preferred
    if not alias:
        try:
            import main as app_main
            alias = app_main._user_analysis_model(user_id)
        except Exception:
            alias = None
    alias = alias or billing.DEFAULT_MODEL

    provider, model, key = ai_keys.resolve(user_id, alias)
    if key and can_see(provider, model):
        return provider, model, key, ""

    blind = f"{provider}:{model}" if provider else "your model"
    # Anything else they hold a key for that can see. Ordered by how well these
    # read a chart in practice, not alphabetically.
    for p, m in (("anthropic", "claude-sonnet-4-5-20250929"),
                 ("openai", "gpt-4o"),
                 ("gemini", "gemini-2.0-flash"),
                 ("grok", "grok-4"),
                 ("groq", "meta-llama/llama-4-scout-17b-16e-instruct")):
        try:
            k = ai_keys.user_key(user_id, p) or ai_keys.admin_key(p)
        except Exception:
            k = None
        if k:
            return p, m, k, (f"{blind} cannot read images, so this was analysed with {p}.")
    return None, None, None, (f"{blind} cannot read images, and no model that can is "
                              f"connected. Connect OpenAI, Anthropic or Gemini to analyse "
                              f"a chart.")


# What separates a reading from a description. The first version of this asked
# the model to "say what the chart shows", and it did: an inventory of the levels
# the person had drawn themselves, handed back with both outcomes listed and no
# side taken. They can see their own lines. What they cannot get from looking is
# somebody willing to be wrong about which way it goes.
PROMPT = """You are looking at a screenshot of a trading chart from the user's own screen, \
including any lines, zones or levels THEY drew on it.

Give them a TRADE VERDICT. You are being asked to take a side, not to describe a picture.

Start with exactly this, on its own line:

**BUY** or **SELL** or **STAND ASIDE** — conviction: high / medium / low

Then, briefly:
- **Entry** — the price to get in at, and whether that is now or on a pullback to a level.
- **Invalidation** — the price that proves this wrong. A verdict without one is not a trade.
- **Target** — the first place to take money off, and the next one if it runs.
- **Why** — three or four lines at most. Read the PRICE ACTION: who is winning, \
where price got rejected and where it got accepted, whether highs and lows are rising or \
falling, whether pushes are expanding or fading, where the obvious stops are sitting. \
Say what their drawings get right or wrong.

Rules:
- COMMIT. "It could go either way", "wait for a break", "a decisive move will decide the \
direction" — these are refusals dressed as analysis. If price breaks their level, you \
already know which side you would take on that break, so say it now.
- STAND ASIDE is a real verdict, but only when the chart genuinely offers nothing, and you \
must say what you are waiting for. It is not the safe escape from having an opinion.
- Do NOT inventory what is on the screen. They drew those levels. Listing them back is \
telling somebody what they already know.
- Read levels off the axis and quote real numbers. If the image is too unclear to read a \
number, say so instead of inventing one.
- Be direct and short. No preamble, no restating the question, no offer of further analysis \
at the end."""


def _messages(png_b64: str, question: str, provider: str):
    """The same request in each wire format. Anthropic nests the image
    differently from everyone else, and that is the whole difference."""
    prompt = PROMPT + (
        f"\n\nThey also asked: {question}" if question else
        "\n\nThey clicked Analyse chart, so the question is simply: what do I do here?"
    )
    if provider == "anthropic":
        return [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": png_b64}},
            {"type": "text", "text": prompt},
        ]}]
    return [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
        {"type": "text", "text": prompt},
    ]}]


# Anthropic requires a ceiling, so it gets a generous one. The OpenAI-compatible
# call gets none, for the reason agent.py already found the hard way: max_tokens
# covers thinking as well as output, so any cap we pick is one a reasoning model
# can exhaust mid-thought and return an empty string with no error at all.
_ANTHROPIC_MAX_TOKENS = 4000


def analyse(user_id, png_bytes: bytes, question: str = "", preferred=None) -> dict:
    """Look at the picture and say which way to trade it."""
    import requests
    import ai_keys

    provider, model, key, note = pick(user_id, preferred)
    if not provider:
        return {"error": note}

    b64 = base64.b64encode(png_bytes).decode()
    msgs = _messages(b64, question, provider)

    try:
        if provider == "anthropic":
            r = requests.post(
                "https://api.anthropic.com/v1/messages", timeout=120,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": _ANTHROPIC_MAX_TOKENS,
                      "messages": msgs})
            data = r.json()
            if data.get("error"):
                return {"error": data["error"].get("message") or "the model refused the image"}
            text = "".join(b.get("text", "") for b in (data.get("content") or []))
        else:
            base = ai_keys.OPENAI_WIRE.get(provider)
            r = requests.post(
                f"{base}/chat/completions", timeout=120,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json={"model": model, "messages": msgs})
            data = r.json()
            if data.get("error"):
                return {"error": (data["error"] or {}).get("message") or "the model refused the image"}
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    if not text.strip():
        return {"error": "the model returned nothing for this image"}
    return {"analysis": text, "model": f"{provider}:{model}", "note": note}
