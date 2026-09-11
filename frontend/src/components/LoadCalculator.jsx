import { useEffect, useMemo, useState } from 'react'
import { errorDetail, floorPlanFromTruck } from '../lib/solve.js'

const BIND_CLASS = { volume: 'volume', weight: 'weight', 'weight and volume': 'both' }

/** Standalone box-into-vehicle calculator. No part required — the truck
 *  numbers and the floor pattern both come straight off POST /api/truck-fit
 *  (engine.parts_per_truck), never re-derived here (CLAUDE.md hard rule 9;
 *  the client-side floor-fit this used to carry disagreed with the backend
 *  on the floor pattern — F-AUDIT-1). */
export default function LoadCalculator() {
  const [packaging, setPackaging] = useState([])
  const [vehicles, setVehicles] = useState([])
  const [boxId, setBoxId] = useState('') // packaging id or 'custom'
  const [vehicleId, setVehicleId] = useState('')
  const [custom, setCustom] = useState({ l: '', b: '', h: '', code: '' })
  const [weightPerBox, setWeightPerBox] = useState('')
  const [maxStack, setMaxStack] = useState('')
  const [saveMsg, setSaveMsg] = useState('')
  const [truck, setTruck] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/packaging').then((r) => r.json()).then((d) => {
      setPackaging(d)
      if (d.length) setBoxId(String(d[0].id))
    })
    fetch('/api/vehicles').then((r) => r.json()).then((d) => {
      setVehicles(d)
      if (d.length) setVehicleId(String(d[0].id))
    })
  }, [])

  const box = useMemo(() => {
    if (boxId === 'custom') {
      const l = +custom.l, b = +custom.b, h = +custom.h
      if (!(l > 0 && b > 0 && h > 0)) return null
      return { outer_l_mm: l, outer_b_mm: b, outer_h_mm: h, max_weight_kg: null }
    }
    return packaging.find((p) => String(p.id) === boxId) || null
  }, [boxId, custom, packaging])

  const vehicle = vehicles.find((v) => String(v.id) === vehicleId) || null

  // default the per-box weight to the box's rated max when a standard box is picked
  useEffect(() => {
    if (box && box.max_weight_kg != null) setWeightPerBox(String(box.max_weight_kg))
  }, [boxId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced POST /api/truck-fit on any change of box/vehicle/weight/stack.
  // AbortController drops a stale response if the inputs change again before
  // it lands (F2-2 F4 pattern, applied here too).
  useEffect(() => {
    if (!box || !vehicle) { setTruck(null); setError(''); return undefined }
    const controller = new AbortController()
    const t = setTimeout(() => {
      fetch('/api/truck-fit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          outer_l_mm: box.outer_l_mm, outer_b_mm: box.outer_b_mm, outer_h_mm: box.outer_h_mm,
          kg_per_box: +weightPerBox || 0,
          vehicle: vehicle.name,
          max_stack: +maxStack || 0,
        }),
        signal: controller.signal,
      }).then(async (r) => {
        if (!r.ok) throw new Error(errorDetail((await r.json().catch(() => null))?.detail, `Truck fit failed (${r.status})`))
        return r.json()
      }).then((d) => { setTruck(d); setError('') })
        .catch((e) => { if (e.name !== 'AbortError') { setTruck(null); setError(e.message) } })
    }, 250)
    return () => { clearTimeout(t); controller.abort() }
  }, [box, vehicle, weightPerBox, maxStack])

  async function saveCustomBox() {
    setSaveMsg('')
    try {
      const r = await fetch('/api/packaging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_code: custom.code,
          // inner unknown for an ad-hoc box; store outer as placeholder bounds
          inner_l_mm: +custom.l, inner_b_mm: +custom.b, inner_h_mm: +custom.h,
          outer_l_mm: +custom.l, outer_b_mm: +custom.b, outer_h_mm: +custom.h,
          max_weight_kg: +weightPerBox || 1,
        }),
      })
      if (!r.ok) throw new Error(errorDetail((await r.json().catch(() => null))?.detail, 'Save failed'))
      const saved = await r.json()
      setPackaging((p) => [...p, saved])
      setBoxId(String(saved.id))
      setSaveMsg(`Saved ${saved.item_code} as draft`)
    } catch (e) { setSaveMsg(e.message) }
  }

  const noWeightGiven = (+weightPerBox || 0) === 0
  const bindClass = truck ? (truck.boxes === 0 ? 'none' : (BIND_CLASS[truck.limited_by] || truck.limited_by)) : ''
  const cubeUtilization = truck && box
    ? (truck.boxes * box.outer_l_mm * box.outer_b_mm * box.outer_h_mm)
      / (vehicle.cargo_l_mm * vehicle.cargo_b_mm * vehicle.cargo_h_mm)
    : 0
  const plan = truck && box && truck.boxes > 0 ? floorPlanFromTruck(truck, box) : null

  return (
    <>
      <div className="card form-card" style={{ marginTop: 0 }}>
        <h2>Load calculator</h2>
        <div className="form-grid">
          <label className="field">
            <span>Box</span>
            <select value={boxId} onChange={(e) => setBoxId(e.target.value)}>
              {packaging.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.item_code} — {p.outer_l_mm}×{p.outer_b_mm}×{p.outer_h_mm}
                  {p.status === 'draft' ? ' (draft)' : ''}
                </option>
              ))}
              <option value="custom">Custom box…</option>
            </select>
          </label>
          <label className="field">
            <span>Vehicle</span>
            <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
              {vehicles.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name.replaceAll('_', ' ')} — {v.cargo_l_mm}×{v.cargo_b_mm}×{v.cargo_h_mm}, {v.payload_kg} kg
                </option>
              ))}
            </select>
          </label>
          <span />
          {boxId === 'custom' && (
            <>
              <label className="field">
                <span>Box length (mm)</span>
                <input type="number" value={custom.l}
                  onChange={(e) => setCustom({ ...custom, l: e.target.value })} />
              </label>
              <label className="field">
                <span>Box breadth (mm)</span>
                <input type="number" value={custom.b}
                  onChange={(e) => setCustom({ ...custom, b: e.target.value })} />
              </label>
              <label className="field">
                <span>Box height (mm)</span>
                <input type="number" value={custom.h}
                  onChange={(e) => setCustom({ ...custom, h: e.target.value })} />
              </label>
            </>
          )}
          <label className="field">
            <span>Weight per loaded box (kg)</span>
            <input type="number" value={weightPerBox}
              onChange={(e) => setWeightPerBox(e.target.value)} />
          </label>
          <label className="field">
            <span>Max stack (boxes high, blank = by height)</span>
            <input type="number" value={maxStack}
              onChange={(e) => setMaxStack(e.target.value)} />
          </label>
        </div>

        {boxId === 'custom' && (
          <div className="form-footer">
            <input className="search" style={{ width: 180 }} placeholder="Item code (e.g. CUST01)"
              value={custom.code}
              onChange={(e) => setCustom({ ...custom, code: e.target.value })} />
            <button className="btn-primary" disabled={!box || !custom.code}
              onClick={saveCustomBox}>
              Save to master list
            </button>
            {saveMsg && <span className="status">{saveMsg}</span>}
          </div>
        )}
      </div>

      {error && <div className="warning">⚠ <span>{error}</span></div>}

      {truck && vehicle && box && (
        <div className="card form-card result-card">
          <h2>Result</h2>
          <div className="result-grid">
            <div>
              <div className="big-number">
                {truck.boxes}
                <span className="big-unit"> boxes</span>
              </div>
              <div className={`bind-chip ${bindClass}`}>
                {truck.boxes === 0 && "box doesn't fit this vehicle"}
                {truck.boxes > 0 && truck.limited_by === 'weight' && '⚠ payload limits this load'}
                {truck.boxes > 0 && truck.limited_by === 'volume' && 'volume limits this load'}
                {truck.boxes > 0 && truck.limited_by === 'weight and volume' && 'volume and payload balanced'}
              </div>
              <dl className="result-facts">
                <div><dt>Floor</dt><dd className="mono">
                  {truck.floor_grid[0] * truck.floor_grid[1]} positions — {truck.floor_grid[0]} × {truck.floor_grid[1]}</dd></div>
                <div><dt>Stacking</dt><dd className="mono">{truck.stack} high</dd></div>
                <div><dt>By volume</dt><dd className="mono">{truck.by_volume} boxes
                  ({Math.round(cubeUtilization * 100)}% cube at final count)</dd></div>
                <div><dt>By payload</dt><dd className="mono">
                  {noWeightGiven ? '— (no box weight given)' : `${truck.by_weight} boxes`}
                </dd></div>
              </dl>
            </div>
            {plan && <FloorPlan vehicle={vehicle} placements={plan.floor.placements} />}
          </div>
        </div>
      )}
    </>
  )
}

/** Top view of the vehicle floor with box placements. */
function FloorPlan({ vehicle, placements }) {
  const W = 420
  const scale = W / vehicle.cargo_l_mm
  const H = vehicle.cargo_b_mm * scale
  return (
    <div>
      <svg width={W} height={H} className="floor-svg"
        viewBox={`0 0 ${W} ${H}`}>
        <rect x="0" y="0" width={W} height={H} className="floor-bed" />
        {placements.map((p, i) => (
          <rect key={i}
            x={p.x * scale + 1} y={p.y * scale + 1}
            width={p.w * scale - 2} height={p.h * scale - 2}
            className="floor-box" rx="2"
          />
        ))}
      </svg>
      <div className="floor-caption">
        Top view — {vehicle.name.replaceAll('_', ' ')} floor,{' '}
        {vehicle.cargo_l_mm} × {vehicle.cargo_b_mm} mm
      </div>
    </div>
  )
}
