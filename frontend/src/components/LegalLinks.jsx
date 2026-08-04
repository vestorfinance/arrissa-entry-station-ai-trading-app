import { Link } from 'react-router-dom'
import { useLegalBase } from '../services/appConfig.js'

// Terms, Privacy and the Licence, pointed at whichever copy is authoritative.
//
// On the hosted service these are pages of this app and route internally. On a
// Community install they are OUR documents, not the operator's, and a
// self-hosted copy becomes a stale fork of a legal text the moment either side
// is updated — so they open entrystation.com instead, in a new tab, because a
// person halfway through creating an account should not lose the form to read
// the terms.
export default function LegalLinks({ licence = true }) {
  const base = useLegalBase()
  const items = [['/terms', 'Terms of use'], ['/privacy', 'Privacy policy']]
  if (licence) items.push(['/licence', 'Licence'])

  return (
    <div className="auth-legal">
      {items.map(([to, label], i) => (
        <span key={to}>
          {i > 0 && <span className="auth-legal-sep">|</span>}
          {base
            ? <a href={base + to} target="_blank" rel="noreferrer">{label}</a>
            : <Link to={to}>{label}</Link>}
        </span>
      ))}
    </div>
  )
}
