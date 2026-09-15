/**
 * InsertIso — shared isometric-SVG helpers (CLAUDE.md hard rule 5: shared iso
 * helpers live here and are reused elsewhere — TruckLoadIso.jsx's truck-load
 * drawing is the other consumer). Pure SVG: no WebGL context, so any number
 * of figures can sit on one page, and they print cleanly.
 *
 * Projection is standard 30° isometric with the camera on the +x+y+z side,
 * so the visible faces are those with +x / +y / +z normals. Inside each
 * pocket that means its floor and the two walls at min-x / min-y; in
 * orthographic projection everything visible through a rectangular opening
 * projects inside the opening's screen polygon, so pocket interiors are
 * simply clipped to it (correct occlusion for any depth, no z-sorting).
 *
 * The tray-drawing component this file used to also export by default is
 * gone (F10): the insert tab now shows only the backend's own dunnage
 * drawing (DunnageBom in PackingRecommendation.jsx), never a client-derived
 * one.
 */
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

