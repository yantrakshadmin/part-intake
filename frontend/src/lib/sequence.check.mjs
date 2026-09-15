// Self-check for the `sequence` fixture PackAnimation is built and tested
// against (F4). Asserts the shape PackAnimation actually depends on:
// steps sorted by `i`, every part origin inside the box, and the right
// total part count for the TRW ground-truth run (48, PLS1280).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const sequence = JSON.parse(
  readFileSync(new URL('./sequence.fixture.json', import.meta.url), 'utf8'))

const { inner, steps } = sequence

// steps sorted by i
const order = steps.map((s) => s.i)
for (let k = 1; k < order.length; k++) {
  assert.ok(order[k] > order[k - 1], `steps not sorted by i at index ${k}: ${order[k - 1]} -> ${order[k]}`)
}

// every origin (dunnage cuboids + parts) inside the box
const insideBox = (origin) => origin.every((v, j) => v >= 0 && v <= inner[j])
for (const step of steps) {
  for (const c of step.cuboids || []) {
    assert.ok(insideBox(c.origin), `cuboid ${c.name} origin ${c.origin} outside inner ${inner}`)
  }
  for (const p of step.parts || []) {
    assert.ok(insideBox(p.origin), `part origin ${p.origin} outside inner ${inner}`)
  }
}

// 48 part origins total (TRW ground truth: PLS1280 -> 48)
const totalParts = steps
  .filter((s) => s.kind === 'parts')
  .reduce((n, s) => n + s.parts.length, 0)
assert.equal(totalParts, 48, `expected 48 part origins, got ${totalParts}`)

console.log('sequence.check: 48 origins, all inside inner, steps sorted by i')
