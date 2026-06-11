# Part Intake

Phase 1 of the packaging insert designer: register parts manually or by
uploading a STEP file. STEP dimensions are extracted via minimum-volume
oriented bounding box (orientation-independent) and confirmed by the user
in a 3D viewer.

## Quick start
```bash
docker-compose up --build        # API on :8000
cd frontend && npm install && npm run dev   # UI on :5173
```

See PLANNING.md (architecture, phases) and CLAUDE.md (dev workflow).
