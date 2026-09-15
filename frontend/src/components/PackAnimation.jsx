/**
 * PackAnimation — the packing order as a live, seekable Three.js animation
 * of the real part in its resting pose, replacing (well: sitting alongside)
 * the fixed GIF. Ported from the standalone reference prototype
 * (scratchpad/anim/build_template.html) — same timeline/easing/camera
 * approach, driven generically off `sequence` instead of hard-coded TRW
 * numbers.
 *
 * Everything placed here is the backend's own number (hard rule 9): box
 * size, cuboid origins/sizes/colours, part origins, captions. This
 * component only decides WHEN to reveal them, never re-derives a count or
 * a position.
 */
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { MM_PER_M, computeTightBounds } from '../lib/glbModel.js'

const T_BOX = 0.6      // box wireframe fade-in
const T_DUN = 0.5      // one dunnage step (its cuboids drop together)
const T_PART = 0.6     // one part's drop-in
const STAG = 0.12      // stagger between parts within the same step
const HOLD = 3.0       // hold on the finished box before looping
const DROP_DUN = 300   // mm a dunnage cuboid falls from
const DROP_PART = 420  // mm a part falls from

const easeOut = (p) => 1 - Math.pow(1 - p, 3)
const clamp01 = (v) => Math.max(0, Math.min(1, v))

/** Deterministic, time-driven timeline off the backend's own step order —
 *  nothing here decides step order, captions or counts, only start/dur. */
function buildTimeline(steps) {
  const sorted = [...(steps || [])].sort((a, b) => a.i - b.i)
  const events = [] // {step, start, dur, part?}
  let t = T_BOX
  for (const step of sorted) {
    if (step.kind === 'parts') {
      const parts = step.parts || []
      parts.forEach((part, i) => {
        events.push({ step, start: t + STAG * i, dur: T_PART, part })
      })
      t += STAG * Math.max(0, parts.length - 1) + T_PART
    } else {
      events.push({ step, start: t, dur: T_DUN })
      t += T_DUN
    }
  }
  return { events, tEnd: t, total: t + HOLD }
}

function currentStep(events, t) {
  let cur = null
  for (const ev of events) { if (ev.start <= t) cur = ev.step }
  return cur
}

