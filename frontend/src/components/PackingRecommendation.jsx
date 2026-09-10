import { useEffect, useMemo, useRef, useState } from 'react'
import { groupInserts, insertType, layerSummary, trayGeometry } from '../lib/packing.js'
import { runSolve, layoutToFit, floorPlanFromTruck, errorDetail } from '../lib/solve.js'
import { fmtQty, fmtSize, basisClass, unknownNote, heightBudget, hasDrawing } from '../lib/bom.js'
import { downloadFigure } from '../lib/download.js'
import InsertTray3D from './InsertTray3D.jsx'
import InsertIso from './InsertIso.jsx'
import TruckLoadIso from './TruckLoadIso.jsx'

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
  return { vehicleId: '', tareKg: '', assets: [] }
}

const MAX_ASSETS = 20 // SolveIn.assets: max_length=20 (backend/app/schemas.py)

/**
 * Which box codes to rank — multi-select over the catalogue's containers.
 * Empty (default) ranks everything; that default must never change itself.
 * Draft boxes are selectable (and marked) — evaluating the box you just
 * added is the point (UI-3).
 */
function BoxPicker({ packaging, selected, onChange }) {
  const [open, setOpen] = useState(false)
  const containers = packaging.filter((p) => p.kind === 'container')
  const atMax = selected.length >= MAX_ASSETS
  const toggle = (code) => {
    onChange(selected.includes(code)
      ? selected.filter((c) => c !== code)
      : atMax ? selected : [...selected, code])
  }
  const summary = selected.length === 0
    ? `All boxes (${containers.length})`
    : `${selected.length} selected`

  return (
    <label className="field box-picker">
      <span>Boxes to rank</span>
      <button type="button" className="btn-ghost box-picker-toggle"
        onClick={() => setOpen((o) => !o)}>
        {summary} <span className="bp-caret">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="box-picker-list">
          {containers.map((c) => (
            <label key={c.item_code} className="box-picker-row">
              <input type="checkbox" checked={selected.includes(c.item_code)}
                disabled={!selected.includes(c.item_code) && atMax}
                onChange={() => toggle(c.item_code)} />
              <span className="mono">{c.item_code}</span>
              {c.status === 'draft' && <span className="badge manual">draft</span>}
            </label>
          ))}
          {containers.length === 0 && <p className="muted" style={{ margin: 4 }}>No containers yet.</p>}
        </div>
      )}
    </label>
  )
}

/**
 * Solve controls — lives in the left rail next to the part inputs: tare,
 * vehicle and box selection all change the solve's answer, so they sit
 * together (drawing-only clearance/wall/foam moved to the insert tab, D4 —
 * they never change a count, only where they're edited).
 */
