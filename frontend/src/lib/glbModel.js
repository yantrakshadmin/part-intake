import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

// Backend matrices are row-major, Z-up, mesh-space(mm) -> resting pose.
// Scenes here are Y-up, so every view wraps with this basis swap. Keep this
// the single definition (PLANNING.md "Gotchas": must match backend).
export const zUpToYUp = new THREE.Matrix4().makeRotationX(-Math.PI / 2)

export function candidateMatrix(candidate) {
  const T = new THREE.Matrix4().set(...candidate.rotation_matrix.flat())
  return zUpToYUp.clone().multiply(T)
}

// THREE.Box3().setFromObject() unions each mesh's *local* bounding box after
// transforming its corners by the mesh's world matrix. For an assembly whose
// sub-meshes carry their own rotations (e.g. a pulley mounted at an angle),
// that corner-transform over-estimates the true extent — it can be 15-20%
// larger than the backend's vertex-based OBB (dims_lbh). Compute the AABB
// from actual transformed vertices instead so dimensions match the backend.
export function computeTightBounds(object) {
  const box = new THREE.Box3()
  const v = new THREE.Vector3()
  object.updateMatrixWorld(true)
  object.traverse((o) => {
    if (!o.isMesh) return
    const pos = o.geometry.attributes.position
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i)
      v.applyMatrix4(o.matrixWorld)
      box.expandByPoint(v)
    }
  })
  return box
}

// Loads a GLB, scales metres -> mm, and applies the candidate's resting-pose
// transform. Returns a group wrapping the positioned model plus its
// world-space bounding box (in mm).
//
// Two separate transforms, like OrientationViewer: the model itself carries
// the metres->mm scale (auto-composed matrix); the wrapping group carries the
// candidate's resting-pose matrix. Collapsing these into one object's matrix
// would silently drop the x1000 scale.
/** Metres -> mm. THE single frontend definition of the scale contract
 *  (hard rule 1: backend scales the mesh x1000, frontend scales the model
 *  x1000, and it must not live in two places). `scale.check.mjs` fails if a
 *  second literal appears. */
export const MM_PER_M = 1000

export function loadOrientedModel(glbUrl, candidate) {
  return new Promise((resolve, reject) => {
    new GLTFLoader().load(glbUrl, (gltf) => {
      const model = gltf.scene
      model.scale.setScalar(MM_PER_M) // must match the backend
      model.traverse((o) => {
        if (o.isMesh) {
          o.material = new THREE.MeshStandardMaterial({
            color: 0x8899aa, metalness: 0.3, roughness: 0.55,
          })
        }
      })
      const group = new THREE.Group()
      group.matrixAutoUpdate = false
      group.matrix.copy(candidateMatrix(candidate))
      group.add(model)
      group.updateMatrixWorld(true)
      const box = computeTightBounds(group)
      resolve({ model: group, box })
    }, undefined, reject)
  })
}
