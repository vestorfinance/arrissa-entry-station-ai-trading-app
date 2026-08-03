// How a "Trigger on Intervals" node reads, in one place — the canvas node and the
// settings panel must never describe the same schedule differently.
//
// The authority on whether a schedule is VALID is the server (backend/
// agent_schedule.py, which also runs it). This is presentation only: it says what
// the node currently holds, and leaves judgement to the thing that will act on it.

export const UNITS = ['seconds', 'minutes', 'hours', 'days']

// Anything under this is run at this — the server floors it, so the UI says so
// before the user wonders why "every 5 seconds" fires every thirty.
export const MIN_INTERVAL_S = 30

const UNIT_S = { seconds: 1, minutes: 60, hours: 3600, days: 86400 }

export function intervalSeconds(values = {}) {
  const n = Number(values.every || 0)
  const u = UNIT_S[values.unit] || 60
  return n > 0 ? n * u : 0
}

export function scheduleLabel(values = {}) {
  const mode = values.mode || 'every'
  if (mode === 'cron') {
    const expr = (values.cron || '').trim()
    return expr ? `cron · ${expr}` : ''
  }
  const n = Number(values.every || 0)
  if (!(n > 0)) return ''
  const unit = values.unit || 'minutes'
  return `every ${n} ${n === 1 ? unit.slice(0, -1) : unit}`
}

// The floor, phrased as a consequence rather than a rule.
export function floorNote(values = {}) {
  const s = intervalSeconds(values)
  return s > 0 && s < MIN_INTERVAL_S
    ? `Runs every ${MIN_INTERVAL_S}s — the shortest interval allowed, since every run is a real model call.`
    : ''
}
