import { BaseEdge, EdgeLabelRenderer, getBezierPath } from 'reactflow'
import { X } from 'lucide-react'

// Connector between two nodes. Hovering the edge (or selecting it) reveals a
// disconnect button at its midpoint; the endpoints can also be dragged off a
// handle to disconnect.
export default function DeletableEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, markerEnd, style, selected, data,
}) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <button
          className={'edge-cut' + (selected ? ' edge-cut--on' : '')}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          title="Disconnect"
          onClick={(e) => { e.stopPropagation(); data?.onDelete?.(id) }}
        >
          <X size={12} strokeWidth={2.5} />
        </button>
      </EdgeLabelRenderer>
    </>
  )
}
