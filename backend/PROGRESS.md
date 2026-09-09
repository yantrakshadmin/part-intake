# Backend progress notes

Read `../CLAUDE.md` and `../PLANNING.md` first — those are the source of
truth for rules/architecture. This file is just "where things stand" so a
session can resume quickly.

## Status: stable; OCP fallback reader added for degenerate STEP files

This session (2026-06-11): a real SolidWorks 2022 export
(`107011JCB32..STEP`, JCB part) failed in cascadio — OpenCascade's FixShape
healing throws `Geom_TrimmedCurve::U1 == U2` and cascadio silently writes a
header-only 264-byte GLB. Fixes:
- `app/step_fallback.py` (new): OCP-based STEP→GLB reader that skips the
  healing sequence and meshes face-by-face (1725/1754 faces meshed; the 29
  degenerate ones don't affect the bounding box). Outputs GLB in metres,
  same contract as cascadio.
- `convert_step_to_glb` now validates the GLB actually contains triangles
  (`_glb_has_geometry`) and retries via the fallback before erroring.
- `detect_step_length_unit` scanned only the first 500 KB; SolidWorks wrote
  `SI_UNIT (.MILLI., .METRE.)` at byte ~1.9 MB. Now scans whole file (64 MB cap).
- New dep: `cadquery-ocp` (installed in venv + requirements.txt).
- Verified: JCB part → 220.0 x 153.5 x 58.0 mm, 4 candidates; sample.step
  regression unchanged (127.0 x 63.5 x 34.92 mm, inch warning intact).
- Note: `.SLDPRT` files are native SolidWorks binary — unsupported by
  design; upload endpoint correctly rejects them with a 400.

`app/geometry.py` was re-validated this session against a real multi-solid
assembly (M&M pump, 10 solids, non-watertight) and is correct:
- `load_unified_mesh` produces a mesh whose vertex positions match what
  `scene.graph`-composed transforms (and Three.js's GLTFLoader) produce.
- `minimum_obb` + `generate_orientation_candidates` produce `dims_lbh` and
  `rotation_matrix` that, when applied to the actual mesh vertices, give an
  AABB that exactly equals `dims_lbh` (verified to ~1e-6mm).
- The mismatch the user saw between extracted dims and the 2D view dims was
  a **frontend** bug (THREE.Box3 overestimate), not a backend one — see
  `../frontend/PROGRESS.md`. **No backend matrices/semantics changed.**

## Phase 2 start: packaging & vehicle master data (2026-06-11, later)

- `app/models.py`: new `Packaging` + `Vehicle` tables; `app/seed_data.py`
  seeds them from `packaging_and_vehicles (1).xlsx` (15 boxes, 11 vehicles)
  on startup only when empty — user edits never overwritten.
- `GET/POST /api/packaging` (custom boxes arrive as `status=draft`;
  xlsx rows are `checked`), `GET /api/vehicles`.
- Known data gaps to add later: box tare weight, stack/crush rating,
  collapsed height for FLC/FSC foldables (matters for return trips).

## Local dev (no docker — skipped per user request)

```bash
cd backend
INTAKE_DATABASE_URL=sqlite:///dev.db INTAKE_LOCAL_STORAGE_DIR=/tmp/intake \
  ./venv/bin/uvicorn app.main:app --reload --port 8000

INTAKE_DATABASE_URL=sqlite:///dev.db INTAKE_LOCAL_STORAGE_DIR=/tmp/intake \
INTAKE_REDIS_URL=redis://localhost:6379/0 \
  ./venv/bin/celery -A app.worker worker --loglevel=info
```
Redis must be running locally (`brew services start redis` or `redis-server`).
SQLite db at `backend/dev.db`, uploaded files/GLBs at `/tmp/intake/`.

## Test fixtures

- `tests/fixtures/sample.step` — single solid, inch units. Validated dims
  127.00 x 63.50 x 34.92 mm. Run: `./venv/bin/python tests/test_geometry.py`
  (or pass a path to test any other .stp file).
- A real customer file (M&M water pump assembly, 10 solids, non-watertight,
  dims 155.91 x 134.20 x 86.29 mm) was used for verification this session
  but **not yet added as a fixture**. It currently lives only in
  `/tmp/intake/f4bade1e-911f-42d7-bc82-8c529d33fea9.step` (a temp upload dir,
  may get cleaned). If you want a permanent regression fixture for
  multi-solid assemblies, copy it into `tests/fixtures/` and add a check.

## Remaining Near-term TODO (CLAUDE.md, in order, stop before Phase 2)

- [ ] #2 Test with more real Yantra Packs `.stp` files — collect failures
      into `tests/fixtures/`. (One real file tested this session, passed.)
- [x] #3 Part list page — done (frontend v7 rail list + search).
- [x] #4/#5 Dimension labels + wireframe OBB — done (frontend).
- [ ] #6 Delete/edit part profile endpoints + UI — **next backend work**:
      `DELETE /api/parts/{id}` + `PATCH /api/parts/{id}`, then wire into
      the Parts rail.

## Upload failure root-caused + dev fallback (2026-06-12)

User hit "upload failed". Two stacked causes:
1. `settings.redis_url` defaults to `redis://redis:6379/0` (docker-compose
   hostname) — outside compose, Celery enqueue can't reach the broker, and
   after a failed attempt the API process's Celery client wedges itself
   ("Retry limit exceeded… must be restarted"). Local dev needs
   `INTAKE_REDIS_URL=redis://localhost:6379/0` (CLAUDE.md updated).
2. No worker was running, so even a successful enqueue would have sat
   "pending" forever.

Hardening: `worker.run_extraction(job_id)` is now a plain function (the
Celery task wraps it), and `main.upload_step` catches enqueue failure and
runs extraction in a daemon thread as a dev fallback — uploads never
hard-fail because redis/celery are absent. Verified: Playwright e2e with
redis+worker up (upload → candidates render), and curl against a second
instance pointed at a dead broker port (job went pending → processing →
done via the thread fallback in ~4 s).
