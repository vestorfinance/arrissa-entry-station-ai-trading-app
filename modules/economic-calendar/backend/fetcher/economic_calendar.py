"""Fetch the Investing.com / ForexPros economic calendar and watch live releases.

Reverse-engineered from sslecal2.forexprostools.com.har: the calendar widget at
https://sslecal2.forexprostools.com/ renders every event as an HTML <tr> row
carrying a GMT `event_timestamp`, an impact rating (the sentiment cell's title:
"High/Moderate/Low Volatility Expected"), and actual / forecast / previous cells.
An event's `actual` cell is empty until the number is released.

This module does two things:

  1. fetch_events() / --once : scrape the calendar, keeping HIGH-impact events only.
  2. watch_next_event() / --watch : find the next high-impact event, start polling
     5 seconds before its scheduled time, and keep re-scraping until every event
     at that timestamp has an `actual` value — or until 90 seconds have passed.

Requests go through curl_cffi (Chrome impersonation).

Usage:
    python economic_calendar.py --once            # print upcoming high-impact events
    python economic_calendar.py --once --out cal.json
    python economic_calendar.py --watch           # watch the next high-impact release
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone

from curl_cffi import requests

import impact as impact_module

CALENDAR_URL = "https://sslecal2.forexprostools.com/"
# JSON delta endpoint the widget polls for live actual/forecast/previous updates.
REFRESHER_URL = "https://sslecal2.forexprostools.com/refresher.php"
REFRESHER_VERSION = "v1.6.9"
# refresher.php normally answers sub-second but occasionally stalls; cap the wait
# so one slow poll can't block the watch loop (a stalled poll just yields nothing).
REFRESHER_TIMEOUT = 8

# Watch tuning (seconds).
PRE_EVENT_LEAD = 5      # start scraping this many seconds before the event time
WATCH_TIMEOUT = 90      # give up this many seconds after the event time
POLL_INTERVAL = 3       # seconds between scrapes while watching
REFRESH_INTERVAL = 1800  # when the week's window is exhausted, re-check every 30 min

TAG_RE = re.compile(r"<[^>]+>")
ROW_RE = re.compile(
    r'<tr id="eventRowId_(?P<id>\d+)"[^>]*event_timestamp="(?P<ts>[^"]+)"[^>]*>'
    r'(?P<body>.*?)</tr>',
    re.S,
)
IMPACT_MAP = {"High": "high", "Moderate": "medium", "Low": "low"}


def _iso_z(dt: datetime) -> str:
    """ISO 8601 with a trailing Z for UTC (instead of +00:00)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp, tolerating a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clean(fragment: str | None) -> str | None:
    """Strip tags/nbsp; return None for empty cells."""
    if fragment is None:
        return None
    text = TAG_RE.sub(" ", fragment).replace("\xa0", " ").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _cell(body: str, cell_id_prefix: str) -> str | None:
    m = re.search(rf'id="{cell_id_prefix}_\d+[^"]*"[^>]*>(.*?)</td>', body, re.S)
    return _clean(m.group(1)) if m else None


def fetch_calendar_html(retries: int = 4, session=None) -> str:
    last_error: Exception | None = None
    getter = session.get if session is not None else \
        (lambda url, **kw: requests.get(url, impersonate="chrome", **kw))
    for attempt in range(retries):
        try:
            resp = getter(CALENDAR_URL, timeout=30)
            resp.raise_for_status()
            if "eventRowId" in resp.text:
                return resp.text
            raise RuntimeError("calendar returned no event rows")
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch calendar after {retries} attempts: {last_error}")


def fetch_refresher_updates(session) -> dict[str, dict]:
    """Poll the JSON delta endpoint for live actual/forecast/previous updates.

    Returns {row_ID: {"actual", "forecast", "previous"}} for events with fresh
    data. Empty when nothing is releasing (it's a delta, not the full calendar),
    so this replaces re-parsing the whole HTML page during the watch window.
    """
    try:
        resp = session.get(REFRESHER_URL,
                           params={"refresher_version": REFRESHER_VERSION, "lang": "1"},
                           headers={"x-requested-with": "XMLHttpRequest", "referer": CALENDAR_URL},
                           timeout=REFRESHER_TIMEOUT)
        body = resp.text.strip()
        if not body:
            return {}
        data = resp.json()
    except Exception:
        return {}

    block = data.get("EconomicCalendar") or []
    rows = block.values() if isinstance(block, dict) else block
    updates: dict[str, dict] = {}
    for ev in rows:
        raw_actual = ev.get("event_actual_formatted") or ""
        # A "sandClock" placeholder means the number hasn't printed yet.
        actual = None if "sandClock" in raw_actual else _clean(raw_actual)
        updates[str(ev.get("row_ID"))] = {
            "actual": actual,
            "forecast": _clean(ev.get("event_forecast_formatted")),
            "previous": _clean(ev.get("event_previous_formatted")),
        }
    return updates


