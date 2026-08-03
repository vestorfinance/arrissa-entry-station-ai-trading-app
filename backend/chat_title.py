"""The name a conversation carries in the sidebar — read out of the reply.

A chat used to be titled with the first thing typed into it. "analyse gold for
me now" records what was ASKED, not what the conversation turned out to be
about, and two chats opened the same way got the same name.

The answer is where the subject actually is, so the answer is what gets read.
No model call: a title is not worth a request, a token or a second of waiting.
Three passes, in order of how much they know:

  1. The reply's own heading. Agents open with one — "## XAUUSD Fundamental
     Analysis", "**Gold — the case for a long**" — and a heading the model
     already wrote beats anything reconstructed from keywords.
  2. Instrument + subject. The ticker as it appears (or the word for it: gold,
     cable, nasdaq), plus what was being done with it, giving "XAUUSD
     Fundamental Analysis", "US30 Order Rejected", "Account Balance".
  3. The question, tidied. What the sidebar showed before, and still better
     than "New chat".
"""
import re

MAX_LEN = 56

# ── what the instrument is called ────────────────────────────────────────────
CURRENCIES = ("USD EUR GBP JPY CHF AUD NZD CAD ZAR SEK NOK DKK PLN HUF CZK TRY "
              "MXN SGD HKD CNH THB ILS").split()

# Instruments that are not two currency codes stuck together.
NAMED = ("XAUUSD XAGUSD XPTUSD XPDUSD US30 US500 USTEC NAS100 SPX500 DJ30 DE30 DE40 "
         "GER30 GER40 UK100 FR40 EU50 JP225 HK50 AUS200 USOIL UKOIL XTIUSD XBRUSD "
         "BTCUSD ETHUSD XRPUSD SOLUSD LTCUSD ADAUSD DOGEUSD DXY").split()

# The words people use instead of the ticker.
ALIASES = {
    "gold": "XAUUSD", "silver": "XAGUSD", "platinum": "XPTUSD", "palladium": "XPDUSD",
    "oil": "USOIL", "crude": "USOIL", "wti": "USOIL", "brent": "UKOIL",
    "nasdaq": "USTEC", "dow": "US30", "dow jones": "US30", "s&p": "US500",
    "sp500": "US500", "dax": "DE40", "ftse": "UK100", "nikkei": "JP225",
    "bitcoin": "BTCUSD", "btc": "BTCUSD", "ethereum": "ETHUSD", "eth": "ETHUSD",
    "cable": "GBPUSD", "dollar index": "DXY",
}

# ── what was being done with it ──────────────────────────────────────────────
# First match wins, so the specific sits above the general.
SUBJECTS = [
    (r"\bfundamental|macro\b|\bcpi\b|\bnfp\b|non-?farm|\bfomc\b|\bfed\b|rate (decision|cut|hike)|"
     r"inflation|central bank|economic calendar", "Fundamental Analysis"),
    (r"\bsentiment\b|positioning|long/short ratio|retail (traders|positioning)|myfxbook", "Sentiment Check"),
    (r"\btechnical|support|resistance|trend ?line|moving average|\brsi\b|\bmacd\b|fibonacci|"
     r"chart pattern|price action", "Technical Analysis"),
    (r"\bbacktest|historical (test|performance)|win rate\b", "Backtest Review"),
    (r"reject(ed|ion)|retcode|\berror\b|failed to (place|open|close)|invalid stops?", "Order Rejected"),
    (r"pending order|buy stop|sell stop|buy limit|sell limit|modif(y|ied)|cancel(led)?|"
     r"close (all|every|the|my)|closed \d+ position|close (the |my )?(position|order)",
     "Order Management"),
    (r"over the weekend|weekend (hold|risk|gap)|sunday gap|friday close", "Weekend Hold"),
    (r"lot size|position siz|risk per trade|drawdown|risk manage|\bsl\b|\btp\b|stop loss|take profit",
     "Position Sizing"),
    (r"\bbalance\b|\bequity\b|free margin|account summary|open positions|\bp/?l\b|profit and loss",
     "Account Review"),
    (r"watch ?list|daily scan|instruments to watch", "Daily Watch List"),
    (r"schedul(e|ed)|every (day|hour)|cron|recurring", "Scheduled Actions"),
    (r"\bnews\b|headline|truth social|trump post", "News Review"),
    (r"\bbond|yield|treasur(y|ies)", "Bond Yields"),
    (r"\bapi\b|endpoint|api key|integration", "API Help"),
    (r"buy|sell|long|short|entry|setup|trade idea|opportunit", "Trade Setup"),
    (r"analy[sz]", "Analysis"),
]