export function PackingParams({ params, onChange, vehicles, packaging, onAddBox }) {
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
      if (!r.ok) throw new Error(errorDetail((await r.json().catch(() => null))?.detail, 'Save failed'))
      const saved = await r.json()
      onAddBox(saved)
      setShowCustom(false)
      setCustomMsg(`Added ${saved.item_code} (draft)`)
    } catch (e) { setCustomMsg(e.message) }
  }

  return (
    <div className="card form-card">
      <h2>Ship it in</h2>
      <div className="form-grid rail-grid">
        <label className="field">
          <span>Box tare weight (kg)</span>
          <input type="number" value={params.tareKg} placeholder="unknown"
            onChange={set('tareKg')} />
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
        <BoxPicker packaging={packaging} selected={params.assets}
          onChange={(assets) => onChange({ ...params, assets })} />
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
        A solve takes ~11s, so changes here apply on the next Save &amp; calculate
        or Re-solve — not as you type.
      </p>

      <button className="btn-ghost" style={{ marginTop: 14 }}
        onClick={() => setShowCustom(!showCustom)}>
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
 * Ranked packaging fit for one part, from the real nesting engine
 * (POST /api/parts/{id}/solve). Solves on mount and on an explicit
 * Re-solve — never on a keystroke, since a solve is an ~11s server job.
 */
export default function PackingResults({ part, params, packaging, vehicles }) {
  const vehicle = vehicles.find((v) => String(v.id) === params.vehicleId) || null
  const type = insertType(part)

  const [job, setJob] = useState({ status: 'idle' })
  const [startedAt, setStartedAt] = useState(null)
  const [, forceTick] = useState(0)
  const [version, setVersion] = useState(0)
  const [selectedAsset, setSelectedAsset] = useState(null)

  useEffect(() => {
    if (!part?.id) { setJob({ status: 'no-id' }); return undefined }
    // AbortController, not a `cancelled` flag: a flag only stops the state
    // update, not the fetch/poll loop itself, so StrictMode's double-mount
    // (or clicking through parts fast) left every previous solve running to
    // completion on the worker — see F2-2 F4.
    const controller = new AbortController()
    setJob({ status: 'pending' })
    setStartedAt(Date.now())
    setSelectedAsset(null)
    runSolve(part.id, {
      tareKg: params.tareKg ? +params.tareKg : null,
      vehicleName: vehicle?.name,
      topN: 5,
      assets: params.assets,
      signal: controller.signal,
    }).then((result) => setJob({ status: 'done', result }))
      .catch((err) => { if (err.name !== 'AbortError') setJob({ status: 'failed', error: err.message }) })
    return () => controller.abort()
    // Fires on mount + Re-solve (version) only — params/vehicle are read at
    // that moment, not on every keystroke. See D11.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [part?.id, version])

  // Elapsed-seconds tick for the pending spinner — independent of the poll
  // loop inside runSolve, which owns no UI state of its own.
  useEffect(() => {
    if (job.status !== 'pending') return undefined
    const t = setInterval(() => forceTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [job.status])

  const elapsedS = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0

  return (
    <div className="card form-card result-card">
      <h2>
        Packaging fit — {part.part_number}
        <span className="muted" style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
          {' '}({part.length_mm} × {part.breadth_mm} × {part.height_mm} mm, {part.weight_kg} kg
          {' '}· insert: {type})
        </span>
      </h2>

      {job.status === 'no-id' && (
        <p className="muted">Save this part first — the packing fit runs the
          real nesting solve against its saved CAD file.</p>
      )}

      {job.status === 'pending' && (
        <div className="solve-pending">
          <div className="spinner" /><span>Solving… {elapsedS}s</span>
        </div>
      )}

      {job.status === 'failed' && (
        <div className="warning">⚠ <span>{job.error}</span></div>
      )}

      {job.status === 'done' && (
        <ResultView result={job.result} part={part} type={type}
          packaging={packaging} vehicles={vehicles} params={params}
          selectedAsset={selectedAsset} onSelectAsset={setSelectedAsset} />
      )}

      {job.status !== 'pending' && job.status !== 'no-id' && (
        <button className="btn-ghost" style={{ marginTop: 14 }}
          onClick={() => setVersion((v) => v + 1)}>
          ↻ Re-solve
        </button>
      )}
    </div>
  )
}

const CUSTOM_KEY = '__custom__'

/** The custom design carries the same grid/extent_lbh/pitch_lbh/pose_label
 *  shape as a catalogue layout, so it goes through the same layoutToFit
 *  adapter — no second adapter for it. asset_name is synthetic (packaging
 *  has no row for a design that only exists in this run). */
function asCustomLayout(custom) {
  return { ...custom, asset_name: CUSTOM_KEY }
}

/** The custom box's own inner/outer, in the shape trayGeometry/InsertPanel
 *  expect — it is not a saved packaging row. */
function customBox(custom) {
  const [il, ib, ih] = custom.inner
  const [ol, ob, oh] = custom.outer
  return { item_code: 'Custom design', status: 'synthesized',
    inner_l_mm: il, inner_b_mm: ib, inner_h_mm: ih,
    outer_l_mm: ol, outer_b_mm: ob, outer_h_mm: oh, max_weight_kg: null }
}

/** The box `truck.asset_name` names — "custom" is the backend's own name for
 *  the synthesised design (distinct from CUSTOM_KEY, the card-selection
 *  sentinel used only inside this component). Null when there is no
 *  packaging row for a named catalogue asset. */
function boxForTruckAsset(assetName, packaging, custom) {
  if (assetName === 'custom') return custom ? customBox(custom) : null
  return packaging.find((p) => p.item_code === assetName) || null
}

function ResultView({ result, part, type, packaging, vehicles, params, selectedAsset, onSelectAsset }) {
  const { catalogue, custom, custom_beats_catalogue: beatsCatalogue, truck, warnings } = result
  const empty = catalogue.length === 0 && !custom
  const customLayout = custom ? asCustomLayout(custom) : null
  const options = customLayout ? [...catalogue, customLayout] : catalogue
  const selected = options.find((l) => l.asset_name === selectedAsset) || options[0] || null
  const selectedBox = selected?.asset_name === CUSTOM_KEY
    ? customBox(custom) : packaging.find((p) => p.item_code === selected?.asset_name)
  const truckBox = truck ? boxForTruckAsset(truck.asset_name, packaging, custom) : null
  // One tab set for whichever box is selected — switching boxes keeps the
  // reader on the same question (e.g. still looking at the truck plan).
  const [tab, setTab] = useState('layers')

  return (
    <>
      {/* D8: every warning, verbatim, no filter/slice/first-only. */}
      {warnings.map((w, i) => (
        <div key={i} className="warning">⚠ <span>{w}</span></div>
      ))}

      {empty ? (
        <div className="empty-stage" style={{ padding: '24px 0' }}>
          <div className="es-icon">▦</div>
          <h2>No layout found</h2>
          <p>See the warning above for why.</p>
        </div>
      ) : (
        <>
          <div className="box-cards">
            {catalogue.map((layout, i) => (
              <LayoutCard key={layout.asset_name} layout={layout} rank={i}
                box={packaging.find((p) => p.item_code === layout.asset_name)}
                selected={layout.asset_name === selected?.asset_name}
                onClick={() => onSelectAsset(layout.asset_name)} />
            ))}
            {customLayout && (
              <LayoutCard layout={customLayout} rank={-1} box={customBox(custom)}
                label="Custom design" beats={beatsCatalogue}
                selected={selected?.asset_name === CUSTOM_KEY}
                onClick={() => onSelectAsset(CUSTOM_KEY)} />
            )}
          </div>

          {selected && (
            <LayoutDetail layout={selected} box={selectedBox}
              part={part} type={type} tab={tab} onTabChange={setTab}
              truck={truck} truckBox={truckBox} vehicles={vehicles}
              dropdownVehicleId={params.vehicleId} />
          )}
        </>
      )}
    </>
  )
}

function LayoutCard({ layout, rank, box, selected, onClick, label, beats }) {
  return (
    <button className={`box-card${selected ? ' selected' : ''}`} onClick={onClick}>
      <div className="bc-code">
        {label ?? layout.asset_name}
        {rank === 0 && <span className="badge stp">best</span>}
        {beats && <span className="badge stp">beats catalogue</span>}
      </div>
      <div className="bc-count">{layout.count}<span> parts / box</span></div>
      <div className="bc-sub">grid {layout.grid.join(' × ')} · {layout.pose_label}</div>
      <div className="bc-sub">
        {box
          ? `${box.inner_l_mm} × ${box.inner_b_mm} × ${box.inner_h_mm} mm inner`
          : 'no packaging record for this box'}
      </div>
      {layout.limited_by === 'weight' && <div className="bc-truck">weight-limited</div>}
    </button>
  )
}

/**
 * Layers, insert design and truck load for one selected catalogue layout,
 * built from the layoutToFit adapter (lib/solve.js) so the drawing helpers
 * below are unchanged from before this ticket. The three heavy blocks are
 * tabbed — chips (the selected box's headline numbers) stay visible across
 * every tab so switching tabs never loses the "what am I looking at" answer.
 */
function LayoutDetail({ layout, box, part, type, tab, onTabChange, truck, truckBox, vehicles, dropdownVehicleId }) {
  const fit = useMemo(() => layoutToFit(layout), [layout])
  // Clearance/wall/foam are drawing-only (D4): they size the insert tray and
  // never change a count, so they live here — next to the drawing they
  // affect — not in the solve rail. Local state: nothing outside the insert
  // tab reads them.
  const [drawParams, setDrawParams] = useState({ clearance: '7.5', wall: '10', foam: '10' })
  const clearance = +drawParams.clearance || 0
  const wall = +drawParams.wall || 0
  const foam = +drawParams.foam || 0

  const contentWeight = layout.count * part.weight_kg
  const stackHeight = layout.extent_lbh[2] + (layout.grid[2] - 1) * layout.pitch_lbh[2]
  const dummies = fit.layerConfig.reduce((s, l) => s + (l.count - l.filled), 0)

  const tabs = [
    ['layers', 'Layers'],
    ['insert', 'Insert design'],
    ...(truck ? [['truck', 'Truck load']] : []),
  ]
  const activeTab = tabs.some(([k]) => k === tab) ? tab : 'layers'

  return (
    <div className="box-detail">
      <div className="detail-summary">
        <div className="sum-chip primary">
          <strong>{fit.total}</strong><span>parts / box</span></div>
        <div className="sum-chip">
          <strong>{fit.layers}</strong><span>layer{fit.layers !== 1 ? 's' : ''}</span></div>
        <div className="sum-chip">
          <strong>{fit.perLayer}</strong><span>per layer</span></div>
        {layout.limited_by && (
          <div className="sum-chip">
            <strong>{layout.limited_by}</strong><span>limited by</span></div>
        )}
        {box?.max_weight_kg > 0 && (
          <div className="sum-chip">
            <strong>{Math.round((contentWeight / box.max_weight_kg) * 100)}%</strong>
            <span>weight used</span></div>
        )}
      </div>

      <div className="result-tabs" role="tablist">
        {tabs.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={activeTab === key}
            className={`result-tab${activeTab === key ? ' active' : ''}`}
            onClick={() => onTabChange(key)}>
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'layers' && (
        <div className="detail-block">
          <h3><span className="h-icon">▤</span> Layers ({fit.layers})
            <span className="muted" style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
              {' '}— {layerSummary(fit)}</span></h3>
          <table className="parts-table layer-table">
            <thead>
              <tr><th>Layer</th><th>Pose</th>
                <th className="num">Parts</th><th className="num">Dummy pockets</th>
                <th className="num">Layer pitch (mm)</th></tr>
            </thead>
            <tbody>
              {fit.layerConfig.map((l, i) => (
                <tr key={i}>
                  <td className="mono">{i + 1}</td>
                  <td><span className="orient-chip">{l.label}</span></td>
                  <td className="num mono strong">{l.filled}</td>
                  <td className="num mono">{l.count - l.filled > 0 ? l.count - l.filled : '—'}</td>
                  <td className="num mono">{fmtMm(l.layerH)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3 style={{ marginTop: 18 }}><span className="h-icon">▦</span> Box packing analysis</h3>
          <dl className="result-facts">
            <div><dt>Container</dt><dd className="mono">
              {box ? `${box.item_code} (${box.status})` : `${layout.asset_name} (no packaging record)`}</dd></div>
            {box && (
              <div><dt>Inner / outer</dt><dd className="mono">
                {box.inner_l_mm} × {box.inner_b_mm} × {box.inner_h_mm} /{' '}
                {box.outer_l_mm} × {box.outer_b_mm} × {box.outer_h_mm} mm</dd></div>
            )}
            <div><dt>Part dims</dt><dd className="mono">{part.length_mm} × {part.breadth_mm} × {part.height_mm} mm · {part.weight_kg} kg</dd></div>
            <div><dt>Insert</dt><dd>{type}{type === 'comb/slot'
              ? ' — slotted supports + PP/EVA bottom sheet'
              : ' — divider walls forming pockets'}</dd></div>
            <div><dt>Pattern</dt><dd>{fit.fill.desc}, {fit.layers} high</dd></div>
            <div><dt>Measured pitch</dt><dd className="mono">
              {fmtMm(layout.pitch_lbh[0])} × {fmtMm(layout.pitch_lbh[1])} × {fmtMm(layout.pitch_lbh[2])} mm</dd></div>
            {layout.interleave && (
              <div><dt>Interleave</dt><dd className="mono">
                {layout.interleave.map(fmtMm).join(' × ')}</dd></div>
            )}
            {dummies > 0 && (
              <div><dt>Dummy pockets</dt><dd>{dummies} — the top tray does not fill</dd></div>
            )}
            {box && (
              <div><dt>Headroom</dt><dd className="mono">
                {fmtMm(Math.max(0, box.inner_h_mm - stackHeight))} mm unused height</dd></div>
            )}
            {box?.max_weight_kg > 0 && (
              <div><dt>Content weight</dt><dd className="mono">
                {fmtMm(contentWeight)} / {box.max_weight_kg} kg</dd></div>
            )}
          </dl>
        </div>
      )}

      {activeTab === 'insert' && (
        box ? (
          <>
            <details className="disclosure">
              <summary>Insert drawing parameters</summary>
              <p className="muted" style={{ fontSize: 12, margin: '6px 0 10px' }}>
                Drawing only — part spacing comes from the measured nesting
                pitch above, not from these. Changing them redraws the tray
                below immediately.
              </p>
              <div className="form-grid rail-grid">
                <label className="field">
                  <span>Clearance / side (mm)</span>
                  <input type="number" value={drawParams.clearance}
                    onChange={(e) => setDrawParams({ ...drawParams, clearance: e.target.value })} />
                </label>
                <label className="field">
                  <span>Divider wall (mm)</span>
                  <input type="number" value={drawParams.wall}
                    onChange={(e) => setDrawParams({ ...drawParams, wall: e.target.value })} />
                </label>
                <label className="field">
                  <span>Layer foam (mm)</span>
                  <input type="number" value={drawParams.foam}
                    onChange={(e) => setDrawParams({ ...drawParams, foam: e.target.value })} />
                </label>
              </div>
            </details>
            <InsertPanel box={box} fit={fit} layout={layout} type={type}
              clearance={clearance} wall={wall} foam={foam} />
          </>
        ) : (
          <p className="muted">No packaging record for {layout.asset_name} —
            add it to the master list to see the insert tray drawing.</p>
        )
      )}

      {/* Outside the `box` branch on purpose: the BOM comes off the measured
          lattice and needs no packaging row, so a layout missing from the
          master list still gets its insert design. */}
      {activeTab === 'insert' && (
        <DunnageBom dunnage={layout.dunnage} drawingUrl={layout.drawing_url} />
      )}

      {activeTab === 'truck' && truck && (
        <TruckSection truck={truck} vehicles={vehicles} box={truckBox}
          dropdownVehicleId={dropdownVehicleId} />
      )}
    </div>
  )
}

/** Build the drawable floor plan straight from the backend's own winning
 *  arrangement (D0) — truck.floor_grid × truck.floor_rotated is the exact
 *  fit `truck.boxes` was computed from, so the drawing and the number can
 *  never disagree (F2-2 F3; the old mixed-orientation split-strip helper
 *  this replaced could return a floor pattern the parts/truck figure was
 *  never derived from). `nx` cells run along cargo length, `ny` along
 *  cargo breadth. */

/** Numbers are always truck.* (D10). Label and cargo dims resolve from
 *  `truck.vehicle` — the vehicle the backend actually solved against — not
 *  the params dropdown, which can move on after a solve finishes (F2-2 F2).
 *  `box` is the packaging/custom row truck.asset_name names; when there is
 *  no row for that asset (not saved to the master list), the chips still
 *  render, only the drawing is skipped. */
function TruckSection({ truck, vehicles, dropdownVehicleId, box }) {
  const truckRef = useRef(null)
  const vehicle = vehicles.find((v) => v.name === truck.vehicle) || null
  // Dropdown no longer matches what these numbers are for — flag it next to
  // Re-solve rather than silently relabel or auto-solve.
  const stale = !!dropdownVehicleId && !!vehicle && String(vehicle.id) !== dropdownVehicleId

  // A vehicle we cannot resolve costs us the DRAWING, not the numbers —
  // boxes/parts/kg per box/limited_by came from the backend and need no
  // cargo dims. Returning null here hid a correct answer, which is the same
  // defect this block already avoids for a missing packaging row.
  const plan = vehicle && box ? floorPlanFromTruck(truck, box) : null

  return (
    <div className="truck-section" ref={truckRef} style={{ marginTop: 18 }}>
      <div className="insert-card-head">
        <h3 style={{ margin: 0 }}><span className="h-icon">▣</span>
          {' '}Truck loading plan — {truck.vehicle.replaceAll('_', ' ')}
          {' '}({truck.asset_name}{!box && ' — no packaging record'}
          {box && !vehicle && ' — vehicle not in list'})</h3>
        {plan && (
          <button className="btn-ghost" title="Download as PNG"
            onClick={() => downloadFigure(truckRef.current,
              `truck-${truck.vehicle}-${truck.asset_name}.png`)}>
            ⤓ PNG
          </button>
        )}
      </div>
      <div className="truck-chips">
        <span className="sum-chip"><strong>{truck.boxes}</strong>
          <span>boxes loaded</span></span>
        <span className="sum-chip"><strong>{truck.parts}</strong>
          <span>parts / truck</span></span>
        <span className="sum-chip"><strong>{fmtMm(truck.kg_per_box)}</strong>
          <span>kg / box</span></span>
        <span className="sum-chip"><strong>{fmtMm(truck.tare_kg)}</strong>
          <span>kg tare (this box)</span></span>
        <span className="sum-chip">
          <strong>{truck.limited_by}</strong><span>bound</span></span>
      </div>
      {plan && <TruckLoadIso vehicle={vehicle} box={box} plan={plan} />}
      {stale && (
        <div className="warning" style={{ marginTop: 10 }}>⚠ <span>
          This plan is for {truck.vehicle.replaceAll('_', ' ')} — the vehicle
          dropdown has changed since this solve. Re-solve to match it.
        </span></div>
      )}
    </div>
  )
}

/**
 * The generated insert BOM (F-BOM) — `layout.dunnage` (DunnageOut,
 * backend/app/schemas.py), nothing computed here (CLAUDE.md hard rule 9).
 * Not every layout carries one yet, so this renders nothing when absent —
 * same convention as the rest of the tab (no packaging record → no drawing).
 */
function DunnageBom({ dunnage, drawingUrl }) {
  const [exploded, setExploded] = useState(false)
  if (!dunnage) return null
  const budget = heightBudget(dunnage)
  const showExplode = hasDrawing(drawingUrl)

  return (
    <div className="detail-block dunnage-block">
      <div className="insert-card-head">
        <h3 style={{ margin: 0 }}><span className="h-icon">▤</span> Insert BOM</h3>
        {showExplode && (
          <button className="btn-ghost" onClick={() => setExploded(true)}>
            ⛶ Explode
          </button>
        )}
      </div>

      <div className={`height-budget${budget.fits ? '' : ' fail'}`}>
        <strong>{budget.fitsLabel}</strong> — {budget.buildLine}
        {budget.nestLine && <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{budget.nestLine}</div>}
      </div>

      <table className="parts-table bom-table">
        <thead>
          <tr>
            <th>Element</th><th className="num">Size (mm)</th>
            <th className="num">Qty</th><th>Spec</th><th>Basis</th>
          </tr>
        </thead>
        <tbody>
          {dunnage.elements.map((el, i) => {
            const note = unknownNote(el.unknown)
            return (
              <tr key={i}>
                <td>{el.name}</td>
                <td className="num mono">{fmtSize(el.size)}</td>
                <td className="num mono">{fmtQty(el.qty)}</td>
                <td className="mono">{el.spec ?? '—'}</td>
                <td>
                  <span className={`badge ${basisClass(el.basis)}`}>{el.basis}</span>
                  {note && <div className="muted" style={{ fontSize: 11.5, marginTop: 2 }}>{note}</div>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {/* Verbatim — it says physical trials still decide; not ours to paraphrase. */}
      {dunnage.caveat && <p className="muted" style={{ marginTop: 10 }}>{dunnage.caveat}</p>}

      {exploded && (
        <ExplodeModal url={drawingUrl} onClose={() => setExploded(false)} />
      )}
    </div>
  )
}

/** Click-outside and Esc close, focus starts on the close button. No new
 *  dependency — CLAUDE.md keeps frontend deps at react + three. */
function ExplodeModal({ url, onClose }) {
  const closeRef = useRef(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label="Exploded insert view">
        <div className="modal-head">
          <span>Exploded view</span>
          <button ref={closeRef} className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <img src={url} alt="Exploded insert drawing" className="modal-img" />
      </div>
    </div>
  )
}

/**
 * All insert trays for the selected box, each as its own dimensioned
 * isometric drawing + spec card (no tabs — a solution with two tray designs
 * shows both). The rotating 3D view is an optional per-tray toggle.
 */
function InsertPanel({ box, fit, layout, type, clearance, wall, foam }) {
  // Interleaved layouts (pitch < extent, in-plane): a pocket-per-part tray
  // would draw pockets that overlap by (extent - pitch) mm — unmanufacturable.
  // The count and pitch are still measured and correct; only the tray
  // drawing for this pose does not exist yet (F2-2 F1). No groupInserts /
  // trayGeometry call for it — there is no valid pocket geometry to compute.
  if (fit.interleaved) {
    const [pl, pb] = layout.pitch_lbh
    const [el, eb] = layout.extent_lbh
    const axis = pl < el ? { pitch: pl, extent: el } : { pitch: pb, extent: eb }
    return (
      <div className="detail-views">
        <h3><span className="h-icon">▦</span> Insert design</h3>
        <div className="foam-note">Parts interleave in this pose (pitch{' '}
          {fmtMm(axis.pitch)} mm &lt; part width {fmtMm(axis.extent)} mm), so
          the insert is a slotted comb, not one pocket per part. Count and
          pitch are measured and correct; the tray drawing for interleaved
          poses is not built yet.</div>
      </div>
    )
  }

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
