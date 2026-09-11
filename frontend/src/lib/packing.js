/**
 * packing.js — insert-tray drawing helpers (Phase 3).
 *
 * The vehicle floor-planning that used to live here is gone (F-AUDIT-1): it
 * re-derived a floor pattern the backend's own `engine.parts_per_truck`
 * already computed, and the two disagreed (CLAUDE.md hard rule 9). The
 * truck plan now comes straight off `POST /api/truck-fit` / `result.truck`
 * — see LoadCalculator.jsx and lib/solve.js's `floorPlanFromTruck`.
 *
 * All dims mm; x runs along vehicle length, y along breadth.
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
 * Group a fit's layer config into distinct insert trays: layers with
 * the same orientation code share one tray design (fill is computed once
 * per orientation, so the placements are identical too).
 */
export function groupInserts(fit) {
  const groups = []
  fit.layerConfig.forEach((l, i) => {
    // a partially-filled layer (weight cap) gets its own card — its tray is
    // cut the same but loaded differently, and only it has dummy pockets
    const partial = l.filled < l.count
    const key = `${l.code}${partial ? `·${l.filled}` : ''}`
    let g = groups.find((x) => x.key === key)
    if (!g) {
      g = { key, code: l.code, label: l.label, layers: [], fill: l.fill,
            partH: l.partH, count: l.count, filled: l.filled }
      groups.push(g)
    }
    g.layers.push(i + 1)
  })
  return groups
}

/**
 * Physical tray geometry for one insert group — built from the *actual*
 * packing placements the engine measured, so the drawing always matches the
 * parts-per-box math, including rotated end/side strips. The placement
 * cloud is centered in the box cavity; placement cells (pitch − wall =
 * part + 2·clearance) are the pocket cutouts.
 *
 * Walls stand on the foam/PP floor sheet: total slab H = pocket depth
 * (partH + clearance) + floor (the inter-layer foam).
 *
 * `bom` is the layout's `dunnage` (DunnageOut) when the archetype is
 * 'pocket_tray': the pocket depth and floor-sheet thickness it carries are
 * backend-measured (dunnage.py::_pocket_tray), so they replace the
 * clearance/foam drawing knobs rather than sit alongside them (CLAUDE.md
 * hard rule 9) — elements[0] is the tray (cell_mm = [L, B, pocket depth]),
 * elements[1] is the separator sheet (dims_mm[2] = sheet thickness).
 */
export function trayGeometry({ box, group, clearance = 7.5, wall = 10, foam = 10, bom = null }) {
  const L = box.inner_l_mm
  const B = box.inner_b_mm
  const fromBom = bom?.archetype === 'pocket_tray' ? bom.elements : null
  const wallH = fromBom ? fromBom[0].cell_mm[2] : group.partH + clearance
  const floorH = fromBom ? fromBom[1].dims_mm[2] : foam
  const pl = group.fill.placements

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of pl) {
    if (p.x < minX) minX = p.x
    if (p.y < minY) minY = p.y
    if (p.x + p.w > maxX) maxX = p.x + p.w
    if (p.y + p.h > maxY) maxY = p.y + p.h
  }
  const dx = (L - (maxX - minX)) / 2 - minX
  const dy = (B - (maxY - minY)) / 2 - minY

  return {
    L, B, H: wallH + floorH,
    wallH, floorH, wall,
    pocketDepth: wallH,
    pockets: pl.map((p) => ({ x: p.x + dx, y: p.y + dy, w: p.w, h: p.h, rot: p.rot })),
  }
}

/**
 * Insert archetype per PLANNING.md Phase 3: long tube-like parts get
 * comb/slot dunnage; compact prismatic parts get pocket/cell dividers.
 */
export function insertType(part) {
  const aspect = part.length_mm / Math.max(part.breadth_mm, 1)
  return aspect >= 5 ? 'comb/slot' : 'pocket/cell'
}
