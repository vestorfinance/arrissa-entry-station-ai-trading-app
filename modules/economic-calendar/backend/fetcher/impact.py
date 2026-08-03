"""Tier-1 (rule-based, no AI) market-impact scoring and instrument extraction.

Shared by fxstreet_news.py and tradingview_news.py. Everything here is deterministic
and offline: a curated keyword lexicon scores how likely a headline is to move
markets, and a curated instrument dictionary extracts which instruments it touches.

Public API:
    extract_instruments(text) -> list[str]
    score_impact(text, meta=None) -> {"impact_level", "impact_score", "impact_reasons"}
    annotate(article, text_fields=(...), meta=None) -> article  (adds both, in place)
"""

import re

# --- Instrument dictionary -------------------------------------------------
# Ticker -> alias patterns (matched case-insensitively as whole words).
# Every ticker key is a member of the user's symbols.json, so extracted
# instruments are emitted as those exact tickers (e.g. "US Dollar" -> "DXY").
# Detection is deliberately limited to instruments that can be named
# unambiguously in prose; single-letter/ambiguous equity tickers are excluded
# to avoid false positives (over-tagging).
SYMBOL_ALIASES: dict[str, list[str]] = {
    # FX majors / crosses
    "EURUSD": [r"eur/usd", r"\beuro\b"],
    "GBPUSD": [r"gbp/usd", r"pound sterling", r"\bsterling\b", r"british pound", r"\bcable\b"],
    "USDJPY": [r"usd/jpy", r"japanese yen", r"\byen\b"],
    "USDCHF": [r"usd/chf", r"swiss franc"],
    "USDCAD": [r"usd/cad", r"canadian dollar", r"\bloonie\b"],
    "AUDUSD": [r"aud/usd", r"australian dollar", r"\baussie\b"],
    "NZDUSD": [r"nzd/usd", r"new zealand dollar", r"\bkiwi\b"],
    "EURGBP": [r"eur/gbp"],
    "EURJPY": [r"eur/jpy"],
    "GBPJPY": [r"gbp/jpy"],
    "DXY": [r"us dollar", r"u\.s\. dollar", r"greenback", r"dollar index", r"\bdxy\b"],
    # Metals
    "XAUUSD": [r"\bgold\b", r"xau/usd", r"\bxau\b"],
    "XAGUSD": [r"\bsilver\b", r"xag/usd", r"\bxag\b"],
    "XCUUSD": [r"\bcopper\b"],
    "XPTUSD": [r"\bplatinum\b"],
    "XPDUSD": [r"\bpalladium\b"],
    # Energy
    "USOIL": [r"crude oil", r"\bwti\b", r"\boil\b", r"west texas", r"us crude"],
    "UKOIL": [r"\bbrent\b"],
    "XNGUSD": [r"natural gas", r"nat gas"],
    # Indices
    "US500": [r"s&p 500", r"s&p500", r"\bus500\b", r"\bspx\b"],
    "USTEC": [r"\bnasdaq\b", r"us tech 100", r"\bndx\b"],
    "US30": [r"dow jones", r"\bdjia\b", r"\bdji\b", r"\bus30\b", r"\bdow\b"],
    "UK100": [r"\bftse\b", r"uk 100"],
    "DE30": [r"\bdax\b"],
    "JP225": [r"\bnikkei\b", r"jp225"],
    "STOXX50": [r"euro stoxx", r"stoxx 50"],
    "HK50": [r"hang seng"],
    # Crypto
    "BTCUSD": [r"bitcoin", r"\bbtc\b"],
    "ETHUSD": [r"ethereum", r"\beth\b", r"\bether\b"],
    "XRPUSD": [r"\bxrp\b", r"\bripple\b"],
    "DOGEUSD": [r"dogecoin", r"\bdoge\b"],
    "SOLUSD": [r"\bsolana\b", r"\bsol\b"],
    "ADAUSD": [r"\bcardano\b", r"\bada\b"],
    "BNBUSD": [r"\bbnb\b", r"binance coin"],
    "LTCUSD": [r"\blitecoin\b", r"\bltc\b"],
    # Mega-cap equities (matched by full company name only, never bare ticker)
    "AAPL": [r"\bapple\b"],
    "TSLA": [r"\btesla\b"],
    "NVDA": [r"\bnvidia\b"],
    "MSFT": [r"\bmicrosoft\b"],
    "AMZN": [r"\bamazon\b"],
    "META": [r"meta platforms", r"\bfacebook\b"],
    "GOOGL": [r"\balphabet\b", r"\bgoogle\b"],
    "NFLX": [r"\bnetflix\b"],
    "BABA": [r"\balibaba\b"],
    "AMD": [r"\bamd\b", r"advanced micro"],
    "INTC": [r"\bintel\b"],
    "BA": [r"\bboeing\b"],
    "JPM": [r"jpmorgan", r"jp morgan"],
    "XOM": [r"\bexxon\b"],
    "WMT": [r"\bwalmart\b"],
}

