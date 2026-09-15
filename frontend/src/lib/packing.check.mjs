/**
 * packing.check.mjs — acceptance self-check for F2-1 (plain node, no
 * framework). Feeds layoutToFit a real backend layout — a shipped TRW
 * proposal, PLS12803, 48 wheels / (3,2,8) / 6 pockets — not a snapshot of
 * our own output. If the adapter is wrong, this number moves.
 *
 * Run: node src/lib/packing.check.mjs
 */
import assert from 'node:assert/strict'
import { layoutToFit, runSolve, errorDetail, floorPlanFromTruck } from './solve.js'
import { resultRows } from './packing.js'

// Copied verbatim from a real backend /api/parts/{id}/solve run.
const layout = {
  asset_name: 'PLS12803', pose_label: 'Largest face down (most stable)',
  count: 48, grid: [3, 2, 8], extent_lbh: [372.0, 356.0, 140.0],
  pitch_lbh: [377.0, 361.0, 121.0], limited_by: 'geometry',
  interleave: [1.013, 1.014, 0.864],
}
// PLS12803 inner is 1150 x 750 x 1000, outer 1200 x 800 x 1196, 600 kg
const box = { inner_l_mm: 1150, inner_b_mm: 750, inner_h_mm: 1000 }

const fit = layoutToFit(layout)

assert.equal(fit.total, 48)
assert.equal(fit.layers, 8)
assert.equal(fit.perLayer, 6)
assert.equal(fit.layerConfig.length, 8)
for (const l of fit.layerConfig) {
  assert.equal(l.filled, layout.count / layout.grid[2]) // 6
  assert.equal(l.filled, l.count)
}
assert.equal(fit.fill.placements.length, 6)
assert.equal(fit.interleaved, false) // non-interleaving path is unchanged (F2-2 F1)

// Derived from fit.layerConfig (partH/layerH), not from the input literals
// directly — this exercises what layoutToFit did with them, not just the
// fixture arithmetic.
const [firstLayer] = fit.layerConfig
const stackHeight = firstLayer.partH + (fit.layers - 1) * firstLayer.layerH
assert.equal(stackHeight, 987)
assert.ok(stackHeight <= box.inner_h_mm, `stack ${stackHeight} > inner H ${box.inner_h_mm}`)

console.log('packing.check.mjs: all assertions passed —', fit.total, '/',
  layout.grid.join(','))

// Mubea stabiliser bar, PLS12801 — the real interleaved case: 40/PLS12801,
// the number this whole project is founded on (best cuboid answer is 8).
// Measured verbatim from tests/test_nesting.py::test_real_bar. Pitch across
// (140) is less than the part's own extent (300): consecutive pockets in
// this pose overlap by 160 mm. layoutToFit must flag this (F2-2 F1) so the
// caller (the backend's own dunnage BOM, since F10) renders a note instead
// of an unmanufacturable pocket-per-part tray.
const barLayout = {
  asset_name: 'PLS12801', count: 40, grid: [1, 4, 10],
  extent_lbh: [1092.0, 300.0, 148.0], pitch_lbh: [1092.0, 140.0, 68.0],
  limited_by: 'geometry', interleave: [1.0, 0.467, 0.459],
  pose_label: 'Largest face down (most stable)',
}
const barFit = layoutToFit(barLayout)
assert.equal(barFit.total, 40)
assert.equal(barFit.layers, 10)
assert.equal(barFit.perLayer, 4)
assert.equal(barFit.interleaved, true)

console.log('packing.check.mjs: interleave check passed —', barFit.total,
  '/', barLayout.grid.join(','), '/ interleaved:', barFit.interleaved)

// --- runSolve: the two F2-2 fixes that had no check at all -----------------
// `fetch` is stubbed, not mocked with a library — this is a self-check, not a
// test suite. Each case asserts the branch that was broken.

// F5: FastAPI puts a LIST of {loc, msg} in `detail`; String() rendered that
// as "[object Object]" in the warning bar. Real payload shape, real bound:
// SolveIn.tare_kg is gt=0, le=200, so 250 produces exactly this.
assert.equal(
  errorDetail([{ loc: ['body', 'tare_kg'], msg: 'Input should be less than or equal to 200' }],
              'fallback'),
  'tare_kg: Input should be less than or equal to 200')
