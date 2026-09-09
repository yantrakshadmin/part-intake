/**
 * Drawing2D — orthographic Front or Top view of the part with overall
 * width/height dimension lines, drawn like a 2D engineering drawing.
 *
 * 'front' view: horizontal = X extent, vertical = Y (height) extent.
 * 'top'   view: horizontal = X extent, vertical = Z (depth) extent.
 * Camera is framed in its own local axes, so the up/right convention of
 * each view doesn't affect the dimension math below.
 */
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { loadOrientedModel } from '../lib/glbModel.js'

const SIZE = 300
const SVG_NS = 'http://www.w3.org/2000/svg'

function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag)
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v))
  return e
}

export default function Drawing2D({ glbUrl, candidate, view, title }) {
  const mountRef = useRef(null)
  const svgRef = useRef(null)
  const stateRef = useRef({})

  // One-time scene setup
  useEffect(() => {
    const mount = mountRef.current
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xffffff)
    scene.add(new THREE.HemisphereLight(0xffffff, 0x888899, 1.2))
    const dir = new THREE.DirectionalLight(0xffffff, 0.9)
    dir.position.set(1, 2, 1.5)
    scene.add(dir)

    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1e7)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setSize(SIZE, SIZE)
    renderer.setPixelRatio(window.devicePixelRatio)
    mount.appendChild(renderer.domElement)

    stateRef.current = { scene, camera, renderer, model: null }
    renderer.render(scene, camera)

    return () => {
      renderer.dispose()
      mount.removeChild(renderer.domElement)
    }
  }, [])

  // Load model + frame camera + draw dimensions whenever inputs change
  useEffect(() => {
    if (!glbUrl || !candidate) return
    let cancelled = false
    loadOrientedModel(glbUrl, candidate).then(({ model, box }) => {
      if (cancelled) return
      const { scene, camera, renderer } = stateRef.current
      if (stateRef.current.model) scene.remove(stateRef.current.model)
      scene.add(model)
      stateRef.current.model = model

      const center = box.getCenter(new THREE.Vector3())
      const size = box.getSize(new THREE.Vector3())

      let hSize, vSize, dist
      if (view === 'top') {
        hSize = size.x; vSize = size.z
        dist = size.y * 2 + 1000
        camera.position.set(center.x, center.y + dist, center.z)
        camera.up.set(0, 0, -1)
      } else {
        hSize = size.x; vSize = size.y
        dist = size.z * 2 + 1000
        camera.position.set(center.x, center.y, center.z + dist)
        camera.up.set(0, 1, 0)
      }
      camera.lookAt(center)

      // Square frustum (matches square viewport); part sits lower-left,
      // leaving room top/right for dimension lines (like a CAD drawing).
      const maxExtent = Math.max(hSize, vSize)
      const pad = maxExtent * 0.18
      const frustum = maxExtent + pad * 3
      camera.left = -hSize / 2 - pad
      camera.right = camera.left + frustum
      camera.bottom = -vSize / 2 - pad
      camera.top = camera.bottom + frustum
      camera.near = 0.1
      camera.far = dist * 2 + maxExtent * 4
      camera.updateProjectionMatrix()

      renderer.render(scene, camera)
      drawDimensions(svgRef.current, camera, hSize, vSize)
    })
    return () => { cancelled = true }
  }, [glbUrl, candidate, view])

  return (
    <div style={{ position: 'relative', width: SIZE, height: SIZE }}>
      <div ref={mountRef} />
      <svg ref={svgRef} width={SIZE} height={SIZE}
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} />
    </div>
  )
}

function drawDimensions(svg, camera, hSize, vSize) {
  while (svg.firstChild) svg.removeChild(svg.firstChild)

  const defs = svgEl('defs', {})
  const arrow = svgEl('marker', {
    id: 'dim-arrow', viewBox: '0 0 10 10', refX: 5, refY: 5,
    markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse',
  })
  arrow.appendChild(svgEl('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#333' }))
  defs.appendChild(arrow)
  svg.appendChild(defs)

  const { left, right, bottom, top } = camera
  const toPx = (x, y) => [
    ((x - left) / (right - left)) * SIZE,
    SIZE - ((y - bottom) / (top - bottom)) * SIZE,
  ]

  const hMax = hSize / 2
  const vMax = vSize / 2
  const gap = Math.max(hSize, vSize) * 0.08
  const overshoot = gap * 0.4

  // --- width dimension (above the part) ---
  const dimY = vMax + gap
  for (const x of [-hMax, hMax]) {
    const [x1, y1] = toPx(x, vMax)
    const [x2, y2] = toPx(x, dimY + overshoot)
    svg.appendChild(svgEl('line', { x1, y1, x2, y2, stroke: '#999', 'stroke-width': 1 }))
  }
  {
    const [x1, y1] = toPx(-hMax, dimY)
    const [x2, y2] = toPx(hMax, dimY)
    svg.appendChild(svgEl('line', {
      x1, y1, x2, y2, stroke: '#333', 'stroke-width': 1,
      'marker-start': 'url(#dim-arrow)', 'marker-end': 'url(#dim-arrow)',
    }))
    const text = svgEl('text', {
      x: (x1 + x2) / 2, y: y1 - 6, 'text-anchor': 'middle',
      'font-size': 13, fill: '#333', 'font-family': 'monospace',
    })
    text.textContent = hSize.toFixed(2)
    svg.appendChild(text)
  }

  // --- height/depth dimension (right of the part) ---
  const dimX = hMax + gap
  for (const y of [-vMax, vMax]) {
    const [x1, y1] = toPx(hMax, y)
    const [x2, y2] = toPx(dimX + overshoot, y)
    svg.appendChild(svgEl('line', { x1, y1, x2, y2, stroke: '#999', 'stroke-width': 1 }))
  }
  {
    const [x1, y1] = toPx(dimX, -vMax)
    const [x2, y2] = toPx(dimX, vMax)
    svg.appendChild(svgEl('line', {
      x1, y1, x2, y2, stroke: '#333', 'stroke-width': 1,
      'marker-start': 'url(#dim-arrow)', 'marker-end': 'url(#dim-arrow)',
    }))
    const tx = x1 + 14
    const ty = (y1 + y2) / 2
    const text = svgEl('text', {
      x: tx, y: ty, 'text-anchor': 'middle',
      'font-size': 13, fill: '#333', 'font-family': 'monospace',
      transform: `rotate(-90 ${tx} ${ty})`,
    })
    text.textContent = vSize.toFixed(2)
    svg.appendChild(text)
  }
}
