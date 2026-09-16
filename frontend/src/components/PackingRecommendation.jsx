import { useEffect, useMemo, useRef, useState } from 'react'
import { resultRows } from '../lib/packing.js'
import { runSolve, floorPlanFromTruck, errorDetail } from '../lib/solve.js'
import { fmtQty, fmtSize, insertUrlFor } from '../lib/bom.js'
import { downloadFigure } from '../lib/download.js'
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
 * vehicle, box selection and in-plane clearance all change the solve's
 * answer, so they sit together and only take effect on Re-run (F13: the
 * old drawing-only clearance/wall/foam knobs this comment used to describe
 * are gone — clearance is a real solve parameter now, per-box wall/foam
 * choices don't exist).
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
        </label>
      </div>

      <button className="btn-ghost" style={{ marginTop: 14 }}
        onClick={() => setShowCustom(!showCustom)}>
        {showCustom ? 'Cancel custom box' : '+ Custom box'}
      </button>

      {rerunReady && (
        <button className="btn-ghost" style={{ marginTop: 10 }}
          disabled={rerunBusy} onClick={onRerun}>
          {rerunBusy ? 'Solving…' : '↻ Re-run'}
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
  // F13 round 2: one line, project name (the h2 above) + the date only —
  // extent/mass/insert/clearance/vehicle are all already in the kv list or
  // the parameters panel, not repeated here. No label prefix — the line's
  // position (right under the project name) already says what it is.
  const runCaption = job.status === 'done' && run ? fmtRunDate(run.created_at) : null

  return (
    <div className="card form-card result-card">
      <h2>
        {/* F6: the top-level Truck project tab reuses this same mounted
            PackingResults (never a second one — 40-50s solve) with `tab`
            steered to 'truck'; only the heading and the stage body below
            change for it. */}
        {tab === 'truck' ? 'Truck load' : 'Packaging fit'} — {part.part_number}
      </h2>

      {job.status === 'done' && (
        <RunCaptionRow caption={runCaption} />
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
        <ResultView result={job.result} part={part}
          packaging={packaging} vehicles={vehicles} params={params}
          selectedAsset={selectedAsset} onSelectAsset={setSelectedAsset}
          controlledTab={tab} />
      )}
    </div>
  )
}

const CUSTOM_KEY = '__custom__'

/** The custom design carries the same grid/extent_lbh/pitch_lbh/pose_label
 *  shape as a catalogue layout, so it renders through the same ResultsPanel/
 *  resultRows/LayoutCard — no second code path for it. asset_name is
 *  synthetic (packaging has no row for a design that only exists in this
 *  run). */
function asCustomLayout(custom) {
  return { ...custom, asset_name: CUSTOM_KEY }
}

/** The custom box's own inner/outer, in the shape the results panel and
 *  card expect — it is not a saved packaging row. */
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

function ResultView({ result, part, packaging, vehicles, params, selectedAsset, onSelectAsset, controlledTab }) {
  const { catalogue, custom, custom_beats_catalogue: beatsCatalogue, truck } = result
  const empty = catalogue.length === 0 && !custom
  const customLayout = custom ? asCustomLayout(custom) : null
  const options = customLayout ? [...catalogue, customLayout] : catalogue
  // The default is the run's BEST layout, not always catalogue[0]: when the
  // custom design wins (backend's own custom_beats_catalogue flag — never
  // re-derived client-side, hard rule 9), options[0] is still catalogue[0]
  // because customLayout is appended at the END of `options`.
  const heroLayout = beatsCatalogue && customLayout ? customLayout : (catalogue[0] || customLayout || null)
  // F13: the picture + key/value list track whichever candidate card is
  // selected (default: the best one) — a click swaps both, not a detail
  // panel underneath a fixed hero.
  const selected = options.find((l) => l.asset_name === selectedAsset) || heroLayout || options[0] || null
  const selectedBox = selected?.asset_name === CUSTOM_KEY
    ? customBox(custom) : packaging.find((p) => p.item_code === selected?.asset_name)
  const truckBox = truck ? boxForTruckAsset(truck.asset_name, packaging, custom) : null
  // F6: the Truck project tab shows the whole-run truck plan instead of the
  // packing panel — same `truck`/`truckBox`, reused via <TruckSection>,
  // never a second truck-fit expression (hard rule 9).
  const isTruckTab = controlledTab === 'truck'
  // `truck` is one whole-run plan for a single asset (TruckFitOut.asset_name)
  // — only offered to the results panel when it was actually computed for
  // the currently selected card, never mixed with a different box's numbers.
  const truckMatchesSelected = truck && selected && (
    selected.asset_name === truck.asset_name ||
    (selected.asset_name === CUSTOM_KEY && truck.asset_name === 'custom'))

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
          {/* ResultsPanel (and the PackAnimation it may mount) stays in the
              tree even while the Truck tab hides it — display:none, never a
              conditional unmount — so switching Packaging <-> Truck never
              remounts the animation or restarts its timeline. */}
          <div style={isTruckTab ? { display: 'none' } : undefined}>
            {selected && (
              <ResultsPanel layout={selected} box={selectedBox} part={part}
                truck={truckMatchesSelected ? truck : null}
                renderStatus={result.render_status ?? 'done'} renderError={result.render_error}
                label={selected.asset_name === CUSTOM_KEY ? 'Custom design' : undefined} />
            )}
          </div>

          {isTruckTab && (
            truck ? (
              <TruckSection truck={truck} vehicles={vehicles} box={truckBox}
                dropdownVehicleId={params.vehicleId} />
            ) : (
              <div className="empty-stage" style={{ padding: '24px 0' }}>
                <div className="es-icon">▣</div>
                <h2>No truck fit for this run</h2>
                <p>See the note above for why, or add a tare weight and re-run.</p>
              </div>
            )
          )}

          <h3 className="ranked-heading"><span className="h-icon">▦</span> Ranked box comparison</h3>
          <div className="box-cards">
            {catalogue.map((layout) => (
              <LayoutCard key={layout.asset_name} layout={layout}
                box={packaging.find((p) => p.item_code === layout.asset_name)}
                truck={truck}
                selected={layout.asset_name === selected?.asset_name}
                onClick={() => onSelectAsset(layout.asset_name)} />
            ))}
            {customLayout && (
              <LayoutCard layout={customLayout} box={customBox(custom)}
                truck={truck} label="Custom design"
                selected={selected?.asset_name === CUSTOM_KEY}
                onClick={() => onSelectAsset(CUSTOM_KEY)} />
            )}
          </div>

          {/* The generated insert BOM — a plain section, not a tab (F13). */}
          {selected?.dunnage && <DunnageBom dunnage={selected.dunnage} insertUrls={selected.insert_urls} />}
        </>
      )}
    </>
  )
}

