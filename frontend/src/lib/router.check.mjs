/**
 * router.check.mjs — parseHash contract (F2 acceptance check #2).
 * Run: node src/lib/router.check.mjs
 */
import assert from 'node:assert/strict'
import { parseHash } from './router.js'

assert.deepEqual(parseHash('#/projects/12/runs'), { page: 'project', id: 12, tab: 'runs', query: {} })
assert.deepEqual(parseHash('#/projects/12'), { page: 'project', id: 12, tab: 'overview', query: {} })
assert.deepEqual(
  parseHash('#/projects/1/packaging?run=abc'),
  { page: 'project', id: 1, tab: 'packaging', query: { run: 'abc' } })
assert.deepEqual(parseHash('#/projects/new'), { page: 'new-project' })
assert.deepEqual(parseHash('#/assets'), { page: 'assets' })
assert.deepEqual(parseHash('#/tools/load-calculator'), { page: 'load-calculator' })
assert.deepEqual(parseHash(''), { page: 'projects' })
assert.deepEqual(parseHash('#/nope'), { page: 'projects' })
assert.deepEqual(parseHash('#/projects'), { page: 'projects' })

console.log('router.check.mjs: all assertions passed')
