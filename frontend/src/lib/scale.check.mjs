// Hard rule 1: "Never change the metre<->mm scaling in only one place.
// Backend scales mesh x1000; frontend scales model x1000. A contract test
// enforces this." That test did not exist. It does now.
//
// The frontend has two separate GLTF load paths -- `glbModel.loadOrientedModel`
// for the 2D drawing, and `OrientationViewer`'s own loader, which keeps a
// persistent group so it can reapply a candidate without reloading. Both must
// scale, and both must read the SAME constant: they each carried their own
// `setScalar(1000)` literal, so a unit-detection fix in one would silently not
// reach the other.
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { MM_PER_M } from './glbModel.js'

assert.equal(MM_PER_M, 1000, 'the backend scales the mesh x1000; this must match')

const ROOT = new URL('..', import.meta.url).pathname

function* sources(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) yield* sources(p)
    else if (/\.(jsx?|mjs)$/.test(e.name)) yield p
  }
}

// `setScalar(<literal>)` anywhere is a second definition of the scale contract.
// The constant itself is fine; a bare number is not.
const offenders = []
for (const f of sources(ROOT)) {
  const src = readFileSync(f, 'utf8')
  for (const m of src.matchAll(/scale\.set(?:Scalar)?\(\s*([^)]*?)\s*\)/g)) {
    if (/^\d/.test(m[1])) offenders.push(`${f.replace(ROOT, '')}: scale.set(${m[1]})`)
  }
}
assert.deepEqual(offenders, [],
  'metre->mm scale hard-coded outside glbModel.MM_PER_M:\n  ' + offenders.join('\n  '))

// And it must still be applied on BOTH load paths -- deleting a setScalar
// would satisfy the check above while breaking the render.
// Three paths: glbModel.loadOrientedModel, OrientationViewer, PackAnimation
// (which passes MM_PER_M three times to `.scale.set`, same constant).
const scaled = [...sources(ROOT)].filter((f) =>
  /scale\.set(?:Scalar)?\(\s*MM_PER_M\b/.test(readFileSync(f, 'utf8')))
assert.equal(scaled.length, 3,
  `expected all three GLTF load paths to scale by MM_PER_M, found ${scaled.length}: ${scaled}`)

console.log('scale.check: MM_PER_M is the single definition, applied on all three load paths')
