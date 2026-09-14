import { useCallback, useEffect, useRef, useState } from 'react'
import { navigate } from '../lib/router.js'
import { readError } from '../lib/api.js'
import PackingResults, {
  PackingParams, usePackingData, defaultPackingParams,
} from './PackingRecommendation.jsx'

const STATUSES = ['draft', 'solved', 'proposal_sent', 'trial', 'approved', 'archived']
const TABS = [
  ['overview', 'Overview'], ['packaging', 'Packaging'], ['truck', 'Truck'],
  ['runs', 'Runs'], ['proposal', 'Proposal'],
]

function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** The run to show: `?run=<id>` if it names one of this project's runs,
 *  else the recommended run, else the newest done run, else none (callers
 *  treat null as "nothing stored yet — solve on mount"). Every number a
 *  screen renders off a run comes from this one object — never a second
 *  run picked a different way.
 *
 *  When `?run=` is set but not (yet) in `project.runs` — the window between
 *  a just-finished solve's navigate() and the reload() that lands the new
 *  run in `project.runs` — return a stub carrying only that id rather than
 *  falling back to recommended/newest. PackingResults already has this
 *  run's result in memory (its `shownRunId`) and only reads `solve_job_id`
 *  off what's passed here, so the stub is enough to stop it from fetching
 *  the wrong (old) run for that one render. */
function pickedRun(project, query) {
  const runs = project.runs || []
  if (query?.run != null) {
    const r = runs.find((r) => String(r.solve_job_id) === String(query.run))
    return r || { solve_job_id: query.run }
  }
  if (project.recommended_run_id != null) {
    const r = runs.find((r) => r.solve_job_id === project.recommended_run_id)
    if (r) return r
  }
  const done = runs.find((r) => r.status === 'done')
  if (done) return done
  // No finished run yet — if one is already pending/processing, show that
  // instead of null. PackingResults polls a stored run in those states
  // every 3s and shows it once it lands, so a remount (e.g. Packaging ->
  // Overview -> Packaging) finds a run to poll here instead of falling
  // through to the mount-solve path and starting a SECOND solve for the
  // same part (PROD defect: two solve_part tasks 35s apart).
  const busy = runs.filter((r) => r.status === 'pending' || r.status === 'processing')
  return busy.reduce((newest, r) => (
    !newest || new Date(r.created_at) > new Date(newest.created_at) ? r : newest
  ), null)
}

/**
 * Project page (`#/projects/:id/:tab`) — header + tab strip, wired to
 * GET/PATCH /api/projects/{id}. Packaging data (catalogue + vehicles) and
 * solve params are fetched/held once here and handed to whichever tab needs
 * them, so switching Packaging <-> Truck doesn't re-fetch the catalogue.
 */