assert.equal(errorDetail('plain string detail', 'fallback'), 'plain string detail')
assert.equal(errorDetail(undefined, 'fallback'), 'fallback')
assert.equal(errorDetail([], 'fallback'), 'fallback')  // empty list -> not ''

const jsonRes = (body, ok = true, status = 200) =>
  ({ ok, status, json: async () => body })

// A 422 must surface the field message, not "[object Object]".
globalThis.fetch = async () => jsonRes(
  { detail: [{ loc: ['body', 'tare_kg'], msg: 'Input should be greater than 0' }] },
  false, 422)
await assert.rejects(runSolve(1, { tareKg: 0 }),
  (e) => { assert.match(e.message, /tare_kg: Input should be greater than 0/); return true })

// F4: abort mid-poll must reject with AbortError and stop polling. The job
// never leaves "pending", so without the signal this would run to MAX_POLLS_PENDING.
let polls = 0
globalThis.fetch = async (url) => {
  // Discriminate on '/solve-jobs/', NOT '/solve' — the status URL
  // (/api/solve-jobs/j1) contains '/solve' too, so the loose match answered
  // every poll with the POST body, left `polls` at 0, and made the
  // "polling stopped" assertion below pass without polling ever happening.
  if (String(url).includes('/solve-jobs/')) { polls++; return jsonRes({ status: 'pending' }) }
  return jsonRes({ solve_job_id: 'j1' })
}
const ac = new AbortController()
const pending = runSolve(1, { tareKg: 30, signal: ac.signal })
setTimeout(() => ac.abort(), 50)
await assert.rejects(pending, (e) => { assert.equal(e.name, 'AbortError'); return true })
const pollsAtAbort = polls
assert.ok(pollsAtAbort > 0, 'no poll ever happened — the stub never answered a status GET')
await new Promise((r) => setTimeout(r, 200))
assert.equal(polls, pollsAtAbort, `polling continued after abort: ${pollsAtAbort} -> ${polls}`)

// An already-aborted signal must not sit out a poll interval first.
const ac2 = new AbortController()
ac2.abort()
const t0 = Date.now()
await assert.rejects(runSolve(1, { signal: ac2.signal }),
  (e) => { assert.equal(e.name, 'AbortError'); return true })
assert.ok(Date.now() - t0 < 500, `pre-aborted signal waited ${Date.now() - t0}ms`)

console.log('packing.check.mjs: runSolve checks passed — 422 detail readable,'
  + ` abort stops polling (${pollsAtAbort} poll(s), none after)`)

// Ticket 2b: counts land (job.status "done") before drawing_url/gif_url/
// packed_url do. onCounts must fire with the still-pending result as soon
// as status flips to "done", and runSolve must keep polling the SAME job
// (not stop, not throw) until render_status leaves "pending".
let renderPolls = 0
const onCountsSeen = []
globalThis.fetch = async (url) => {
  if (String(url).includes('/solve-jobs/')) {
    renderPolls++
    return renderPolls < 3
      ? jsonRes({ status: 'done', result: { best_count: 48, render_status: 'pending' } })
      : jsonRes({ status: 'done', result: { best_count: 48, render_status: 'done', drawing_url: '/x.png' } })
  }
  return jsonRes({ solve_job_id: 'j2' })
}
const { result: renderResult } = await runSolve(1, { onCounts: (r) => onCountsSeen.push(r.render_status) })
assert.deepEqual(onCountsSeen, ['pending', 'pending'], 'onCounts should fire once per still-pending poll')
assert.equal(renderResult.render_status, 'done')
assert.equal(renderResult.drawing_url, '/x.png')
assert.equal(renderPolls, 3, 'should have kept polling the same job through the pending renders')

console.log('packing.check.mjs: render_status pending->done checks passed —'
  + ` ${renderPolls} polls, onCounts fired ${onCountsSeen.length}x before drawing_url landed`)

