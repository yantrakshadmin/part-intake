/**
 * InsertIso — static isometric SVG drawing of an insert tray with CAD-style
 * dimension callouts (overall L × B × H plus pocket cutout sizes), matching
 * the team's SketchUp reference renders. Pure SVG: no WebGL context, so any
 * number of insert figures can sit on one page, and they print cleanly.
 *
 * Geometry comes from trayGeometry() (lib/packing.js): a slab L×B×H with
 * rectangular pockets cut from the top down to the floor sheet (floorH).
 *
 * Projection is standard 30° isometric with the camera on the +x+y+z side,
 * so the visible faces are those with +x / +y / +z normals. Inside each
 * pocket that means its floor and the two walls at min-x / min-y; in
 * orthographic projection everything visible through a rectangular opening
 * projects inside the opening's screen polygon, so pocket interiors are
 * simply clipped to it (correct occlusion for any depth, no z-sorting).
 */
import { useId } from 'react'

const C = Math.cos(Math.PI / 6)
const S = Math.sin(Math.PI / 6)
const P = (x, y, z) => [(x - y) * C, (x + y) * S - z]

export { P as isoProject, path as isoPath, fmt as fmtMm, dimension as isoDimension }

const COL = {
  top: '#7b84ec', right: '#5a63dd', front: '#4a51c5',
  wallX: '#6a73e4', wallY: '#5159cf',
  floor: '#eef0fb', floorDummy: '#9aa0b8',
  edge: '#2a3070', dim: '#3f3f46', ext: '#a1a1aa', text: '#27272a',
}

const fmt = (v) => {
  const r = Math.round(v * 10) / 10
  return Number.isInteger(r) ? String(r) : r.toFixed(1)
}
const path = (pts) =>
  'M' + pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join('L') + 'Z'
const mv = (p, d, t) => [p[0] + d[0] * t, p[1] + d[1] * t, p[2] + d[2] * t]

/**
 * One dimension callout: extension lines from model points a→b offset along
 * model direction `dir`, a dimension line with 45° architectural ticks, and
 * a horizontal bold label. Returns elements + the screen points it spans
 * (for viewBox fitting).
 */
function dimension({ a, b, dir, off, label, k }) {
  const A = P(...mv(a, dir, off))
  const B = P(...mv(b, dir, off))
  const over = off * 1.14
  const exts = [
    [P(...mv(a, dir, off * 0.1)), P(...mv(a, dir, over))],
    [P(...mv(b, dir, off * 0.1)), P(...mv(b, dir, over))],
  ]

  // screen-space unit vector along the dimension line + 45° tick
  const ux = B[0] - A[0], uy = B[1] - A[1]
  const ul = Math.hypot(ux, uy) || 1
  const u = [ux / ul, uy / ul]
  const t = [(u[0] - u[1]) * 0.707, (u[0] + u[1]) * 0.707]
  const tl = 5.5 * k

  // label sits past the line along the (projected) offset direction
  const dp = P(...dir)
  const dl = Math.hypot(dp[0], dp[1]) || 1
  const mid = [(A[0] + B[0]) / 2, (A[1] + B[1]) / 2]
  const lp = [mid[0] + (dp[0] / dl) * 14 * k, mid[1] + (dp[1] / dl) * 14 * k]

  const els = (
    <g>
      {exts.map(([p, q], i) => (
        <line key={i} x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]}
          stroke={COL.ext} strokeWidth={0.9 * k} />
      ))}
      <line x1={A[0]} y1={A[1]} x2={B[0]} y2={B[1]}
        stroke={COL.dim} strokeWidth={1.1 * k} />
      {[A, B].map((p, i) => (
        <line key={'t' + i}
          x1={p[0] - t[0] * tl} y1={p[1] - t[1] * tl}
          x2={p[0] + t[0] * tl} y2={p[1] + t[1] * tl}
          stroke={COL.dim} strokeWidth={1.1 * k} />
      ))}
      <text x={lp[0]} y={lp[1]} textAnchor="middle" dominantBaseline="middle"
        fontSize={13.5 * k} fontWeight="700" fill={COL.text}
        stroke="#fff" strokeWidth={3 * k} paintOrder="stroke"
        fontFamily="'Fira Sans', system-ui, sans-serif">
        {label}
      </text>
    </g>
  )
  return { els, pts: [A, B, ...exts.flat(), lp] }
}

