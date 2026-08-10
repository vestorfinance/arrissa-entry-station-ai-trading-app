import { flagsFor } from '../data/flags.js'

// The flag(s) for an instrument, wherever one is named. A pair shows both legs
// overlapped so it reads as one instrument; gold, oil, an index or a coin show
// their own mark, drawn in the same circular style so a list never has gaps.
export default function InstrumentFlag({ symbol, size = '', className = '' }) {
  const marks = flagsFor(symbol)
  if (!marks.length) return null
  const cls = ['inst-flags', size ? `inst-flags--${size}` : '', className].filter(Boolean).join(' ')
  return (
    <span className={cls} title={symbol}>
      {marks.map((m, i) => (
        <img key={`${m.src}:${i}`} className="inst-flag" src={m.src} alt="" loading="lazy" />
      ))}
    </span>
  )
}
