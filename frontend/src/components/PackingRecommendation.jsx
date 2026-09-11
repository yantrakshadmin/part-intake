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
  return { vehicleId: '', tareKg: '', assets: [], confirmedPoseOnly: false, clearanceMm: '' }
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
export function PackingParams({ params, onChange, vehicles, packaging, onAddBox, title = 'Ship it in' }) {
  const [showCustom, setShowCustom] = useState(false)
  const [custom, setCustom] = useState({
    code: '', il: '', ib: '', ih: '', ol: '', ob: '', oh: '', wt: '',
  })
  const [customMsg, setCustomMsg] = useState('')
  const set = (key) => (e) => onChange({ ...params, [key]: e.target.value })

  // Vehicle select showed "—" until a run mirrored one in (PROD defect) —
  // once the vehicle list loads, default a still-blank selection to the
  // backend's own SolveIn default (SolveIn.vehicle = "32_ft_sxl"), else the
  // first vehicle, so a fresh project's rail already names a real truck.
  useEffect(() => {
    if (params.vehicleId || vehicles.length === 0) return
    const preferred = vehicles.find((v) => v.name === '32_ft_sxl') || vehicles[0]
    onChange({ ...params, vehicleId: String(preferred.id) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicles])

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
      <h2>{title}</h2>
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
      <div className="form-grid rail-grid" style={{ marginTop: 10 }}>
        <label className="field">
          <span>Clearance (mm)</span>
          <input type="number" min="0" max="50" value={params.clearanceMm} placeholder="default 5"
            onChange={(e) => onChange({ ...params, clearanceMm: e.target.value })} />
        </label>
        <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={!!params.confirmedPoseOnly}
            onChange={(e) => onChange({ ...params, confirmedPoseOnly: e.target.checked })} />
          <span>Search confirmed pose only</span>
        </label>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
        A solve takes about a minute (drawings and the packing GIF are rendered with it), so changes here apply on the next Save &amp; calculate
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

function fmtRunDate(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/**
 * Ranked packaging fit for one part. Two ways to land on a result, and
 * `runSolve` (a real POST that costs 20-50s) is called from exactly two
 * places: the mount effect below when there's no stored `run` to read yet
 * (the very first solve, right after New project's Save & calculate), and
 * the explicit "Re-run" button. Everything else — including switching
 * Packaging <-> Truck, or opening a project that already has a run — reads
 * the stored result over GET /api/solve-jobs/{id} and never re-solves
 * (PRD F5: "any row opens the existing results view").
 */
export default function PackingResults({ part, params, packaging, vehicles, projectId, tab, run, onSolved, onSolveStarted }) {
  const vehicle = vehicles.find((v) => String(v.id) === params.vehicleId) || null
  const type = insertType(part)

  const [job, setJob] = useState({ status: 'idle' })
  const [startedAt, setStartedAt] = useState(null)
  const [, forceTick] = useState(0)
  const [selectedAsset, setSelectedAsset] = useState(null)
  // The solve_job_id currently on screen, so a `run` prop that changes to
  // the id we just re-ran to (via onSolved -> parent navigate) is
  // recognised as already-shown instead of re-fetched.
  const shownRunId = useRef(null)
  // One AbortController for whichever fetch/poll is in flight — button
  // click, stored-run GET or the no-run mount solve all go through here, so
  // starting one always aborts whatever the previous one left running
  // (F2-2 F4), and unmounting aborts it too (cleanup below reads the ref,
  // not a closed-over variable).
  const controllerRef = useRef(null)

  function solve() {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setJob({ status: 'pending', kind: 'solve' })
    setStartedAt(Date.now())
    setSelectedAsset(null)
    runSolve(part.id, {
      tareKg: params.tareKg ? +params.tareKg : null,
      vehicleName: vehicle?.name,
      topN: 5,
      assets: params.assets,
      confirmedPoseOnly: params.confirmedPoseOnly,
      clearanceMm: params.clearanceMm !== '' ? +params.clearanceMm : null,
      signal: controller.signal,
      projectId,
      // Fires as soon as the POST returns the id, well before this promise
      // resolves — lets the caller land the pending job in project.runs
      // right away (PROD defect: a remount that only sees `run: null`
      // starts a second solve for the same part). Mark it shown here too,
      // same as the `.then` below, so a reload that hands this same run
      // back through `run` is recognised as already in flight, not re-polled.
      onStarted: (id) => { shownRunId.current = id; onSolveStarted?.(id) },
    }).then(({ result, solveJobId }) => {
      shownRunId.current = solveJobId
      setJob({ status: 'done', result })
      onSolved?.(solveJobId)
    }).catch((err) => { if (err.name !== 'AbortError') setJob({ status: 'failed', error: err.message }) })
  }

  // Stored-run path: nothing here ever POSTs a solve. A run that's still
  // pending/processing (opened right after a solve was kicked off
  // elsewhere, e.g. another tab) is the ordinary pending state, not a
  // failure — re-poll it every 3s, same as the shape of runSolve's own
  // poll loop, until it's done or actually failed.
  useEffect(() => {
    if (!run?.solve_job_id) return undefined
    if (shownRunId.current === run.solve_job_id) return undefined // already showing it — e.g. just re-ran to it
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setJob({ status: 'pending', kind: 'load' })
    setSelectedAsset(null)

    async function poll() {
      while (true) {
        const r = await fetch(`/api/solve-jobs/${run.solve_job_id}`, { signal: controller.signal })
        if (!r.ok) throw new Error(`Failed to load run (${r.status})`)
        const j = await r.json()
        if (j.status === 'done') {
          shownRunId.current = run.solve_job_id
          setJob({ status: 'done', result: j.result })
          return
        }
        if (j.status === 'failed') {
          setJob({ status: 'failed', error: j.error || 'Run failed' })
          return
        }
        setJob({ status: 'pending', kind: 'processing' })
        await new Promise((resolve, reject) => {
          const t = setTimeout(resolve, 3000)
          controller.signal.addEventListener('abort',
            () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')) }, { once: true })
        })
      }
    }

    poll().catch((err) => { if (err.name !== 'AbortError') setJob({ status: 'failed', error: err.message }) })
    return undefined
  }, [run?.solve_job_id])

  // No-run mount path — the very first solve for a project with no runs
  // yet. AbortController, not a `cancelled` flag: a flag only stops the
  // state update, not the fetch/poll loop itself, so StrictMode's
  // double-mount (or clicking through parts fast) left every previous solve
  // running to completion on the worker — see F2-2 F4.
  useEffect(() => {
    if (run?.solve_job_id) return undefined
    if (!part?.id) { setJob({ status: 'no-id' }); return undefined }
    solve()
    return undefined
    // Fires once on mount only, when there's no stored run to show —
    // params/vehicle are read at that moment. See D11.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [part?.id])

  // Unmount (or part swap) safety net — abort whatever's in flight,
  // regardless of which of the three paths above started it.
  useEffect(() => () => controllerRef.current?.abort(), [part?.id])

  // Elapsed-seconds tick for the pending spinner — independent of the poll
  // loop inside runSolve, which owns no UI state of its own.
  useEffect(() => {
    if (job.status !== 'pending') return undefined
    const t = setInterval(() => forceTick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [job.status])

  const elapsedS = startedAt ? Math.round((Date.now() - startedAt) / 1000) : 0
  const runDate = fmtRunDate(run?.created_at)
  const runCaption = job.status === 'done' && run
    ? [
        runDate ? `Run of ${runDate}` : 'Stored run',
        job.result.clearance_mm != null ? `${job.result.clearance_mm} mm clearance` : null,
        job.result.truck?.vehicle ? job.result.truck.vehicle : null,
      ].filter(Boolean).join(' · ')
    : null

  return (
    <div className="card form-card result-card">
      <h2>
        Packaging fit — {part.part_number}
        <span className="muted" style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
          {' '}({part.length_mm} × {part.breadth_mm} × {part.height_mm} mm, {part.weight_kg} kg
          {' '}· insert: {type})
        </span>
      </h2>

      {runCaption && <p className="muted" style={{ margin: '-8px 0 14px', fontSize: 12.5 }}>{runCaption}</p>}

      {job.status === 'no-id' && (
        <p className="muted">Save this part first — the packing fit runs the
          real nesting solve against its saved CAD file.</p>
      )}

      {job.status === 'pending' && (
        <div className="solve-pending">
          <div className="spinner" />
          <span>
            {job.kind === 'processing' ? 'Run is still processing…'
              : job.kind === 'load' ? 'Loading stored run…'
              : `Solving… ${elapsedS}s`}
          </span>
        </div>
      )}

      {job.status === 'failed' && (
        <div className="warning">⚠ <span>{job.error}</span></div>
      )}

      {job.status === 'done' && (
        <ResultView result={job.result} part={part} type={type}
          packaging={packaging} vehicles={vehicles} params={params}
          selectedAsset={selectedAsset} onSelectAsset={setSelectedAsset}
          controlledTab={tab} />
      )}

      {job.status !== 'pending' && job.status !== 'no-id' && (
        <button className="btn-ghost" style={{ marginTop: 14 }}
          onClick={() => solve()}>
          ↻ Re-run with these parameters
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

function ResultView({ result, part, type, packaging, vehicles, params, selectedAsset, onSelectAsset, controlledTab }) {
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
  const [tab, setTab] = useState(controlledTab || 'layers')
  // controlledTab is how the Project page's Packaging/Truck tabs steer a
  // single, already-mounted PackingResults (no second solve): it only
  // fires when the PROP changes (i.e. the project tab switched), so a
  // manual click on Layers/Insert/Truck inside LayoutDetail below isn't
  // fought back to controlledTab on every render.
  useEffect(() => { if (controlledTab) setTab(controlledTab) }, [controlledTab])

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

          {(result.poses_searched?.length > 0 || result.clearance_mm != null) && (
            <p className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>
              {result.poses_searched?.length > 0 && `Poses searched: ${result.poses_searched.join(', ')}`}
              {result.poses_searched?.length > 0 && result.clearance_mm != null && ' — '}
              {result.clearance_mm != null &&
                // Only the in-plane clearance is a solve parameter
                // (SolveIn.clearance_mm); stacking clearance is a fixed
                // engine constant (nesting.DEFAULT_STACK_CLEARANCE_MM = 0),
                // not returned by the API, so it's stated here as a label.
                `Clearance ${result.clearance_mm} mm in-plane, 0 mm between layers`}
            </p>
          )}
        </>
      )}
    </>
  )
}

function LayoutCard({ layout, rank, box, selected, onClick, label, beats }) {
  return (
    <div className="box-card-cell">
      <button className={`box-card${selected ? ' selected' : ''}`} onClick={onClick}>
        <div className="bc-code">
          {label ?? layout.asset_name}
          {rank === 0 && <span className="badge stp">best</span>}
          {beats && <span className="badge stp">beats catalogue</span>}
        </div>
        <div className="bc-count">
          {layout.count}<span> parts / box</span>
          {/* cuboid_count is a required int on LayoutOut/BoxDesignOut, so
              stored results that predate the field default to 0 rather than
              null — `> 0`, not `!= null`, or old runs show "cuboid 0". */}
          {layout.cuboid_count > 0 && (
            <span className="badge manual" style={{ marginLeft: 8, fontSize: 11, verticalAlign: 'middle' }}>
              cuboid {layout.cuboid_count}
            </span>
          )}
        </div>
        {layout.count_upper > layout.count && (
          <div className="muted" style={{ fontSize: 11.5 }}>
            geometry allows up to {layout.count_upper}
            {layout.silhouette?.cell_mm
              ? ` — ${layout.count} is the ${fmtMm(layout.silhouette.cell_mm)}mm raster floor`
              : ` — ${layout.count} is the raster floor`}
          </div>
        )}
        <div className="bc-sub">grid {layout.grid.join(' × ')} · {layout.pose_label}</div>
        <div className="bc-sub">
          {box
            ? `${box.inner_l_mm} × ${box.inner_b_mm} × ${box.inner_h_mm} mm inner`
            : 'no packaging record for this box'}
        </div>
        {layout.limited_by === 'weight' && <div className="bc-truck">weight-limited</div>}
      </button>
      {layout.reasons?.length > 0 && (
        <details className="disclosure" onClick={(e) => e.stopPropagation()}>
          <summary>Why</summary>
          <ul className="reasons-list">
            {layout.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </details>
      )}
    </div>
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
  // Pocket depth / floor sheet come off the measured insert BOM when this
  // layout has one (dunnage.py::_pocket_tray) — that supersedes the
  // clearance/foam drawing knobs below (CLAUDE.md hard rule 9). `wall` is
  // drawing-only (the divider width) either way, so it is never in this BOM.
  const bom = layout.dunnage?.archetype === 'pocket_tray' ? layout.dunnage : null

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
            {bom && (
              <p className="muted" style={{ fontSize: 12, margin: '6px 0 10px' }}>
                Pocket depth {fmtMm(bom.elements[0].cell_mm[2])} mm, layer sheet{' '}
                {fmtMm(bom.elements[1].dims_mm[2])} mm — from the insert BOM.
              </p>
            )}
            <details className="disclosure">
              <summary>Insert drawing parameters</summary>
              <p className="muted" style={{ fontSize: 12, margin: '6px 0 10px' }}>
                Drawing only — part spacing comes from the measured nesting
                pitch above, not from these. Changing them redraws the tray
                below immediately.
              </p>
              <div className="form-grid rail-grid">
                {/* Clearance/foam are superseded by the BOM's pocket depth /
                    layer sheet above when one is present — showing them too
                    would look editable but silently do nothing. Divider wall
                    has no BOM source (F-AUDIT-2 flag) — it's a pure drawing
                    knob and stays editable either way. */}
                {!bom && (
                  <label className="field">
                    <span>Clearance / side (mm)</span>
                    <input type="number" value={drawParams.clearance}
                      onChange={(e) => setDrawParams({ ...drawParams, clearance: e.target.value })} />
                  </label>
                )}
                <label className="field">
                  <span>Divider wall (mm)</span>
                  <input type="number" value={drawParams.wall}
                    onChange={(e) => setDrawParams({ ...drawParams, wall: e.target.value })} />
                </label>
                {!bom && (
                  <label className="field">
                    <span>Layer foam (mm)</span>
                    <input type="number" value={drawParams.foam}
                      onChange={(e) => setDrawParams({ ...drawParams, foam: e.target.value })} />
                  </label>
                )}
              </div>
            </details>
            <InsertPanel box={box} fit={fit} layout={layout} type={type}
              clearance={clearance} wall={wall} foam={foam} bom={bom} />
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
        <DunnageBom dunnage={layout.dunnage} drawingUrl={layout.drawing_url} gifUrl={layout.gif_url} />
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
function DunnageBom({ dunnage, drawingUrl, gifUrl }) {
  const [modalView, setModalView] = useState(null) // null: closed; else 'png' | 'gif'
  if (!dunnage) return null
  const budget = heightBudget(dunnage)
  const showExplode = hasDrawing(drawingUrl)
  const showPack = hasDrawing(gifUrl)

  return (
    <div className="detail-block dunnage-block">
      <div className="insert-card-head">
        <h3 style={{ margin: 0 }}><span className="h-icon">▤</span> Insert BOM</h3>
        {(showExplode || showPack) && (
          <div className="insert-card-actions">
            {showExplode && (
              <button className="btn-ghost" onClick={() => setModalView('png')}>
                ⛶ Explode
              </button>
            )}
            {showPack && (
              <button className="btn-ghost" onClick={() => setModalView('gif')}>
                ▶ Pack
              </button>
            )}
          </div>
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

      {modalView && (
        <ExplodeModal drawingUrl={drawingUrl} gifUrl={gifUrl} initial={modalView}
          onClose={() => setModalView(null)} />
      )}
    </div>
  )
}

/** Click-outside and Esc close, focus starts on the close button. No new
 *  dependency — CLAUDE.md keeps frontend deps at react + three. `initial`
 *  picks which image opens (the button clicked, 'png' or 'gif'); the toggle
 *  only appears when both exist — one image never needs a choice. */
function ExplodeModal({ drawingUrl, gifUrl, initial, onClose }) {
  const closeRef = useRef(null)
  const hasBoth = hasDrawing(drawingUrl) && hasDrawing(gifUrl)
  const [view, setView] = useState(initial)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const showGif = view === 'gif'
  const label = showGif ? 'Packing sequence' : 'Exploded view'

  return (
    <div className="modal-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label="Insert view">
        <div className="modal-head">
          {hasBoth ? (
            <div className="view-toggle" role="group" aria-label="View">
              <button type="button" aria-pressed={showGif} onClick={() => setView('gif')}>
                Packing sequence
              </button>
              <button type="button" aria-pressed={!showGif} onClick={() => setView('png')}>
                Exploded view
              </button>
            </div>
          ) : (
            <span>{label}</span>
          )}
          <button ref={closeRef} className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <img src={showGif ? gifUrl : drawingUrl}
          alt={showGif ? 'Packing sequence' : 'Exploded insert drawing'}
          className="modal-img" />
      </div>
    </div>
  )
}

/**
 * All insert trays for the selected box, each as its own dimensioned
 * isometric drawing + spec card (no tabs — a solution with two tray designs
 * shows both). The rotating 3D view is an optional per-tray toggle.
 */
function InsertPanel({ box, fit, layout, type, clearance, wall, foam, bom }) {
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
          geom={trayGeometry({ box, group: g, clearance, wall, foam, bom })}
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