export default function InsertIso({ geom, dummySet, width = 460 }) {
  const uid = useId()
  const { L, B, H, floorH, pockets } = geom
  const dummies = dummySet ?? new Set()

  // provisional scale: projected tray width ≈ (L + B) · cos30
  const k = ((L + B) * C * 1.34) / width

  const D = Math.max(L, B)
  const g1 = Math.max(0.075 * D, 26 * k) // pocket + height dims
  const g2 = g1 * 2.2                    // overall dims

  const top = [P(0, 0, H), P(L, 0, H), P(L, B, H), P(0, B, H)]
  const corners = [
    ...top, P(0, B, 0), P(L, B, 0), P(L, 0, 0),
  ]

  const pocketEls = pockets.map((p, i) => {
    const { x, y, w, h } = p
    const x1 = x + w, y1 = y + h
    const open = [P(x, y, H), P(x1, y, H), P(x1, y1, H), P(x, y1, H)]
    const id = `${uid}-pk${i}`
    return (
      <g key={i}>
        <clipPath id={id}><path d={path(open)} /></clipPath>
        <g clipPath={`url(#${id})`}>
          <path d={path([P(x, y, floorH), P(x1, y, floorH),
                         P(x1, y1, floorH), P(x, y1, floorH)])}
            fill={dummies.has(i) ? COL.floorDummy : COL.floor} />
          <path d={path([P(x, y, floorH), P(x, y1, floorH),
                         P(x, y1, H), P(x, y, H)])} fill={COL.wallX} />
          <path d={path([P(x, y, floorH), P(x1, y, floorH),
                         P(x1, y, H), P(x, y, H)])} fill={COL.wallY} />
        </g>
      </g>
    )
  })

  // top lattice: outer rect minus all pocket openings (evenodd)
  const lattice = path(top) + pockets
    .map((p) => path([P(p.x, p.y, H), P(p.x + p.w, p.y, H),
                      P(p.x + p.w, p.y + p.h, H), P(p.x, p.y + p.h, H)]))
    .join('')

  // dimensions: overall L (upper right), B (upper left), H (right side),
  // plus the cutout of the pocket nearest each labeled edge
  const dims = [
    dimension({ a: [0, 0, H], b: [L, 0, H], dir: [0, -1, 0], off: g2,
                label: fmt(L), k }),
    dimension({ a: [0, 0, H], b: [0, B, H], dir: [-1, 0, 0], off: g2,
                label: fmt(B), k }),
    dimension({ a: [L, 0, 0], b: [L, 0, H], dir: [0.707, -0.707, 0], off: g1,
                label: fmt(H), k }),
  ]
  if (pockets.length) {
    const pw = [...pockets].sort((p, q) => p.y - q.y || p.x - q.x)[0]
    const ph = [...pockets].sort((p, q) => p.x - q.x || p.y - q.y)[0]
    dims.push(dimension({
      a: [pw.x, pw.y, H], b: [pw.x + pw.w, pw.y, H],
      dir: [0, -1, 0], off: g1 + pw.y, label: fmt(pw.w), k,
    }))
    dims.push(dimension({
      a: [ph.x, ph.y, H], b: [ph.x, ph.y + ph.h, H],
      dir: [-1, 0, 0], off: g1 + ph.x, label: fmt(ph.h), k,
    }))
  }

  // fit viewBox around everything
  const all = [...corners, ...dims.flatMap((d) => d.pts)]
  let mnX = Infinity, mnY = Infinity, mxX = -Infinity, mxY = -Infinity
  for (const [x, y] of all) {
    if (x < mnX) mnX = x
    if (y < mnY) mnY = y
    if (x > mxX) mxX = x
    if (y > mxY) mxY = y
  }
  const pad = 34 * k
  const vb = [mnX - pad, mnY - pad, mxX - mnX + 2 * pad, mxY - mnY + 2 * pad]
  const height = Math.round(width * (vb[3] / vb[2]))

  return (
    <svg className="insert-iso" viewBox={vb.join(' ')}
      width={width} height={height} role="img"
      aria-label={`Insert tray ${fmt(L)} × ${fmt(B)} × ${fmt(H)} mm`}>
      {/* outer faces: x=L (right), y=B (front-left) */}
      <path d={path([P(L, 0, 0), P(L, B, 0), P(L, B, H), P(L, 0, H)])}
        fill={COL.right} stroke={COL.edge} strokeWidth={0.8 * k} />
      <path d={path([P(0, B, 0), P(L, B, 0), P(L, B, H), P(0, B, H)])}
        fill={COL.front} stroke={COL.edge} strokeWidth={0.8 * k} />
      {pocketEls}
      <path d={lattice} fillRule="evenodd" fill={COL.top}
        stroke={COL.edge} strokeWidth={0.8 * k} />
      {dims.map((d, i) => <g key={i}>{d.els}</g>)}
    </svg>
  )
}
