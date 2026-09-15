import { useEffect, useMemo, useRef, useState } from 'react'
import { groupInserts, insertType, layerSummary, trayGeometry } from '../lib/packing.js'
import { runSolve, layoutToFit, floorPlanFromTruck, errorDetail } from '../lib/solve.js'
import { fmtQty, fmtSize, basisClass, unknownNote, heightBudget, hasDrawing } from '../lib/bom.js'
import { downloadFigure } from '../lib/download.js'
import InsertTray3D from './InsertTray3D.jsx'
import InsertIso from './InsertIso.jsx'
import TruckLoadIso from './TruckLoadIso.jsx'
import PackAnimation from './PackAnimation.jsx'

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
    : selected.length <= 4
      ? selected.join(', ')
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
export function PackingParams({ params, onChange, vehicles, packaging, onAddBox, title = 'Ship it in',
  onRerun, rerunReady, rerunBusy }) {
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
      // "draft" = unverified dims (backend/app/models.py Packaging.status);
      // the row is global, not project-scoped, so it's excluded from the
      // default ranked search everywhere until someone marks it checked —
      // not hidden from other projects.
      setCustomMsg(`Added ${saved.item_code} — draft, excluded from default rankings until checked`)
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
        <label className="field" style={{ gap: 2 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={!!params.confirmedPoseOnly}
              onChange={(e) => onChange({ ...params, confirmedPoseOnly: e.target.checked })} />
            Only this pose
          </span>
          <span className="muted" style={{ fontSize: 11.5 }}>
            Off = solver picks the best pose (recommended)
          </span>
        </label>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
        Changes here apply when you press "Re-run with these parameters"
        below (or Save &amp; calculate for a new project) — not as you type.
      </p>

      <button className="btn-ghost" style={{ marginTop: 14 }}
        onClick={() => setShowCustom(!showCustom)}>
        {showCustom ? 'Cancel custom box' : '+ Custom box'}
      </button>

      {rerunReady && (
        <button className="btn-ghost" style={{ marginTop: 10 }}
          disabled={rerunBusy} onClick={onRerun}>
          {rerunBusy ? 'Solving…' : '↻ Re-run with these parameters'}
        </button>
      )}

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
export default function PackingResults({ part, params, packaging, vehicles, projectId, tab, run, onSolved, onSolveStarted,
  rerunRef, onRerunStateChange }) {
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
    // Ticket 2b: the count can land (result.render_status "pending") well
    // before drawing_url/gif_url/packed_url do. `showCounts` is what flips
    // the screen to "done" the first time that happens, from whichever of
    // onCounts (still rendering) / the resolved promise (already done, or
    // render finished) gets there first — guarded so it only fires once.
    let countsShown = false
    function showCounts(result, id) {
      countsShown = true
      shownRunId.current = id
      setJob({ status: 'done', result })
      onSolved?.(id)
    }
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
      // Fires once the counts exist, while runSolve keeps polling the same
      // job for the pictures — shownRunId is already set by onStarted above.
      onCounts: (result) => showCounts(result, shownRunId.current),
    }).then(({ result, solveJobId }) => {
      // Either the first time we hear "done" (render_status was never
      // "pending" — old runs / a render that beat this poll), or the final
      // update once render_status leaves "pending" — refresh the drawing
      // fields without re-triggering onSolved a second time.
      if (!countsShown) showCounts(result, solveJobId)
      else setJob((prev) => (prev.status === 'done' ? { ...prev, result } : prev))
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
          // Ticket 2b: counts exist as soon as status is "done", but
          // drawing_url/gif_url/packed_url can still be on the way —
          // render_status "pending" means keep polling this same job, same
          // 3s cadence, instead of returning; the screen already shows the
          // count from the setJob above, only the image stage is waiting.
          if ((j.result.render_status ?? 'done') === 'pending') {
            await new Promise((resolve, reject) => {
              const t = setTimeout(resolve, 3000)
              controller.signal.addEventListener('abort',
                () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')) }, { once: true })
            })
            continue
          }
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

  // F1: the "Re-run" button now lives in the left rail (PackingParams), a
  // sibling component under a shared parent — it needs this render's `solve`
  // closure (always current params/vehicle/etc, same as the old in-place
  // button had for free) and the busy/ready flags that used to gate it.
  // Mutating a ref during render is fine (no dev warning, unlike a parent
  // setState call would be); the ready/busy flags DO need to re-render the
  // sibling, so those go up via a state-setter prop from an effect instead.
  if (rerunRef) rerunRef.current = solve
  useEffect(() => {
    onRerunStateChange?.({ busy: job.status === 'pending', ready: job.status !== 'no-id' })
  }, [job.status]) // eslint-disable-line react-hooks/exhaustive-deps

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

      {job.status === 'done' && (
        <RunCaptionRow caption={runCaption} warnings={job.result.warnings} />
      )}

      {job.status === 'no-id' && (
        <p className="muted">Save the part first to see the fit.</p>
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
          packaging={packaging} vehicles={vehicles} params={params} run={run}
          selectedAsset={selectedAsset} onSelectAsset={setSelectedAsset}
          controlledTab={tab} />
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

function ResultView({ result, part, type, packaging, vehicles, params, run, selectedAsset, onSelectAsset, controlledTab }) {
  const { catalogue, custom, custom_beats_catalogue: beatsCatalogue, truck } = result
  const empty = catalogue.length === 0 && !custom
  const customLayout = custom ? asCustomLayout(custom) : null
  const options = customLayout ? [...catalogue, customLayout] : catalogue
  // The hero is the run's BEST layout, not always catalogue[0]: when the
  // custom design wins (backend's own custom_beats_catalogue flag — never
  // re-derived client-side, hard rule 9), options[0] is still catalogue[0]
  // because customLayout is appended at the END of `options`. Rahul's
  // live-use feedback: this is the first thing the user should see, so it
  // leads the screen as a hero, independent of whatever card is clicked
  // below (that click only changes LayoutDetail, never the hero/`selected`).
  const heroLayout = beatsCatalogue && customLayout ? customLayout : (catalogue[0] || customLayout || null)
  // Nothing clicked yet -> the detail below describes the HERO box, not
  // catalogue[0]: when the custom design wins they differ, and the analysis
  // table would otherwise quote another box's pitch/pattern under the hero.
  const selected = options.find((l) => l.asset_name === selectedAsset) || heroLayout || options[0] || null
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
      {empty ? (
        <div className="empty-stage" style={{ padding: '24px 0' }}>
          <div className="es-icon">▦</div>
          <h2>No layout found</h2>
          <p>See the warning above for why.</p>
        </div>
      ) : (
        <>
          {heroLayout && (
            <HeroSolution layout={heroLayout} part={part} run={run}
              label={heroLayout.asset_name === CUSTOM_KEY ? 'Custom design' : undefined}
              beatsCatalogue={beatsCatalogue}
              renderStatus={result.render_status ?? 'done'} renderError={result.render_error} />
          )}

          <h3 className="ranked-heading"><span className="h-icon">▦</span> Ranked box comparison</h3>
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

/** F1 follow-up: the run caption ("Run of 15 Sept · 5 mm clearance · ...")
 *  and D8's warnings ("N notes") used to be two separate rows above the
 *  hero, each its own bar — folded into one line to reclaim the vertical
 *  space the hero animation needed back. Same `.warning` rows, verbatim, no
 *  filter/slice/first-only, once opened — only the trigger moved from its
 *  own bordered bar to inline text at the end of the caption. */
function RunCaptionRow({ caption, warnings }) {
  const [open, setOpen] = useState(false)
  const hasWarnings = warnings && warnings.length > 0
  if (!caption && !hasWarnings) return null
  return (
    <div className="run-caption-row" style={{ margin: '-8px 0 14px' }}>
      <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
        {caption}
        {hasWarnings && (
          <>
            {caption && ' · '}
            <button type="button" className="inline-disclosure" aria-expanded={open}
              onClick={() => setOpen((o) => !o)}>
              {warnings.length} note{warnings.length !== 1 ? 's' : ''} {open ? '▾' : '▸'}
            </button>
          </>
        )}
      </p>
      {hasWarnings && open && warnings.map((w, i) => (
        <div key={i} className="warning">⚠ <span>{w}</span></div>
      ))}
    </div>
  )
}

/** Image + its view toggle, shared by the hero solution image and the
 *  Insert BOM modal (moved out of what used to be ExplodeModal's own
 *  toggle+img markup — one renderer, not two). `views` is whichever of
 *  packed/exploded/packing-order actually exist; a single view renders with
 *  no toggle at all. `onImageClick`, when given, makes the image itself a
 *  zoom-in trigger (F2: click any drawing to open it full-size) — the modal
 *  reuses this same component without it, since its image is already the
 *  full-size view. F4: an 'anim' entry carries `sequence`/`glbUrl` instead
 *  of `url` and renders the live PackAnimation — same toggle, same modal,
 *  so it opens large for free rather than needing its own zoom plumbing. */
function ImageStage({ views, view, onViewChange, onImageClick, imgClassName = '', animHeight = 420 }) {
  if (views.length === 0) return null
  const active = views.find((v) => v.key === view) || views[0]
  const cls = `modal-img${onImageClick ? ' zoomable' : ''}${imgClassName ? ` ${imgClassName}` : ''}`
  return (
    <>
      {views.length > 1 && (
        <div className="view-toggle" role="group" aria-label="View">
          {views.map((v) => (
            <button key={v.key} type="button" aria-pressed={active.key === v.key}
              onClick={() => onViewChange(v.key)}>{v.label}</button>
          ))}
        </div>
      )}
      {active.kind === 'anim' ? (
        <PackAnimation sequence={active.sequence} glbUrl={active.glbUrl} height={animHeight}
          onStageClick={onImageClick ? () => onImageClick(active.key) : undefined} />
      ) : onImageClick ? (
        <img src={active.url} alt={active.label} className={cls}
          role="button" tabIndex={0}
          onClick={() => onImageClick(active.key)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onImageClick(active.key) }
          }} />
      ) : (
        <img src={active.url} alt={active.label} className={cls} />
      )}
    </>
  )
}

/** Packed (default when it exists) / Exploded / Packing order for the hero
 *  layout. `packed_url` is a backend field (CLAUDE.md hard rule 9 — nothing
 *  here re-derives it); when it's null there is no packed render yet, so no
 *  "Packed" tab is shown at all (a fake one, aliasing the exploded drawing,
 *  is worse than none — it tells the reader the box is packed when the only
 *  image on screen is exploded). Stage defaults to Exploded in that case.
 *  F4: an Animation view (live, seekable) is added — and becomes the
 *  default — whenever the run has a `sequence` and the part has a GLB; the
 *  GIF stays as the fallback for runs/browsers without it. */
function SolutionImage({ layout, part, renderStatus = 'done', renderError }) {
  const hasAnim = Boolean(layout.sequence && part?.glb_url)
  const views = useMemo(() => {
    const list = []
    if (hasAnim) list.push({ key: 'anim', label: 'Animation', kind: 'anim', sequence: layout.sequence, glbUrl: part.glb_url })
    if (hasDrawing(layout.packed_url)) list.push({ key: 'packed', label: 'Packed', url: layout.packed_url })
    if (hasDrawing(layout.drawing_url)) list.push({ key: 'exploded', label: 'Exploded', url: layout.drawing_url })
    if (hasDrawing(layout.gif_url)) list.push({ key: 'gif', label: 'Packing order', url: layout.gif_url })
    return list
  }, [layout, part, hasAnim])
  const defaultView = hasAnim ? 'anim' : (hasDrawing(layout.packed_url) ? 'packed' : 'exploded')
  const [view, setView] = useState(defaultView)
  useEffect(() => setView(defaultView), [layout?.asset_name, defaultView])
  // F2: at hero size the drawing/GIF is unreadable — click it to reopen the
  // same modal used elsewhere (below), starting on whichever view was
  // showing when clicked.
  const [zoomOpen, setZoomOpen] = useState(false)

  // Ticket 2b: the count/certificate render before the pictures do — while
  // render_status is "pending" there's nothing to gate on but that flag
  // (rule 9: never infer it from the null urls, which also happen on old
  // runs with no drawings at all). Same .hero-image box either way so the
  // layout doesn't jump when the image arrives.
  return (
    <div className="hero-image">
      {views.length > 0 ? (
        <>
          <ImageStage views={views} view={view} onViewChange={setView}
            onImageClick={() => setZoomOpen(true)} animHeight={320} />
          {zoomOpen && (
            <ExplodeModal views={views} initial={view} onClose={() => setZoomOpen(false)} />
          )}
        </>
      ) : renderStatus === 'pending' ? (
        <p className="muted hero-image-wait">Drawing the packed box… (about a minute)</p>
      ) : renderStatus === 'failed' ? (
        <div className="muted hero-image-wait" style={{ flexDirection: 'column' }}>
          <p style={{ margin: 0 }}>Drawings could not be rendered</p>
          {renderError && (
            <details style={{ marginTop: 4 }}>
              <summary>Why ▸</summary>
              <p style={{ margin: '4px 0 0' }}>{renderError}</p>
            </details>
          )}
        </div>
      ) : (
        <p className="muted">No solution drawing for this run yet.</p>
      )}
    </div>
  )
}

/**
 * First-viewport hero for the top-ranked solution — Rahul's live-use
 * feedback: "the user's first screen should be the solution, not what boxes
 * have what configuration". `layout` is options[0] (ResultView), never
 * re-picked here; clicking a different card below only changes LayoutDetail,
 * not this block.
 *
 * F1 (Gemini UX audit): the right-hand details grid (calculated count, upper
 * bound, pattern, layers, pitch, clearance, limited-by, weight cap) used to
 * sit here as `CertificateCard`, pushing the ranked cards below the fold.
 * Every one of those rows already exists elsewhere on this screen — the
 * detail-summary chips, the Box Packing Analysis table (which gained
 * "Geometric upper bound" and the grid dims in the Pattern row), or the
 * clearance footer note below LayoutDetail — so removing the grid here loses
 * nothing (CLAUDE.md rule 9: hide/move, never drop). The WHY disclosure has
 * no other home for the hero layout specifically, so it moves down here,
 * under the viewer, verbatim (same `layout.reasons` list CertificateCard
 * rendered).
 */
function HeroSolution({ layout, part, run, label, beatsCatalogue, renderStatus, renderError }) {
  const runLabel = run?.solve_job_id
    ? `Run #${String(run.solve_job_id).slice(0, 8)} · ${fmtRunDate(run.created_at) || 'stored run'}`
    : null
  const cuboidLine = run?.cuboid_count != null
    ? `${layout.count} vs ${run.cuboid_count} cuboid${run.gain_vs_cuboid != null ? ` (${run.gain_vs_cuboid}×)` : ''}`
    : null
  const customerLine = run?.customer_count != null
    ? `${layout.count} vs ${run.customer_count} today${run.gain_vs_customer_pct != null
      ? ` (${run.gain_vs_customer_pct >= 0 ? '+' : ''}${run.gain_vs_customer_pct}%)` : ''}`
    : null
  const metaText = [runLabel, cuboidLine, customerLine].filter(Boolean).join(' · ')
  const hasReasons = layout.reasons?.length > 0
  // F1 follow-up: the WHY trigger used to be its own bordered bar below the
  // viewer — folded inline into the meta line instead to give the space
  // back to the animation; plain state (not <details>) because the expanded
  // list still renders below the viewer, i.e. NOT right after this trigger
  // in the DOM, which <details>/<summary> can't do on its own.
  const [whyOpen, setWhyOpen] = useState(false)

  return (
    <div className="hero-solution">
      <div className="hero-headline">
        <span className="hero-count">{layout.count}</span>
        <div className="hero-headline-text">
          <div className="hero-asset">
            {label ?? layout.asset_name}
            {beatsCatalogue && label && <span className="badge stp">beats catalogue</span>}
            <span className="muted hero-pose"> · {layout.pose_label}</span>
          </div>
          <div className="muted hero-meta">
            {metaText}
            {hasReasons && (
              <>
                {metaText && ' · '}
                <button type="button" className="inline-disclosure" aria-expanded={whyOpen}
                  onClick={() => setWhyOpen((o) => !o)}>
                  {whyOpen ? '▾' : '▸'} why
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      <SolutionImage layout={layout} part={part} renderStatus={renderStatus} renderError={renderError} />

      {hasReasons && whyOpen && (
        <ul className="reasons-list hero-why">
          {layout.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
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
            {/* F1: was the hero's own "Geometric upper bound" row
                (CertificateCard) — moved here verbatim, same field. */}
            <div><dt>Geometric upper bound</dt><dd className="mono">{layout.count_upper}</dd></div>
            <div><dt>Pattern</dt><dd className="mono">
              {layout.grid.join(' × ')} · {fit.fill.desc}, {fit.layers} high</dd></div>
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
        <DunnageBom dunnage={layout.dunnage} drawingUrl={layout.drawing_url} gifUrl={layout.gif_url}
          sequence={layout.sequence} glbUrl={part?.glb_url} />
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
function DunnageBom({ dunnage, drawingUrl, gifUrl, sequence, glbUrl }) {
  const [modalView, setModalView] = useState(null) // null: closed; else 'anim' | 'png' | 'gif'
  if (!dunnage) return null
  const budget = heightBudget(dunnage)
  const showAnim = Boolean(sequence && glbUrl)
  const showExplode = hasDrawing(drawingUrl)
  const showPack = hasDrawing(gifUrl)
  // Same order as before this ticket (gif, then exploded) with 'anim' ahead
  // of both as the F4 default — only the modal's own views list moved up
  // here so ExplodeModal can be shared with the hero image below without
  // knowing about sequence/drawingUrl/gifUrl at all.
  const views = [
    ...(showAnim ? [{ key: 'anim', label: 'Animation', kind: 'anim', sequence, glbUrl }] : []),
    ...(showPack ? [{ key: 'gif', label: 'Packing sequence', url: gifUrl }] : []),
    ...(showExplode ? [{ key: 'png', label: 'Exploded view', url: drawingUrl }] : []),
  ]

  return (
    <div className="detail-block dunnage-block">
      <div className="insert-card-head">
        <h3 style={{ margin: 0 }}><span className="h-icon">▤</span> Insert BOM</h3>
        {(showAnim || showExplode || showPack) && (
          <div className="insert-card-actions">
            {showAnim && (
              <button className="btn-ghost" onClick={() => setModalView('anim')}>
                ▶ Animate
              </button>
            )}
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
        <ExplodeModal views={views} initial={modalView}
          onClose={() => setModalView(null)} />
      )}
    </div>
  )
}

/** Click-outside and Esc close, focus starts on the close button. No new
 *  dependency — CLAUDE.md keeps frontend deps at react + three; no pan-zoom
 *  library, just a Fit/1:1 plain-state toggle (F2). `views` is whatever list
 *  the caller already built (hero packed/exploded/gif, or the insert BOM's
 *  gif/png) — this modal is the one shared popup for every drawing image,
 *  not a second one. `initial` opens on the view that was clicked. */
function ExplodeModal({ views, initial, onClose }) {
  const closeRef = useRef(null)
  const [view, setView] = useState(initial)
  const [zoom, setZoom] = useState('fit') // 'fit' | 'actual' — plain state, no pan-zoom lib

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label="Insert view">
        <div className="modal-head">
          <span>Insert view</span>
          <div className="modal-head-actions">
            <button className="btn-ghost" onClick={() => setZoom((z) => (z === 'fit' ? 'actual' : 'fit'))}>
              {zoom === 'fit' ? '1:1' : 'Fit'}
            </button>
            <button ref={closeRef} className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </div>
        {/* Toggle + image are the same ImageStage the hero solution image
            uses (moved, not duplicated — see SolutionImage above). At 1:1
            the card itself scrolls (overflow: auto, index.css) instead of
            the image shrinking to fit. */}
        <ImageStage views={views} view={view} onViewChange={setView}
          imgClassName={zoom === 'actual' ? 'actual' : ''}
          animHeight={Math.round(window.innerHeight * 0.72)} />
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
