# CLAUDE.md — Part Intake

Read `PLANNING.md` first. It is the plan of record (v1 archived as `PLANNING.v1.md`).

Internal tool for the Yantra Packs projects team. Goal: cut time spent on
fitting analysis.

---

## The one fact that drives everything

Real proposals: a Mubea stabiliser bar fits **40** per PLS12801. The best cuboid
answer over all six orientations is **8**. A tool that treats parts as boxes is
wrong by 32 parts per shipment.

Baselines use the *best* orientation, never a fixed one — fixing the orientation
flatters the nesting engine. `backend/tests/ground_truth.py` is the contract.

Ground-truth targets — any engine change is measured against these:
- Mubea stabiliser bar → **40** / PLS12801
- TRW steering wheel → **48** / PLS1280

---

## Development cycle

We work as a small team. Rahul and Claude are the PMs; implementation is delegated.

1. **PM writes the ticket.** One scoped task, with the acceptance check stated up
   front. A ticket without a check is not ready.
2. **Implementer** — `geometry`, `backend`, or `frontend` agent. One task each.
   Parallel work = multiple instances of the right agent, not more agent types.
3. **`tester` verifies** against real files. It runs commands and pastes output.
   It never fixes; findings go back to the implementer.
4. **`/code-review`** before anything lands.
5. **Ground-truth regression must pass.** Non-negotiable.

See `.claude/agents/README.md` for why the team is four roles and not twelve.

---

## Environment landmines on this machine

1. **Port 8000 is contested** — a Trakkia Django LAN server also uses it. Bind
   uvicorn to `127.0.0.1`. Run `lsof -i :8000` before assuming the API is yours.
2. **Celery does not hot-reload.** Restart the worker after any change to worker
   or geometry code. A stale worker is the most common false bug report here.
3. `INTAKE_REDIS_URL` defaults to a docker hostname. Outside compose it silently
   breaks uploads. Always set it.
4. The API falls back to in-process STEP extraction when the broker is down.
   Deliberate dev convenience — it also masks a dead worker. When diagnosing,
   confirm which path actually ran.

---

## Commands

```bash
# Full stack
docker-compose up --build          # api :8000

# Frontend
cd frontend && npm install && npm run dev    # :5173, proxies /api -> :8000

# Backend without docker
cd backend
export INTAKE_DATABASE_URL=sqlite:///dev.db \
       INTAKE_LOCAL_STORAGE_DIR=/tmp/intake \
       INTAKE_REDIS_URL=redis://localhost:6379/0
uvicorn app.main:app --reload --host 127.0.0.1
celery -A app.worker worker --loglevel=info

# Geometry against any CAD file
cd backend && python tests/test_geometry.py path/to/part.stp
```

---

## Hard rules

1. **Never change the metre↔mm scaling in only one place.** Backend scales mesh
   ×1000; frontend scales model ×1000. A contract test enforces this.
2. **Dims are never silently auto-filled.** The user sees the part and confirms;
   fields stay editable. On the Y2V_YK9 Housing, automation alone is off by 6× —
   this rule is why that doesn't ship.
3. **Largest-connected-body filter is mandatory** on CAD import. Stray reference
   geometry is normal in customer files.
4. **Never report volume or mass** from customer CAD. They are open surface
   models; those values are meaningless.
5. `.SLDASM` fails loudly with guidance. No silent partial handling.
6. Orientation transforms are Z-up, mesh-space → resting-pose-on-floor.
7. Heavy geometry work stays in the Celery worker.
8. Surgical changes over rewrites.

---

## Style

- Python: type-hinted, no framework magic, stdlib logging
- React: functional components, no state library until there's a reason
- Frontend deps stay short (react, three)
- Non-trivial logic leaves one runnable check behind

---

## UI direction

Data-Dense Dashboard. Fira Sans body / Fira Code for numbers and IDs.
`#1E40AF` primary, `#D97706` accent, `#F8FAFC` ground. 8px spacing rhythm,
tabular figures in every data column. The ranked-solution comparison is the
core screen — this is an engineering instrument, not a generic admin panel.