/** The run caption ("15 Sept") — just the run date, one de-emphasised
 *  metadata line above the results panel; nothing else survives here
 *  (F13 round 2).
 *  Solve warnings don't surface on this screen at all: `warnings[0]` isn't
 *  necessarily about the selected layout (e.g. a custom-box tare warning
 *  showing under a catalogue card) — a per-layout constraint sentence is a
 *  backend field to add later (F13a), not something to guess client-side. */
function RunCaptionRow({ caption }) {
  if (!caption) return null
  return <p className="muted run-caption-row" style={{ margin: '-8px 0 14px', fontSize: 12.5 }}>{caption}</p>
}

/** Image (or live animation) + its view toggle — one renderer shared by the
 *  results panel and the zoom modal (ExplodeModal), so a picture never gets
 *  a second, slightly-different implementation for its full-size view.
 *  `views` is normally a single-item list here (F13 dropped the packed/
 *  exploded/packing-order toggle from this screen); the toggle only renders
 *  when more than one view is passed. `onImageClick`, when given, makes the
 *  image itself a zoom-in trigger (F2: click any drawing to open it
 *  full-size) — the modal reuses this same component without it, since its
 *  image is already the full-size view. An 'anim' entry carries
 *  `sequence`/`glbUrl` instead of `url` and renders the live PackAnimation. */
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
        <PackAnimation sequence={active.sequence} glbUrl={active.glbUrl} dunnage={active.dunnage} height={animHeight}
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

