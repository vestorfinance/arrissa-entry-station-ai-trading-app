import { Handle, Position } from 'reactflow'
import { Zap, Repeat, Trash2, Copy } from 'lucide-react'
import { scheduleLabel } from './schedule.js'

// A flow's entry point: no target handle, one source handle. Two of them exist —
// "when called by another agent" and "on a schedule" — and they differ only in
// what they show, so they share one component rather than two near-copies.
export default function TriggerNode({ id, data, selected }) {
  const scheduled = data.kind === 'trigger-interval'
  const values = data.values || {}

  // What the node is set to do. Unset reads as an instruction, not as an error —
  // a trigger you have not configured yet is normal, it is just not finished.
  const value = scheduled ? scheduleLabel(values) : (values.requirement || '').trim()
  const unset = scheduled ? 'set an interval — click to configure'
                          : 'describe the input — click to configure'
  const request = scheduled ? (values.text || '').trim() : ''

  return (
    <div className={'fnode fnode--trigger' + (selected ? ' fnode--selected' : '')}>
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
        <span className={'fnode-icon fnode-icon--' + (scheduled ? 'schedule' : 'trigger')}>
          {scheduled ? <Repeat size={16} strokeWidth={1.75} /> : <Zap size={16} strokeWidth={1.75} />}
        </span>
        <div className="fnode-head-main">
          <div className="fnode-title">{data.label}</div>
          <div className="fnode-sub">{data.sub}</div>
        </div>
      </div>

      <div className="fnode-args">
        {value ? (
          <>
            <div className="fnode-value">{value}</div>
            {/* the schedule says WHEN; this says what it runs on when it fires */}
            {request && <div className="fnode-value fnode-value--sub">{request}</div>}
          </>
        ) : (
          <div className="fnode-arg fnode-arg--unset">
            <span className="fnode-arg-name">{scheduled ? 'schedule' : 'requirement'}</span>
            <span className="fnode-arg-type">{unset}</span>
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  )
}
