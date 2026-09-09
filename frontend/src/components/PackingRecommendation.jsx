import { useEffect, useMemo, useRef, useState } from 'react'
import { partsPerBox, loadPlan, insertType, trayGeometry, groupInserts, layerSummary } from '../lib/packing.js'
import { downloadFigure } from '../lib/download.js'
import InsertTray3D from './InsertTray3D.jsx'
import InsertIso from './InsertIso.jsx'
import TruckLoadIso from './TruckLoadIso.jsx'

const VISIBLE_CARDS = 6

const fmtMm = (v) => {
  const r = Math.round(v * 10) / 10
  return Number.isInteger(r) ? String(r) : r.toFixed(1)
}

/** Master data shared by the params rail and the results stage. */
export function usePackingData() {
  const [packaging, setPackaging] = useState([])
  const [vehicles, setVehicles] = useState([])
  useEffect(() => {
    fetch('/api/packaging').then((r) => r.json()).then(setPackaging)
    fetch('/api/vehicles').then((r) => r.json()).then(setVehicles)
  }, [])
  return { packaging, setPackaging, vehicles }
}

export function defaultPackingParams() {
  return { clearance: '7.5', wall: '10', foam: '10', thisSideUp: true, vehicleId: '' }
}

/**
 * Packing parameters — lives in the left rail next to the part inputs.
 * Numeric insert parameters, vehicle pick, orientation lock, custom box.
 */
export function PackingParams({ params, onChange, vehicles, onAddBox }) {
  const [showCustom, setShowCustom] = useState(false)
  const [custom, setCustom] = useState({
    code: '', il: '', ib: '', ih: '', ol: '', ob: '', oh: '', wt: '',
  })
  const [customMsg, setCustomMsg] = useState('')
  const set = (key) => (e) => onChange({ ...params, [key]: e.target.value })

  async function saveCustomBox() {
    setCustomMsg('')
    try {
      const r = await fetch('/api/packaging', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_code: custom.code,
          inner_l_mm: +custom.il, inner_b_mm: +custom.ib, inner_h_mm: +custom.ih,
          outer_l_mm: +custom.ol || +custom.il + 50,
          outer_b_mm: +custom.ob || +custom.ib + 50,
          outer_h_mm: +custom.oh || +custom.ih + 120,
          max_weight_kg: +custom.wt,
        }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || 'Save failed')
      const saved = await r.json()
      onAddBox(saved)
      setShowCustom(false)
      setCustomMsg(`Added ${saved.item_code} (draft)`)
    } catch (e) { setCustomMsg(e.message) }
  }

  return (
    <div className="card form-card">
      <h2>Packing parameters</h2>
      <div className="form-grid rail-grid">
        <label className="field">
          <span>Clearance / side (mm)</span>
          <input type="number" value={params.clearance} onChange={set('clearance')} />
        </label>
        <label className="field">
          <span>Divider wall (mm)</span>
          <input type="number" value={params.wall} onChange={set('wall')} />
        </label>
        <label className="field">
          <span>Layer foam (mm)</span>
          <input type="number" value={params.foam} onChange={set('foam')} />
        </label>
        <label className="field">
          <span>Vehicle (for truck plan)</span>
          <select value={params.vehicleId} onChange={set('vehicleId')}>
            <option value="">—</option>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>{v.name.replaceAll('_', ' ')}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="check-field">
        <input type="checkbox" checked={params.thisSideUp}
          onChange={(e) => onChange({ ...params, thisSideUp: e.target.checked })} />
        <span>This side up — keep the confirmed resting orientation</span>
      </label>
      <button className="btn-ghost" onClick={() => setShowCustom(!showCustom)}>
        {showCustom ? 'Cancel custom box' : '+ Custom box'}
      </button>

      {showCustom && (
        <div className="custom-box-form">
          <div className="form-grid rail-grid">
            <label className="field"><span>Item code</span>
              <input value={custom.code} placeholder="CUST01"
                onChange={(e) => setCustom({ ...custom, code: e.target.value })} /></label>
            <label className="field"><span>Max content weight (kg)</span>
              <input type="number" value={custom.wt}
                onChange={(e) => setCustom({ ...custom, wt: e.target.value })} /></label>
            <label className="field"><span>Inner L × B × H (mm)</span>
              <div className="dim-trio">
                <input type="number" placeholder="L" value={custom.il}
                  onChange={(e) => setCustom({ ...custom, il: e.target.value })} />
                <input type="number" placeholder="B" value={custom.ib}
                  onChange={(e) => setCustom({ ...custom, ib: e.target.value })} />
                <input type="number" placeholder="H" value={custom.ih}
                  onChange={(e) => setCustom({ ...custom, ih: e.target.value })} />
              </div></label>
            <label className="field"><span>Outer L × B × H (optional)</span>
              <div className="dim-trio">
                <input type="number" placeholder="L" value={custom.ol}
                  onChange={(e) => setCustom({ ...custom, ol: e.target.value })} />
                <input type="number" placeholder="B" value={custom.ob}
                  onChange={(e) => setCustom({ ...custom, ob: e.target.value })} />
                <input type="number" placeholder="H" value={custom.oh}
                  onChange={(e) => setCustom({ ...custom, oh: e.target.value })} />
              </div></label>
          </div>
          <div className="form-footer" style={{ marginTop: 10 }}>
            <button className="btn-primary"
              disabled={!custom.code || !(+custom.il > 0 && +custom.ib > 0 && +custom.ih > 0 && +custom.wt > 0)}
              onClick={saveCustomBox}>
              Add box (draft)
            </button>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
            Outer defaults to inner + 50/50/120 mm if blank — fix before
            trusting truck math.
          </p>
        </div>
      )}
      {customMsg && <p className="status">{customMsg}</p>}
    </div>
  )
}