// --- floorPlanFromTruck: the drawing must not disagree with the number -----
// Real backend output for the wheel (tests/test_solve_api.py block 2):
// PLS12803, 48 boxes, 2304 parts, floor_grid (8,3), floor_rotated false.
// 32ft SXL cargo is 9754 x 2438 x 2438 (app/seed_data.py VEHICLES),
// so 2438 / 1196mm box outer height = 2 layers.
// 8 x 3 x 2 = 48 == truck.boxes. That equality is the whole point.
const truck = {
  vehicle: '32_ft_sxl', asset_name: 'PLS12803', boxes: 48, parts: 2304,
  kg_per_box: 150.0, limited_by: 'volume', floor_grid: [8, 3], floor_rotated: false,
}
const truckBox = { outer_l_mm: 1200, outer_b_mm: 800, outer_h_mm: 1196 }
const plan = floorPlanFromTruck(truck, truckBox)
assert.equal(plan.floor.count, 24)
assert.equal(plan.floor.placements.length, 24)
assert.equal(plan.layers, 2)
assert.equal(plan.floor.count * plan.layers, truck.boxes)   // drawing == number
assert.equal(plan.total, 48)
// Every box on the floor, inside the trailer, un-rotated footprint.
for (const p of plan.floor.placements) {
  assert.equal(p.w, 1200)
  assert.equal(p.h, 800)
  assert.ok(p.x + p.w <= 9754, `box overruns cargo length: ${p.x + p.w}`)
  assert.ok(p.y + p.h <= 2438, `box overruns cargo breadth: ${p.y + p.h}`)
}
// Rotated: the footprint swaps, and the grid is read in the rotated frame.
const rot = floorPlanFromTruck(
  { ...truck, floor_grid: [3, 2], floor_rotated: true, boxes: 6 }, truckBox)
assert.equal(rot.floor.placements[0].w, 800)
assert.equal(rot.floor.placements[0].h, 1200)
assert.equal(rot.floor.count, 6)

console.log('packing.check.mjs: truck floor plan passed —',
  `${truck.floor_grid.join(' x ')}/floor x ${plan.layers} = ${truck.boxes} boxes`)

// --- F-AUDIT-4: interleaved must prefer the backend-shipped `interleave` ----
// ratios (pitch/extent per in-plane axis) over `dunnage.archetype`. Since
// F-AUDIT-4, archetype is 'bar_and_rod' only when a layout interleaves BOTH
// in-plane AND vertically — a layout that overlaps in plan but stacks flat
// comes back 'pocket_tray' (a slotted tray), so archetype alone can no
// longer tell the pocket-per-part-tray-is-invalid case apart. `interleave`
// is required on LayoutOut and is always the more specific signal.
const planOverlapOnly = {
  ...barLayout, interleave: [1.01, 0.9, 1], dunnage: { archetype: 'pocket_tray' },
}
assert.equal(layoutToFit(planOverlapOnly).interleaved, true)

// Converse: only the stacking axis overlaps (normal layer nesting depth,
// not a plan overlap) — must read non-interleaved even if some dunnage
// classification said otherwise; axis 2 is deliberately excluded.
const stackOnlyOverlap = {
  ...barLayout, interleave: [1, 1, 0.9], dunnage: { archetype: 'bar_and_rod' },
}
assert.equal(layoutToFit(stackOnlyOverlap).interleaved, false)

// Fallback to dunnage.archetype only when `interleave` itself is absent
// (the custom design's BoxDesignOut carries no `interleave` field).
const { interleave: _drop, ...barLayoutNoInterleave } = barLayout
assert.equal(
  layoutToFit({ ...barLayoutNoInterleave, pitch_lbh: [1092.0, 400.0, 68.0],
    dunnage: { archetype: 'bar_and_rod' } }).interleaved, true)
assert.equal(
  layoutToFit({ ...barLayoutNoInterleave, dunnage: { archetype: 'pocket_tray' } }).interleaved,
  false)

// Fallback to the raw pitch/extent compare when neither `interleave` nor
// `dunnage` is present at all.
assert.equal(layoutToFit(barLayoutNoInterleave).interleaved, true) // pitch[1] 140 < extent[1] 300