export default function PackAnimation({ sequence, glbUrl, height = 420, onStageClick }) {
  const mountRef = useRef(null)
  const [caption, setCaption] = useState({ title: 'Empty box', text: '', meta: '' })
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(1)
  const [seekPct, setSeekPct] = useState(0)
  const [timeLabel, setTimeLabel] = useState('0.0 s')
  // playing/speed/manual-seek live in a ref too, so the rAF loop (which does
  // not want to re-run on every state change) always reads the latest value.
  const ctrlRef = useRef({ playing: true, speed: 1, seekTo: null })
  useEffect(() => { ctrlRef.current.playing = playing }, [playing])
  useEffect(() => { ctrlRef.current.speed = speed }, [speed])

  useEffect(() => {
    if (!sequence) return undefined
    const mount = mountRef.current
    const W = mount.clientWidth || 480
    let disposed = false

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(W, height)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const world = new THREE.Group()
    world.rotation.x = -Math.PI / 2 // Z-up model space -> three's Y-up world
    scene.add(world)
    const camera = new THREE.PerspectiveCamera(28, W / height, 10, 20000)
    scene.add(new THREE.HemisphereLight(0xffffff, 0xcbd5e1, 0.9))
    const sun = new THREE.DirectionalLight(0xffffff, 0.7)
    sun.position.set(1500, 2500, 1800)
    scene.add(sun)

    const [Lx, By, Hz] = sequence.inner
    // The 8 box corners in WORLD space, for the camera-fit projection in
    // render() below. `world.rotation.x = -PI/2` (Z-up model -> Y-up world)
    // maps local (x, y, z) -> world (x, z, -y) -- same mapping the comment
    // by the camera block below already relies on for the -By/2 centring.
    const boxCorners = []
    for (const bx of [0, Lx]) for (const by of [0, By]) for (const bz of [0, Hz]) {
      boxCorners.push(new THREE.Vector3(bx, bz, -by))
    }
    const projTmp = new THREE.Vector3()
    let rGuess = Math.hypot(Lx, By, Hz) // seed for the first frame only
    // box corner is (0,0,0) per the sequence contract; BoxGeometry is
    // centred, so shift every box mesh by half its size to align corners.
    const boxEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(Lx, By, Hz)),
      new THREE.LineBasicMaterial({ color: 0x64748b, transparent: true, opacity: 0 }))
    boxEdges.position.set(Lx / 2, By / 2, Hz / 2)
    world.add(boxEdges)
    const wallMat = new THREE.MeshStandardMaterial({
      color: 0x93c5fd, transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false,
    })
    const walls = new THREE.Mesh(new THREE.BoxGeometry(Lx, By, Hz), wallMat)
    walls.position.copy(boxEdges.position)
    world.add(walls)

    const { events, tEnd, total } = buildTimeline(sequence.steps)

    // dunnage: one mesh per cuboid, across every dunnage step, parked
    // invisible above its rest position until its own step's window.
    const dunnage = [] // {ev, mesh, restZ}
    for (const ev of events) {
      if (ev.part) continue
      for (const c of ev.step.cuboids || []) {
        const geo = new THREE.BoxGeometry(...c.size)
        const mat = new THREE.MeshStandardMaterial({
          color: c.colour ?? '#60a5fa',
          transparent: (c.alpha ?? 1) < 1,
          opacity: c.alpha ?? 1,
          roughness: 0.6,
        })
        const mesh = new THREE.Mesh(geo, mat)
        const restZ = c.origin[2] + c.size[2] / 2
        mesh.position.set(c.origin[0] + c.size[0] / 2, c.origin[1] + c.size[1] / 2, restZ)
        mesh.visible = false
        world.add(mesh)
        dunnage.push({ ev, mesh, restZ })
      }
    }

    // parts: one InstancedMesh per GLB primitive, one instance per part
    // origin across every "parts" step (in timeline order).
    //
    // Deliberately NOT glbModel's `loadOrientedModel`/`candidateMatrix`: those
    // bake the Z-up->Y-up scene flip into the model's own matrix, for
    // callers that add the model straight to a plain (Y-up) scene. Here the
    // whole scene — box, dunnage, parts — is built once in the sequence's
    // own Z-up mm frame and flipped ONE time by `world.rotation.x` above
    // (same structure as the reference prototype), because a part's
    // `origin` is a Z-up point and the AABB min it's translated against
    // must be computed in that SAME frame — composing a second, per-model
    // Y-up flip on top of `world`'s would double-rotate it. `MM_PER_M` and
    // `computeTightBounds` are still the shared definitions (hard rule 1);
    // only the Y-up conversion itself is intentionally left out here.
    const partEvents = events.filter((ev) => ev.part)
    const instanced = []
    const tmp = new THREE.Matrix4()
    const partMat = new THREE.MeshStandardMaterial({ color: 0x8b8f5c, roughness: 0.55, metalness: 0.05 })
    let aabbMin = null
    let dirty = true
    if (glbUrl && partEvents.length && sequence.pose_matrix) {
      new GLTFLoader().load(glbUrl, (gltf) => {
        if (disposed) return
        dirty = true // parts arrived after the last paused frame
        const model = gltf.scene
        model.scale.set(MM_PER_M, MM_PER_M, MM_PER_M) // metres -> mm, same constant as glbModel.js
        const poseGroup = new THREE.Group()
        poseGroup.matrixAutoUpdate = false
        poseGroup.matrix.set(...sequence.pose_matrix.flat()) // Z-up mesh-space -> Z-up resting pose only
        poseGroup.add(model)
        poseGroup.updateMatrixWorld(true)
        const box = computeTightBounds(poseGroup)
        aabbMin = box.min.clone()
        poseGroup.traverse((o) => {
          if (!o.isMesh) return
          const geo = o.geometry.clone()
          geo.applyMatrix4(o.matrixWorld) // bake scale + resting pose, still Z-up
          const im = new THREE.InstancedMesh(geo, partMat, partEvents.length)
          im.frustumCulled = false
          world.add(im) // child of the single Z-up->Y-up flip, like the box/dunnage
          instanced.push(im)
        })
      }, undefined, () => {})
    }

    function render(t) {
      const bp = clamp01(t / T_BOX)
      boxEdges.material.opacity = 0.9 * bp
      wallMat.opacity = 0.06 * bp

      for (const { ev, mesh, restZ } of dunnage) {
        const q = clamp01((t - ev.start) / ev.dur)
        mesh.visible = q > 0
        mesh.position.z = restZ + (1 - easeOut(q)) * DROP_DUN
      }

      if (aabbMin) {
        for (const im of instanced) {
          partEvents.forEach((ev, n) => {
            const q = clamp01((t - ev.start) / ev.dur)
            if (q <= 0) {
              tmp.makeScale(0, 0, 0)
            } else {
              const o = ev.part.origin
              const e = easeOut(q)
              tmp.makeTranslation(
                o[0] - aabbMin.x,
                o[1] - aabbMin.y,
                o[2] - aabbMin.z + (1 - e) * DROP_PART,
              )
            }
            im.setMatrixAt(n, tmp)
          })
          im.instanceMatrix.needsUpdate = true
        }
      }

      // camera: gentle orbit, fixed iso-ish view, rises as the stack builds.
      // `world` is rotated -90deg about X, so the box's Y (By) lands on world -Z:
      // its centre is at -By/2, not +By/2 (tester caught the box cropped off-frame).
      const MARGIN = 0.92
      const a = -0.62 + 0.1 * Math.sin((t / total) * Math.PI)
      const h = Hz * 1.4 + Hz * 0.5 * clamp01(t / tEnd)
      const setCam = (r) => {
        camera.position.set(r * Math.sin(a) + Lx / 2, h, r * Math.cos(a) - By / 2)
        camera.lookAt(Lx / 2, Hz * 0.48, -By / 2)
        camera.updateMatrixWorld(true) // project() below needs a fresh matrixWorldInverse
      }
      // A bounding-sphere fit (radius = diag/2, fit to the FOV) covers the
      // worst case over every orientation, but at this iso-ish angle the
      // box's actual silhouette is far smaller than its circumscribed
      // sphere -- that made the hero box shrink to under half the stage
      // height. Fit the real projected footprint instead: project the 8
      // box corners (already in world space, see `boxCorners` above) at a
      // trial radius, read off m = max(|ndc.x|, |ndc.y|) (NDC already
      // bakes in aspect via the projection matrix, so this is exact on
      // both axes at once), and rescale r towards m / MARGIN. The camera
      // height `h` stays fixed while r moves, so the response is NOT linear
      // in r: a plain `r *= m / MARGIN` oscillates (1.69 -> 0.61 -> 1.19 ...)
      // and left the first frames cropped on 11 of 17 catalogue boxes.
      // Damped (sqrt) steps converge in <= 7 passes on every catalogue box
      // at 900x320, 730x1000 and 1400x700 (scratch sim, code review F8);
      // after the first frame `rGuess` is already converged so this is
      // normally one pass.
      let r = rGuess
      for (let pass = 0; pass < 8; pass++) {
        setCam(r)
        let m = 1e-6
        for (const c of boxCorners) {
          projTmp.copy(c).project(camera)
          m = Math.max(m, Math.abs(projTmp.x), Math.abs(projTmp.y))
        }
        const k = m / MARGIN
        if (Math.abs(k - 1) < 0.01) break
        r *= Math.sqrt(k)
      }
      setCam(r)
      rGuess = r
      renderer.render(scene, camera)
    }

    let last = performance.now()
    let tCur = 0
    let raf
    function tick(now) {
      raf = requestAnimationFrame(tick)
      const dt = (now - last) / 1000
      last = now
      if (ctrlRef.current.seekTo != null) {
        tCur = ctrlRef.current.seekTo
        ctrlRef.current.seekTo = null
      } else if (ctrlRef.current.playing) {
        tCur = (tCur + dt * ctrlRef.current.speed) % total
      } else if (!dirty) {
        return // paused: render only when something changed (GLB arrived, resize)
      }
      if (!mount.offsetParent) { dirty = true; return } // hidden (Truck tab): keep time, skip GPU work
      dirty = false
      render(tCur)
      setSeekPct(clamp01(tCur / total) * 1000)
      setTimeLabel(tCur.toFixed(1) + ' s')
      const step = currentStep(events, tCur)
      setCaption((prev) => {
        if (prev._step === step) return prev
        return step
          ? { title: step.title, text: step.text, meta: step.meta, _step: step }
          : { title: 'Empty box', text: '', meta: '', _step: null }
      })
    }
    raf = requestAnimationFrame(tick)

    function resize() {
      dirty = true
      const w = mount.clientWidth || W
      renderer.setSize(w, height, false)
      camera.aspect = w / height
      camera.updateProjectionMatrix()
    }
    window.addEventListener('resize', resize)
    resize()

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose()
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose())
      })
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
    // sequence/glbUrl identity change rebuilds the whole scene — cheap
    // enough (one run per solve/view-switch) and much simpler than patching
    // an existing scene in place.
  }, [sequence, glbUrl, height])

  if (!sequence) return null

  return (
    <div className="pack-anim">
      <div className={`pack-anim-stage${onStageClick ? ' zoomable' : ''}`} ref={mountRef}
        onClick={onStageClick} style={{ position: 'relative', height }}>
        <div className="pack-anim-caption">
          <div className="l1">{caption.title}</div>
          <div className="l2">{caption.text}</div>
          <div className="l3 mono pack-anim-meta">{caption.meta}</div>
        </div>
      </div>
      <div className="pack-anim-controls">
        <button type="button" className="btn-ghost" onClick={() => setPlaying((p) => !p)}>
          {playing ? 'Pause' : 'Play'}
        </button>
        <input type="range" min={0} max={1000} value={seekPct}
          onChange={(e) => {
            const pct = Number(e.target.value)
            setSeekPct(pct)
            setPlaying(false)
            // total is recomputed from `sequence` on the fly since the rAF
            // loop, not this handler, owns tCur — route the seek through
            // ctrlRef so the very next tick jumps there.
            ctrlRef.current.seekTo = (pct / 1000) * buildTimeline(sequence.steps).total
          }} />
        <span className="t mono">{timeLabel}</span>
        <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
        </select>
      </div>
    </div>
  )
}
