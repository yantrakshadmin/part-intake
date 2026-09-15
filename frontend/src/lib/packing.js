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