/**
 * Ranked packaging fit for one part — box cards, then the selected box's
 * layers / analysis / insert designs / truck loading plan. Lives in the
 * right stage; inputs come from PackingParams in the rail.
 */
export default function PackingResults({ part, params, packaging, vehicles }) {
  const [selectedId, setSelectedId] = useState(null)
  const [showAllBoxes, setShowAllBoxes] = useState(false)

  const vehicle = vehicles.find((v) => String(v.id) === params.vehicleId) || null
  const type = insertType(part)

  const rows = useMemo(() => {
    const opts = {
      clearance: +params.clearance || 0, wall: +params.wall || 0,
      foam: +params.foam || 0, thisSideUp: params.thisSideUp,
    }
    return packaging
      .map((box) => {
        const fit = partsPerBox({ part, box, ...opts })
        let truck = null
        if (vehicle && fit.total > 0) {
          const plan = loadPlan({
            vehicle, box,
            weightPerBox: fit.contentWeight, // box tare unknown — content only
            maxStack: 0,
          })
          truck = { boxes: plan.total, parts: plan.total * fit.total, binding: plan.binding, plan }
        }
        return { box, fit, truck }
      })
      .sort((a, b) => b.fit.total - a.fit.total)
  }, [packaging, part, vehicle, params])

  const selected = rows.find((r) => r.box.id === selectedId) || null
  const visibleRows = showAllBoxes ? rows : rows.slice(0, VISIBLE_CARDS)

  // Auto-select the best box so the insert design is one scroll away,
  // not one more click. Re-selects if the current pick stops fitting.
  useEffect(() => {
    if (!rows.length) return
    const cur = rows.find((r) => r.box.id === selectedId)
    if (!cur || cur.fit.total === 0) {
      const best = rows.find((r) => r.fit.total > 0)
      if (best) setSelectedId(best.box.id)
    }
  }, [rows, selectedId])

  return (
    <div className="card form-card result-card">
      <h2>
        Packaging fit — {part.part_number}
        <span className="muted" style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
          {' '}({part.length_mm} × {part.breadth_mm} × {part.height_mm} mm, {part.weight_kg} kg
          {' '}· insert: {type})
        </span>
      </h2>

      <div className="box-cards">
        {visibleRows.map(({ box, fit, truck }, i) => (
          <button key={box.id}
            className={`box-card${box.id === selectedId ? ' selected' : ''}${fit.total === 0 ? ' nofit' : ''}`}
            onClick={() => setSelectedId(box.id)}>
            <div className="bc-code">
              {box.item_code}
              {i === 0 && fit.total > 0 && <span className="badge stp">best</span>}
              {box.status === 'draft' && <span className="badge manual">draft</span>}
            </div>
            {fit.total === 0 ? (
              <div className="bc-nofit">doesn't fit</div>
            ) : (
              <>
                <div className="bc-count">{fit.total}<span> parts / box</span></div>
                <div className="bc-sub">
                  {fit.layers > 1
                    ? `${layerSummary(fit)} across ${fit.layers} layers`
                    : `${fit.total} in 1 layer`}
                  {fit.orientation.startsWith('mixed') && ' · mixed'}
                </div>
                <div className="bc-bars">
                  <div className="bc-bar" title={`weight ${(fit.weightUtilization * 100).toFixed(0)}%`}>
                    <i style={{ width: `${Math.min(100, fit.weightUtilization * 100)}%` }}
                      className={fit.binding === 'weight' ? 'bar-bind' : ''} />
                  </div>
                  <div className="bc-bar" title={`volume ${(fit.volumeUtilization * 100).toFixed(0)}%`}>
                    <i style={{ width: `${Math.min(100, fit.volumeUtilization * 100)}%` }}
                      className={fit.binding === 'volume' ? 'bar-bind' : ''} />
                  </div>
                </div>
                <div className="bc-bar-labels">
                  <span>wt {(fit.weightUtilization * 100).toFixed(0)}%</span>
                  <span>vol {(fit.volumeUtilization * 100).toFixed(0)}%</span>
                </div>
                {truck && (
                  <div className="bc-truck">
                    {truck.boxes} box{truck.boxes !== 1 ? 'es' : ''} · {truck.parts} / truck
                  </div>
                )}
              </>
            )}
          </button>
        ))}
      </div>
      {rows.length > VISIBLE_CARDS && (
        <button className="btn-ghost show-more" onClick={() => setShowAllBoxes(!showAllBoxes)}>
          {showAllBoxes ? 'Show fewer boxes' : `Show all ${rows.length} boxes`}
        </button>
      )}

      {selected && selected.fit.total > 0 && (
        <BoxDetail row={selected} part={part} type={type} vehicle={vehicle}
          wall={+params.wall || 0} foam={+params.foam || 0}
          clearance={+params.clearance || 0} />
      )}
    </div>
  )
}