_INSTRUMENT_RES = {
    ticker: re.compile(r"(?<!\w)(?:" + "|".join(aliases) + r")(?!\w)", re.I)
    for ticker, aliases in SYMBOL_ALIASES.items()
}


def extract_instruments(text: str) -> list[str]:
    """Return the tickers mentioned in text, ordered by first mention."""
    if not text:
        return []
    hits: list[tuple[int, str]] = []
    for ticker, pattern in _INSTRUMENT_RES.items():
        match = pattern.search(text)
        if match:
            hits.append((match.start(), ticker))
    hits.sort()
    return [ticker for _, ticker in hits]


# --- Impact keyword lexicon ------------------------------------------------
# category -> (weight, [term patterns]). Higher weight = more market-moving.
IMPACT_KEYWORDS: dict[str, tuple[int, list[str]]] = {
    "central_bank": (
        3,
        [r"federal reserve", r"\bfed\b", r"\bfomc\b", r"\becb\b", r"\bboj\b",
         r"bank of japan", r"bank of england", r"\bboe\b", r"powell", r"lagarde",
         r"rate decision", r"interest rate", r"rate hike", r"rate cut",
         r"monetary policy", r"hawkish", r"dovish", r"quantitative"],
    ),
    "econ_data": (
        3,
        [r"\bcpi\b", r"inflation", r"\bpce\b", r"nonfarm", r"\bnfp\b",
         r"jobless claims", r"unemployment", r"\bgdp\b", r"retail sales",
         r"\bpmi\b", r"payrolls", r"consumer confidence"],
    ),
    "geopolitics": (
        3,
        [r"\bwar\b", r"invasion", r"sanctions", r"tariff", r"\bopec\b",
         r"missile", r"attack", r"conflict", r"airstrike", r"ceasefire",
         r"nuclear", r"embargo"],
    ),
    "market_structure": (
        2,
        [r"earnings", r"guidance", r"merger", r"acquisition", r"bankruptcy",
         r"default", r"downgrade", r"upgrade", r"\bipo\b"],
    ),
    "regulation": (
        2,
        [r"\bsec\b", r"\betf\b", r"regulation", r"lawsuit", r"\bact\b", r"\bban\b"],
    ),
}

# Trailing `s?` lets singular stems also match their plural ("attack" -> "attacks").
_IMPACT_RES = {
    cat: (weight, re.compile(r"(?<!\w)(?:" + "|".join(terms) + r")s?(?!\w)", re.I))
    for cat, (weight, terms) in IMPACT_KEYWORDS.items()
}

# Raw score is an additive tally. To present a bounded, cross-source-comparable
# 0-100 value we divide by a saturation point and clamp. SATURATION is the sum of
# the keyword categories (13) — the ceiling reachable from headline text alone.
# A raw score at or above it reads as "maximally impactful"; sources that also
# have metadata bonuses simply clamp to 100. This neither understates strong
# text-only news (13 -> 100, not /21) nor overstates weak news.
SATURATION = sum(weight for weight, _ in IMPACT_KEYWORDS.values())

