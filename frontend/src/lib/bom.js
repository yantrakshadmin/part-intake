/**
 * bom.js — pure formatting for the insert BOM (`LayoutOut.dunnage` /
 * `BoxDesignOut.dunnage`, DunnageOut/DunnageElementOut in
 * backend/app/schemas.py). No React import so bom.check.mjs runs under bare
 * node. Nothing here computes a number — every value is rendered verbatim
 * from the backend (CLAUDE.md hard rule 9); this module only decides how a
 * null/empty value reads on screen.
 */

/** `qty: null` means "needs a deck" — render "?", never "0" or "". */
export function fmtQty(qty) {
  return qty == null ? '?' : String(qty)
}

/** `size` is already formatted server-side, "?" and all — render verbatim.
 *  `null` means no basis at all to size it from. */
export function fmtSize(size) {
  return size == null ? 'not derivable' : size
}

/** derived (trustworthy) / pattern (inferred) / unknown (needs input) —
 *  maps to the .badge classes in index.css (green / amber / red tokens). */
export function basisClass(basis) {
  return { derived: 'basis-derived', pattern: 'basis-pattern', unknown: 'basis-unknown' }[basis]
    || 'basis-unknown'
}

/** The row-level "needs deck: d, L, qty" note. Absent (null) when the list
 *  is empty — an empty "needs deck:" line is worse than no line. */
export function unknownNote(unknown) {
  return unknown && unknown.length ? `needs deck: ${unknown.join(', ')}` : null
}

/** F17: `layout.insert_urls[i]` is the dimensioned manufacturing sheet for
 *  `dunnage.elements[i]` — same index, no other correspondence (CLAUDE.md
 *  hard rule 9: pair by index only, never filter/re-derive). Undefined/short
 *  arrays are the ordinary case (render not requested yet, or it failed for
 *  just that element) and must read as "no thumbnail", never an error. */
export function insertUrlFor(insertUrls, i) {
  return insertUrls?.[i] || null
}

/** `drawing_url` is optional: the worker renders the exploded PNG at solve
 *  time and a render failure deliberately leaves the field absent rather than
 *  failing the solve. So the Explode button must never appear on a stub,
 *  placeholder or empty value — nothing here fakes a URL the user could
 *  click into. */
export function hasDrawing(url) {
  return typeof url === 'string' && url.length > 0
}

/** The height-budget line above the table: build vs inner, the fits state
 *  stated loudly (never softened for a false), and what the nest depth
 *  means for stack height. Pure data — the component decides markup. */
export function heightBudget(dunnage) {
  const { build_height_mm, inner_h_mm, fits, nest_depth_mm } = dunnage
  return {
    fits,
    fitsLabel: fits ? 'Fits' : 'Does not fit',
    buildLine: `${build_height_mm} mm build vs ${inner_h_mm} mm inner`,
    nestLine: nest_depth_mm > 0
      ? `${nest_depth_mm} mm nest depth — dunnage lying inside this depth adds no stack height`
      : null,
  }
}