/**
 * F13: the results screen is one 3D picture + one key/value list, for
 * whichever candidate card is selected (ResultView, default: the run's
 * best) — no fixed hero pinned to the winner while the list describes a
 * different box. The 3D view is the live PackAnimation (F14); the packed/
 * exploded stills and the packing-order GIF are gone from this screen
 * (F13) — `layout.ortho_url` (F15, Front/Side/Top) is a thumbnail strip
 * underneath instead, zoomable in the same modal as everywhere else (F2).
 * Every row in the list is a field straight off `layout`/`layout.dunnage`/
 * `truck` (CLAUDE.md hard rule 9 — see packing.js's `resultRows`, the pure
 * function this renders). No binding-constraint line yet (F13a, backend).
 */
function ResultsPanel({ layout, box, part, truck, label, renderStatus = 'done', renderError }) {
  const hasAnim = Boolean(layout.sequence && part?.glb_url)
  const animViews = useMemo(() => (hasAnim
    ? [{ key: 'anim', label: 'Animation', kind: 'anim', sequence: layout.sequence, glbUrl: part.glb_url, dunnage: layout.dunnage }]
    : []), [layout, part, hasAnim])
  // null closed, else the (single-item) views array ExplodeModal/ImageStage
  // should show full-size — the animation and the ortho thumbnail each build
  // their own one-item list rather than sharing a toggle (they're two
  // separate pictures, not views of the same one).
  const [zoomViews, setZoomViews] = useState(null)
  const rows = useMemo(() => resultRows({ layout, box, truck, assetLabel: label }),
    [layout, box, truck, label])

  return (
    <div className="hero-solution">
      <div className="results-grid">
        <div className="hero-image">
          {hasAnim ? (
            <ImageStage views={animViews} view="anim" onViewChange={() => {}}
              onImageClick={() => setZoomViews(animViews)} animHeight={360} />
          ) : renderStatus === 'pending' ? (
            <p className="muted hero-image-wait">Drawing the packed box…</p>
          ) : renderStatus === 'failed' || renderError ? (
            // render_error is also set with status "done" when one layout's
            // insert BOM refused to render (F11): show the reason, not "yet".
            <p className="muted hero-image-wait">{renderError || 'Drawing could not be rendered'}</p>
          ) : (
            <p className="muted hero-image-wait">No packed view for this run yet.</p>
          )}
          {layout.ortho_url && (
            <button type="button" className="ortho-strip"
              onClick={() => setZoomViews([{ key: 'ortho', label: 'Front / Side / Top', url: layout.ortho_url }])}>
              <img src={layout.ortho_url} alt="Front / Side / Top" />
            </button>
          )}
        </div>

        <div className="box-detail results-kv">
          <dl className="result-facts">
            {rows.map((r) => (
              <div key={r.label}><dt>{r.label}</dt><dd className="mono">{r.value}</dd></div>
            ))}
          </dl>
        </div>
      </div>

      {zoomViews && (
        <ExplodeModal views={zoomViews} initial={zoomViews[0].key} onClose={() => setZoomViews(null)} />
      )}
    </div>
  )
}

/** Ranked candidate card — exactly three numbers (F13): box name/outer
 *  dims, parts/box, and parts/truck when the run's single truck plan was
 *  computed for this box, else layers (truck is one whole-run plan for one
 *  asset, never per-candidate — see boxForTruckAsset above). Rank/"best"
 *  and "beats catalogue" badges are gone: the list order and the selected
 *  card's picture already say which one leads. */