export default function ProjectPage({ id, tab, query }) {
  const [project, setProject] = useState(null)
  const [error, setError] = useState('')
  const packing = usePackingData()
  const [params, setParams] = useState(defaultPackingParams())
  // The run id Packaging/Truck currently show, kept alive across the
  // nav-strip switch to Proposal (which navigates without `?run=`, so
  // Proposal can't just read it off `query`). Set only while Packaging or
  // Truck is the active tab — Proposal reads this, never re-picks its own
  // "newest run" (CLAUDE.md rule 9).
  const [packagingRunId, setPackagingRunId] = useState(null)

  // Returns the fetch promise so an explicit re-run can `await reload()`
  // before navigating to the new run's id — otherwise the navigate would
  // land on a `?run=` the just-fetched project doesn't know about yet.
  const reload = useCallback(() => (
    fetch(`/api/projects/${id}`)
      .then((r) => { if (!r.ok) throw new Error('Failed to load project'); return r.json() })
      .then(setProject)
      .catch((e) => setError(e.message))
  ), [id])

  useEffect(() => { setProject(null); setError(''); setPackagingRunId(null); reload() }, [id, reload])

  // Guarded for `project` being null (the loading render) — this must run
  // on every render, same as every other hook above, or React throws
  // "Rendered more hooks than during the previous render" the moment
  // `project` lands and a below-the-early-return hook would suddenly
  // start being called.
  const activeTab = TABS.some(([k]) => k === tab) ? tab : 'overview'
  const packagingRun = project && (activeTab === 'packaging' || activeTab === 'truck') && project.part
    ? pickedRun(project, query) : null
  useEffect(() => {
    if (packagingRun) setPackagingRunId(packagingRun.solve_job_id)
  }, [packagingRun])

  async function patch(body) {
    const r = await fetch(`/api/projects/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error(await readError(r, 'Update failed'))
    const updated = await r.json()
    setProject(updated)
    return updated
  }

  if (error) return <div className="warning">⚠ <span>{error}</span></div>
  if (!project) return <p className="muted">Loading…</p>

  // A solve just finished — point the URL at it FIRST (so the very next
  // render already asks for this run — pickedRun's stub covers the gap
  // before `project.runs` catches up) and only then reload; reloading
  // first would render one frame with the old recommended/newest run and
  // fetch it, flashing the previous result before the new one lands.
  async function onSolved(newRunId) {
    if (newRunId) navigate(`#/projects/${id}/${activeTab}?run=${newRunId}`)
    await reload()
  }

  // Fired the moment a solve is POSTed (rule 2 of the PROD fix above) — just
  // reload so the new pending job lands in `project.runs`; no navigate here,
  // `run` on screen is already this one (PackingResults marks it shown as
  // soon as it starts, see onSolveStarted in solve()).
  async function onSolveStarted() { await reload() }

  return (
    <div>
      <ProjectHeader project={project} onPatch={patch} />

      <div className="result-tabs" role="tablist" style={{ marginTop: 20 }}>
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={activeTab === key}
            className={`result-tab${activeTab === key ? ' active' : ''}`}
            onClick={() => navigate(`#/projects/${id}/${key}`)}>
            {label}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 20 }}>
        {activeTab === 'overview' && <Overview project={project} onPatch={patch} />}
        {/* Packaging and Truck are the same PackingResults instance, kept
            mounted across the switch (same element position => React
            doesn't remount it) — only the controlled `tab` prop changes
            which of its result tabs (layers/insert/truck) is shown. A
            second mount would mean a second ~40-50s solve. */}
        {(activeTab === 'packaging' || activeTab === 'truck') && (
          <PackagingTruckTab project={project} run={packagingRun} packing={packing} params={params}
            onParamsChange={setParams} onSolved={onSolved} onSolveStarted={onSolveStarted}
            resultTab={activeTab === 'truck' ? 'truck' : 'layers'} />
        )}
        {activeTab === 'runs' && <RunsTab project={project} onPatch={patch} />}
        {activeTab === 'proposal' && <ProposalTab project={project} query={query} runId={packagingRunId} />}
      </div>
    </div>
  )
}

function ProjectHeader({ project, onPatch }) {
  const [owner, setOwner] = useState(project.owner || '')
  const [ownerErr, setOwnerErr] = useState('')
  const [statusErr, setStatusErr] = useState('')
  useEffect(() => setOwner(project.owner || ''), [project.owner])

  async function commitOwner() {
    if (owner === (project.owner || '')) return
    try { setOwnerErr(''); await onPatch({ owner: owner.trim() || null }) }
    catch (e) { setOwnerErr(e.message) }
  }

  async function commitStatus(e) {
    try { setStatusErr(''); await onPatch({ status: e.target.value }) }
    catch (err) { setStatusErr(err.message) }
  }

  return (
    <div className="card form-card project-header">
      <div className="project-header-row">
        <strong>{project.customer}</strong>
        <span className="mono">{project.part_number}</span>
        <select className={`badge status-select status-${project.status}`}
          value={project.status} onChange={commitStatus}>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input className="owner-input" placeholder="Owner (unassigned)"
          value={owner} onChange={(e) => setOwner(e.target.value)} onBlur={commitOwner} />
        <span className="muted">updated {fmtDate(project.updated_at)}</span>
      </div>
      {(ownerErr || statusErr) && (
        <div className="warning" style={{ marginTop: 8 }}>⚠ <span>{ownerErr || statusErr}</span></div>
      )}
    </div>
  )
}

/** Formats a ratio/percentage the backend already computed — never derives
 *  one (CLAUDE.md hard rule 9). `toFixed` is display rounding, not maths on
 *  best_count/cuboid_count/customer_count/annual_volume. */
