import { useEffect, useState } from 'react'
import OrientationViewer from './OrientationViewer.jsx'
import Drawing2D from './Drawing2D.jsx'
import { currentStep } from '../lib/steps.js'
import { readError } from '../lib/api.js'
import { navigate } from '../lib/router.js'

const api = {
  async uploadStep(file) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/parts/upload-step', { method: 'POST', body: fd })
    if (!r.ok) throw new Error(await readError(r, 'Upload failed'))
    return r.json()
  },
  async jobStatus(id) {
    const r = await fetch(`/api/jobs/${id}`)
    return r.json()
  },
  async createProject(payload) {
    const r = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!r.ok) throw new Error(await readError(r, 'Project save failed'))
    return r.json()
  },
  async createPart(payload) {
    const r = await fetch('/api/parts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!r.ok) throw new Error(await readError(r, 'Part save failed'))
    return r.json()
  },
}

// Deep-link support for /feedback.html review page: ?mode=&demo=1
const q = new URLSearchParams(window.location.search)

/**
 * New project — today's intake flow (dropzone/manual → confirm orientation
 * → save), moved out of App.jsx largely as-is (F2), fronted by the project
 * fields the backend needs to create the Project row. Saving creates the
 * project, then attaches the part to it, then hands off to the project's
 * Packaging tab — this page never shows a solve itself.
 */
