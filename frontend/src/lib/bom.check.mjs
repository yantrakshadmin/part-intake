/**
 * bom.check.mjs — acceptance self-check for F-BOM (plain node, no
 * framework). Asserts the parts that would silently lie if broken: a null
 * qty/size rendering as "0"/"" instead of "?"/"not derivable", an unknown
 * list going missing or rendering empty, and a false `fits` reading as
 * "fits".
 *
 * Run: node src/lib/bom.check.mjs
 */
import assert from 'node:assert/strict'
import { fmtQty, fmtSize, unknownNote, heightBudget, basisClass, hasDrawing, insertUrlFor } from './bom.js'

// --- fmtQty / fmtSize: null must never read as 0 or blank -------------------
assert.equal(fmtQty(null), '?')
assert.equal(fmtQty(0), '0')       // a real zero is still a real number
assert.equal(fmtQty(10), '10')

assert.equal(fmtSize(null), 'not derivable')
// Verbatim, "?" and all — this is the backend's formatting, not ours to redo.
assert.equal(fmtSize('750x?x73'), '750x?x73')
assert.equal(fmtSize('1150x750x75'), '1150x750x75')

// --- unknownNote: every entry, absent when empty ----------------------------
assert.equal(unknownNote([]), null)
assert.equal(unknownNote(undefined), null)
assert.equal(unknownNote(['d', 'L', 'qty']), 'needs deck: d, L, qty')
assert.equal(unknownNote(['spec']), 'needs deck: spec')

// --- basisClass: a class per basis, never blank -----------------------------
assert.equal(basisClass('derived'), 'basis-derived')
assert.equal(basisClass('pattern'), 'basis-pattern')
assert.equal(basisClass('unknown'), 'basis-unknown')

// --- heightBudget: the Mubea PLS12801 real payload (ticket-F-BOM.md) -------
const mubea = {
  archetype: 'bar_and_rod', build_height_mm: 732, inner_h_mm: 790,
  fits: true, nest_depth_mm: 75, stack_height_mm: 732,
  caveat: 'physical trials still decide', elements: [],
}
const okBudget = heightBudget(mubea)
assert.equal(okBudget.fits, true)
assert.match(okBudget.fitsLabel, /fits/i)
assert.match(okBudget.buildLine, /732/)
assert.match(okBudget.buildLine, /790/)
assert.match(okBudget.nestLine, /75/)

// --- heightBudget: the known failure mode — TRW custom box, 990 into 987 ---
// A budget summary that says "fits" for this build is exactly the bug this
// check exists to catch.
const trw = {
  archetype: 'pocket_tray', build_height_mm: 990, inner_h_mm: 987,
  fits: false, nest_depth_mm: 0, stack_height_mm: 987,
  caveat: 'physical trials still decide', elements: [],
}
const badBudget = heightBudget(trw)
assert.equal(badBudget.fits, false)
assert.match(badBudget.fitsLabel, /does not fit/i)
assert.doesNotMatch(badBudget.fitsLabel, /^fits$/i)
assert.equal(badBudget.nestLine, null) // zero nest depth: nothing to explain

// --- hasDrawing: the Explode-button gate. drawing_url does not exist in the
// response yet (B-DRAW adds it) — undefined/absent must read as "no button",
// same as an empty string ever sent by mistake.
assert.equal(hasDrawing(undefined), false)
assert.equal(hasDrawing(null), false)
assert.equal(hasDrawing(''), false)
assert.equal(hasDrawing('/api/parts/1/drawing.png'), true)

// --- insertUrlFor (F17): pairing is by index only, a short/empty array
// yields no thumbnail for the elements past its end — never an error, and
// never "borrowed" from a neighbouring index.
const sheets = ['/api/files/insert_1_0_0.png', '/api/files/insert_1_0_1.png']
assert.equal(insertUrlFor(sheets, 0), sheets[0])
assert.equal(insertUrlFor(sheets, 1), sheets[1])
assert.equal(insertUrlFor(sheets, 2), null)   // shorter than elements
assert.equal(insertUrlFor([], 0), null)       // empty
assert.equal(insertUrlFor(undefined, 0), null)
assert.equal(insertUrlFor(null, 0), null)

console.log('bom.check.mjs: all assertions passed —',
  'qty/size null-safety, unknown list, and 990-into-987 reported as not fitting')
