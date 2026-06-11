# CLAUDE.md — Part Intake

Instructions for Claude Code working in this repo. Read PLANNING.md first.

## Current state
- `backend/app/geometry.py` — **tested and working**. STEP→GLB→min-OBB→
  orientation candidates, validated against a real STEP file (inch-unit part,
  exact extents recovered). Treat as stable; change only with a failing test.
- `backend/app/{main,worker,models,schemas,config}.py` — working skeleton,
  smoke-tested (end-to-end job flow ran against the real file with SQLite).
- `frontend/` — functional skeleton. Flow works; styling is deliberately bare.

## Commands
```bash
# Full stack
docker-compose up --build          # api :8000, frontend run separately

# Frontend dev
cd frontend && npm install && npm run dev    # :5173, proxies /api -> :8000

# Backend dev without docker (needs local redis + postgres, or use SQLite env)
cd backend
INTAKE_DATABASE_URL=sqlite:///dev.db INTAKE_LOCAL_STORAGE_DIR=/tmp/intake \
  uvicorn app.main:app --reload
INTAKE_DATABASE_URL=sqlite:///dev.db INTAKE_LOCAL_STORAGE_DIR=/tmp/intake \
  celery -A app.worker worker --loglevel=info

# Test geometry against any STEP file
cd backend && python tests/test_geometry.py path/to/part.stp
```

## Hard rules
1. **Never change the metre↔mm scaling in only one place.** Backend scales
   mesh ×1000 (`load_unified_mesh`); frontend scales model ×1000
   (`OrientationViewer`). They must match. See PLANNING.md "Gotchas".
2. **Dims are never silently auto-filled** from STP extraction. The user must
   see the part in the viewer and confirm; fields stay editable.
3. The **Part Profile contract** (PLANNING.md) is the interface to Phase 2/3.
   Extend it additively; don't rename fields.
4. Orientation transforms are Z-up, mesh-space → resting-pose-on-floor.
   Phase 3 insert generation will consume `confirmed_orientation` — preserve
   semantics.
5. Heavy geometry work stays in the Celery worker. Nothing CPU-bound in
   FastAPI handlers.
6. Surgical changes over rewrites. Keep diffs small and focused.

## Style
- Python: type-hinted, no framework magic, stdlib logging
- React: functional components, no state library until there's a reason
- Keep the frontend dependency list short (react, three — that's it for now)

## Near-term TODO (in order)
1. Verify `docker-compose up --build` works end to end in browser
2. Test with real Yantra Packs .stp files — collect failures into
   `tests/fixtures/` and fix
3. Part list page (GET /api/parts exists; needs UI)
4. Dimension labels rendered in the 3D viewer (L/B/H arrows on the OBB)
5. Wireframe OBB box around the part in the viewer
6. Delete/edit part profile endpoints + UI
7. Then stop — Phase 2 starts only after intake is trusted by the projects team
