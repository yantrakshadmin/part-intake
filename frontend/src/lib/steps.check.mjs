/**
 * steps.check.mjs — the one contract UI-4 depends on: the results step is
 * unreachable until a part is saved, and reachable immediately after.
 * Run: node src/lib/steps.check.mjs
 */
import assert from 'node:assert/strict'
import { currentStep } from './steps.js'

// Nothing yet: intake, regardless of mode.
assert.equal(currentStep({ mode: 'manual', result: null, savedPart: null }), 'intake')
assert.equal(currentStep({ mode: 'stp', result: null, savedPart: null }), 'intake')

// STEP upload extracted candidates but nothing saved yet: confirm, not results.
assert.equal(currentStep({ mode: 'stp', result: { candidates: [] }, savedPart: null }), 'confirm')

// Manual mode never has a confirm step — dims go straight from intake to a save.
assert.equal(currentStep({ mode: 'manual', result: { candidates: [] }, savedPart: null }), 'intake')

// The result step is unreachable without a saved part, even with candidates in hand.
assert.notEqual(
  currentStep({ mode: 'stp', result: { candidates: [] }, savedPart: null }), 'results')

// ...and reachable the instant a part is saved, confirm candidates or not.
assert.equal(currentStep({ mode: 'stp', result: { candidates: [] }, savedPart: { id: 1 } }), 'results')
assert.equal(currentStep({ mode: 'manual', result: null, savedPart: { id: 1 } }), 'results')

console.log('steps.check.mjs: all assertions passed')
