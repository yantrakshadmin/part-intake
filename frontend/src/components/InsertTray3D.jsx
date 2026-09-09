/**
 * InsertTray3D — optional rotatable 3D view of an insert tray. Consumes the
 * same trayGeometry() object as the static InsertIso drawing, so the two
 * views can never disagree: a foam floor sheet + a wall lattice extruded
 * from the slab outline with the pocket rectangles as holes (which handles
 * rotated end/side strips and mixed pocket sizes, not just uniform grids).
 */
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

export default function InsertTray3D({ geom }) {
  const mountRef = useRef(null)

  useEffect(() => {
    const mount = mountRef.current
    const W = mount.clientWidth || 420
    const H = 280

    // preserveDrawingBuffer so the download button can capture the canvas
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(W, H)
    renderer.setClearColor(0xf5f6fc, 1)
    mount.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.add(new THREE.AmbientLight(0xffffff, 0.9))
    const dl = new THREE.DirectionalLight(0xffffff, 1.2)
    dl.position.set(3, 5, 2)
    scene.add(dl)
    const fl = new THREE.DirectionalLight(0xffffff, 0.4)
    fl.position.set(-2, 2, -2)
    scene.add(fl)

    const cam = new THREE.PerspectiveCamera(42, W / H, 0.01, 1e6)

    const { L, B, wallH, floorH, pockets } = geom
    const s = 1 / Math.max(L, B)

    const foamMat = new THREE.MeshPhongMaterial({
      color: 0x5b64de, emissive: 0x0a0d33, shininess: 35, specular: 0x333a99,
      side: THREE.DoubleSide,
    })
    const edgeMat = new THREE.LineBasicMaterial({
      color: 0x1e2455, transparent: true, opacity: 0.6,
    })

    // floor sheet
    const floorGeo = new THREE.BoxGeometry(L * s, floorH * s, B * s)
    const floor = new THREE.Mesh(floorGeo, foamMat)
    floor.position.set((L / 2) * s, (floorH / 2) * s, (B / 2) * s)
    scene.add(floor)
    const floorEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(floorGeo), edgeMat)
    floorEdges.position.copy(floor.position)
    scene.add(floorEdges)

    // wall lattice: slab outline minus pocket holes, extruded upward
    const shape = new THREE.Shape()
    shape.moveTo(0, 0); shape.lineTo(L, 0); shape.lineTo(L, B)
    shape.lineTo(0, B); shape.closePath()
    for (const p of pockets) {
      const hole = new THREE.Path()
      hole.moveTo(p.x, p.y); hole.lineTo(p.x + p.w, p.y)
      hole.lineTo(p.x + p.w, p.y + p.h); hole.lineTo(p.x, p.y + p.h)
      hole.closePath()
      shape.holes.push(hole)
    }
    const wallGeo = new THREE.ExtrudeGeometry(shape, {
      depth: wallH, bevelEnabled: false,
    })
    wallGeo.scale(s, s, s)
    const walls = new THREE.Mesh(wallGeo, foamMat)
    // shape XY plane → horizontal, extrusion → +Y (up)
    walls.rotation.x = -Math.PI / 2
    walls.position.set(0, floorH * s, B * s)
    scene.add(walls)
    const wallEdges = new THREE.LineSegments(
      new THREE.EdgesGeometry(wallGeo, 30), edgeMat)
    wallEdges.rotation.copy(walls.rotation)
    wallEdges.position.copy(walls.position)
    scene.add(wallEdges)

    const totalH = (floorH + wallH) * s
    const ctr = new THREE.Vector3((L * s) / 2, totalH / 2, (B * s) / 2)
    cam.position.set(ctr.x + 1.1, ctr.y + 1.3, ctr.z + 1.6)

    const controls = new OrbitControls(cam, renderer.domElement)
    controls.target.copy(ctr)
    controls.enableDamping = true

    let raf
    const animate = () => {
      raf = requestAnimationFrame(animate)
      controls.update()
      renderer.render(scene, cam)
    }
    animate()

    return () => {
      cancelAnimationFrame(raf)
      controls.dispose()
      wallGeo.dispose()
      floorGeo.dispose()
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
  }, [geom])

  return <div ref={mountRef} className="tray-wrap" />
}
