import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  RefreshCw,
  RotateCw,
  Smartphone,
} from 'lucide-react'

// Phone presets — CSS pixel viewports, portrait. The frame is scaled to fit the
// window with a transform, which leaves the iframe's own layout viewport at the
// exact size below, so media queries / window.innerWidth inside stay mobile.
const DEVICES = [
  { id: 'iphone-se', name: 'iPhone SE', w: 375, h: 667 },
  { id: 'iphone-14', name: 'iPhone 14', w: 390, h: 844 },
  { id: 'iphone-15-pm', name: '15 Pro Max', w: 430, h: 932 },
  { id: 'pixel-7', name: 'Pixel 7', w: 412, h: 915 },
  { id: 'galaxy-s21', name: 'Galaxy S21', w: 360, h: 800 },
]
const DEFAULT_DEVICE = 'iphone-14'
const DEFAULT_PATH = '/dashboard'

// The simulator must never load itself (infinite nesting of frames).
function safePath(raw) {
  let p = (raw || '').trim()
  if (!p) return DEFAULT_PATH
  if (!p.startsWith('/')) p = '/' + p
  if (p === '/mobile-simulator' || p.startsWith('/mobile-simulator/') || p.startsWith('/mobile-simulator?')) {
    return DEFAULT_PATH
  }
  return p
}

export default function MobileSimulator() {
  const [params, setParams] = useSearchParams()
  const device = DEVICES.find((d) => d.id === params.get('device')) || DEVICES.find((d) => d.id === DEFAULT_DEVICE)
  const landscape = params.get('o') === 'landscape'
  const urlPath = safePath(params.get('path') || DEFAULT_PATH)

  // What the iframe is loading (remounts on change) vs. what the address bar shows
  // (also follows navigation that happens *inside* the frame).
  const [frameSrc, setFrameSrc] = useState(urlPath)
  const [address, setAddress] = useState(urlPath)
  const [reloadKey, setReloadKey] = useState(0)
  const [scale, setScale] = useState(1)
  const stageRef = useRef(null)
  const frameRef = useRef(null)

  const w = landscape ? device.h : device.w
  const h = landscape ? device.w : device.h

  // Deep links / refreshes keep the simulated page.
  useEffect(() => {
    setFrameSrc(urlPath)
    setAddress(urlPath)
  }, [urlPath])

  // Scale the phone down when the window can't fit it at 1:1 (never up).
  useLayoutEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const fit = () => {
      const pad = 32
      const availW = stage.clientWidth - pad
      const availH = stage.clientHeight - pad
      // +24 / +72 leaves room for the device bezel drawn around the screen
      setScale(Math.min(1, availW / (w + 24), availH / (h + 72)))
    }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(stage)
    return () => ro.disconnect()
  }, [w, h])

  const patch = useCallback(
    (next) => {
      const merged = new URLSearchParams(params)
      Object.entries(next).forEach(([k, v]) => {
        if (v === null) merged.delete(k)
        else merged.set(k, v)
      })
      setParams(merged, { replace: true })
    },
    [params, setParams],
  )

  // Same-origin frame, so we can read where the user navigated to inside it.
  function syncAddress() {
    try {
      const loc = frameRef.current?.contentWindow?.location
      if (loc) setAddress(loc.pathname + loc.search + loc.hash)
    } catch {
      /* cross-origin (shouldn't happen) — leave the address as typed */
    }
  }

  function go(e) {
    e.preventDefault()
    const p = safePath(address)
    setAddress(p)
    setFrameSrc(p)
    patch({ path: p })
  }

  function frameHistory(delta) {
    try {
      frameRef.current?.contentWindow?.history.go(delta)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="msim">
      <header className="msim-bar">
        <div className="msim-brand">
          <Smartphone size={18} strokeWidth={1.75} />
          <span>Mobile simulator</span>
        </div>

        <div className="pill-row msim-devices">
          {DEVICES.map((d) => (
            <button
              key={d.id}
              type="button"
              className={`pill-opt${d.id === device.id ? ' pill-opt--on' : ''}`}
              onClick={() => patch({ device: d.id })}
              title={`${d.w} × ${d.h}`}
            >
              {d.name}
            </button>
          ))}
        </div>

        <div className="msim-actions">
          <button
            type="button"
            className={`btn btn--icon${landscape ? ' btn--primary' : ''}`}
            title={landscape ? 'Back to portrait' : 'Rotate to landscape'}
            onClick={() => patch({ o: landscape ? null : 'landscape' })}
          >
            <RotateCw size={16} strokeWidth={1.75} />
          </button>
          <a
            className="btn btn--icon btn--ghost"
            href={frameSrc}
            target="_blank"
            rel="noreferrer"
            title="Open this page in a new tab"
          >
            <ExternalLink size={16} strokeWidth={1.75} />
          </a>
        </div>
      </header>

      <div className="msim-addressbar">
        <button type="button" className="btn btn--icon btn--ghost" title="Back" onClick={() => frameHistory(-1)}>
          <ArrowLeft size={16} strokeWidth={1.75} />
        </button>
        <button type="button" className="btn btn--icon btn--ghost" title="Forward" onClick={() => frameHistory(1)}>
          <ArrowRight size={16} strokeWidth={1.75} />
        </button>
        <button
          type="button"
          className="btn btn--icon btn--ghost"
          title="Reload"
          onClick={() => setReloadKey((k) => k + 1)}
        >
          <RefreshCw size={16} strokeWidth={1.75} />
        </button>
        <form className="msim-address" onSubmit={go}>
          <input
            className="input"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            spellCheck={false}
            aria-label="Path to simulate"
            placeholder="/dashboard"
          />
        </form>
        <span className="msim-size">
          {w} × {h}
          <span className="msim-zoom">{Math.round(scale * 100)}%</span>
        </span>
      </div>

      <div className="msim-stage" ref={stageRef}>
        <div
          className="msim-device"
          style={{ width: w, height: h, transform: `scale(${scale})` }}
        >
          <div className="msim-notch" />
          <iframe
            key={`${frameSrc}#${reloadKey}`}
            ref={frameRef}
            className="msim-frame"
            title="Mobile preview"
            src={frameSrc}
            onLoad={syncAddress}
          />
          <div className="msim-homebar" />
        </div>
      </div>
    </div>
  )
}