function fmtGainLine(run) {
  if (run.cuboid_count == null) return null
  // gain_vs_cuboid arrives already rounded to 2dp from the backend — no
  // second toFixed here, or 4.98 prints as 5.0.
  const ratio = run.gain_vs_cuboid != null ? ` (${run.gain_vs_cuboid}×)` : ''
  return `${run.best_count ?? '—'} vs ${run.cuboid_count} cuboid${ratio}`
}

function fmtCustomerLine(run) {
  if (run.customer_count == null) return null
  const pctVal = run.gain_vs_customer_pct
  return (
    <>
      {run.best_count ?? '—'} vs {run.customer_count} today
      {pctVal != null && (
        <span style={pctVal < 0 ? { color: 'var(--red)' } : undefined}>
          {' '}({pctVal >= 0 ? '+' : ''}{Number(pctVal).toFixed(1)}%)
        </span>
      )}
    </>
  )
}

function Overview({ project, onPatch }) {
  if (!project.part) return <NoPart />

  const run = pickedRun(project)

  if (!run) {
    return (
      <div className="card empty-stage">
        <div className="es-icon">▦</div>
        <h2>No solve yet</h2>
        <p>Open Packaging to run the first one.</p>
        <button className="btn-primary" onClick={() => navigate(`#/projects/${project.id}/packaging`)}>
          Open Packaging
        </button>
      </div>
    )
  }

  const gainLine = fmtGainLine(run)
  const customerLine = fmtCustomerLine(run)

  return (
    <>
      <div className="card form-card">
        <div className="detail-summary">
          <div className="sum-chip primary">
            <strong>{run.best_count ?? '—'}</strong><span>parts / box</span>
          </div>
          {run.best_asset && (
            <div className="sum-chip">
              <strong className="mono" style={run.best_asset === 'custom' ? { color: 'var(--accent2)' } : undefined}>
                {run.best_asset === 'custom' ? 'Custom box' : run.best_asset}
              </strong>
              <span>asset</span>
            </div>
          )}
          {run.clearance_mm != null && (
            <div className="sum-chip"><strong>{run.clearance_mm}</strong><span>clearance mm</span></div>
          )}
          {run.inputs?.vehicle && (
            <div className="sum-chip"><strong>{run.inputs.vehicle}</strong><span>vehicle</span></div>
          )}
        </div>
        {(gainLine || customerLine) && (
          <div style={{ marginTop: 12 }}>
            {gainLine && <p className="mono">{gainLine}</p>}
            {customerLine && <p className="mono" style={{ marginTop: 4 }}>{customerLine}</p>}
          </div>
        )}
        <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
          <button className="btn-primary" onClick={() => navigate(`#/projects/${project.id}/packaging`)}>
            Re-run with changes
          </button>
          <button className="btn-ghost"
            onClick={() => navigate(`#/projects/${project.id}/proposal?generate=1`)}>
            Generate proposal
          </button>
        </div>
      </div>

      <ProjectInputs project={project} onPatch={onPatch} />

      <WhyThisDesign run={run} />

      <div className="overview-cards">
        <div className="card form-card">
          <h2>Packaging</h2>
          <dl className="result-facts">
            <div><dt>Catalogue</dt><dd className="mono">
              {run.catalogue_count != null ? `${run.catalogue_count} in ${run.catalogue_asset}` : '—'}</dd></div>
            <div><dt>Custom box</dt><dd className="mono">{run.custom_count ?? '—'}</dd></div>
          </dl>
        </div>
        <div className="card form-card">
          <h2>Truck</h2>
          <dl className="result-facts">
            <div><dt>Truck boxes</dt><dd className="mono">{run.truck_boxes ?? '—'}</dd></div>
            <div><dt>Vehicle</dt><dd className="mono">{run.truck_vehicle ?? '—'}</dd></div>
            <div><dt>Parts per truck</dt><dd className="mono">{run.parts_per_truck ?? '—'}</dd></div>
            <div><dt>Trips per year</dt><dd className="mono">
              {run.trips_per_year ?? (project.annual_volume == null ? 'enter annual volume' : '—')}</dd></div>
          </dl>
        </div>
      </div>
    </>
  )
}

/** F4: prints `run.reasons` verbatim, never composes its own wording
 *  (CLAUDE.md hard rule 9). Runs from before this field existed carry no
 *  reasons — the card stays, with a caption instead of an empty list. */
