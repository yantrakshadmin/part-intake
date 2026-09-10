/**
 * OrientationViewer — shows the extracted part resting on a ground plane,
 * with clickable candidate orientations and labeled L/B/H.
 *
 * Scale note: cascadio emits GLB in metres (glTF convention). All dims from
 * the backend are mm. We scale the loaded model x1000 so the scene works in
 * mm, matching the dimension labels and the wireframe OBB.
 */
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { MM_PER_M, candidateMatrix, computeTightBounds } from '../lib/glbModel.js'

export default function OrientationViewer({ glbUrl, candidate }) {
  const mountRef = useRef(null)
  const stateRef = useRef({})

  // One-time scene setup
  useEffect(() => {
    const mount = mountRef.current
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xf5f6f8)

    const camera = new THREE.PerspectiveCamera(
      45, mount.clientWidth / mount.clientHeight, 1, 100000
    )
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(mount.clientWidth, mount.clientHeight)
    renderer.setPixelRatio(window.devicePixelRatio)
    mount.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true

    scene.add(new THREE.HemisphereLight(0xffffff, 0x888899, 1.1))
    const dir = new THREE.DirectionalLight(0xffffff, 1.2)
    dir.position.set(1, 2, 1.5)
    scene.add(dir)

    const grid = new THREE.GridHelper(2000, 40, 0xbbbbbb, 0xe2e2e2)
    scene.add(grid)

    const partGroup = new THREE.Group()
    scene.add(partGroup)

    stateRef.current = { scene, camera, renderer, controls, partGroup, mesh: null }

    let raf
    const animate = () => {
      raf = requestAnimationFrame(animate)
      controls.update()
      renderer.render(scene, camera)
    }
    animate()

    const onResize = () => {
      camera.aspect = mount.clientWidth / mount.clientHeight
      camera.updateProjectionMatrix()
      renderer.setSize(mount.clientWidth, mount.clientHeight)
    }
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
  }, [])

  // Load model when URL changes
  useEffect(() => {
    if (!glbUrl) return
    const { partGroup } = stateRef.current
    new GLTFLoader().load(glbUrl, (gltf) => {
      partGroup.clear()
      const model = gltf.scene
      model.scale.setScalar(MM_PER_M) // must match the backend
      model.traverse((o) => {
        if (o.isMesh) {
          o.material = new THREE.MeshStandardMaterial({
            color: 0x8899aa, metalness: 0.3, roughness: 0.55,
          })
        }
      })
      partGroup.add(model)
      stateRef.current.mesh = model
      applyCandidate()
    })
  }, [glbUrl])

  // Apply orientation when candidate changes
  useEffect(() => { applyCandidate() }, [candidate])

  function applyCandidate() {
    const { scene, partGroup, mesh, camera, controls } = stateRef.current
    if (!mesh || !candidate) return

    partGroup.matrixAutoUpdate = false
    partGroup.matrix.copy(candidateMatrix(candidate))
    partGroup.updateMatrixWorld(true)

    // Wireframe OBB so the resting orientation/footprint is visible
    if (stateRef.current.boxHelper) scene.remove(stateRef.current.boxHelper)
    const box = computeTightBounds(partGroup)
    const boxHelper = new THREE.Box3Helper(box, 0x3366cc)
    scene.add(boxHelper)
    stateRef.current.boxHelper = boxHelper

    // Frame the camera on the part
    const [L, , H] = candidate.dims_lbh
    const d = Math.max(L, H) * 2.2
    camera.position.set(d, d * 0.7, d)
    controls.target.set(0, candidate.height / 2, 0)
  }

  return <div ref={mountRef} style={{ width: '100%', height: 300 }} />
}
