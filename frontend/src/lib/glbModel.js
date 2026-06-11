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

// Loads a GLB, scales metres -> mm, and applies the candidate's resting-pose
// transform. Returns a group wrapping the positioned model plus its
// world-space bounding box (in mm).
//
// Two separate transforms, like OrientationViewer: the model itself carries
// the metres->mm scale (auto-composed matrix); the wrapping group carries the
// candidate's resting-pose matrix. Collapsing these into one object's matrix
// would silently drop the x1000 scale.
export function loadOrientedModel(glbUrl, candidate) {
  return new Promise((resolve, reject) => {
    new GLTFLoader().load(glbUrl, (gltf) => {
      const model = gltf.scene
      model.scale.setScalar(1000) // metres -> mm, must match backend
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
      const box = new THREE.Box3().setFromObject(group)
      resolve({ model: group, box })
    }, undefined, reject)
  })
}