_TICKER = re.compile(r"\b(" + "|".join(NAMED) + r"|(?:" + "|".join(CURRENCIES) + r"){2})\b", re.I)
_HEADING = re.compile(r"^\s*(?:#{1,4}\s*(?P<h>[^\n#]{3,70})|\*\*(?P<b>[^*\n]{3,70})\*\*)\s*$", re.M)
# Politeness and greetings only. Stripping an interrogative leaves a fragment:
# "what do you think about gold" would become "Do you think about gold".
_NOISE = re.compile(r"^(hi|hey|hello|please|can you|could you|i want to|i need to|"
                    r"tell me|show me|give me)\b[\s,:-]*", re.I)


def _tidy(text: str) -> str | None:
    """Strip the decoration models and users add, and cap it to the column."""
    if not text:
        return None
    t = re.sub(r"[*_`#]+", " ", text).strip()
    t = re.sub(r'^["\'“‘]+|["\'”’]+$', "", t).strip()
    t = re.sub(r"\s+", " ", t).strip(" .,:;-—–")
    if len(t) < 3:
        return None
    if len(t) > MAX_LEN:
        t = t[:MAX_LEN].rsplit(" ", 1)[0].strip(" ,:;-—–")
    return t or None


def _sentence(text: str) -> str:
    """Upper-case the first letter and nothing else — .capitalize() lowers the
    rest, and would turn XAUUSD into Xauusd."""
    return text[:1].upper() + text[1:] if text else text


def _first(messages, role):
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == role and (m.get("text") or "").strip():
            return m["text"].strip()
    return ""


def _instrument(text: str) -> str | None:
    """The ticker this is about — written as one, or named in words."""
    if not text:
        return None
    hits = [h.group(0).upper() for h in _TICKER.finditer(text)]
    if hits:
        # The one it keeps coming back to. A reply that names three instruments
        # once each — "closed XAUUSD, GBPUSD and US30" — is about all of them,
        # so picking whichever sorted first would put a lie in the sidebar.
        ranked = sorted({h: hits.count(h) for h in hits}.items(), key=lambda kv: -kv[1])
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            return ranked[0][0]
        return None
    low = text.lower()
    for word, symbol in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            return symbol
    return None


def _subject(text: str) -> str | None:
    low = (text or "").lower()
    for pattern, label in SUBJECTS:
        if re.search(pattern, low):
            return label
    return None


def _from_heading(reply: str) -> str | None:
    """The title the reply already carries, if it opens with one. Only the top
    of the message counts — a heading four paragraphs down names a section, not
    the conversation."""
    m = _HEADING.search(reply[:400] or "")
    if not m:
        return None
    return _tidy(m.group("h") or m.group("b"))


def suggest(messages) -> str | None:
    """A title for this conversation, or None to leave the caller's fallback."""
    asked = _first(messages, "user")
    reply = _first(messages, "assistant")
    if not asked and not reply:
        return None

    heading = _from_heading(reply)
    if heading:
        return heading

    # The reply names the instrument properly ("XAUUSD"); the question often
    # only gestures at it ("gold"). Ask the reply first, and let the question
    # answer for what it was about — that is the half the user stated plainly.
    instrument = _instrument(reply) or _instrument(asked)

    # "Analysis" is what is left when nothing more specific matched, so a bare
    # "analyse gold" must not out-rank a reply that is visibly about the Fed.
    subjects = [s for s in (_subject(asked), _subject(reply)) if s]
    subject = next((s for s in subjects if s != "Analysis"), subjects[0] if subjects else None)

    if instrument and subject:
        return f"{instrument} {subject}"
    if instrument:
        return f"{instrument} Analysis"
    if subject:
        return subject

    return _tidy(_sentence(_NOISE.sub("", asked).strip() or asked))