function WhyThisDesign({ run }) {
  const reasons = run.reasons || []
  return (
    <div className="card form-card">
      <h2>Why this design</h2>
      {reasons.length > 0 ? (
        <ul className="reasons-list">
          {reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      ) : (
        <p className="muted">Reasons ship with the next solve.</p>
      )}
    </div>
  )
}

// route_km used to live here: Project.route_km (models.py) is stored and
// echoed on ProjectOut (schemas.py) but no engine calculation reads it
// (checked engine.py — trips_per_year only uses annual_volume and
// parts_per_truck) — dropped per CLAUDE.md rule "never name an action that
// does not exist."
const PROJECT_INPUT_FIELDS = [
  ['annual_volume', "Annual volume (parts/yr)", 'number'],
  ['customer_count', "Customer's current parts per box", 'number'],
  ['customer_box', "Customer's current box", 'text'],
]

/** F3b/F6: entered data, never estimated (CLAUDE.md hard rule 2). Each
 *  field PATCHes on blur and shows the project's own stored value — never
 *  auto-filled or computed here. */
function ProjectInputs({ project, onPatch }) {
  const [values, setValues] = useState(() =>
    Object.fromEntries(PROJECT_INPUT_FIELDS.map(([key]) => [key, project[key] ?? ''])))
  const [error, setError] = useState('')

  useEffect(() => {
    setValues(Object.fromEntries(PROJECT_INPUT_FIELDS.map(([key]) => [key, project[key] ?? ''])))
  }, [project.annual_volume, project.customer_count, project.customer_box])

  async function commit(key, type) {
    const raw = values[key]
    const val = raw === '' ? null : type === 'number' ? Number(raw) : raw
    if (val === (project[key] ?? null)) return
    try { setError(''); await onPatch({ [key]: val }) }
    catch (e) { setError(e.message) }
  }

  return (
    <div className="card form-card">
      <h2>Project inputs</h2>
      {error && <div className="warning">⚠ <span>{error}</span></div>}
      <div className="form-grid rail-grid">
        {PROJECT_INPUT_FIELDS.map(([key, label, type]) => (
          <label className="field" key={key}>
            <span>{label}</span>
            <input type={type} min={type === 'number' ? '1' : undefined}
              value={values[key]}
              onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
              onBlur={() => commit(key, type)} />
            {values[key] === '' && <span className="muted field-note">not provided</span>}
          </label>
        ))}
      </div>
    </div>
  )
}

function NoPart() {
  return (
    <div className="card empty-stage">
      <div className="es-icon">▦</div>
      <h2>No part attached</h2>
      <p>This project has no saved CAD part yet.</p>
      <button className="btn-primary" onClick={() => navigate('#/projects/new')}>New project</button>
    </div>
  )
}

/** Packaging and Truck are one PackingResults, not two — a solve is
 *  40-50s, so a tab switch must never re-mount it. `resultTab` is the only
 *  thing that changes between the two project tabs. */
function PackagingTruckTab({ project, run, packing, params, onParamsChange, onSolved, onSolveStarted, resultTab }) {
  // `run` is computed once in ProjectPage (pickedRun(project, query)) and
  // handed down — Proposal reuses that same value, so the two tabs can
  // never resolve "the run on screen" differently (CLAUDE.md rule 9).

  // The rail must reflect the run on screen, not whatever it was last set
  // to — otherwise "Re-run with these parameters" quietly re-solves with
  // stale rail values while the caption still names the old run's. Solver
  // parameters (clearance/assets/pose/tare/vehicle) mirrored from the run
  // record, not part dims — hard rule 2 is about the part, not this.
  // `run.inputs` only exists on a real RunOut, never on pickedRun's stub,
  // so a just-finished re-run (still a stub for one render) leaves the
  // rail alone — it already holds what was just used to produce it.
  useEffect(() => {
    if (!run?.inputs) return
    const vehicle = packing.vehicles.find((v) => v.name === run.inputs.vehicle)
    onParamsChange((p) => ({
      ...p,
      clearanceMm: run.inputs.clearance_mm ?? '',
      assets: run.inputs.assets ?? [],
      confirmedPoseOnly: !!run.inputs.confirmed_pose_only,
      tareKg: run.inputs.tare_kg ?? '',
      vehicleId: vehicle ? String(vehicle.id) : p.vehicleId,
    }))
    // onParamsChange is a useState setter (stable) and packing.vehicles is
    // read at the moment the run changes, not a reason to re-run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.solve_job_id, run?.inputs])

  if (!project.part) return <NoPart />

  return (
    <div className="workspace">
      <aside className="rail">
        <PackingParams params={params} onChange={onParamsChange}
          vehicles={packing.vehicles} packaging={packing.packaging}
          onAddBox={(b) => packing.setPackaging((p) => [...p, b])}
          title={run ? 'Parameters of this run' : 'Ship it in'} />
      </aside>
      <main className="stage">
        <PackingResults part={project.part} params={params}
          packaging={packing.packaging} vehicles={packing.vehicles}
          projectId={project.id} onSolved={onSolved} onSolveStarted={onSolveStarted}
          tab={resultTab} run={run} />
      </main>
    </div>
  )
}

function RunsTab({ project, onPatch }) {
  const runs = project.runs || []
  const [error, setError] = useState('')
  if (runs.length === 0) return <p className="muted">No runs yet.</p>

  async function markRecommended(e, runId) {
    e.stopPropagation()
    try { setError(''); await onPatch({ recommended_run_id: runId }) }
    catch (err) { setError(err.message) }
  }

  return (
    <>
      {error && <div className="warning">⚠ <span>{error}</span></div>}
      <table className="parts-table">
        <thead>
          <tr>
            <th>Date</th><th>Status</th><th className="num">Clearance</th><th>Confirmed pose</th>
            <th>Assets</th><th>Catalogue</th><th className="num">Custom</th><th className="num">Truck boxes</th><th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.solve_job_id} className="row-click"
              onClick={() => navigate(`#/projects/${project.id}/packaging?run=${r.solve_job_id}`)}>
              <td className="mono">{fmtDate(r.created_at)}</td>
              <td><span className={`badge status-${r.status}`}>{r.status}</span></td>
              <td className="num mono">{r.clearance_mm ?? r.inputs?.clearance_mm ?? '—'}</td>
              <td>{r.inputs?.confirmed_pose_only ? 'yes' : 'no'}</td>
              <td className="mono">{r.inputs?.assets?.length ? r.inputs.assets.join(', ') : 'all'}</td>
              <td className="mono">
                {r.catalogue_count != null ? `${r.catalogue_count} · ${r.catalogue_asset}` : '—'}
              </td>
              <td className="num mono">
                {r.custom_count ?? '—'}
                {r.best_asset === 'custom' && (
                  <span title="custom design wins this run"
                    style={{ color: 'var(--accent2)', marginLeft: 5 }}>●</span>
                )}
              </td>
              <td className="num mono">{r.truck_boxes ?? '—'}</td>
              <td>
                {project.recommended_run_id === r.solve_job_id ? (
                  <span className="badge status-approved">recommended</span>
                ) : r.status === 'done' && (
                  <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }}
                    onClick={(e) => markRecommended(e, r.solve_job_id)}>
                    Mark recommended
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/** The one POST call site (F7: "share one function") — the Proposal tab's
 *  own button and the Overview's both end up here, the Overview's via
 *  `?generate=1` (below), since ProposalTab only exists while its tab is
 *  mounted. `runId` is the run Packaging/Truck had on screen — omitted
 *  when there is none, so the backend falls back to its own default
 *  (recommended run, else newest done) rather than this guessing one. */
async function postProposal(projectId, runId) {
  const qs = runId != null ? `?run_id=${encodeURIComponent(runId)}` : ''
  const r = await fetch(`/api/projects/${projectId}/proposal${qs}`, { method: 'POST' })
  if (!r.ok) throw new Error(await readError(r, 'Could not start proposal'))
  return r.json() // ProposalOut, status "pending"
}

const PROPOSAL_POLL_MS = 2000

function ProposalTab({ project, query, runId }) {
  const [proposals, setProposals] = useState(null) // null = loading
  const [error, setError] = useState('')
  // Separate from `error` above (which covers the proposals-list fetch and
  // renders as the usual yellow warning) — a failed generate (404/409) is
  // the backend naming a specific, expected condition, not a page-level
  // failure, so it prints as one grey line instead.
  const [genError, setGenError] = useState('')
  // The interval reads this, not the `proposals` state directly — keeping
  // the effect keyed on [project.id] means a status update (setProposals)
  // never re-runs the effect, so the interval it set up is never torn down
  // and restarted mid-poll (that restart, with the old pollingIds guard,
  // is what left a rendering row stuck at "Rendering..." forever).
  const proposalsRef = useRef(null)
  useEffect(() => { proposalsRef.current = proposals }, [proposals])
  // Overview's "Generate proposal" navigates here with ?generate=1 — fire
  // the POST once per mount, then strip the flag so a manual refresh of
  // this URL doesn't generate a second proposal.
  const triggeredRef = useRef(false)

  const reload = useCallback(() => (
    fetch(`/api/projects/${project.id}/proposals`)
      .then((r) => { if (!r.ok) throw new Error('Failed to load proposals'); return r.json() })
      .then(setProposals)
      .catch((e) => setError(e.message))
  ), [project.id])

  useEffect(() => {
    setProposals(null); setError('')
    reload()
  }, [project.id, reload])

  async function generate() {
    setGenError('')
    try {
      const row = await postProposal(project.id, runId)
      setProposals((list) => [row, ...(list || [])])
    } catch (e) { setGenError(e.message) }
  }

  // One plain interval per project, not one poll loop per row: every 2s,
  // check whichever rows are currently pending/processing (read off the
  // ref, so this never has to depend on `proposals` itself) and update
  // them in place from the server's own record.
  useEffect(() => {
    const id = setInterval(() => {
      const busy = (proposalsRef.current || []).filter((p) => p.status === 'pending' || p.status === 'processing')
      busy.forEach((p) => {
        fetch(`/api/proposals/${p.id}`)
          .then((r) => { if (!r.ok) throw new Error(`Failed to check proposal (${r.status})`); return r.json() })
          .then((updated) => setProposals((list) => (list || []).map((x) => (x.id === updated.id ? updated : x))))
          .catch((e) => setError(e.message))
      })
    }, PROPOSAL_POLL_MS)
    return () => clearInterval(id)
  }, [project.id])

  useEffect(() => {
    if (query?.generate && proposals !== null && !triggeredRef.current) {
      triggeredRef.current = true
      navigate(`#/projects/${project.id}/proposal`)
      generate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query?.generate, proposals])

  if (error && proposals === null) return <div className="warning">⚠ <span>{error}</span></div>
  if (!proposals) return <p className="muted">Loading…</p>

  const anyBusy = proposals.some((p) => p.status === 'pending' || p.status === 'processing')

  return (
    <div className="card form-card">
      <div className="list-head">
        <p className="muted" style={{ margin: 0 }}>
          {runId != null ? (
            <>Generated from <span className="mono">Run #{String(runId).slice(0, 8)}</span></>
          ) : (
            `Generated from the ${project.recommended_run_id != null ? 'recommended run' : 'newest solve'}`
          )}
        </p>
        <button className="btn-primary" disabled={anyBusy} onClick={generate}>
          Generate proposal
        </button>
      </div>

      {error && <div className="warning">⚠ <span>{error}</span></div>}
      {genError && <p className="muted" style={{ marginTop: 8 }}>{genError}</p>}

      {proposals.length === 0 ? (
        <div className="empty-stage" style={{ padding: '24px 0' }}>
          <div className="es-icon">▦</div>
          <h2>No proposal yet</h2>
          <p>Generate one from the recommended run.</p>
        </div>
      ) : (
        <table className="parts-table" style={{ marginTop: 16 }}>
          <thead>
            <tr><th>Date</th><th>Run</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {proposals.map((p) => (
              <tr key={p.id}>
                <td className="mono">{fmtDate(p.created_at)}</td>
                <td className="mono">{String(p.run_id).slice(0, 8)}</td>
                <td><span className={`badge status-${p.status}`}>{p.status}</span></td>
                <td>
                  {(p.status === 'pending' || p.status === 'processing') && (
                    <span className="solve-pending" style={{ margin: 0 }}>
                      <span className="spinner" /> Rendering…
                    </span>
                  )}
                  {p.status === 'failed' && <span style={{ color: 'var(--red)' }}>{p.error || 'Proposal failed'}</span>}
                  {p.status === 'done' && p.pdf_url && (
                    <>
                      <a className="btn-ghost" href={p.pdf_url} target="_blank" rel="noreferrer">Download PDF</a>
                      <span className="mono muted" style={{ marginLeft: 8 }}>Run #{String(p.run_id).slice(0, 8)}</span>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
