import { useEffect, useState } from 'react'
import { navigate } from '../lib/router.js'

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/**
 * Projects list (`#/projects`) — the default screen. Search debounces into
 * `?q=` on the API rather than filtering client-side, since the backend
 * already owns the ordering (updated_at desc) and the search.
 */
export default function ProjectsList() {
  const [query, setQuery] = useState('')
  const [projects, setProjects] = useState(null) // null = loading
  const [error, setError] = useState('')

  useEffect(() => {
    const t = setTimeout(() => {
      const url = query.trim() ? `/api/projects?q=${encodeURIComponent(query.trim())}` : '/api/projects'
      fetch(url)
        .then((r) => { if (!r.ok) throw new Error('Failed to load projects'); return r.json() })
        .then(setProjects)
        .catch((e) => setError(e.message))
    }, 250)
    return () => clearTimeout(t)
  }, [query])

  return (
    <div className="card form-card">
      <div className="list-head">
        <h2>Projects {projects && <span className="count">{projects.length}</span>}</h2>
        <button className="btn-primary" onClick={() => navigate('#/projects/new')}>New project</button>
      </div>

      <input className="search" placeholder="Search customer or part number…"
        value={query} onChange={(e) => setQuery(e.target.value)} />

      {error && <div className="warning">⚠ <span>{error}</span></div>}
      {!projects && !error && <p className="muted">Loading…</p>}

      {projects && projects.length === 0 && (
        <div className="empty-stage" style={{ padding: '24px 0' }}>
          <div className="es-icon">▦</div>
          <h2>No projects yet</h2>
          <p>Give us the customer's part and its annual volume. You get back
            the insert design, the box and truck plan it drives, and the gain
            against what the customer ships today.</p>
        </div>
      )}

      {projects && projects.length > 0 && (
        <table className="parts-table">
          <thead>
            <tr>
              <th>Customer</th><th>Part</th><th>Status</th>
              <th>Best</th><th>vs cuboid</th><th className="num">Runs</th><th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id} className="row-click"
                onClick={() => navigate(`#/projects/${p.id}/overview`)}>
                <td>{p.customer}</td>
                <td>
                  <span className="mono strong">{p.part_number}</span>
                  {' '}<span className="muted">{p.part_name}</span>
                </td>
                <td><span className={`badge status-${p.status}`}>{p.status}</span></td>
                <td className="mono">
                  {p.best_count != null ? (
                    <>{p.best_count} · {p.best_asset === 'custom'
                      ? <span style={{ color: 'var(--accent2)' }}>Custom box</span>
                      : p.best_asset}</>
                  ) : '—'}
                </td>
                <td className="mono">
                  {p.gain_vs_cuboid != null ? (
                    <>
                      {/* gain_vs_cuboid is already 2dp from the backend — no second toFixed. */}
                      {p.gain_vs_cuboid}×
                      {p.gain_vs_customer_pct != null && (
                        <div style={{ fontSize: 11.5, color: p.gain_vs_customer_pct < 0 ? 'var(--red)' : 'var(--text-3)' }}>
                          {p.gain_vs_customer_pct >= 0 ? '+' : ''}{Number(p.gain_vs_customer_pct).toFixed(1)}%
                        </div>
                      )}
                    </>
                  ) : '—'}
                </td>
                <td className="num mono">{p.run_count}</td>
                <td className="mono">{fmtDate(p.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
