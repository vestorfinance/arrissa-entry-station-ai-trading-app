import { useEffect, useState } from 'react'
import { ArrowUpCircle, X, Check, Copy } from 'lucide-react'
import * as api from '../services/api.js'

// "There is a new version" — said once, at the top, where it cannot be missed.
//
// Two kinds of update and they are not equally easy, which is the whole reason
// this component is more than a link.
//
// A MODULE updates from a button, genuinely: the store hands over a newer signed
// archive and the installer swaps it in while the app runs. That already worked;
// it just had nowhere to be offered from.
//
// CORE cannot update itself. Inside a container the code is the image, and no
// process can rebuild the image it is running in; on a bare install it would
// have to pip-install into its own venv and then restart itself mid-request. So
// the honest button is the command, copyable, rather than an action that half
// works and leaves an instance in a state nobody can describe.
//
// It asks once per session, not on a timer. An update is not urgent enough to
// poll for, and a banner that appears while somebody is mid-sentence is worse
// than one they meet on their next visit.

const DISMISSED = 'entrystation:update-dismissed'

export default function UpdateRibbon() {
  const [info, setInfo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api.moduleUpdates()
      .then((u) => {
        if (!u?.count) return
        // Dismissal is remembered against WHAT was dismissed, so a newer
        // version says so again rather than being silenced for ever by one
        // click on an older one.
        const stamp = `${u.core?.latest || ''}|${(u.modules || []).map((m) => m.id + m.to).join(',')}`
        if (localStorage.getItem(DISMISSED) === stamp) return
        setInfo({ ...u, stamp })
      })
      .catch(() => {})   // the store being unreachable is not worth a banner
  }, [])

  if (!info) return null

  const mods = (info.modules || []).filter((m) => m.can_update)
  const coreNew = info.core?.update_available

  function dismiss() {
    try { localStorage.setItem(DISMISSED, info.stamp) } catch { /* private mode */ }
    setInfo(null)
  }

  async function updateModules() {
    setBusy(true)
    let ok = 0
    for (const m of mods) {
      try { await api.updateModule(m.id); ok += 1 } catch { /* reported below */ }
    }
    setBusy(false)
    // Not "reload". Modules load at import time, so a browser refresh shows
    // exactly the same code — it is the app that has to come back, not the page.
    setDone(`${ok} of ${mods.length} updated. They apply when the app restarts.`)
  }

  const cmd = 'cd ~/entrystation && git pull && docker compose up -d --build'

  return (
    <div className="upd-ribbon">
      <ArrowUpCircle size={16} strokeWidth={2} />
      <span className="upd-text">
        {coreNew
          ? <>A new version of the app is available — <strong>{info.core.latest}</strong>, you
             are on {info.core.version}.</>
          : <>{mods.length} module update{mods.length === 1 ? '' : 's'} available.</>}
        {coreNew && mods.length > 0 && <> {mods.length} module update{mods.length === 1 ? '' : 's'} too.</>}
      </span>

      {done && <span className="upd-done"><Check size={13} /> {done}</span>}

      {!done && mods.length > 0 && (
        <button className="btn btn--primary btn--sm" onClick={updateModules} disabled={busy}>
          {busy ? 'Updating…' : 'Update modules'}
        </button>
      )}

      {coreNew && (
        <button className="btn btn--sm upd-cmd"
                title="Run this on the machine, then reload"
                onClick={() => {
                  navigator.clipboard?.writeText(cmd).then(() => setCopied(true))
                  setTimeout(() => setCopied(false), 1800)
                }}>
          {copied ? <><Check size={13} /> Copied</> : <><Copy size={13} /> Copy update command</>}
        </button>
      )}

      <button className="upd-x" onClick={dismiss} aria-label="Dismiss"><X size={15} /></button>
    </div>
  )
}