def parse_events(html: str, high_only: bool = True) -> list[dict]:
    """Parse calendar HTML into event dicts (high-impact only by default)."""
    events = []
    for m in ROW_RE.finditer(html):
        body = m.group("body")

        impact_m = re.search(r'title="(High|Moderate|Low) Volatility Expected"', body)
        impact = IMPACT_MAP.get(impact_m.group(1)) if impact_m else None
        if high_only and impact != "high":
            continue

        ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

        cur_m = re.search(r'flagCur">.*?title="([^"]*)".*?</span>\s*([A-Za-z]{3})', body, re.S)
        name_m = re.search(r'class="left event">(.*?)</td>', body, re.S)

        currency = cur_m.group(2) if cur_m else None
        event_name = _clean(name_m.group(1)) if name_m else None

        events.append({
            "id": m.group("id"),
            "timestamp_utc": _iso_z(ts),
            "country": cur_m.group(1) if cur_m else None,
            "currency": currency,
            "event": event_name,
            "impact": impact,
            "instruments": impact_module.instruments_for_event(currency, event_name),
            "actual": _cell(body, "eventActual"),
            "forecast": _cell(body, "eventForecast"),
            "previous": _cell(body, "eventPrevious"),
        })
    return events


def fetch_events(high_only: bool = True) -> list[dict]:
    return parse_events(fetch_calendar_html(), high_only=high_only)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def watch_next_event(list_fn=None, poll_fn=None, session=None) -> list[dict] | None:
    """Find the next high-impact event, then poll from PRE_EVENT_LEAD seconds
    before its time until every event at that timestamp has an actual value, or
    WATCH_TIMEOUT seconds after the event time have elapsed.

    The initial listing comes from the HTML page (no JSON API exists for it),
    but the polling uses the /refresher.php JSON delta endpoint — no HTML
    re-parsing during the watch window. `list_fn`/`poll_fn` are injectable for
    testing; by default both share one curl_cffi session.

    Returns the final state of the tracked events (or None if none upcoming).
    """
    if session is None:
        session = requests.Session(impersonate="chrome")
    if list_fn is None:
        list_fn = lambda: parse_events(fetch_calendar_html(session=session), high_only=True)
    if poll_fn is None:
        poll_fn = lambda ids: fetch_refresher_updates(session)

    upcoming = sorted(
        (e for e in list_fn() if _parse_iso(e["timestamp_utc"]) > _now()),
        key=lambda e: e["timestamp_utc"],
    )
    if not upcoming:
        print("No upcoming high-impact events found.")
        return None

    next_ts = upcoming[0]["timestamp_utc"]
    event_time = _parse_iso(next_ts)
    tracked = {e["id"]: e for e in upcoming if e["timestamp_utc"] == next_ts}

    print(f"Next high-impact release: {event_time.isoformat()} ({len(tracked)} event(s))")
    for e in tracked.values():
        print(f"  - {e['currency']} {e['event']} "
              f"(forecast {e['forecast']}, previous {e['previous']})")

    # Wait until PRE_EVENT_LEAD seconds before the event.
    start_at = event_time.timestamp() - PRE_EVENT_LEAD
    while (remaining := start_at - _now().timestamp()) > 0:
        print(f"  waiting {int(remaining)}s until watch window opens...")
        time.sleep(min(remaining, 15))

    print("Watch window open — polling refresher.php for actuals...")
    deadline = event_time.timestamp() + WATCH_TIMEOUT
    announced: set[str] = set()

    def merge(updates: dict) -> None:
        for rid, upd in updates.items():
            if rid in tracked and upd.get("actual") is not None:
                tracked[rid].update({k: v for k, v in upd.items() if v is not None})

    while True:
        merge(poll_fn(set(tracked)))

        for rid, e in tracked.items():
            if e["actual"] is not None and rid not in announced:
                announced.add(rid)
                print(f"  RELEASED: {e['currency']} {e['event']} = {e['actual']} "
                      f"(forecast {e['forecast']}, previous {e['previous']})")

        if all(e["actual"] is not None for e in tracked.values()):
            print("All tracked events have actuals. Done.")
            break
        if _now().timestamp() > deadline:
            # Safety net: one HTML reconcile in case the delta endpoint missed one.
            try:
                html_events = {e["id"]: e for e in parse_events(fetch_calendar_html(session=session))}
                for rid, e in tracked.items():
                    if e["actual"] is None and html_events.get(rid, {}).get("actual"):
                        e.update(html_events[rid])
            except Exception:
                pass
            missing = [e["event"] for e in tracked.values() if e["actual"] is None]
            if missing:
                print(f"Timeout after {WATCH_TIMEOUT}s. Still missing: {', '.join(missing)}")
            break
        time.sleep(POLL_INTERVAL)

    return list(tracked.values())