console.log('packing.check.mjs: interleaved-from-interleave-ratios passed')

// --- F13: resultRows -- the results-panel key/value row builder -----------
// (a) a row is omitted (not NaN/undefined/"—") whenever its field is absent.
const rowLabels = (rows) => rows.map((r) => r.label)
const bareLayout = { asset_name: 'PLS12801', pose_label: 'Largest face down (most stable)',
  count: 40, grid: [1, 4, 10], extent_lbh: [1092.0, 300.0, 148.0], pitch_lbh: [1092.0, 140.0, 68.0] }

const noBoxNoDunnageNoTruck = resultRows({ layout: bareLayout })
assert.ok(!rowLabels(noBoxNoDunnageNoTruck).some((l) =>
  ['Outer L×B×H', 'Inner L×B×H', 'Insert type', 'Build height / inner height',
    'Vertical slack', 'Parts per truck', 'Cuboid baseline'].includes(l)),
  'a row for an absent field must not appear at all')
for (const r of noBoxNoDunnageNoTruck) {
  assert.ok(!String(r.value).includes('NaN'), `NaN rendered in row ${r.label}`)
  assert.ok(!String(r.value).includes('undefined'), `undefined rendered in row ${r.label}`)
}

const rowsBox = { outer_l_mm: 1200, outer_b_mm: 800, outer_h_mm: 1196,
  inner_l_mm: 1150, inner_b_mm: 750, inner_h_mm: 1000 }
const rowsDunnage = { archetype: 'bar_and_rod', build_height_mm: 987, inner_h_mm: 1000,
  slack_lbh: [12, 8, 13] }
const rowsTruck = { asset_name: 'PLS12801', parts: 1600 }
const full = resultRows({ layout: { ...bareLayout, cuboid_count: 8, dunnage: rowsDunnage }, box: rowsBox, truck: rowsTruck })
assert.deepEqual(rowLabels(full),
  ['Box', 'Outer L×B×H', 'Inner L×B×H', 'Parts per box', 'Layers', 'Grid', 'Pose',
    'Part extent L×B×H', 'Pitch L×B×H', 'Cuboid baseline', 'Insert type',
    'Build height / inner height', 'Vertical slack', 'Parts per truck'])

// (b) no row value is computed from two other fields -- each value must be
// traceable to ONE field (or a plain join of one field's own components),
// never an arithmetic combination of two separate backend fields.
const find = (label) => full.find((r) => r.label === label).value
assert.equal(find('Parts per box'), bareLayout.count) // identity, not e.g. count * weight
assert.equal(find('Grid'), bareLayout.grid.join(' × ')) // raw join, not grid[0]*grid[1]*grid[2]
assert.equal(find('Build height / inner height'), '987 / 1000 mm') // two fields shown side by side, not subtracted
assert.equal(find('Cuboid baseline'), 8) // the value passed in, not layout.count or any other field
assert.equal(find('Parts per truck'), rowsTruck.parts)

// F13 round 2: extent is 0 dp (pitch stays 1 dp) -- a fractional extent (as
// extracted from real CAD, e.g. 137.71mm) must round to the nearest mm here,
// not carry decimals into the kv list.
const fractional = resultRows({
  layout: { ...bareLayout, extent_lbh: [371.4, 353.86, 137.71], pitch_lbh: [377.2, 361.0, 116.4] },
})
assert.equal(fractional.find((r) => r.label === 'Part extent L×B×H').value, '371 × 354 × 138 mm')
assert.equal(fractional.find((r) => r.label === 'Pitch L×B×H').value, '377.2 × 361 × 116.4 mm')

// cuboid_count === 0 (the stored-run default, LayoutOut.cuboid_count) reads
// as absent, not a real tie with the cuboid baseline.
assert.ok(!rowLabels(resultRows({ layout: { ...bareLayout, cuboid_count: 0 } }))
  .includes('Cuboid baseline'))

console.log('packing.check.mjs: resultRows passed —',
  full.length, 'rows full,', noBoxNoDunnageNoTruck.length, 'rows bare')
