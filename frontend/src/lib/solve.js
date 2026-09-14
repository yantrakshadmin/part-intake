/**
 * solve.js — client for the real nesting engine (POST /api/parts/{id}/solve,
 * GET /api/solve-jobs/{id}), and the adapter that turns one ranked layout
 * into the `fit`-shaped object the insert-drawing helpers in lib/packing.js
 * already consume (layerSummary, groupInserts, trayGeometry).
 */

const POLL_MS = 1500
// Two separate caps, not one flat one (F-AUDIT-1): the Celery worker's hard
// limit is 600s and real radiator-class parts take 40-500s once a worker
// picks the job up, so a single ~240s cap reported "no Celery worker" on a
// live solve. A job stuck at "pending" (never picked up) is still capped
// tight — that's the dead-worker case a single flat cap used to catch.
const MAX_POLLS_PENDING = 60   // 90s waiting for a worker to start it
const MAX_POLLS_PROCESSING = 440    // 660s total once it's "processing"

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

/** POST the solve, then poll until the job is done/failed. Resolves
 *  `{ result, solveJobId }` (see SolveResultOut on the backend) — the id is
 *  what lets a caller navigate to `?run=<id>` afterwards so the view shows
 *  what was just computed instead of re-solving on the next mount; rejects
 *  with an Error carrying the server's detail message on failure. `signal` (an
 *  AbortController's) cancels both the in-flight fetch and, if we're
 *  between polls, the wait itself — without it, a StrictMode double-mount
 *  or a fast part-to-part click leaves the previous solve's fetch/poll loop
 *  running to completion untracked (F2-2 F4). */
export async function runSolve(partId, { tareKg, vehicleName, topN = 5, assets, confirmedPoseOnly, clearanceMm, signal, projectId, onStarted, onCounts } = {}) {
  // Solving from a project page must land under that project's run list
  // (F2 contract: POST /api/projects/{id}/solve) — omitting projectId keeps
  // today's behaviour, posting straight to the part.
  const url = projectId != null ? `/api/projects/${projectId}/solve` : `/api/parts/${partId}/solve`
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tare_kg: tareKg ?? null, vehicle: vehicleName, top_n: topN,
      // Omitting/empty keeps today's unrestricted ranking (SolveIn.assets
      // defaults to None on the backend) — never send an empty array.
      assets: assets && assets.length ? assets : null,
      confirmed_pose_only: !!confirmedPoseOnly,
      // null (blank in the UI) keeps the backend's own default (5.0mm) —
      // SolveIn.clearance_mm, F-AUDIT-2.
      clearance_mm: clearanceMm ?? null,
    }),
    signal,
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(errorDetail(detail?.detail, `Solve request failed (${r.status})`))
  }
  const { solve_job_id } = await r.json()
  // Fired before the poll loop below, which can run for minutes — lets a
  // caller (ProjectPage) land this job in `project.runs` right away, so a
  // remount of the component that started it finds a pending/processing
  // run to poll (rule 1 of pickedRun) instead of posting a second solve
  // for the same part (PROD defect: two solve_part tasks 35s apart).
  onStarted?.(solve_job_id)
  // Two SEPARATE budgets: pickup (pending) and run (processing). One shared
  // counter charged the processing budget for time spent waiting on a worker,
  // so a slow pickup plus a legitimately long run threw "exceeded 600s" early.
  let pendingPolls = 0
  let processingPolls = 0
  while (true) {
    const res = await fetch(`/api/solve-jobs/${solve_job_id}`, { signal })
    if (!res.ok) throw new Error(`Solve status check failed (${res.status})`)
    const job = await res.json()
    if (job.status === 'done') {
      // Ticket 2b: counts land (status "done") before drawing_url/gif_url/
      // packed_url do -- render_status stays "pending" until a second
      // Celery task fills those in. Tell the caller the count is ready NOW
      // (onCounts) and keep polling this same job/cadence for the pictures
      // instead of resolving. job.status is "done" the whole time we wait
      // here, so neither poll cap above ticks up -- no timeout during the
      // render wait, only during the actual solve.
      const renderStatus = job.result.render_status ?? 'done'
      if (renderStatus !== 'pending') return { result: job.result, solveJobId: solve_job_id }
      onCounts?.(job.result)
    } else if (job.status === 'failed') {
      throw new Error(job.error || 'Solve failed')
    } else if (job.status === 'pending') {
      pendingPolls++
      if (pendingPolls > MAX_POLLS_PENDING) {
        throw new Error(`Solve did not finish in ${Math.round(MAX_POLLS_PENDING * POLL_MS / 1000)}s `
          + "— the job is still 'pending', which usually means no Celery worker is consuming the queue.")
      }
    } else {
      processingPolls++
      if (processingPolls > MAX_POLLS_PROCESSING) {
        throw new Error("Solve exceeded the worker's 600s limit — the job is still 'processing'.")
      }
    }
    await new Promise((resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'))
      // Already aborted? `abort` has fired and will not fire again, so the
      // listener alone would sit out a full POLL_MS before the next fetch
      // threw. And remove the listener on the normal path — otherwise one
      // solve leaves up to MAX_POLLS_PROCESSING of them on the same signal.
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
}

/**
 * Adapt one catalogue layout (grid + measured pitch) into the `fit` shape
 * the insert-drawing helpers consume. Placements come from grid × pitch —
 * the measured pitch already includes whatever clearance the real nesting
 * affords, so nothing here adds clearance or wall on top of it.
 */
export function layoutToFit(layout) {
  const { count, grid, extent_lbh: extent, pitch_lbh: pitch, pose_label, dunnage, interleave } = layout
  const [nx, ny, nz] = grid
  const perLayer = nx * ny

  // Prefer `interleave` (LayoutOut.interleave = pitch/extent per in-plane
  // axis, already measured on the backend) over `dunnage.archetype`: since
  // F-AUDIT-4 archetype is 'bar_and_rod' only when parts interleave BOTH
  // in-plane AND vertically, so a layout that overlaps in plan but stacks
  // flat (archetype 'pocket_tray', a slotted tray) would wrongly read as
  // non-interleaved off archetype alone. A ratio < 1 on either in-plane
  // axis means pitch is smaller than the part's own extent there —
  // consecutive parts overlap the naive pocket-per-part box (this is what
  // earns the Mubea bar 40 over the cuboid answer of 8). Axis 2 (stacking)
  // is excluded on purpose, same as the raw pitch/extent fallback below.
  // Fall back to archetype, then to the raw pitch/extent compare, only when
  // the backend didn't ship `interleave` (CLAUDE.md hard rule 9: prefer
  // what the backend already computed over re-deriving it).
  const interleaved = interleave
    ? interleave[0] < 1 || interleave[1] < 1
    : dunnage
      ? dunnage.archetype === 'bar_and_rod'
      : pitch[0] < extent[0] || pitch[1] < extent[1]

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
 * the number cannot disagree. The frontend used to re-derive its own floor
 * fit client-side (mixed-orientation split strips allowed) and drew 21
 * boxes per floor against the 18 the parts figure assumed.
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
