import { useEffect, useState } from 'react'
import OrientationViewer from './components/OrientationViewer.jsx'
import Drawing2D from './components/Drawing2D.jsx'

const api = {
  async uploadStep(file) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/parts/upload-step', { method: 'POST', body: fd })
    if (!r.ok) throw new Error((await r.json()).detail || 'Upload failed')
    return r.json()
  },
  async jobStatus(id) {
    const r = await fetch(`/api/jobs/${id}`)
    return r.json()
  },
  async createPart(payload) {
    const r = await fetch('/api/parts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!r.ok) throw new Error((await r.json()).detail || 'Save failed')
    return r.json()
  },
}

export default function App() {
  const [mode, setMode] = useState('stp') // 'stp' | 'manual'
  const [job, setJob] = useState(null)
  const [result, setResult] = useState(null)
  const [selected, setSelected] = useState(0)
  const [form, setForm] = useState({
    part_number: '', part_name: '',
    length_mm: '', breadth_mm: '', height_mm: '', weight_kg: '',
  })
  const [status, setStatus] = useState('')

  // Poll job until done
  useEffect(() => {
    if (!job || result) return
    const t = setInterval(async () => {
      const s = await api.jobStatus(job.job_id)
      if (s.status === 'done') {
        clearInterval(t)
        setResult(s.result)
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

  async function onFile(e) {
    const file = e.target.files[0]
    if (!file) return
    setResult(null); setSelected(0); setStatus('Uploading…')
    try {
      const j = await api.uploadStep(file)
      setJob(j)
      setStatus('Extracting dimensions…')
    } catch (err) { setStatus(err.message) }
  }

  function pickCandidate(i) {
    setSelected(i)
    const [L, B, H] = result.candidates[i].dims_lbh
    setForm((f) => ({ ...f, length_mm: L, breadth_mm: B, height_mm: H }))
  }

  async function save() {
    try {
      const payload = {
        ...form,
        length_mm: +form.length_mm, breadth_mm: +form.breadth_mm,
        height_mm: +form.height_mm, weight_kg: +form.weight_kg,
        source: mode === 'stp' && result ? 'stp' : 'manual',
        job_id: mode === 'stp' && job ? job.job_id : null,
        confirmed_orientation:
          mode === 'stp' && result
            ? result.candidates[selected].rotation_matrix : null,
      }
      const p = await api.createPart(payload)
      setStatus(`Saved part #${p.id} (${p.part_number})`)
    } catch (err) { setStatus(err.message) }
  }

  return (
    <div style={{ maxWidth: 980, margin: '0 auto', padding: 24, fontFamily: 'system-ui' }}>
      <h1>Part Intake</h1>

      <div style={{ marginBottom: 16 }}>
        <button onClick={() => setMode('stp')} disabled={mode === 'stp'}>STEP file</button>{' '}
        <button onClick={() => setMode('manual')} disabled={mode === 'manual'}>Manual entry</button>
      </div>

      {mode === 'stp' && (
        <div>
          <input type="file" accept=".stp,.step" onChange={onFile} />
          {result && (
            <>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8 }}>
                <div style={{ width: 300 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Isometric</div>
                  <OrientationViewer
                    glbUrl={result.glb_url}
                    candidate={result.candidates[selected]}
                  />
                </div>
                <Drawing2D glbUrl={result.glb_url} candidate={result.candidates[selected]}
                  view="front" title="Front view" />
                <Drawing2D glbUrl={result.glb_url} candidate={result.candidates[selected]}
                  view="top" title="Top view" />
              </div>
              <div style={{ display: 'flex', gap: 8, margin: '12px 0', flexWrap: 'wrap' }}>
                {result.candidates.map((c, i) => (
                  <button key={i} onClick={() => pickCandidate(i)}
                    style={{ fontWeight: i === selected ? 700 : 400 }}>
                    {c.label}<br />
                    {c.dims_lbh.join(' × ')} mm
                  </button>
                ))}
              </div>
              {result.warnings.map((w, i) => (
                <p key={i} style={{ color: '#a15c00' }}>⚠ {w}</p>
              ))}
            </>
          )}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginTop: 16 }}>
        <label>Part number
          <input value={form.part_number}
            onChange={(e) => setForm({ ...form, part_number: e.target.value })} />
        </label>
        <label>Part name
          <input value={form.part_name}
            onChange={(e) => setForm({ ...form, part_name: e.target.value })} />
        </label>
        <label>Weight (kg)
          <input type="number" value={form.weight_kg}
            onChange={(e) => setForm({ ...form, weight_kg: e.target.value })} />
        </label>
        <label>Length (mm)
          <input type="number" value={form.length_mm}
            onChange={(e) => setForm({ ...form, length_mm: e.target.value })} />
        </label>
        <label>Breadth (mm)
          <input type="number" value={form.breadth_mm}
            onChange={(e) => setForm({ ...form, breadth_mm: e.target.value })} />
        </label>
        <label>Height (mm)
          <input type="number" value={form.height_mm}
            onChange={(e) => setForm({ ...form, height_mm: e.target.value })} />
        </label>
      </div>

      <button onClick={save} style={{ marginTop: 16 }}>Save part profile</button>
      {status && <p>{status}</p>}
    </div>
  )
}