export default function NewProject() {
  const [mode, setMode] = useState(q.get('mode') || 'stp') // 'stp' | 'manual'
  const [job, setJob] = useState(null)
  const [result, setResult] = useState(null)
  const [selected, setSelected] = useState(0)
  const [form, setForm] = useState(q.has('demo')
    ? { customer: 'Demo Customer', part_number: 'DEMO-001', part_name: 'Demo part',
        length_mm: '400', breadth_mm: '300', height_mm: '150', weight_kg: '12' }
    : { customer: '', part_number: '', part_name: '',
        length_mm: '', breadth_mm: '', height_mm: '', weight_kg: '' })
  // Project-level fields that aren't part dims: annual volume up top,
  // the customer's current pack collapsed since most intakes don't have it
  // to hand yet.
  const [pack, setPack] = useState({ annual_volume: '', customer_count: '', customer_box: '' })
  const [status, setStatus] = useState('')
  const [dragging, setDragging] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedPart, setSavedPart] = useState(null)
  // Set once POST /api/projects succeeds. A later failure (POST /api/parts)
  // must not create a second project on retry — Save & calculate reuses
  // this id instead of calling createProject again. Cleared when customer
  // or part_number change, since those are what identifies the project.
  const [createdProjectId, setCreatedProjectId] = useState(null)
  // The confirm-orientation block is a question; once a save answers it,
  // collapse it to a one-line summary — "Change" re-expands in one click.
  const [confirmOpen, setConfirmOpen] = useState(true)

  // A saved part's fit goes stale the moment the form (or the uploaded
  // file) changes again — never keep showing a solve for numbers that no
  // longer match what's on screen.
  useEffect(() => { setSavedPart(null) }, [form])

  useEffect(() => { setCreatedProjectId(null) }, [form.customer, form.part_number])

  const busy = status === 'Uploading…' || status === 'Extracting dimensions…'
  const statusClass =
    status.startsWith('Saved') ? 'status ok'
    : /failed|Failed|error|Error/.test(status) ? 'status err'
    : 'status'

  // Poll job until done
  useEffect(() => {
    if (!job || result) return
    const t = setInterval(async () => {
      const s = await api.jobStatus(job.job_id)
      if (s.status === 'done') {
        clearInterval(t)
        setResult(s.result)
        setConfirmOpen(true)
        const [L, B, H] = s.result.candidates[0].dims_lbh
        setForm((f) => ({ ...f, length_mm: L, breadth_mm: B, height_mm: H }))
        setStatus('')
      } else if (s.status === 'failed') {
        clearInterval(t)
        setStatus(`Extraction failed: ${s.error}`)
      }
    }, 1500)
    return () => clearInterval(t)
  }, [job, result])

  async function handleFile(file) {
    if (!file) return
    setResult(null); setSelected(0); setStatus('Uploading…'); setSavedPart(null)
    try {
      const j = await api.uploadStep(file)
      setJob(j)
      setStatus('Extracting dimensions…')
    } catch (err) { setStatus(err.message) }
  }

  function onDrop(e) {
    e.preventDefault()
    setDragging(false)
    handleFile(e.dataTransfer.files[0])
  }

  function pickCandidate(i) {
    setSelected(i)
    const [L, B, H] = result.candidates[i].dims_lbh
    setForm((f) => ({ ...f, length_mm: L, breadth_mm: B, height_mm: H }))
  }

  async function save() {
    if (saving) return
    if (!form.customer.trim()) { setStatus('Customer is required.'); return }
    if (!form.part_number.trim() || !form.part_name.trim()) {
      setStatus('Part number and part name are required.')
      return
    }
    if (!(+form.weight_kg > 0)) {
      setStatus('Weight (kg) is required and must be > 0.')
      return
    }
    if (!(+form.length_mm > 0 && +form.breadth_mm > 0 && +form.height_mm > 0)) {
      setStatus('All three dimensions (mm) are required and must be > 0.')
      return
    }
    setSaving(true)
    try {
      // Skip creating a second project if the first POST already succeeded
      // and it was only the part save that failed — a retry reuses it.
      let projectId = createdProjectId
      if (projectId == null) {
        const project = await api.createProject({
          customer: form.customer.trim(),
          part_number: form.part_number.trim(),
          part_name: form.part_name.trim(),
          annual_volume: pack.annual_volume !== '' ? +pack.annual_volume : null,
          customer_count: pack.customer_count !== '' ? +pack.customer_count : null,
          customer_box: pack.customer_box.trim() || null,
        })
        projectId = project.id
        setCreatedProjectId(projectId)
      }
      // Built explicitly, not spread from `form` — form also carries
      // customer, which PartIn does not declare (and would silently drop).
      const partPayload = {
        part_number: form.part_number.trim(),
        part_name: form.part_name.trim(),
        length_mm: +form.length_mm, breadth_mm: +form.breadth_mm,
        height_mm: +form.height_mm, weight_kg: +form.weight_kg,
        source: mode === 'stp' && result ? 'stp' : 'manual',
        job_id: mode === 'stp' && job ? job.job_id : null,
        confirmed_orientation:
          mode === 'stp' && result
            ? result.candidates[selected].rotation_matrix : null,
        project_id: projectId,
      }
      try {
        const p = await api.createPart(partPayload)
        setStatus(`Saved part #${p.id} (${p.part_number})`)
        setSavedPart(p)
        if (mode === 'stp' && result) setConfirmOpen(false)
        navigate(`#/projects/${projectId}/packaging`)
      } catch (err) {
        setStatus(`Project #${projectId} created — part save failed: ${err.message}`)
      }
    } catch (err) { setStatus(err.message) }
    finally { setSaving(false) }
  }

  const fields = [
    ['customer', 'Customer', 'text'],
    ['part_number', 'Part number', 'text'],
    ['part_name', 'Part name', 'text'],
    ['weight_kg', 'Weight (kg)', 'number'],
    ['length_mm', 'Length (mm)', 'number'],
    ['breadth_mm', 'Breadth (mm)', 'number'],
    ['height_mm', 'Height (mm)', 'number'],
  ]

  // Sequence, not a pile: intake -> confirm orientation (STEP only) ->
  // results. "results" here just means the part is saved and ready — the
  // packaging stage itself lives on the project page.
  const step = currentStep({ mode, result, savedPart })

  return (
    <div className="workspace">
      <aside className="rail">
        <div className="segmented sub-mode">
          <button className={mode === 'stp' ? 'active' : ''}
            onClick={() => setMode('stp')}>STEP file</button>
          <button className={mode === 'manual' ? 'active' : ''}
            onClick={() => setMode('manual')}>Manual entry</button>
        </div>

        {mode === 'stp' && (
          <div
            className={`dropzone compact${dragging ? ' dragging' : ''}${busy ? ' busy' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            {busy ? (
              <>
                <div className="spinner" />
                <div className="dz-title">{status}</div>
                <div className="dz-hint">Large assemblies can take up to ~30 s</div>
              </>
            ) : (
              <>
                <div className="dz-icon">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                    strokeLinejoin="round">
                    <path d="M12 16V4m0 0L7 9m5-5 5 5" />
                    <path d="M4 17v2a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2" />
                  </svg>
                </div>
                <div className="dz-title">
                  Drop a STEP file, or <em>browse</em>
                </div>
                <div className="dz-hint">
                  .stp / .step / .igs / .iges — dimensions extracted automatically
                </div>
                <input type="file" accept=".stp,.step,.igs,.iges"
                  onChange={(e) => handleFile(e.target.files[0])} />
              </>
            )}
          </div>
        )}

        <div className="card form-card">
          <h2>Part profile</h2>
          <div className="form-grid rail-grid">
            {fields.map(([key, label, type]) => (
              <label key={key} className="field">
                <span>{label}</span>
                <input type={type} value={form[key]}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
              </label>
            ))}
          </div>

          <details className="disclosure" style={{ marginTop: 16 }}>
            <summary>Customer's current pack (optional)</summary>
            <p className="muted" style={{ fontSize: 12, margin: '6px 0 10px' }}>
              Only used to show the gain against what the customer ships today —
              left blank, that comparison just doesn't render.
            </p>
            <div className="form-grid rail-grid">
              <label className="field">
                <span>Annual volume</span>
                <input type="number" value={pack.annual_volume}
                  onChange={(e) => setPack({ ...pack, annual_volume: e.target.value })} />
              </label>
              <label className="field">
                <span>Parts per box today</span>
                <input type="number" value={pack.customer_count}
                  onChange={(e) => setPack({ ...pack, customer_count: e.target.value })} />
              </label>
              <label className="field">
                <span>Current box</span>
                <input type="text" value={pack.customer_box}
                  onChange={(e) => setPack({ ...pack, customer_box: e.target.value })} />
              </label>
            </div>
          </details>

          <div className="form-footer">
            <button className="btn-primary" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save & calculate'}
            </button>
            {saving && <div className="spinner" />}
          </div>
          {status && !busy && <p className={statusClass}>{status}</p>}
        </div>
      </aside>

      <main className="stage">
        {step === 'intake' && (
          <div className="card empty-stage">
            <div className="es-icon">▦</div>
            <h2>Packaging fit appears here</h2>
            <p>Fill in the part profile (or drop a STEP file), confirm
              how it rests if extracted, then Save &amp; calculate —
              boxes, insert trays and the truck loading plan follow.</p>
          </div>
        )}

        {mode === 'stp' && result && (
          <div className="card form-card">
            <div className="confirm-head">
              <h2>Confirm resting orientation</h2>
              {savedPart && (
                <button className="btn-ghost" onClick={() => setConfirmOpen((o) => !o)}>
                  {confirmOpen ? 'Collapse ▴' : 'Change ▾'}
                </button>
              )}
            </div>

            {confirmOpen || !savedPart ? (
              <>
                <div className="views-row">
                  <div className="view-card">
                    <div className="view-title">Isometric</div>
                    <div style={{ width: 300 }}>
                      <OrientationViewer
                        glbUrl={result.glb_url}
                        candidate={result.candidates[selected]}
                      />
                    </div>
                  </div>
                  <div className="view-card">
                    <div className="view-title">Front view</div>
                    <Drawing2D glbUrl={result.glb_url}
                      candidate={result.candidates[selected]} view="front" />
                  </div>
                  <div className="view-card">
                    <div className="view-title">Top view</div>
                    <Drawing2D glbUrl={result.glb_url}
                      candidate={result.candidates[selected]} view="top" />
                  </div>
                </div>

                <div className="candidates">
                  {result.candidates.map((c, i) => (
                    <button key={i}
                      className={`candidate${i === selected ? ' selected' : ''}`}
                      onClick={() => pickCandidate(i)}>
                      <div className="c-label">{c.label}</div>
                      <div className="c-dims">{c.dims_lbh.join(' × ')} mm</div>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <div className="confirm-summary">
                <span className="orient-chip">{result.candidates[selected].label}</span>
                <span className="mono">{result.candidates[selected].dims_lbh.join(' × ')} mm</span>
              </div>
            )}

            {/* D8: extraction warnings render verbatim regardless of the
                collapse state above — never hidden behind a disclosure. */}
            {result.warnings.map((w, i) => (
              <div key={i} className="warning">⚠ <span>{w}</span></div>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
