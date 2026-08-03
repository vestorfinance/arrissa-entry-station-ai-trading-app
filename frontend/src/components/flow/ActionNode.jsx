import { useEffect } from 'react'
import { Handle, Position, useNodeId, useStore, useUpdateNodeInternals } from 'reactflow'
import { Trash2, Copy } from 'lucide-react'
import { paletteItem } from './palette.js'

// Generic action node (Truth Social, News, …). Its icon and tone come from the
// palette entry; its configured values are set in the node settings panel.
export default function ActionNode({ id, data, selected }) {
  const me = useNodeId()
  const refresh = useUpdateNodeInternals()
  // A tentacle comes DOWN from an Octo body, so a node that might be one needs
  // somewhere on top to receive it — dragging to the left handle means looping
  // the edge back on itself. The handle only appears when there is an Octo on
  // the canvas: on a plain chain it would be a connector that means nothing.
  const hasOcto = useStore((s) =>
    [...s.nodeInternals.values()].some((n) => n.type === 'octoAgent'))
  // Handles that come and go must be re-measured, or React Flow keeps the set it
  // saw at mount and the new one accepts nothing.
  useEffect(() => { refresh(me) }, [me, refresh, hasOcto])

  const item = paletteItem(data.kind)
  const Icon = item?.Icon
  const values = data.values || {}
  // A node carrying `name`/`description` args (Versatile) is titled by the user.
  const title = (values.name || '').trim() || data.label
  const sub = (values.description || '').trim() || data.sub
  const isCall = data.kind === 'call-agent'
  const arg = (data.args || []).find((a) => a.type === 'text') || (data.args || [])[0]
  // A call-agent node is defined by WHICH agent it calls, so surface that; its
  // optional instruction is secondary.
  const value = isCall
    ? (values.agent_name ? `Calls: ${values.agent_name}` : '')
    : (values[arg?.name] || '').trim()

  return (
    <div className={'fnode' + (selected ? ' fnode--selected' : '')}>
      {/* Left first, and deliberately without an id: React Flow resolves an edge
          with no targetHandle to the FIRST target handle, which is how every
          flow saved before the Octo existed keeps working. */}
      <Handle type="target" position={Position.Left} />
      {hasOcto && (
        <Handle type="target" id="tool" position={Position.Top} className="octo-in" />
      )}

      <div className="fnode-tools nodrag">
        <button className="fnode-tool" title="Duplicate node"
                onClick={(e) => { e.stopPropagation(); data.onDuplicate?.(id) }}>
          <Copy size={13} strokeWidth={1.75} />
        </button>
        <button className="fnode-tool fnode-tool--del" title="Delete node"
                onClick={(e) => { e.stopPropagation(); data.onDelete?.(id) }}>
          <Trash2 size={13} strokeWidth={1.75} />
        </button>
      </div>

      <div className="fnode-head">
        <span className={`fnode-icon fnode-icon--${item?.tone || 'default'}`}>
          {Icon && <Icon size={16} strokeWidth={1.75} />}
        </span>
        <div className="fnode-head-main">
          <div className="fnode-title">{title}</div>
          <div className="fnode-sub">{sub}</div>
        </div>
      </div>

      <div className="fnode-args">
        {value ? (
          <div className="fnode-value">{value}</div>
        ) : (
          <div className="fnode-arg fnode-arg--unset">
            <span className="fnode-arg-name">{isCall ? 'agent' : arg?.name}</span>
            <span className="fnode-arg-type">
              {isCall ? 'pick an agent — click to configure' : 'not set — click to configure'}
            </span>
          </div>
        )}
      </div>

      {/* Branching nodes expose one labelled handle per output; the rest have a single one. */}
      {item?.outputs ? (
        <div className="fnode-outs">
          {item.outputs.map((o) => (
            <div className={`fnode-out fnode-out--${o.id}`} key={o.id}>
              <span>{o.label}</span>
              <Handle type="source" id={o.id} position={Position.Right} />
            </div>
          ))}
        </div>
      ) : (
        <Handle type="source" position={Position.Right} />
      )}
    </div>
  )
}
