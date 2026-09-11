import { useEffect, useState } from 'react'

/** Read-only catalogue + vehicle tables (F8 is the edit UI, not this). */
export default function Assets() {
  const [packaging, setPackaging] = useState(null)
  const [vehicles, setVehicles] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/packaging').then((r) => r.json()).then(setPackaging).catch((e) => setError(e.message))
    fetch('/api/vehicles').then((r) => r.json()).then(setVehicles).catch((e) => setError(e.message))
  }, [])

  return (
    <>
      {error && <div className="warning">⚠ <span>{error}</span></div>}

      <div className="card form-card">
        <div className="list-head">
          <h2>Packaging catalogue {packaging && <span className="count">{packaging.length}</span>}</h2>
        </div>
        {!packaging && !error && <p className="muted">Loading…</p>}
        {packaging && (
          <table className="parts-table">
            <thead>
              <tr>
                <th>Item code</th><th>Kind</th><th>Status</th>
                <th className="num">Inner L×B×H</th><th className="num">Outer L×B×H</th>
                <th className="num">Max weight</th><th className="num">Tare</th>
              </tr>
            </thead>
            <tbody>
              {packaging.map((p) => (
                <tr key={p.id}>
                  <td className="mono strong">{p.item_code}</td>
                  <td>{p.kind}</td>
                  <td><span className={`badge status-${p.status}`}>{p.status}</span></td>
                  <td className="num mono">{p.inner_l_mm} × {p.inner_b_mm} × {p.inner_h_mm}</td>
                  <td className="num mono">{p.outer_l_mm} × {p.outer_b_mm} × {p.outer_h_mm}</td>
                  <td className="num mono">{p.max_weight_kg}</td>
                  {/* tare_kg is Optional on the API (C-TARE); a draft box
                      genuinely has none on file — never show 0 for that. */}
                  <td className="num mono">{p.tare_kg != null ? p.tare_kg : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card form-card" style={{ marginTop: 24 }}>
        <div className="list-head">
          <h2>Vehicles {vehicles && <span className="count">{vehicles.length}</span>}</h2>
        </div>
        {!vehicles && !error && <p className="muted">Loading…</p>}
        {vehicles && (
          <table className="parts-table">
            <thead>
              <tr><th>Name</th><th className="num">Cargo L×B×H</th><th className="num">Payload</th></tr>
            </thead>
            <tbody>
              {vehicles.map((v) => (
                <tr key={v.id}>
                  <td className="mono strong">{v.name}</td>
                  <td className="num mono">{v.cargo_l_mm} × {v.cargo_b_mm} × {v.cargo_h_mm}</td>
                  <td className="num mono">{v.payload_kg}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
