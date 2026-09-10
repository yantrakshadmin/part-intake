/**
 * solve.js — client for the real nesting engine (POST /api/parts/{id}/solve,
 * GET /api/solve-jobs/{id}), and the adapter that turns one ranked layout
 * into the `fit`-shaped object the insert-drawing helpers in lib/packing.js
 * already consume (layerSummary, groupInserts, trayGeometry).
 */

const POLL_MS = 1500
// A solve is ~11s; 4 minutes (160 polls) is generous. Without a cap, a dead
// worker (job stuck at "pending", never "processing"/"done"/"failed") spins
// this loop silently forever — see the F2-1 review note.
const MAX_POLLS = 160

/** FastAPI's validation error `detail` is a list of {loc, msg, type} dicts,
 *  not a string — `String(detail)` renders "[object Object]" (F2-2 F5).
 *  Used at every HTTP-error call site in this app: the solve POST below, and
 *  the custom-box saves in PackingRecommendation.jsx and LoadCalculator.jsx.
 *  Keep that list honest -- it said "both ... call sites" while
 *  LoadCalculator had its own separate save form that still threw the raw
 *  `detail` and rendered "[object Object]" on any 422. */
export function errorDetail(detail, fallback) {
  if (Array.isArray(detail)) {
    const msg = detail
      .map((d) => (Array.isArray(d?.loc) && d.loc.length ? `${d.loc.at(-1)}: ${d.msg}` : d?.msg))
      .filter(Boolean)
      .join('; ')
    return msg || fallback
  }
  return detail ? String(detail) : fallback
}

/** POST the solve, then poll until the job is done/failed. Resolves the
 *  `result` object (see SolveResultOut on the backend); rejects with an
 *  Error carrying the server's detail message on failure. `signal` (an
 *  AbortController's) cancels both the in-flight fetch and, if we're
 *  between polls, the wait itself — without it, a StrictMode double-mount
 *  or a fast part-to-part click leaves the previous solve's fetch/poll loop
 *  running to completion untracked (F2-2 F4). */
export async function runSolve(partId, { tareKg, vehicleName, topN = 5, assets, signal } = {}) {
  const r = await fetch(`/api/parts/${partId}/solve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tare_kg: tareKg ?? null, vehicle: vehicleName, top_n: topN,
      // Omitting/empty keeps today's unrestricted ranking (SolveIn.assets
      // defaults to None on the backend) — never send an empty array.
      assets: assets && assets.length ? assets : null,
    }),
    signal,
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(errorDetail(detail?.detail, `Solve request failed (${r.status})`))
  }
  const { solve_job_id } = await r.json()
  for (let poll = 0; poll < MAX_POLLS; poll++) {
    const res = await fetch(`/api/solve-jobs/${solve_job_id}`, { signal })
    if (!res.ok) throw new Error(`Solve status check failed (${res.status})`)
    const job = await res.json()
    if (job.status === 'done') return job.result
    if (job.status === 'failed') throw new Error(job.error || 'Solve failed')
    await new Promise((resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'))
      // Already aborted? `abort` has fired and will not fire again, so the
      // listener alone would sit out a full POLL_MS before the next fetch
      // threw. And remove the listener on the normal path — otherwise one
      // solve leaves up to MAX_POLLS of them on the same signal.
      if (signal?.aborted) return abort()
      let t
      const onAbort = () => { clearTimeout(t); abort() }
      t = setTimeout(() => {
        signal?.removeEventListener('abort', onAbort)
        resolve()
      }, POLL_MS)
      signal?.addEventListener('abort', onAbort, { once: true })
    })
  }
  throw new Error(`Solve did not finish in ${Math.round(MAX_POLLS * POLL_MS / 60000)} min — the job `
    + "is still 'pending', which usually means no Celery worker is consuming the queue.")
}

/**
 * Adapt one catalogue layout (grid + measured pitch) into the `fit` shape
 * the insert-drawing helpers consume. Placements come from grid × pitch —
 * the measured pitch already includes whatever clearance the real nesting
 * affords, so nothing here adds clearance or wall on top of it.
 */
export function layoutToFit(layout) {
  const { count, grid, extent_lbh: extent, pitch_lbh: pitch, pose_label } = layout
  const [nx, ny, nz] = grid
  const perLayer = nx * ny

  // In-plane pitch smaller than the part's own extent means consecutive
  // parts overlap the naive pocket-per-part box — they interleave (this is
  // what earns the Mubea bar 40 over the cuboid answer of 8). Z pitch is
  // excluded: pitch[2] < extent[2] is just normal nesting depth between
  // layers, not an overlap (F2-2 F1).
  const interleaved = pitch[0] < extent[0] || pitch[1] < extent[1]

  const placements = []
  for (let i = 0; i < nx; i++)
    for (let j = 0; j < ny; j++)
      placements.push({ x: i * pitch[0], y: j * pitch[1], w: extent[0], h: extent[1], rot: false })
  const fill = { count: placements.length, placements, desc: `${nx} × ${ny} per layer` }

  let left = count
  const layerConfig = Array.from({ length: nz }, () => {
    const filled = Math.min(perLayer, Math.max(0, left))
    left -= filled
    return { code: pose_label, label: pose_label, count: perLayer, filled,
              fill, partH: extent[2], layerH: pitch[2] }
  })

  return { total: count, layers: nz, perLayer, layerConfig, fill, interleaved }
}

/**
 * The truck floor arrangement to DRAW, straight from the backend's own fit.
 *
 * `truck.floor_grid` / `floor_rotated` come from the same expression in
 * `engine.parts_per_truck` that produced `truck.boxes`, so the drawing and
 * the number cannot disagree. The frontend used to re-derive this with
 * `floorFit`/`bestFill`, which allows mixed-orientation split strips, and
 * drew 21 boxes per floor against the 18 the parts figure assumed.
 */
export function floorPlanFromTruck(truck, box) {
  const [nx, ny] = truck.floor_grid
  const [cellL, cellB] = truck.floor_rotated
    ? [box.outer_b_mm, box.outer_l_mm]
    : [box.outer_l_mm, box.outer_b_mm]
  const placements = []
  for (let i = 0; i < nx; i++)
    for (let j = 0; j < ny; j++)
      placements.push({ x: i * cellL, y: j * cellB, w: cellL, h: cellB, rot: truck.floor_rotated })
  const floor = { count: nx * ny, placements, desc: `${nx} × ${ny}` }
  const layers = floor.count > 0 ? Math.max(1, Math.ceil(truck.boxes / floor.count)) : 0
  return { floor, layers, total: truck.boxes }
}
