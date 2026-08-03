#!/usr/bin/env python3
"""Fetch news from TradingView's headline API.

Unlike the FXStreet and Investing scrapers beside this file, this is a REAL API:
`news-headlines.tradingview.com/v2/headlines` returns 200 stories as JSON, and
`/v2/story` returns the body. No key, no token, no HTML parsing — the HAR showed
the /news/ page is server-rendered, so the endpoints it does NOT call turned out
to be the useful ones.

Cloudflare fronts it, so requests go through curl_cffi impersonating Chrome —
the same treatment bond_yields.py needs.

WHAT IT RETURNS AND WHAT WE KEEP
--------------------------------
The three feeds worth having are `forex`, `crypto` and `index`, but each is
mixed: the crypto feed carries NASDAQ:COIN and MSTR, the index feed is mostly
AMZN/MSFT/AAPL, the forex feed drags in Russian tickers and USDKRW. TradingView
tags every story with `relatedSymbols`, so a story is kept ONLY when one of those
symbols is an instrument this app actually trades — FX, metals, energy, indices
and crypto. Single stocks and ETFs are dropped whatever feed they arrive in.

That tagging is also better than guessing: `relatedSymbols` is the publisher's
own mapping, so it catches a story about the yen that never writes "USDJPY".
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests

import impact

BASE = "https://news-headlines.tradingview.com/v2"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.tradingview.com",
    "referer": "https://www.tradingview.com/",
}
CATEGORIES = ("forex", "crypto", "index")   # `indices` is not a value; `index` is
STORY_WORKERS = 6
TIMEOUT = 25

# ── what counts as "an instrument I trade" ──────────────────────────────────────
# Everything six characters and alphabetic in the shared alias table is an FX pair
# or a metal (EURUSD, XAUUSD); the rest are named here. The mega-cap equities in
# that table — AAPL, TSLA, NVDA and friends — are excluded by construction, since
# every one of them is five characters or fewer and not on this list.
_NON_FX_TRADED = {
    "DXY", "USOIL", "UKOIL", "XNGUSD",
    "US500", "USTEC", "US30", "UK100", "DE30", "JP225", "STOXX50", "HK50",
}
TRADED = ({t for t in impact.SYMBOL_ALIASES if len(t) == 6 and t.isalpha()}
          | _NON_FX_TRADED)

# TradingView's ticker for an instrument is often not ours. Left = what comes back
# in relatedSymbols (after the exchange prefix), right = what we store.
TV_TICKERS = {
    "SPX": "US500", "SPX500": "US500", "US500": "US500", "ES1!": "US500",
    "IXIC": "USTEC", "NDX": "USTEC", "NQ1!": "USTEC", "NAS100": "USTEC",
    "DJI": "US30", "DJIA": "US30", "YM1!": "US30", "US30": "US30",
    "UKX": "UK100", "FTSE": "UK100",
    "DAX": "DE30", "DEU40": "DE30", "GER40": "DE30",
    "NI225": "JP225", "NKY": "JP225", "JPN225": "JP225",
    "SXXP": "STOXX50", "SX5E": "STOXX50",
    "HSI": "HK50",
    "GOLD": "XAUUSD", "XAU": "XAUUSD", "SILVER": "XAGUSD", "XAG": "XAGUSD",
    "USOIL": "USOIL", "WTI": "USOIL", "CL1!": "USOIL", "UKOIL": "UKOIL", "BRENT": "UKOIL",
    "BTC": "BTCUSD", "BTCUSDT": "BTCUSD", "XBTUSD": "BTCUSD",
    "ETH": "ETHUSD", "ETHUSDT": "ETHUSD",
    "XRP": "XRPUSD", "XRPUSDT": "XRPUSD",
    "SOL": "SOLUSD", "SOLUSDT": "SOLUSD",
    "DOGE": "DOGEUSD", "ADA": "ADAUSD", "BNB": "BNBUSD", "LTC": "LTCUSD",
    "DXY": "DXY", "USDX": "DXY",
}


def our_ticker(tv_symbol: str) -> str | None:
    """'BITSTAMP:BTCUSD' -> 'BTCUSD'. None when it isn't something we trade —
    which is most of them: single stocks, ETFs and the exotic FX tail."""
    if not tv_symbol:
        return None
    raw = str(tv_symbol).split(":")[-1].upper().strip()
    raw = re.sub(r"[._]P$", "", raw)               # perpetuals: BTCUSDT.P
    mapped = TV_TICKERS.get(raw, raw)
    return mapped if mapped in TRADED else None


def instruments_of(item: dict) -> list[str]:
    """The tradable instruments a story is tagged with, ours-only, in order."""
    out = []
    for s in item.get("relatedSymbols") or []:
        t = our_ticker(s.get("symbol") if isinstance(s, dict) else s)
        if t and t not in out:
            out.append(t)
    return out


# ── fetching ───────────────────────────────────────────────────────────────────
def _get(path: str, params: dict):
    r = requests.get(f"{BASE}/{path}", params=params, headers=HEADERS,
                     impersonate="chrome", timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}")
    return r.json()


def fetch_category(category: str) -> list[dict]:
    """One feed's headlines, already filtered to instruments we trade."""
    items = _get("headlines", {"client": "web", "lang": "en", "category": category}).get("items") or []
    kept = []
    for it in items:
        syms = instruments_of(it)
        if not syms:
            continue                    # a stock, an ETF, or an instrument we don't offer
        it["_instruments"] = syms
        it["_category"] = category
        kept.append(it)
    return kept


def fetch_headlines() -> list[dict]:
    """Every feed, merged and de-duplicated by story id."""
    seen, out = set(), []
    for category in CATEGORIES:
        try:
            items = fetch_category(category)
        except Exception:
            continue                    # one dead feed must not take the others down
        for it in items:
            sid = it.get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            out.append(it)
    return out


def flatten_ast(node) -> str:
    """TradingView writes a story body as a small tree of {type, children}, where
    a leaf is a bare string. Images, tables and quotes carry no text of their own,
    so walking it and keeping the strings is the whole job."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(p for p in (flatten_ast(n) for n in node) if p)
    if isinstance(node, dict):
        return flatten_ast(node.get("children"))
    return ""


def fetch_story(story_id: str) -> dict:
    """One story's text. Returns {} on failure — a headline without its body is
    still worth keeping, and the description usually carries the substance."""
    try:
        s = _get("story", {"id": story_id, "lang": "en"})
    except Exception:
        return {}
    body = re.sub(r"\s+", " ", flatten_ast(s.get("astDescription"))).strip()
    return {"description": (s.get("shortDescription") or "").strip() or None,
            "body": body or None,
            "source_url": s.get("sourceUrl") or s.get("link")}


def fetch_stories(ids: list[str]) -> dict:
    """Bodies for several stories at once, keyed by id."""
    out = {}
    with ThreadPoolExecutor(max_workers=STORY_WORKERS) as pool:
        for sid, res in zip(ids, pool.map(fetch_story, ids)):
            if res:
                out[sid] = res
    return out


if __name__ == "__main__":          # manual check: python tradingview_news.py
    import json
    import time

    items = fetch_headlines()
    print(f"{len(items)} stories about instruments we trade")
    now = time.time()
    for it in sorted(items, key=lambda i: i.get("published") or 0, reverse=True)[:12]:
        age = (now - (it.get("published") or now)) / 60
        print(f"  {age:6.0f}m  {it['_category']:6s} {','.join(it['_instruments'])[:22]:24s} "
              f"{it['title'][:64]}")
    if items:
        print("\nstory sample:", json.dumps(fetch_story(items[0]["id"]), indent=2)[:600])
