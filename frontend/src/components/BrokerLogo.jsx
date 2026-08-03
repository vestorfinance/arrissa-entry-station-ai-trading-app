import { useEffect, useState } from 'react'
import * as api from '../services/api.js'
import * as moduleBus from '../services/moduleBus.js'

// Brokers are modules, so their names and logos come FROM them — core does not
// know what Exness looks like any more than it knows how Exness authenticates.
// This fetches the list once, shares it across every mount, and refreshes when a
// module is switched on or off.
let cache = null
let inflight = null
const subscribers = new Set()

function load() {
  if (inflight) return inflight
  inflight = api.listBrokers()
    .then((r) => { cache = r.brokers || []; return cache })
    .catch(() => { cache = cache || []; return cache })
    .finally(() => { inflight = null; subscribers.forEach((fn) => fn(cache)) })
  return inflight
}

// One refresh for the whole page when modules change, not one per mounted logo.
moduleBus.onChanged(() => { cache = null; load() })

export function useBrokers() {
  const [list, setList] = useState(cache)
  useEffect(() => {
    subscribers.add(setList)
    if (cache === null) load()
    else setList(cache)
    return () => subscribers.delete(setList)
  }, [])
  return list || []
}

/**
 * A broker's mark, always a circle.
 *
 * The logos are square and edge-to-edge, so they are cropped to a circle here
 * rather than trusted to be round themselves — a new broker can drop in any
 * square image and it will match the others.
 */
export default function BrokerLogo({ broker, size = 22, className = '' }) {
  const brokers = useBrokers()
  const b = brokers.find((x) => x.id === broker)
  const label = b?.name || broker || ''
  const [failed, setFailed] = useState(false)

  const style = { width: size, height: size, minWidth: size, fontSize: Math.round(size * 0.42) }

  if (!b?.logo || failed) {
    // No logo, or one that would not load: the initial keeps the same circle, so
    // a row never reflows depending on whether an image happened to arrive.
    return (
      <span className={`broker-logo broker-logo--fallback ${className}`} style={style} title={label}>
        {(label[0] || '?').toUpperCase()}
      </span>
    )
  }
  return (
    <img className={`broker-logo ${className}`} style={style} src={b.logo}
         alt={label} title={label} loading="lazy" onError={() => setFailed(true)} />
  )
}

/** The mark and the name together — for headings and status lines. */
export function BrokerMark({ broker, size = 22, showName = true }) {
  const brokers = useBrokers()
  const b = brokers.find((x) => x.id === broker)
  return (
    <span className="broker-mark">
      <BrokerLogo broker={broker} size={size} />
      {showName && <span>{b?.name || broker}</span>}
    </span>
  )
}