function BoxDetail({ row, part, type, vehicle, wall, foam, clearance }) {
  const { box, fit, truck } = row
  const truckRef = useRef(null)
  return (
    <div className="box-detail">
      <div className="detail-summary">
        <div className="sum-chip primary">
          <strong>{fit.total}</strong><span>parts / box</span></div>
        <div className="sum-chip">
          <strong>{fit.layers}</strong><span>layer{fit.layers !== 1 ? 's' : ''}</span></div>
        <div className="sum-chip">
          <strong>{(fit.weightUtilization * 100).toFixed(0)}%</strong>
          <span>weight used</span></div>
        <div className="sum-chip">
          <strong>{(fit.volumeUtilization * 100).toFixed(0)}%</strong>
          <span>volume used</span></div>
        {truck && (
          <div className="sum-chip">
            <strong>{truck.parts}</strong><span>parts / truck</span></div>
        )}
      </div>

      <div className="detail-block">
        <h3><span className="h-icon">▤</span> Layers ({fit.layers})</h3>
        <table className="parts-table layer-table">
          <thead>
            <tr><th>Layer</th><th>Orientation</th>
              <th className="num">Parts</th><th className="num">Dummy pockets</th>
              <th className="num">Layer H (mm)</th></tr>
          </thead>
          <tbody>
            {fit.layerConfig.map((l, i) => (
              <tr key={i}>
                <td className="mono">{i + 1}</td>
                <td><span className="orient-chip">{l.label}</span></td>
                <td className="num mono strong">{l.filled}</td>
                <td className="num mono">{l.count - l.filled > 0 ? l.count - l.filled : '—'}</td>
                <td className="num mono">{(l.layerH + foam).toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3 style={{ marginTop: 18 }}><span className="h-icon">▦</span> Box packing analysis</h3>
        <dl className="result-facts">
          <div><dt>Container</dt><dd className="mono">{box.item_code} ({box.status})</dd></div>
          <div><dt>Inner / outer</dt><dd className="mono">
            {box.inner_l_mm} × {box.inner_b_mm} × {box.inner_h_mm} /{' '}
            {box.outer_l_mm} × {box.outer_b_mm} × {box.outer_h_mm} mm</dd></div>
          <div><dt>Part dims</dt><dd className="mono">{part.length_mm} × {part.breadth_mm} × {part.height_mm} mm · {part.weight_kg} kg</dd></div>
          <div><dt>Insert</dt><dd>{type}{type === 'comb/slot'
            ? ' — slotted supports + PP/EVA bottom sheet'
            : ' — divider walls forming pockets'}</dd></div>
          <div><dt>Pattern</dt><dd>{fit.fill.desc}</dd></div>
          {fit.dummies > 0 && (
            <div><dt>Dummy pockets</dt><dd>{fit.dummies} — box hits its{' '}
              {fit.binding === 'weight' ? 'weight cap' : 'limit'} before the top tray fills</dd></div>
          )}
          <div><dt>Headroom</dt><dd className="mono">{fit.remainingHeight.toFixed(0)} mm unused height</dd></div>
          <div><dt>Content weight</dt><dd className="mono">{fit.contentWeight.toFixed(0)} / {box.max_weight_kg} kg</dd></div>
        </dl>
      </div>

      <InsertPanel box={box} fit={fit} type={type}
        clearance={clearance} wall={wall} foam={foam} />

      {truck && vehicle && (
        <div className="truck-section" ref={truckRef}>
          <div className="insert-card-head">
            <h3 style={{ margin: 0 }}><span className="h-icon">▣</span>
              {' '}Truck loading plan — {vehicle.name.replaceAll('_', ' ')}</h3>
            <button className="btn-ghost" title="Download as PNG"
              onClick={() => downloadFigure(truckRef.current,
                `truck-${vehicle.name}-${box.item_code}.png`)}>
              ⤓ PNG
            </button>
          </div>
          <div className="truck-chips">
            <span className="sum-chip"><strong>{truck.boxes}</strong>
              <span>boxes loaded</span></span>
            <span className="sum-chip"><strong>{truck.parts}</strong>
              <span>parts / truck</span></span>
            <span className="sum-chip">
              <strong>{(truck.plan.cubeUtilization * 100).toFixed(0)}%</strong>
              <span>cube used</span></span>
            <span className="sum-chip">
              <strong>{truck.binding}</strong><span>bound</span></span>
          </div>
          <TruckLoadIso vehicle={vehicle} box={box} plan={truck.plan} />
        </div>
      )}
    </div>
  )
}

/**
 * All insert trays for the selected box, each as its own dimensioned
 * isometric drawing + spec card (no tabs — a solution with two tray designs
 * shows both). The rotating 3D view is an optional per-tray toggle.
 */
function InsertPanel({ box, fit, type, clearance, wall, foam }) {
  const groups = groupInserts(fit)
  if (!groups.length) return null

  return (
    <div className="detail-views">
      <h3><span className="h-icon">▦</span>
        Insert design{groups.length > 1 ? `s — ${groups.length} trays` : ''}</h3>
      {type === 'comb/slot' && (
        <div className="foam-note">Long part (aspect ≥ 5:1) — pockets act as
          comb/slot channels; slot profiles get refined in Phase 3.</div>
      )}
      {groups.map((g, i) => (
        <InsertCard key={g.key} idx={i + 1} total={groups.length} group={g}
          geom={trayGeometry({ box, group: g, clearance, wall, foam })}
          boxCode={box.item_code} />
      ))}
    </div>
  )
}

function InsertCard({ idx, total, group, geom, boxCode }) {
  const [show3d, setShow3d] = useState(false)
  const figRef = useRef(null)
  const dummies = group.count - group.filled

  // dummy pockets pushed to one side: shade the far-end pockets
  const dummySet = useMemo(() => {
    if (!dummies) return new Set()
    return new Set(
      geom.pockets
        .map((p, i) => [p, i])
        .sort(([p], [q]) => q.x - p.x || q.y - p.y)
        .slice(0, dummies)
        .map(([, i]) => i),
    )
  }, [geom, dummies])

  // distinct cutout sizes (main grid + rotated strip, if any)
  const cutouts = useMemo(() => {
    const m = new Map()
    for (const p of geom.pockets) {
      const key = `${fmtMm(p.w)} × ${fmtMm(p.h)}`
      m.set(key, (m.get(key) || 0) + 1)
    }
    return [...m.entries()]
  }, [geom])

  return (
    <div className="insert-card">
      <div className="insert-card-head">
        <div className="insert-card-title">
          <strong>{total > 1 ? `Insert ${idx} of ${total}` : 'Insert tray'}</strong>
          <span className="muted"> — {group.label} ({group.code}) · layer{group.layers.length > 1 ? 's' : ''} {group.layers.join(', ')}</span>
        </div>
        <div className="insert-card-actions">
          <button className="btn-ghost" title="Download as PNG"
            onClick={() => downloadFigure(figRef.current,
              `insert-${boxCode}-${group.code}${show3d ? '-3d' : ''}.png`)}>
            ⤓ PNG
          </button>
          <button className="btn-ghost" onClick={() => setShow3d(!show3d)}>
            {show3d ? '↩ Drawing' : 'Rotate in 3D'}
          </button>
        </div>
      </div>

      <div ref={figRef}>
        {show3d ? (
          <>
            <InsertTray3D geom={geom} />
            <div className="floor-caption" style={{ textAlign: 'right' }}>
              drag to rotate · scroll to zoom</div>
          </>
        ) : (
          <InsertIso geom={geom} dummySet={dummySet} />
        )}
      </div>

      <dl className="result-facts">
        <div><dt>Tray L × B × H</dt><dd className="mono">
          {fmtMm(geom.L)} × {fmtMm(geom.B)} × {fmtMm(geom.H)} mm</dd></div>
        <div><dt>Pockets</dt><dd className="mono">
          {geom.pockets.length} — {group.fill.desc}</dd></div>
        {cutouts.map(([size, n]) => (
          <div key={size}><dt>Cutout ({n}×)</dt><dd className="mono">
            {size} mm · {fmtMm(geom.pocketDepth)} deep</dd></div>
        ))}
        <div><dt>Wall / floor</dt><dd className="mono">
          {fmtMm(geom.wall)} / {fmtMm(geom.floorH)} mm</dd></div>
        <div><dt>Parts / layer</dt><dd className="mono">
          {group.filled} part{group.filled !== 1 ? 's' : ''}
          {dummies > 0 && ` + ${dummies} dummy pocket${dummies !== 1 ? 's' : ''} (grey — weight cap)`}</dd></div>
      </dl>
    </div>
  )
}
