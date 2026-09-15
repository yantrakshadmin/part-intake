/**
 * packing.js — small text/label helpers for the packing result view.
 *
 * The client-derived insert tray geometry helpers that used to live here are
 * gone (F10): they re-derived a tray drawing the backend's own dunnage BOM
 * already computes, and had no drawing at all for interleaved poses
 * (CLAUDE.md hard rule 9). The insert tab now shows only the backend's
 * `layout.dunnage` drawing/BOM (DunnageBom in PackingRecommendation.jsx).
 *
 * The vehicle floor-planning that used to live here is also gone
 * (F-AUDIT-1): it re-derived a floor pattern the backend's own
 * `engine.parts_per_truck` already computed, and the two disagreed. The
 * truck plan now comes straight off `POST /api/truck-fit` / `result.truck`
 * — see LoadCalculator.jsx and lib/solve.js's `floorPlanFromTruck`.
 */

/**
 * Human layer summary for a fit: "3 × 22" / "2 × 22 + 1" / "42 + 42 + 24"
 * — consecutive layers with the same filled count are grouped.
 */
export function layerSummary(fit) {
  const runs = []
  for (const l of fit.layerConfig) {
    const last = runs.at(-1)
    if (last && last.n === l.filled) last.times += 1
    else runs.push({ n: l.filled, times: 1 })
  }
  return runs
    .map((r) => (r.times > 1 ? `${r.times} × ${r.n}` : `${r.n}`))
    .join(' + ')
}

/**
 * Insert archetype per PLANNING.md Phase 3: long tube-like parts get
 * comb/slot dunnage; compact prismatic parts get pocket/cell dividers.
 */
export function insertType(part) {
  const aspect = part.length_mm / Math.max(part.breadth_mm, 1)
  return aspect >= 5 ? 'comb/slot' : 'pocket/cell'
}

const ARCHETYPE_LABEL = {
  bar_and_rod: 'Bars and rods',
  pocket_tray: 'Pocket tray',
  layer_sheets: 'Layer sheets',
}

const round1 = (v) => {
  const r = Math.round(v * 10) / 10
  return Number.isInteger(r) ? String(r) : r.toFixed(1)
}
const round0 = (v) => String(Math.round(v))
const triple = (v, fmt = round1) => (Array.isArray(v) ? `${v.map(fmt).join(' × ')} mm` : null)

/**
 * F13: the results-panel key/value rows, straight off `layout` (LayoutOut /
 * BoxDesignOut, backend/app/schemas.py) plus its own `dunnage` and
 * (optionally) the run's single `truck` plan. Every value is a field the
 * backend already returns (CLAUDE.md hard rule 9) — never a sum, product or
 * difference of two others. A row is omitted entirely (never "—", never
 * NaN/undefined) when its field is absent, so a run that predates a field,
 * or a box with no packaging record, just shows fewer rows.
 */
export function resultRows({ layout, box, truck, assetLabel } = {}) {
  const rows = []
  const put = (label, value) => { if (value != null && value !== '') rows.push({ label, value }) }
  if (!layout) return rows

  put('Box', assetLabel ?? layout.asset_name)
  if (box) {
    put('Outer L×B×H', triple([box.outer_l_mm, box.outer_b_mm, box.outer_h_mm]))
    put('Inner L×B×H', triple([box.inner_l_mm, box.inner_b_mm, box.inner_h_mm]))
  }
  put('Parts per box', layout.count)
  if (Array.isArray(layout.grid)) {
    put('Layers', layout.grid[2])
    put('Grid', layout.grid.join(' × '))
  }
  put('Pose', layout.pose_label)
  // 0 dp (F13 round 2) — the mm-level precision the extent is measured to
  // reads as noise here; pitch keeps 1 dp since it carries the clearance.
  put('Part extent L×B×H', triple(layout.extent_lbh, round0))
  put('Pitch L×B×H', triple(layout.pitch_lbh))
  // cuboid_count defaults to 0 on stored runs that predate the field
  // (LayoutOut.cuboid_count, schemas.py) — 0 reads as absent, not a real tie.
  if (layout.cuboid_count > 0) put('Cuboid baseline', layout.cuboid_count)

  const d = layout.dunnage
  if (d) {
    put('Insert type', ARCHETYPE_LABEL[d.archetype] ?? d.archetype)
    if (d.build_height_mm != null && d.inner_h_mm != null) {
      put('Build height / inner height', `${round1(d.build_height_mm)} / ${round1(d.inner_h_mm)} mm`)
    }
    if (Array.isArray(d.slack_lbh)) put('Vertical slack', `${round1(d.slack_lbh[2])} mm`)
  }

  if (truck?.parts != null) put('Parts per truck', truck.parts)

  return rows
}
