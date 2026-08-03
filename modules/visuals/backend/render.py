"""
HTML in, PNG out.

One renderer, not two. A candlestick chart and a risk card are both just markup,
so the moment this can turn HTML into an image the "chart engine" question stops
existing — `charts.py` writes HTML and so does the model, and neither needs any
code here to know which it is.

It uses the Chromium that IS ALREADY ON THE BOX. This app drives a real browser
to log a user into their broker, so Edge is installed and proven in this
environment; adding a second headless browser to draw pictures would be 300 MB
to solve a problem that is already solved. Channels are tried in order and the
bundled Chromium is the last resort, so a self-hosted box with none of them gets
a sentence telling it what to install rather than a stack trace.

THREADING. Playwright's sync API is bound to the thread that created it — a
browser launched on a request thread cannot be used from the next request, which
arrives on a different one. So one thread owns the browser for its whole life and
everything else posts jobs to it. That is also what makes it fast: launching per
render costs ~2.4s, reusing a warm browser ~0.2s.
"""
from __future__ import annotations

import queue
import threading

# Order matters: the channels the machine already has, then the download.
CHANNELS = ("msedge", "chrome", "chromium", None)
LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--hide-scrollbars",
               "--force-color-profile=srgb", "--font-render-hinting=none"]

_jobs: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_idle_close_after = 300          # seconds: give the browser back when nobody is drawing


class RenderError(RuntimeError):
    pass


# Measured in the page because only the page knows what it laid out to. The
# widest LINE, not the widest element: a div left at its default width is as
# wide as the viewport and says nothing about the content inside it.
_MEASURE_JS = """() => {
  const b = document.body;
  let w = 0, h = 0;
  const walk = (el) => {
    for (const c of el.children) {
      const s = getComputedStyle(c);
      if (s.display === 'none' || s.position === 'fixed') continue;
      const r = c.getBoundingClientRect();
      const inline = s.display.includes('inline') || s.display === 'table';
      const auto = s.width === 'auto' && !inline;
      if (!auto || !c.children.length) w = Math.max(w, Math.ceil(r.right));
      h = Math.max(h, Math.ceil(r.bottom));
      if (auto) walk(c);
    }
  };
  walk(b);
  const br = b.getBoundingClientRect();
  h = Math.max(h, Math.ceil(br.bottom), b.scrollHeight);
  w = Math.max(w, 40);
  return { w: Math.min(w, Math.ceil(br.width) || w), h };
}"""

# The footer is appended INSIDE the body so the measure above includes it.
_BRAND_JS = """(html) => {
  const d = document.createElement('div');
  d.innerHTML = html;
  document.body.appendChild(d.firstElementChild);
}"""


def _worker():
    """Owns the browser. Sleeps with it open, closes it when nobody has drawn for
    a while — a browser held forever on a small VPS is 200 MB of nothing."""
    from playwright.sync_api import sync_playwright

    pw = browser = None
    channel_used = None
    try:
        while True:
            try:
                job = _jobs.get(timeout=_idle_close_after)
            except queue.Empty:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = None
                if pw:
                    try:
                        pw.stop()
                    except Exception:
                        pass
                    pw = None
                continue
            if job is None:
                break

            html, width, height, scale, full, brand, out = job
            try:
                if pw is None:
                    pw = sync_playwright().start()
                if browser is None:
                    browser, channel_used = _launch(pw)
                page = browser.new_page(
                    viewport={"width": int(width), "height": int(height)},
                    device_scale_factor=float(scale))
                try:
                    page.set_content(html, wait_until="load")
                    # Web fonts and images resolve after `load`; without this a
                    # first render can catch the fallback font mid-swap.
                    try:
                        page.evaluate("document.fonts && document.fonts.ready")
                    except Exception:
                        pass
                    if brand:
                        page.evaluate(_BRAND_JS, brand)
                    if full:
                        # `full_page` alone crops to neither edge: not below the
                        # viewport, and never at all horizontally. A card the
                        # model wrote 1150px wide inside a 1400px viewport came
                        # out with 250px of dead space down the right, which is
                        # the padding that was reported.
                        try:
                            # The BODY's box, not the document's — documentElement
                            # is never smaller than the viewport, so measuring it
                            # just hands back the size you passed in.
                            wh = page.evaluate(_MEASURE_JS)
                            w2 = int(wh.get("w") or 0) or int(width)
                            h2 = int(wh.get("h") or 0) or int(height)
                            if 40 <= w2 <= 4000 and 40 <= h2 <= 8000:
                                page.set_viewport_size({"width": w2, "height": h2})
                                width = w2
                        except Exception:
                            pass
                    png = page.screenshot(type="png", full_page=bool(full))
                finally:
                    page.close()
                out.put(("ok", png))
            except Exception as e:
                # A crashed browser must not poison every later render.
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                browser = None
                out.put(("err", e))
    finally:
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except Exception:
            pass
    _ = channel_used


def _launch(pw):
    tried = []
    for channel in CHANNELS:
        try:
            kw = {"headless": True, "args": LAUNCH_ARGS}
            if channel:
                kw["channel"] = channel
            return pw.chromium.launch(**kw), (channel or "bundled chromium")
        except Exception as e:
            tried.append(f"{channel or 'bundled'}: {str(e).splitlines()[0][:80]}")
    raise RenderError(
        "No Chromium to render with. Install one of Edge, Chrome or Chromium, or run "
        "`playwright install chromium` in the app's environment. Tried — " + " | ".join(tried))


def _ensure_thread():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, daemon=True, name="visuals-render")
            _thread.start()


def html_to_png(html: str, width=1000, height=560, scale=2, full_page=False,
                timeout=60, brand=None) -> bytes:
    """Render markup at `width`×`height` CSS pixels, `scale`× for a sharp image.

    `full_page` lets the CONTENT decide the size — right for a card, whose shape
    depends on what is in it, wrong for a chart, which should be the shape it was
    asked for. `brand` is a footer to append before measuring, so it is inside
    the picture rather than cropped off the end of it."""
    _ensure_thread()
    out: queue.Queue = queue.Queue(maxsize=1)
    _jobs.put((html, width, height, scale, full_page, brand, out))
    try:
        status, payload = out.get(timeout=timeout)
    except queue.Empty:
        raise RenderError("rendering timed out")
    if status == "err":
        raise RenderError(str(payload))
    return payload


def available() -> tuple[bool, str]:
    """Whether anything can actually be drawn, and what with. Cheap enough to
    call from a status endpoint — it renders one real pixel rather than trusting
    that an import means a working browser."""
    try:
        html_to_png("<html><body style='background:#000'></body></html>",
                    width=8, height=8, scale=1, timeout=90)
        return True, "ready"
    except Exception as e:
        return False, str(e)


def stop():
    _jobs.put(None)
