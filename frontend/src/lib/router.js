/**
 * router.js — a hash router in ~20 lines (CLAUDE.md: react + three only,
 * no react-router). parseHash is pure so it's unit-testable without a DOM;
 * useHashRoute wraps it in state that updates on 'hashchange'; navigate is
 * just an alias for setting location.hash (plain <a href="#/..."> works too).
 */
import { useEffect, useState } from 'react'

export function parseHash(hash) {
  // Query string lives after the LAST '?' in the whole hash (e.g.
  // '#/projects/1/packaging?run=abc'), not per-segment — split it off
  // before splitting the path into segments.
  const [path, qs] = String(hash || '').replace(/^#\/?/, '').split('?')
  const parts = path.split('/').filter(Boolean)
  const [head, second, third] = parts
  if (head === 'projects' && second === 'new') return { page: 'new-project' }
  if (head === 'projects' && second) {
    return {
      page: 'project', id: Number(second), tab: third || 'overview',
      query: Object.fromEntries(new URLSearchParams(qs || '')),
    }
  }
  if (head === 'assets') return { page: 'assets' }
  if (head === 'tools' && second === 'load-calculator') return { page: 'load-calculator' }
  return { page: 'projects' }
}

export function navigate(hash) { window.location.hash = hash }

export function useHashRoute() {
  const [route, setRoute] = useState(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
