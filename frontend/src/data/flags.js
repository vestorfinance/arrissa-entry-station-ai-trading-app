// One place that answers "what picture goes with this?" — for a country, a
// currency, or a whole instrument.
//
// Served from our own /flags, not a CDN. A CDN would make every flag in the app
// depend on somebody else's repository staying up, and would fail outright on a
// self-hosted box behind a firewall — which is most of the Community edition.
//
// Assets that are not countries (gold, oil, the indices, crypto) get a mark
// drawn in the same circular style, so a row of instruments reads as one set
// rather than flags with gaps in it.

export const flagUrl = (code) => `/flags/${String(code || '').toLowerCase()}.svg`
export const assetUrl = (name) => `/flags/asset/${name}.svg`

// Currency → the flag that means it. EUR is the union, not a member state.
const CCY = {
  USD: 'us', EUR: 'eu', GBP: 'gb', JPY: 'jp', CHF: 'ch', AUD: 'au', NZD: 'nz',
  CAD: 'ca', CNY: 'cn', CNH: 'cn', ZAR: 'za', SEK: 'se', NOK: 'no', DKK: 'dk',
  PLN: 'pl', TRY: 'tr', MXN: 'mx', SGD: 'sg', HKD: 'hk', INR: 'in', BRL: 'br',
  RUB: 'ru', KRW: 'kr', THB: 'th', CZK: 'cz', HUF: 'hu', ILS: 'il', AED: 'ae',
  SAR: 'sa', RON: 'ro', CLP: 'cl', COP: 'co', PHP: 'ph', IDR: 'id', MYR: 'my',
  TWD: 'tw', VND: 'vn', EGP: 'eg', NGN: 'ng', KES: 'ke', ARS: 'ar', PEN: 'pe',
  ISK: 'is', UAH: 'ua', BGN: 'bg', HRK: 'hr', QAR: 'qa', KWD: 'kw', BHD: 'bh',
}

// Metals and crypto keep their own mark rather than a country's.
const METAL = { XAU: 'xau', XAG: 'xag', XPT: 'xpt', XPD: 'xpd' }
const CRYPTO = { BTC: 'btc', XBT: 'btc', ETH: 'eth', LTC: 'ltc', XRP: 'xrp' }
const CRYPTO_ANY = /^(BTC|XBT|ETH|LTC|XRP|BCH|ADA|SOL|DOT|DOGE|AVAX|LINK|MATIC|TRX|BNB|USDT|USDC)$/

// An index is a country's market, so it flies that country's flag.
const INDEX = {
  US30: 'us', US500: 'us', USTEC: 'us', NAS100: 'us', SPX500: 'us', SP500: 'us',
  DJ30: 'us', US2000: 'us', RUSSELL: 'us', DXY: 'us', USDX: 'us',
  GER40: 'de', GER30: 'de', DE40: 'de', DE30: 'de', DAX: 'de', DAX40: 'de',
  UK100: 'gb', FTSE100: 'gb', FRA40: 'fr', CAC40: 'fr', ESP35: 'es', IBEX35: 'es',
  ITA40: 'it', EU50: 'eu', STOXX50: 'eu', SWI20: 'ch', NETH25: 'nl',
  JP225: 'jp', NIKKEI: 'jp', HK50: 'hk', HSI: 'hk', CHINA50: 'cn', CN50: 'cn',
  AUS200: 'au', ASX200: 'au', INDIA50: 'in', SA40: 'za', SING30: 'sg',
}

const ENERGY = /(^|[^A-Z])(WTI|BRENT|CRUDE|USOIL|UKOIL|OIL|XTI|XBR)([^A-Z]|$)/
const GASRE = /(NGAS|NATGAS|XNG)/

// Broker suffixes: Exness appends m/z/c, others append .r, _raw, micro …
const clean = (s) => String(s || '').toUpperCase()
  .replace(/[._-]?(RAW|ECN|PRO|MICRO|CENT|STD|R|Z|C|M)$/i, '')
  .replace(/[^A-Z0-9]/g, '')

/** The country/asset codes behind an instrument, in order. */
export function partsOf(symbol) {
  const s = clean(symbol)
  if (!s) return []

  if (INDEX[s]) return [{ kind: 'flag', code: INDEX[s], label: s }]
  if (ENERGY.test(s)) return [{ kind: 'asset', code: 'oil', label: 'Oil' }]
  if (GASRE.test(s)) return [{ kind: 'asset', code: 'gas', label: 'Natural gas' }]

  // A six-letter pair splits down the middle: EURUSD, XAUUSD, BTCUSD.
  const legs = s.length >= 6 ? [s.slice(0, 3), s.slice(3, 6)] : [s]
  const out = []
  for (const leg of legs) {
    if (METAL[leg]) out.push({ kind: 'asset', code: METAL[leg], label: leg })
    else if (CRYPTO[leg]) out.push({ kind: 'asset', code: CRYPTO[leg], label: leg })
    else if (CCY[leg]) out.push({ kind: 'flag', code: CCY[leg], label: leg })
    else if (CRYPTO_ANY.test(leg)) out.push({ kind: 'asset', code: 'gen', label: leg })
  }
  if (out.length) return out

  // Unknown, but clearly an instrument. A neutral mark beats an empty space.
  return [{ kind: 'asset', code: s.length > 6 ? 'idx' : 'gen', label: s }]
}

/** Image sources for an instrument: one for an index or metal, two for a pair. */
export function flagsFor(symbol) {
  return partsOf(symbol).map((p) => ({
    src: p.kind === 'flag' ? flagUrl(p.code) : assetUrl(p.code),
    label: p.label,
  }))
}

/** The flag for a currency code on its own (what a calendar event carries). */
export function currencyFlag(ccy) {
  const c = String(ccy || '').toUpperCase()
  if (CCY[c]) return { src: flagUrl(CCY[c]), label: c }
  if (METAL[c]) return { src: assetUrl(METAL[c]), label: c }
  return null
}

/** The flag for an ISO-3166 alpha-2 country code. */
export function countryFlag(code) {
  const c = String(code || '').trim()
  return c.length === 2 ? { src: flagUrl(c), label: c.toUpperCase() } : null
}

// Every token we can recognise as an instrument, longest first so "XAUUSD"
// matches before "USD" and "NAS100" before "NAS".
const KNOWN = [
  ...Object.keys(INDEX),
  ...Object.keys(METAL).flatMap((m) => [m + 'USD', m + 'EUR', m]),
  ...Object.keys(CRYPTO).flatMap((c) => [c + 'USD', c + 'USDT', c]),
  ...Object.keys(CCY).flatMap((a) => Object.keys(CCY).filter((b) => b !== a).map((b) => a + b)),
  'USOIL', 'UKOIL', 'WTI', 'BRENT', 'CRUDE', 'NGAS', 'NATGAS',
].sort((a, b) => b.length - a.length)
const KNOWN_SET = new Set(KNOWN)

/** The instrument a piece of text is about, or null.
 *
 * Word-bounded and case-insensitive, so "gold" in prose does not match but
 * "XAUUSD" does — the point is to flag a chat that IS about an instrument, not
 * to guess at every mention of a market. */
export function detectSymbol(text) {
  const up = String(text || '').toUpperCase()
  if (!up) return null
  for (const m of up.matchAll(/[A-Z0-9]{3,10}/g)) {
    const t = m[0]
    if (KNOWN_SET.has(t)) return t
    // a broker suffix on the end (EURUSDM, XAUUSD.R already stripped by the regex)
    if (t.length >= 7 && KNOWN_SET.has(t.slice(0, -1))) return t.slice(0, -1)
  }
  return null
}