function LayoutCard({ layout, box, truck, selected, onClick, label }) {
  const matchesTruck = truck && (
    layout.asset_name === truck.asset_name ||
    (layout.asset_name === CUSTOM_KEY && truck.asset_name === 'custom'))
  return (
    <div className="box-card-cell">
      <button className={`box-card${selected ? ' selected' : ''}`} onClick={onClick}>
        <div className="bc-code">{label ?? layout.asset_name}</div>
        <div className="bc-sub">
          {box
            ? `${box.outer_l_mm} × ${box.outer_b_mm} × ${box.outer_h_mm} mm outer`
            : 'no packaging record for this box'}
        </div>
        <div className="bc-count">{layout.count}<span> parts / box</span></div>
        <div className="bc-sub">
          {matchesTruck
            ? `${truck.parts} parts / truck`
            : `${layout.grid[2]} layer${layout.grid[2] !== 1 ? 's' : ''}`}
        </div>
      </button>
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
 * Not every layout carries one yet, so this renders nothing when absent.
 * F13: a plain section below the picture+list, not a tab — the animation/
 * exploded-view/packing-order toggle this used to also carry is gone (the
 * animation is already the results panel's own 3D view above; the exploded
 * PNG and packing GIF are off this screen entirely).
 *
 * F13 round 2: Element / Size / Qty / Spec only — the height-budget line
 * (`dunnage.build_height_mm`/`inner_h_mm`) is already the "Build height /
 * inner height" kv row above, and the basis-of-estimate column (badges) /
 * its unknown-fields sub-note / the caveat paragraph are the proposal
 * PDF's own reading, not this screen's — Rahul does not want badges or
 * that sub-note back on this screen.
 *
 * F17: `insertUrls[i]` is the dimensioned manufacturing sheet for
 * `dunnage.elements[i]` (same index, paired by `insertUrlFor` — CLAUDE.md
 * hard rule 9, never filtered/re-derived). A thumbnail column only appears
 * once at least one sheet exists; a row with no sheet at its index gets an
 * empty cell, not a placeholder. Clicking a thumbnail opens the same
 * ExplodeModal/ImageStage everything else on this screen zooms into.
 */
function DunnageBom({ dunnage, insertUrls }) {
  if (!dunnage) return null
  const hasThumbs = insertUrls && insertUrls.length > 0
  const [zoomViews, setZoomViews] = useState(null)

  return (
    <div className="detail-block dunnage-block">
      <h3 style={{ margin: 0 }}><span className="h-icon">▤</span> Insert BOM</h3>

      <table className="parts-table bom-table">
        <thead>
          <tr>
            {hasThumbs && <th />}
            <th>Element</th><th className="num">Size (mm)</th>
            <th className="num">Qty</th><th>Spec</th>
          </tr>
        </thead>
        <tbody>
          {dunnage.elements.map((el, i) => {
            const url = insertUrlFor(insertUrls, i)
            return (
              <tr key={i}>
                {hasThumbs && (
                  <td>
                    {url && (
                      <button type="button" className="bom-thumb"
                        onClick={() => setZoomViews([{ key: 'insert', label: el.name, url }])}>
                        <img src={url} alt={el.name} />
                      </button>
                    )}
                  </td>
                )}
                <td>{el.name}</td>
                <td className="num mono">{fmtSize(el.size)}</td>
                <td className="num mono">{fmtQty(el.qty)}</td>
                <td className="mono">{el.spec ?? '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {zoomViews && (
        <ExplodeModal views={zoomViews} initial={zoomViews[0].key} onClose={() => setZoomViews(null)} />
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
        {/* Same ImageStage the results panel uses inline (not duplicated —
            see ResultsPanel above). At 1:1 the card itself scrolls
            (overflow: auto, index.css) instead of the image shrinking to
            fit. */}
        <ImageStage views={views} view={view} onViewChange={setView}
          imgClassName={zoom === 'actual' ? 'actual' : ''}
          animHeight={Math.round(window.innerHeight * 0.72)} />
      </div>
    </div>
  )
}

