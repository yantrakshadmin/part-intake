# Part Intake & Insert Designer — PLANNING.md

## Vision

A web app for returnable-packaging insert design (Yantra Packs / Trakkia context).
Users register parts (manually or by uploading a STEP file), the system selects
predefined packaging, computes how many parts fit per box, and generates draft
insert designs (pocket/cell, comb/slot, hybrid archetypes) for the projects
team to refine. The tool is a **70% draft generator**, not a CAD replacement.

## Phases

### Phase 1 — Part Intake  ← CURRENT
Get trustworthy part data in. Two paths:
- **Manual**: form with L/B/H (mm) + weight (kg), validated, canonicalized to L≥B≥H.
- **STEP upload**: backend extracts true dimensions via minimum-volume OBB
  (orientation-independent — auto OEM files are in vehicle coordinates, so
  axis-aligned boxes are wrong). User confirms resting orientation in a 3D
  viewer; dims are auto-filled but always editable, never silently accepted.

Output contract — the **Part Profile**:
```
part_number, part_name
L ≥ B ≥ H (mm, canonical), weight (kg)
source: manual | stp
confirmed_orientation (4x4 matrix, STP only)  ← stored for Phase 3
glb_path (STP only)                            ← stored for Phase 3
```

### Phase 2 — Packaging selection & packing
- Predefined packaging master list (CRUD): inner dims, weight capacity, type
- 3D arrangement with insert-aware clearances:
  - divider wall thickness 8–15 mm (PP)
  - part clearance 5–10 mm per side
  - foam/bottom sheet thickness between layers (PP sheet + EVA foam)
- Output: parts per layer, layers per box, cube utilization, weight check

### Phase 3 — Insert generation
- Classify part → archetype:
  - aspect ratio > ~5:1, tube-like → **comb/slot dunnage** (e.g. stabilizer bars)
  - compact prismatic → **pocket/cell** with divider walls (e.g. gear shifters)
  - else → hybrid tray
- Pocket/cell: project part silhouette at confirmed orientation, offset by
  clearance, generate divider layout → parametric model → STEP/DXF export
- Comb/slot: find 3–4 stable support cross-sections along long axis,
  generate slot profiles from local cross-section + clearance

### Phase 4 — Coupling / nesting optimization
Interlocking complex parts to increase density. Hard problem — heuristics
first (mirrored pairs), not full 3D nesting.

## Architecture (Phase 1)

```
Browser (React + Vite + Three.js)
   │  multipart upload (dev) / S3 presigned PUT (prod)
   ▼
FastAPI ──> Postgres (jobs, part profiles)
   │ enqueue
   ▼
Redis <── Celery worker: cascadio (STEP→GLB) → trimesh (hull → min OBB
              → ranked resting orientations)
   │
   └─> GLB to shared volume (dev) / S3 (prod)
```

### Key technical decisions
| Decision | Choice | Why |
|---|---|---|
| STEP parsing | `cascadio` | pip-only OpenCascade wrapper, no conda; STEP→GLB direct |
| Geometry math | `trimesh` + numpy/scipy | `oriented_bounds` = min-volume OBB; battle-tested |
| Dimension basis | min OBB of convex hull | orientation-independent; solves the "unknown plane" problem |
| Orientation | candidates + user confirm | full automation is unreliable; 2-second human confirm kills the error class |
| Heavy work | Celery + Redis | STEP conversion is 2–30 s CPU-bound; never in request cycle; workers scale horizontally |
| API | FastAPI + Pydantic | strict contracts, async, OpenAPI for free |
| DB | Postgres | matches team's RDS experience |
| GLB scale | **metres in file, ×1000 in app** | glTF spec is metres; backend scales mesh to mm for math, frontend scales model ×1000 — these MUST stay in sync |

### Gotchas already handled (do not regress)
1. **GLB is in metres** — backend `load_unified_mesh` applies ×1000;
   frontend `OrientationViewer` applies `model.scale.setScalar(1000)`.
2. **STEP units**: `CONVERSION_BASED_UNIT ('INCH')` takes precedence over
   `SI_UNIT($, .METRE.)`. Detection is warn-only; OpenCascade normalizes.
3. **Assemblies**: multi-solid files are unioned into one packable unit,
   with a warning surfaced (`solid_count`).
4. **Non-watertight geometry**: dims still reliable (convex hull); volume is not.
5. **Orientation matrices are Z-up** (backend convention). Three.js scene is
   Y-up — viewer wraps with a `rotateX(-π/2)` basis swap.
6. **Coordinate frame**: candidate transforms map *mesh space → resting pose
   on z=0 floor*, OBB-centered. Phase 3 depends on this; don't change silently.

## Production path (when needed, not before)
- Direct upload → S3 presigned PUT (files never transit the API)
- GLB served via CloudFront
- docker-compose on one EC2 → ECS Fargate (api / worker services, ElastiCache)
- `Base.metadata.create_all` → Alembic migrations
- Auth (none in v1 — internal tool)

## Milestones
- [x] Geometry pipeline validated against real STEP file (inch-unit part,
      exact extents recovered: 127.0 × 63.5 mm)
- [ ] `docker-compose up` runs api + worker + db + redis
- [ ] Upload → poll → viewer → confirm → save flow works in browser
- [ ] Tested against 5+ real Yantra Packs part files (stab bar, shifter, etc.)
- [ ] Part list page with search
- [ ] Phase 2 kickoff: packaging master list