# Level thresholds on the normalized 0-100 score.
HIGH_THRESHOLD = 60
MEDIUM_THRESHOLD = 25


def normalize(raw: int) -> int:
    """Map a raw additive score to a clamped 0-100 scale."""
    return round(min(raw / SATURATION, 1.0) * 100)


def score_impact(text: str, meta: dict | None = None) -> dict:
    """Score potential market impact from text plus optional feed metadata.

    meta may include:
      important (bool)         - editor breaking/important flag
      economic_event_ids (list) - tied to economic-calendar events
      primary_instruments (int) - count of instruments the article is primarily about
    """
    text = text or ""
    raw = 0
    reasons: list[str] = []

    for category, (weight, pattern) in _IMPACT_RES.items():
        found = set(m.group(0).lower() for m in pattern.finditer(text))
        if found:
            raw += weight
            reasons.append(f"{category} ({', '.join(sorted(found))})")

    meta = meta or {}
    if meta.get("important"):
        raw += 4
        reasons.append("editor-flagged important")
    if meta.get("economic_event_ids"):
        raw += 3
        reasons.append("tied to economic-calendar event")
    primary = meta.get("primary_instruments") or 0
    if primary >= 2:
        raw += 1
        reasons.append(f"{primary} primary instruments")

    score = normalize(raw)
    if score >= HIGH_THRESHOLD:
        level = "high"
    elif score >= MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    return {"impact_level": level, "impact_score": score, "impact_reasons": reasons}


# --- Currency -> affected instruments (for economic-calendar events) ---------
# All tickers below are members of symbols.json. Scoped to liquid majors that
# actually trade on macro releases (not illiquid exotics like USDAOA/USDIQD).
_FX_PAIRS = [t for t in SYMBOL_ALIASES if len(t) == 6 and t.isalpha()]

# Region equity index most sensitive to a currency's data.
_INDEX_BY_CCY = {
    "USD": ["US500", "USTEC", "US30"],
    "EUR": ["DE30", "STOXX50"],
    "GBP": ["UK100"],
    "JPY": ["JP225"],
    "HKD": ["HK50"],
    "AUD": ["AUS200"],
}
# USD is the global pricing currency for metals / energy / crypto, so USD data
# moves all of these regardless of the FX pair.
_USD_PRICED = ["DXY", "XAUUSD", "XAGUSD", "USOIL", "UKOIL", "XNGUSD", "BTCUSD", "ETHUSD"]


def instruments_for_currency(ccy: str) -> list[str]:
    """Tickers a currency's economic data tends to move, derived dynamically
    from the curated symbol universe (FX pairs containing the currency, its
    region index, and — for USD — dollar-priced commodities/crypto)."""
    ccy = (ccy or "").upper()
    if len(ccy) != 3:
        return []
    result = [t for t in _FX_PAIRS if t[:3] == ccy or t[3:] == ccy]
    result += _INDEX_BY_CCY.get(ccy, [])
    if ccy == "USD":
        result += _USD_PRICED
    seen: set[str] = set()
    return [t for t in result if not (t in seen or seen.add(t))]


def instruments_for_event(currency: str, event_name: str) -> list[str]:
    """Instruments affected by a calendar event: any named in the event title
    (e.g. 'Crude Oil Inventories' -> USOIL) first, then the currency's majors."""
    named = extract_instruments(event_name or "")
    seen = set(named)
    return named + [t for t in instruments_for_currency(currency)
                    if not (t in seen or seen.add(t))]


def annotate(article: dict, meta: dict | None = None) -> dict:
    """Add `instruments` and impact fields to an article dict, in place.

    Instruments are read from title + description only (what the article is
    *about*), avoiding boilerplate like price tables in the full body. Impact
    is scored over title + description + full text.
    """
    headline = " ".join(str(article.get(f) or "") for f in ("title", "description"))
    full = f"{headline} {article.get('text') or ''}"
    article["instruments"] = extract_instruments(headline)
    article.update(score_impact(full, meta))
    return article
