import { useEffect } from 'react'
import { Handle, Position, useNodeId, useStore, useUpdateNodeInternals } from 'reactflow'
import { Trash2, Copy } from 'lucide-react'
import { paletteItem } from './palette.js'

// The Octo body.
//
// Every other node on this canvas is wired to say WHAT HAPPENS NEXT. This one is
// wired to say what is AVAILABLE: the nodes hanging from the handle underneath
// are tentacles, and which of them actually run is decided per request, by the
// body, against the question being asked.
//
// Two source handles, and BOTH carry an id. React Flow keys its handle bookkeeping
// on that id, so a node with one identified handle and one anonymous one measures
// and connects unpredictably — the bottom handle was there in the markup and not
// on the canvas. Both are also attached to the node root rather than nested inside
// the label strip, because handle geometry is measured against the node.
export default function OctoNode({ id, data, selected }) {
  const item = paletteItem(data.kind)
  const Icon = item?.Icon
  const values = data.values || {}
  const brief = (values.text || '').trim()
  const me = useNodeId()
  const refresh = useUpdateNodeInternals()

  // Handles that are not on the node's own edge have to be re-measured once the
  // node has actually laid out, or React Flow keeps the bounds it guessed.
  useEffect(() => { refresh(me) }, [me, refresh, brief])

  // Count the tentacles actually attached, so the node says how many arms it has
  // rather than making you trace the edges yourself.
  const arms = useStore((s) =>
    s.edges.filter((e) => e.source === me && e.sourceHandle === 'tools').length)

  return (
    <div className={'fnode fnode--octo' + (selected ? ' fnode--selected' : '')}>
      <Handle type="target" position={Position.Left} />

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
          <div className="fnode-title">{data.label}</div>
          <div className="fnode-sub">{data.sub}</div>
        </div>
      </div>

      <div className="fnode-args">
        {brief ? (
          <div className="fnode-value">{brief}</div>
        ) : (
          <div className="fnode-arg fnode-arg--unset">
            <span className="fnode-arg-name">brief</span>
            <span className="fnode-arg-type">not set — click to configure</span>
          </div>
        )}
      </div>

      <div className="octo-arms">
        <span className="octo-arms-label">
          {arms ? `${arms} tentacle${arms === 1 ? '' : 's'}` : 'drag the dot below onto a tool'}
        </span>
      </div>

      <Handle type="source" id="tools" position={Position.Bottom} className="octo-handle" />
      <Handle type="source" id="next" position={Position.Right} />
    </div>
  )
}