def run_watcher(refresh_interval: int = REFRESH_INTERVAL, max_cycles: int | None = None,
                list_fn=None, poll_fn=None) -> None:
    """Continuously watch each high-impact release, rolling into the next week
    automatically. Runs until interrupted (Ctrl-C).

    State-driven, not a fixed heartbeat: it re-fetches the listing each cycle, so
    new/updated events are always picked up. Only when the current week's window
    is fully exhausted does it idle-poll every `refresh_interval` seconds waiting
    for next week's data to appear. `list_fn`/`poll_fn`/`max_cycles` are for tests.
    """
    session = requests.Session(impersonate="chrome")
    fetch_list = list_fn or (lambda: parse_events(fetch_calendar_html(session=session), high_only=True))
    last_handled: str | None = None
    cycles = 0

    print("Economic calendar watcher started (Ctrl-C to stop).")
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        try:
            events = fetch_list()
        except Exception as exc:
            print(f"  listing fetch failed: {exc}; retrying in {refresh_interval}s")
            time.sleep(refresh_interval)
            continue

        # Upcoming = future high-impact events we haven't already handled.
        upcoming = sorted(
            (e for e in events
             if _parse_iso(e["timestamp_utc"]) > _now()
             and (last_handled is None or e["timestamp_utc"] > last_handled)),
            key=lambda e: e["timestamp_utc"],
        )

        if not upcoming:
            print(f"  [{_now().strftime('%Y-%m-%d %H:%M UTC')}] window exhausted — "
                  f"re-checking for next week's data in {refresh_interval // 60} min")
            time.sleep(refresh_interval)
            continue

        next_ts = upcoming[0]["timestamp_utc"]
        watch_next_event(list_fn=lambda: upcoming,
                         poll_fn=poll_fn or (lambda ids: fetch_refresher_updates(session)),
                         session=session)
        last_handled = next_ts  # don't re-watch this timestamp bucket


def main() -> None:
    parser = argparse.ArgumentParser(description="Economic calendar fetcher + live watcher")
    parser.add_argument("--once", action="store_true", help="fetch and print current high-impact events")
    parser.add_argument("--watch", action="store_true", help="watch the next high-impact release, then exit")
    parser.add_argument("--run", action="store_true",
                        help="run continuously: watch every release, roll into next week automatically")
    parser.add_argument("--all", action="store_true", help="include all impact levels (with --once)")
    parser.add_argument("--out", help="write fetched events to a JSON file (with --once)")
    args = parser.parse_args()

    if args.run:
        try:
            run_watcher()
        except KeyboardInterrupt:
            print("\nWatcher stopped.")
        return

    if args.watch:
        watch_next_event()
        return

    # default action is --once
    events = fetch_events(high_only=not args.all)
    now = _now()
    print(f"{len(events)} {'events' if args.all else 'high-impact events'} "
          f"(now {now.isoformat(timespec='seconds')}):\n")
    for e in events:
        ts = _parse_iso(e["timestamp_utc"])
        when = "past" if ts < now else "upcoming"
        actual = e["actual"] if e["actual"] is not None else "—"
        print(f"  {e['timestamp_utc'][:16]}  {when:8} {e['currency']}  {e['event']}")
        print(f"      actual {actual} | forecast {e['forecast']} | previous {e['previous']}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(events, f, indent=2)
        print(f"\nSaved {len(events)} events to {args.out}")


if __name__ == "__main__":
    main()
