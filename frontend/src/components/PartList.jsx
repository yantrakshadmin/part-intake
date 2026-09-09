import { useEffect, useMemo, useState } from 'react'
import PackingResults, { PackingParams, defaultPackingParams } from './PackingRecommendation.jsx'

/** Saved parts as a rail list; selecting one fills the stage with its
 *  packaging fit. `packing` (master data) comes from App so the params
 *  rail and results stay in sync across pages. */
export default function PartList({ packing }) {
  const [parts, setParts] = useState(null) // null = loading
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const [params, setParams] = useState(defaultPackingParams())

  useEffect(() => {
    fetch('/api/parts')
      .then((r) => { if (!r.ok) throw new Error('Failed to load parts'); return r.json() })
      .then(setParts)
      .catch((e) => setError(e.message))
  }, [])

  const filtered = useMemo(() => {
    if (!parts) return []
    const q = query.trim().toLowerCase()
    if (!q) return parts
    return parts.filter((p) =>
      p.part_number.toLowerCase().includes(q) ||
      p.part_name.toLowerCase().includes(q)
    )
  }, [parts, query])

  // Auto-select the first part so the stage is never empty for no reason
  useEffect(() => {
    if (!selected && filtered.length > 0) setSelected(filtered[0])
  }, [filtered, selected])

  return (
    <div className="workspace">
      <aside className="rail">
        <div className="card form-card">
          <div className="list-head">
            <h2>Saved parts {parts && <span className="count">{parts.length}</span>}</h2>
          </div>
          <input
            className="search"
            placeholder="Search part number or name…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />

          {error && <div className="warning">⚠ <span>{error}</span></div>}
          {!parts && !error && <p className="muted">Loading…</p>}

          {parts && filtered.length === 0 && (
            <p className="muted">
              {parts.length === 0
                ? 'No parts saved yet — register one from the New part tab.'
                : `No parts match “${query}”.`}
            </p>
          )}

          <div className="part-rail-list">
            {filtered.map((p) => (
              <button key={p.id}
                className={`part-row${selected?.id === p.id ? ' selected' : ''}`}
                onClick={() => setSelected(p)}>
                <div className="pr-line">
                  <span className="mono strong">{p.part_number}</span>
                  <span className={`badge ${p.source}`}>
                    {p.source === 'stp' ? 'STEP' : 'Manual'}
                  </span>
                </div>
                <div className="pr-sub">
                  {p.part_name} · {p.length_mm} × {p.breadth_mm} × {p.height_mm} mm · {p.weight_kg} kg
                </div>
              </button>
            ))}
          </div>
        </div>

        <PackingParams params={params} onChange={setParams}
          vehicles={packing.vehicles}
          onAddBox={(b) => packing.setPackaging((prev) => [...prev, b])} />
      </aside>

      <main className="stage">
        {selected ? (
          <PackingResults part={selected} params={params}
            packaging={packing.packaging} vehicles={packing.vehicles} />
        ) : (
          <div className="card empty-stage">
            <div className="es-icon">▤</div>
            <h2>Select a part</h2>
            <p>Pick a saved part on the left to see its packaging fit,
              insert designs and truck loading plan.</p>
          </div>
        )}
      </main>
    </div>
  )
}
