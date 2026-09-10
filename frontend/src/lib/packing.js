/**
 * packing.js — box-into-vehicle floor planning (Phase 2).
 *
 * Boxes stay upright ("this way up"), so floor placement allows only 0°/90°
 * rotations. We try both uniform grids plus split-strip hybrids (main grid +
 * leftover strip filled with rotated boxes) and keep the best. Returns
 * placements for drawing the top view.
 *
 * All dims mm; x runs along vehicle length, y along breadth.
 */

function uniformGrid(L, B, pitchX, pitchY, wall = 0, x0 = 0, y0 = 0, rot = false) {
  // n cells across a span need n*pitch - wall (walls shared, none at the
  // edges), so n = floor((span + wall) / pitch). Cell drawn at pitch - wall.
  const nx = Math.floor((L + wall) / pitchX)
  const ny = Math.floor((B + wall) / pitchY)
  const placements = []
  for (let i = 0; i < nx; i++)
    for (let j = 0; j < ny; j++)
      placements.push({
        x: x0 + i * pitchX, y: y0 + j * pitchY,
        w: pitchX - wall, h: pitchY - wall, rot,
      })
  return placements
}

/**
 * Best rectangular fill of pitchL×pitchB cells into a spanL×spanB area:
 * both uniform orientations plus split-strip hybrids (main grid + leftover
 * strip filled with 90°-rotated cells — mixed orientation in one layer).
 */
function bestFill(spanL, spanB, pitchL, pitchB, wall = 0) {
  const candidates = []

  for (const [pl, pb, rot] of [[pitchL, pitchB, false], [pitchB, pitchL, true]]) {
    const grid = uniformGrid(spanL, spanB, pl, pb, wall, 0, 0, rot)
    candidates.push({ placements: grid, desc: gridDesc(spanL, spanB, pl, pb, wall) })

    // leftover strip at the end (along length), filled rotated
    const nx = Math.floor((spanL + wall) / pl)
    const remL = spanL - nx * pl
    if (nx > 0 && remL + wall >= Math.min(pl, pb)) {
      const main = uniformGrid(nx * pl - wall, spanB, pl, pb, wall, 0, 0, rot)
      const strip = uniformGrid(remL, spanB, pb, pl, wall, nx * pl, 0, !rot)
      if (strip.length > 0)
        candidates.push({
          placements: [...main, ...strip],
          desc: `${gridDesc(nx * pl - wall, spanB, pl, pb, wall)} + ${strip.length} rotated in end strip`,
        })
    }

    // leftover strip on the side (along breadth), filled rotated
    const ny = Math.floor((spanB + wall) / pb)
    const remB = spanB - ny * pb
    if (ny > 0 && remB + wall >= Math.min(pl, pb)) {
      const main = uniformGrid(spanL, ny * pb - wall, pl, pb, wall, 0, 0, rot)
      const strip = uniformGrid(spanL, remB, pb, pl, wall, 0, ny * pb, !rot)
      if (strip.length > 0)
        candidates.push({
          placements: [...main, ...strip],
          desc: `${gridDesc(spanL, ny * pb - wall, pl, pb, wall)} + ${strip.length} rotated in side strip`,
        })
    }
  }

  candidates.sort((a, c) => c.placements.length - a.placements.length)
  const best = candidates[0]
  return { count: best.placements.length, placements: best.placements, desc: best.desc }
}

/** Best floor fill of boxes into a vehicle floor (no walls between boxes). */
export function floorFit(vehL, vehB, boxL, boxB) {
  return bestFill(vehL, vehB, boxL, boxB, 0)
}

function gridDesc(L, B, pl, pb, wall = 0) {
  return `${Math.floor((L + wall) / pl)} deep × ${Math.floor((B + wall) / pb)} across`
}

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
 * packing placements (bestFill), so the drawing always matches the
 * parts-per-box math, including rotated end/side strips. The placement
 * cloud is centered in the box cavity; placement cells (pitch − wall =
 * part + 2·clearance) are the pocket cutouts.
 *
 * Walls stand on the foam/PP floor sheet: total slab H = pocket depth
 * (partH + clearance) + floor (the inter-layer foam).
 */
export function trayGeometry({ box, group, clearance = 7.5, wall = 10, foam = 10 }) {
  const L = box.inner_l_mm
  const B = box.inner_b_mm
  const wallH = group.partH + clearance
  const floorH = foam
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

/**
 * Full load plan: floor fit × stacking, capped by payload.
 * weightPerBox is the gross loaded box weight the user expects.
 */
export function loadPlan({ vehicle, box, weightPerBox, maxStack }) {
  const floor = floorFit(vehicle.cargo_l_mm, vehicle.cargo_b_mm,
                         box.outer_l_mm, box.outer_b_mm)

  let layers = Math.floor(vehicle.cargo_h_mm / box.outer_h_mm)
  if (maxStack > 0) layers = Math.min(layers, maxStack)

  const byVolume = floor.count * layers
  const byWeight = weightPerBox > 0
    ? Math.floor(vehicle.payload_kg / weightPerBox)
    : Infinity

  const total = Math.min(byVolume, byWeight)
  const binding = total === 0 ? 'none'
    : byWeight < byVolume ? 'weight'
    : byWeight === byVolume ? 'both' : 'volume'

  const cubeUsed = total *
    (box.outer_l_mm * box.outer_b_mm * box.outer_h_mm) /
    (vehicle.cargo_l_mm * vehicle.cargo_b_mm * vehicle.cargo_h_mm)

  return {
    floor,
    layers,
    byVolume,
    byWeight,
    total,
    binding,
    cubeUtilization: cubeUsed,
    weightUtilization: weightPerBox > 0
      ? (total * weightPerBox) / vehicle.payload_kg : 0,
  }
}
