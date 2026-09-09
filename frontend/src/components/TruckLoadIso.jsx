/**
 * TruckLoadIso — animated isometric SVG of the truck loading plan: the
 * vehicle cargo space drawn as an open wireframe, boxes from the floor-fit
 * placements stacked layer by layer, each box sliding in with a staggered
 * CSS animation (reads like a GIF, stays crisp/zoomable/downloadable).
 *
 * Same 30° isometric projection and painter's-order rules as InsertIso:
 * camera on +x+y+z, so a box's visible faces are +x / +y / +z and boxes
 * are drawn back-to-front (low z, then low x+y) — which is also the
 * natural loading order, so DOM order doubles as the animation order.
 */
import { useMemo, useState } from 'react'
import { isoProject as P, isoPath as path, fmtMm as fmt, isoDimension } from './InsertIso.jsx'

const COL = {
  bedFloor: '#eceef6', bedWallX: '#e2e5f1', bedWallY: '#d7dbec',
  edge: '#2a3070', frame: '#9aa0b8',
}

const LAYER_SHADES = [
  { top: '#7b84ec', right: '#5a63dd', front: '#4a51c5' },
  { top: '#93a0f2', right: '#7280e6', front: '#5f6cd2' },
]

const MAX_BOXES = 240

export default function TruckLoadIso({ vehicle, box, plan }) {
  const [run, setRun] = useState(0) // bump to replay the animation

  const { L, B, H, boxes, shown, total } = useMemo(() => {
    const L = vehicle.cargo_l_mm, B = vehicle.cargo_b_mm, H = vehicle.cargo_h_mm
    const bh = box.outer_h_mm
    const all = []
    outer:
    for (let layer = 0; layer < plan.layers; layer++) {
      for (const p of plan.floor.placements) {
        if (all.length >= plan.total) break outer
        all.push({ x: p.x, y: p.y, z: layer * bh, w: p.w, d: p.h, h: bh, layer })
      }
    }
    // painter + loading order: low layers first, then back-to-front
    all.sort((a, b) => a.z - b.z || (a.x + a.y) - (b.x + b.y))
    return { L, B, H, boxes: all.slice(0, MAX_BOXES), shown: Math.min(all.length, MAX_BOXES), total: all.length }
  }, [vehicle, box, plan])

  const k = ((L + B) * Math.cos(Math.PI / 6) * 1.3) / 760
  const g = Math.max(0.06 * Math.max(L, B), 30 * k)

  const dims = [
    isoDimension({ a: [0, 0, H], b: [L, 0, H], dir: [0, -1, 0], off: g, label: fmt(L), k }),
    isoDimension({ a: [0, 0, H], b: [0, B, H], dir: [-1, 0, 0], off: g, label: fmt(B), k }),
    isoDimension({ a: [L, 0, 0], b: [L, 0, H], dir: [0.707, -0.707, 0], off: g, label: fmt(H), k }),
  ]

  // viewBox over cargo corners + dim extents
  const pts = [
    P(0, 0, 0), P(L, 0, 0), P(0, B, 0), P(L, B, 0),
    P(0, 0, H), P(L, 0, H), P(0, B, H), P(L, B, H),
    ...dims.flatMap((d) => d.pts),
  ]
  let mnX = Infinity, mnY = Infinity, mxX = -Infinity, mxY = -Infinity
  for (const [x, y] of pts) {
    if (x < mnX) mnX = x
    if (y < mnY) mnY = y
    if (x > mxX) mxX = x
    if (y > mxY) mxY = y
  }
  const pad = 34 * k
  const vb = [mnX - pad, mnY - pad, mxX - mnX + 2 * pad, mxY - mnY + 2 * pad]
  const height = Math.round(760 * (vb[3] / vb[2]))

  // cargo edges drawn over the boxes, like a CAD wireframe
  const edges = [
    [[0, 0, 0], [L, 0, 0]], [[0, 0, 0], [0, B, 0]], [[L, 0, 0], [L, B, 0]], [[0, B, 0], [L, B, 0]],
    [[0, 0, H], [L, 0, H]], [[0, 0, H], [0, B, H]], [[L, 0, H], [L, B, H]], [[0, B, H], [L, B, H]],
    [[0, 0, 0], [0, 0, H]], [[L, 0, 0], [L, 0, H]], [[0, B, 0], [0, B, H]], [[L, B, 0], [L, B, H]],
  ]

  return (
    <div className="truck-iso-wrap">
      <svg key={run} className="truck-iso insert-iso" viewBox={vb.join(' ')}
        width={760} height={height} role="img"
        aria-label={`Truck loading plan: ${total} boxes`}>
        {/* cargo bed: floor + the two far walls */}
        <path d={path([P(0, 0, 0), P(L, 0, 0), P(L, B, 0), P(0, B, 0)])} fill={COL.bedFloor} />
        <path d={path([P(0, 0, 0), P(0, B, 0), P(0, B, H), P(0, 0, H)])} fill={COL.bedWallX} />
        <path d={path([P(0, 0, 0), P(L, 0, 0), P(L, 0, H), P(0, 0, H)])} fill={COL.bedWallY} />

        {boxes.map((b, i) => {
          const c = LAYER_SHADES[b.layer % 2]
          const x1 = b.x + b.w, y1 = b.y + b.d, z1 = b.z + b.h
          return (
            <g key={i} className="truck-box"
              style={{ animationDelay: `${Math.min(i * 45, 6000)}ms` }}>
              <path d={path([P(x1, b.y, b.z), P(x1, y1, b.z), P(x1, y1, z1), P(x1, b.y, z1)])}
                fill={c.right} stroke={COL.edge} strokeWidth={0.7 * k} />
              <path d={path([P(b.x, y1, b.z), P(x1, y1, b.z), P(x1, y1, z1), P(b.x, y1, z1)])}
                fill={c.front} stroke={COL.edge} strokeWidth={0.7 * k} />
              <path d={path([P(b.x, b.y, z1), P(x1, b.y, z1), P(x1, y1, z1), P(b.x, y1, z1)])}
                fill={c.top} stroke={COL.edge} strokeWidth={0.7 * k} />
            </g>
          )
        })}

        {edges.map(([a, b], i) => {
          const [x1, y1] = P(...a), [x2, y2] = P(...b)
          return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
            stroke={COL.frame} strokeWidth={1 * k} strokeDasharray={`${5 * k} ${4 * k}`} />
        })}

        {dims.map((d, i) => <g key={'d' + i}>{d.els}</g>)}
      </svg>
      <div className="truck-iso-foot">
        <span className="muted">
          {total} boxes · {plan.floor.desc} · {plan.layers} high
          {shown < total && ` · first ${shown} drawn`}
        </span>
        <button className="btn-ghost" onClick={() => setRun(run + 1)}>↻ Replay</button>
      </div>
    </div>
  )
}
