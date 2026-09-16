# PROGRESS

Running log. `PLANNING.md` says what we are building; this says where we got to.
**Append a dated entry at the end of every work session.** Newest last.

Read order for a new session: `CLAUDE.md` → `PLANNING.md` → the last entry here.

---

## Now

**Phases 2–4 are in and committed. The engine reaches ground truth, generates
the insert BOM, draws it — and as of this session the whole stack has been
driven end to end over real HTTP with a fresh Celery worker.**

Ground truth, the contract from `CLAUDE.md`, reproduced live:

```
40 in PLS12801 (1, 4, 10)  pitch (1097.0, 145.0, 68.0)
48 in PLS1280  (3, 2, 8)   pitch (377.0, 361.0, 116.0)
```

The tree was uncommitted through Phases 1 and 2. It no longer is — six commits
on `master`, **no remote, nothing pushed**:

| commit | what |
|---|---|
| `c650fcf` | Phases 2–4: nesting engine, insert BOM, packing recommendation (43 files) |
| `c062ca1` | Session log, planning updates, the `/code-review` command |
| `b85fc18` | G-UNIT — read the unit a CAD file declares instead of assuming it |
| `7c1c00b` | G-POSE — audit `max_candidates=4`, and find the wrong ranking key |
| `815bba0` | Record the picker/nester ranking mismatch as an open question |
| `4d78f95` | Break ranking ties by box size, not by seed order |

**What the last review arc changed.** Five tickets; three of the five were
**reporting** bugs rather than computation bugs — the engine had the right
number and said the wrong thing, which is the class every unit test missed
because every unit test asserted the computed value. Two of the five were
defects I had introduced myself.

| ticket | finding | cost | outcome |
|---|---|---|---|
| R1 | insert drawing dropped elements silently | drawing disagreed with its own BOM | fixed |
| R2 | coplanar bars billed height twice | **reported DOES NOT FIT for stacks that fit** | fixed |
| R3 | collision check never saw the diagonal | 640 mm³ of voxel skin | documented, engine untouched |
| G-UNIT | unit warning lied on both formats | reporting only — conversion was always right | fixed |
| G-POSE | pose cap, and area as the ranking key | zero parts today; mis-ranks 2 real files | audited, engine untouched |
| — | ranking ties fell to seed order | dominated crate shown as runner-up | fixed |

**The stack runs clean.** First true end-to-end HTTP run confirmed the plumbing,
not just the numbers: extraction went through **Celery**, not the in-process
fallback (landmine 6); `canonical_dims_lbh` came back byte-identical to the
fixture; hard rule 9 holds on the solve payload (every ranked entry carries
`asset_name`/`extent_lbh`/`pitch_lbh`/`grid`/`dunnage`/`drawing_url`, and
`truck` carries its own `asset_name`); hard rule 4 holds too — `mesh_volume_mm3`
is null on a non-watertight model, with a warning that says why.

**Still unverified: the UI.** The browser has not been opened since the UI-4
restructure. The BOM table, the Explode modal, the Truck Load tab and the Load
Calculator result card have never been rendered for a human. The last browser
session returned **six defects that 20+ green automated checks had all missed**,
so this remains the largest untested surface in the project.

Suites — 13 backend, all green (from `backend/`):

```
for s in ground_truth test_cad_import test_clearance test_dunnage test_nesting \
         test_catalogue test_engine test_synthesis test_scale_contract \
         test_solve_api test_iges_units test_pose_search; do
  venv/bin/python tests/$s.py
done
venv/bin/python -m app.insert_drawing      # NOT tests/…; relative import needs -m
```

Plus the frontend, all green (from `frontend/`):

```
node src/lib/packing.check.mjs   node src/lib/bom.check.mjs
node src/lib/scale.check.mjs     node src/lib/steps.check.mjs
npm run build
```

Bring the stack up (redis db 9 and port 8011 keep it clear of the Trakkia
Django server on :8000 — landmine 1):

```
cd backend
export INTAKE_DATABASE_URL=sqlite:///dev.db \
       INTAKE_LOCAL_STORAGE_DIR=/tmp/intake \
       INTAKE_REDIS_URL=redis://127.0.0.1:6379/9
venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011
venv/bin/celery -A app.worker worker --loglevel=info
cd ../frontend && API_URL=http://127.0.0.1:8011 npm run dev   # use localhost:5173, vite binds IPv6
```

`dev.db` holds part **1**, `YNT-TRW-SW-1704` (the TRW wheel, extracted from the
real fixture, GLB linked, solve completed) — so the workspace opens populated.

---

## Session log

### 2026-09-09 — Phase 0 and Phase 1

**Phase 0.** `backend/tests/ground_truth.py` encodes the two shipped proposals as
the acceptance contract: Mubea 40/PLS12801, TRW 48/PLS1280. Baseline tries all six
axis permutations — fixing one flatters the engine. Self-test asserts a cuboid
engine *fails* both cases. Running it corrected the Mubea baseline from 6 to 8
(gain 5.0×, not 6.7×), which is exactly what the harness was built to catch.

**Phase 1.** Implemented by a background agent against an acceptance test written
first. The agent refused to make the test pass and was right.

*Four of my reference dimensions were wrong.* I generated them with an ad-hoc IGES
parser that scooped up knot vectors and type-124 transformation matrices as if they
were control points. Verified independently: `Y2V_YK9_Housing.igs` is named
`WITHOUT_REF_HOUSING_7` in its own global section — a re-export with reference
geometry already stripped, zero point entities, no float in the parameter section
above 2377. The 4622×1470×1459 and 767×534×182 figures in PLANNING v2 do not exist
in that file. Re-running the same parsing approach on the YXA bar reports datum
points spanning 5332mm on a 1092mm part, and an all-coordinates hull of
1262×1263×1256 — near-equal on all axes, the signature of the bug.

*Consequence for the test.* Accuracy and drift are now separate. The steering wheel
is the only fixture with external ground truth (TRW proposal, a part that shipped)
and asserts correctness at ±5% — it returns 371×354×138 against 370×360×135. The
four IGES parts have no trustworthy measurement, so they are snapshot baselines at
±2%, labelled in the docstring as *not* ground truth. They catch reader regressions;
they do not confirm the reader is right.

*Real bug found.* OCP 8.x renamed `TopoDS.Face_s`. Every IGES import and every STEP
fallback was crashing with `AttributeError` before the fix.

*Largest-body filter deliberately not shipped,* with measurements. IGES faces share
no topology, so `trimesh.split` yields 48,960 components on the IBJ (largest 7.96%).
After `BRepBuilderAPI_Sewing` the largest threshold that is a no-op across all five
files is 0.01%; at 0.10% the Rack loses a real 124.7mm panel. Meshing `TopAbs_FACE`
only already excludes points and free curves — that is the filter. Revisit when a
file actually exhibits the problem.

**Environment.** Every session so far launched Claude Code from `~`, so
`.claude/agents/` (geometry, backend, frontend, tester) never loaded and this repo's
`CLAUDE.md` was never auto-read. Launch from the repo directory. `/agents` is
removed in this build; agents load at session start only.

---

### 2026-09-09 (later) — Phase 2, nesting engine core

**`backend/app/nesting.py` (170 lines) + `backend/tests/test_nesting.py`.**
Both ground truths reproduce exactly: Mubea **40** = (1,4,10), TRW **48** = (3,2,8).
The real YXA bar independently gives **40** in a PLS12801 against a cuboid 8.

```
./venv/bin/python tests/ground_truth.py     # 40 / 48, cuboid 8 / 42, selftest ok
./venv/bin/python tests/test_nesting.py     # all checks passed
./venv/bin/python tests/test_cad_import.py  # all checks passed
```

**PLANNING §4 is wrong and this is the session's finding.** "2D irregular nest
per layer, then stack" divides the asset height by the layer height — which is
the cuboid assumption reintroduced on the vertical axis, and it is where almost
all of the Mubea gain lives:

| | lateral | vertical |
|---|---|---|
| Mubea, implied by the deck | 285 → 155mm (1.8×) | 190 → 66.7mm (2.8×) |
| TRW, stated in the deck | 370 → 376.6mm (none) | 135 → 120mm (1.1×) |
| YXA bar, measured | 300 → 140mm (2.1×) | 148 → 68mm (2.2×) |

A 2D nester cannot see any of the right-hand column. TRW is the same phenomenon
as Mubea, not a different archetype: a 135mm wheel on a 120mm pitch because it
sits *into* its 117mm tray. Both proposals are regular lattices of one part, so
the problem is not "how many boxes fit in a box" — it is **how close can two
copies of this part sit on each axis**.

**Engine.** `n = floor((inner - extent) / pitch) + 1` per axis, with pitch
measured by sliding a voxel occupancy grid against itself one voxel at a time
until the two copies stop overlapping. Same primitive on all three axes.

**Neither `jagua-rs` nor NFP + bottom-left-fill was used** (PLANNING §4 named
both, rung 5). Reasons, in order:
1. Both are 2D. They cannot see the vertical interleave above.
2. A raster needs no clean outline. These are open surface models — the IBJ
   splits into 48,960 disconnected faces; polygonising that for an NFP is a
   project on its own. Voxel occupancy does not care.
3. No cargo on this machine (jagua-rs is a Rust crate), and no shapely (NFP).
   The raster needs numpy + trimesh, both already installed. Zero new deps.

**`ground_truth.py` gained two fields per case,** `pose_lbh` and `pitch_lbh`.
Note the harness's `evaluate(engine)` signature is `engine(part_lbh, asset)` —
no engine given only bounding dims can beat `cuboid_baseline`, so 40 was
unreachable through it. Docstring now says so. TRW's pitch comes straight off
the deck (pocket 376.6×367.5×117 + 3mm sheet); Mubea's is the loosest spacing
consistent with 4/layer × 10 layers, corroborated by the deck's own dunnage —
11 Top Center Bars 66mm tall for 10 layers. The alternative reading (resting on
285 instead of 190) gives 186.67/56.11 and the same 40, so the formula holds
either way.

**Bug worth knowing:** exact fits are the normal case here, not the edge case
(4 × 155 in 750−285 is exact; so is 3 × 376.6). Float division loses a whole
row — a pitch typed `186.7` instead of `560/3` gives 30 instead of 40. There is
an `EPS` in `lattice_count` and a test pinning it.

**Mirrored pairs deliberately not shipped.** Measured on the YXA bar, alternating
180° drops vertical pitch 68mm → 40mm, giving ~14 layers where 10 shipped.
Overshooting ground truth is not a win — what shipped reflects manufacturability
we cannot see. Self-pitch already hits both targets. Reasoning is in the
`test_nesting.py` docstring with the numbers.

**Not done, and why — Phase 2 is not closed.** The pose × asset × custom-box
ranking loop (PLANNING §4/§6, "always top 2 catalogue + 1 custom"):
- `nest(mesh, candidates, asset)` ranks poses for *one* asset. The loop over the
  catalogue is next. ~~`app/seed_data.py` contains no assets~~ — **wrong, corrected
  same session:** it carries 15 `PACKAGING` rows (a vetted subset of the 49), and
  `PLS12801` at 1150×750×790 matches ground truth exactly. All 15 are containers,
  so PLANNING §5's warehouse-rack hazard does not bite for them. The other 34 stay
  out until typed.
- Custom-box synthesis needs `DOMAIN.md` (clearances, wall thicknesses, foam
  rules). Still the same blocker as at Phase 1 handoff. `nesting.py` takes
  `clearance_mm` as a per-call parameter with a PLANNING §6 default rather than
  baking a constant, so the knob is there when the numbers arrive.

### 2026-09-09 (later still) — Phase 2 closed out

Ran the CLAUDE.md cycle: two tickets (B2-1 catalogue adapter → `backend`, B2-2
custom box synthesis → `geometry`) in parallel, then integration, then
`/code-review`. Six suites green:

```
tests/ground_truth.py   tests/test_nesting.py    tests/test_catalogue.py
tests/test_synthesis.py tests/test_engine.py     tests/test_cad_import.py
```

| File | Lines | What |
|---|---|---|
| `app/nesting.py` | 247 | Lattice engine (from the earlier entry) + `measure_poses` / `layouts_for` / `rank_catalogue`. |
| `app/catalogue.py` | 63 | `Container` + `containers(db=None)`. DB-backed or straight off the seed constants. |
| `app/synthesis.py` | 147 | `synthesise()` — solves for custom box height over the §6 bounded space. |
| `app/engine.py` | 130 | `solve()` = the §6 result set, and `parts_per_truck`. |
| `app/models.py` | +1 | `Packaging.kind` — PLANNING §5 item 4, so the optimiser cannot pack into a rack. |

---

#### `/code-review high` — 9 findings, all fixed

One of them was a genuine correctness bug in the core primitive, and it is the
kind this project is most exposed to.

**HIGH — `min_pitch` only checked the neighbouring offset, not the lattice's
multiples.** A lattice puts copies at 0, p, 2p, 3p..., so every multiple has to
be clear. Disjointness at p does *not* imply it at 2p once the part is
non-convex — and non-convex comb/bent-bar parts are the entire reason this
engine exists. A part occupying cells `{0,1,4}` is pairwise-clear at pitch 2,
but copies 0 and 2 both want cell 4:

```
min_pitch reported 2.0  ->  lattice at 0,2,4,6
  COLLISION cell 4 (copies 0 and 2)
  COLLISION cell 6 (copies 1 and 3)
```

It reported 4 parts where 2 fit. Fixed by requiring `k*shift` clear for every
`k`, and `test_pitch_is_clear_at_every_lattice_multiple` now lays out the
implied lattice and looks for two copies claiming the same cell.

**Our headline numbers were never affected** — the YXA bar's measured pitches
are unchanged at 140/68mm, so its real lattice was already multiple-safe. This
was a landmine, not an active error. It would have gone off on the first part
with a genuinely periodic profile, which is exactly the comb archetype.

**MEDIUM — `lattice_count` returned a weight-capped count next to the full
geometric grid.** `(10, (3,3,3), "weight")` — and 3×3×3 is 27. Any UI rendering
`Layout` as "10 = 3x3x3" prints two numbers that disagree, which is *literally*
the PLANNING §7 defect this engine was supposed to stop repeating, and the old
test pinned the inconsistency. The cap now drops whole layers (9 in 3×3×1),
cascading to rows then columns, so `count == product(grid)` always. Tested at
four caps.

Also fixed: `synthesis.py` used an absolute `from app.nesting import`, which
broke every import from any cwd but `backend/` (verified from `/tmp`);
`extract_dimensions` leaked a temp GLB per call (now 99 → 99 across 3 calls;
the older `extract_part` leak is still open and pre-existing);
`containers(db)` filtered `kind` but not `status`, so a draft row with a
PLANNING §5 trailing-zero 6000kg limit would have been ranked *first* once the
full export loads; `Packaging.kind` had no `server_default`, so a manual
`ALTER TABLE` on an existing dev.db would have been wrong; and
`custom_beats_catalogue()` returned False when the catalogue was empty, hiding
the only viable design for a part too big for every stock box.

Two test defects worth naming, because both made suites claim more than they
checked:
- `test_cad_import.py` gated the whole run on an **IGES** glob and had no `else`
  on its one accuracy check. Rename or lose the STEP wheel and it still printed
  "all checks passed" backed by nothing but snapshots of our own output — the
  exact overclaim its docstring was written to prevent. It now fails loudly
  (verified by hiding the fixture).
- `test_real_bar` compared the YXA bar's count against **Mubea's** cuboid
  baseline. Two different parts, so the assertion could never fail. Now uses the
  bar's own baseline — 40 vs 10, a real 4× and a real check.

---

#### The engine reproduces a shipped proposal from CAD alone

Every other check feeds the engine a pitch derived from a deck. This one starts
from the customer's STEP file and nothing else:

```
CAD only -> 48 in PLS12803 (3, 2, 8)
   measured pitch (377.0, 361.0, 121.0)   deck pocket+sheet (376.6, 367.5, 120.0)
   dims (371.0, 353.9, 137.7)             deck 370 x 360 x 135
```

TRW shipped **48 in a PLS1280** as a 3×2 matrix, 8 layers. The engine picks the
same asset, the same grid, the same count — and its CAD-measured pitch lands on
the deck's own printed pocket (376.6 × 367.5 × 117, +3mm separator sheet on the
vertical axis only) within 0.4mm on L and 1mm on H. The 6.5mm on B is the
reader's known 353.9-vs-360 delta on that dimension, already documented in
Phase 1 inside the ±5% accuracy check — not a nesting error.

Two independent routes to the same three numbers. This is the strongest
validation in the project so far and it is pinned in `tests/test_engine.py`.

**It also settles PLANNING §7.** The TRW deck states 46 kits by weight, and §7
objected that 155kg/kit against a 9000kg payload gives 58. Independently: 58 by
weight, 48 by volume, so 48 ships and it is right *by volume*. §7 was correct
and the deck's 46 was wrong. Pinned too.

---

#### The finding: box-level gain does not survive to the truck

**This is the important thing in this entry.** For the Mubea part, a stock
**PLS12103 holds 65** where the shipped **PLS12801 holds 40** — +62%. Per 32ft
SXL:

| Box | per box | boxes/truck | parts/truck | limited by |
|---|---|---|---|---|
| PLS12801 (shipped) | 40 | 39 | **1560** | weight |
| PLS12103 (best on count) | 65 | 25 | **1625** | weight |

**+62% per box is +4% per truck.** At 5kg a part both options run out of the
9000kg payload before they run out of space — the ceiling is 1800 parts however
you pack. A tool that reported the box number alone would tell the team to
re-tool an insert for nothing. Pinned in `tests/test_engine.py`.

So `parts_per_truck` went into `engine.py` now rather than waiting for Phase 3
tiering. It is box → truck only; the pallet tier is still skipped, with a
`ponytail:` comment saying why (none of the 15 loaded assets has a pallet spec).

**Tare is load-bearing and we do not have it.** With `tare=0` the ranking above
*inverts* — 1800 vs 1755, PLS12801 wins. `Packaging` has no tare column. The
test uses the decks' 30kg. **This is the one thing I need from the team** (see
Open). Until then `parts_per_truck` takes tare as a required argument with no
default, so nobody can get a plausible-looking wrong answer by omitting it.

**Open question for the team, not for code:** why did Mubea ship in a PLS12801
when a PLS12103 holds 65? Either a real 25-parts-per-shipment miss, or a
constraint we do not model (customer pallet standard, box availability, the
insert's own manufacturability). Worth one conversation.

---

#### Other calls made

**Synthesis overturned one of my ticket assumptions, correctly.** I specified
that the 35mm base separator and 5mm top clearance come out of the inner
height. They cannot: ground truth measures inner height as *the height the
lattice gets*. Mubea stacks `190 + 9 × 66.67 = 790.0` in a 790mm inner while
also carrying a 123mm bottom separator, and TRW stacks `8 × 120 = 960` in a
1000mm inner. Charging those 40mm to the inner height costs TRW a whole layer
(42, not 48). They are now added to the **outer** height. It is a
datum-definition question and belongs in `DOMAIN.md`.

**Synthesis shrinks the box under the weight cap** rather than capping the count
of a taller box. Otherwise `design.count` and `lattice_count(design.inner)`
disagree whenever weight binds — which is literally PLANNING §7's "minimum of
48 and 46" defect. Consequence: a part too heavy for even one layer returns
`None` rather than a box holding a partial layer. Refusing is the honest answer
inside "footprint must be a standard pallet".

**Pitch measurement hoisted out of the asset loop.** Pitch is a property of the
part in a pose, not of the asset, so `measure_poses` runs once and
`rank_catalogue` reuses it — O(poses), not O(poses × assets). At ~2.4s per pose
that is 10s instead of ~2.5 minutes over 15 containers. `nest()` is now a thin
wrapper. A test asserts the wall-clock so the hoist cannot silently regress.

**`min_pitch` had unreachable dead code** — the loop ran to `shift == n`, where
both slices are empty, which reads as "no collision" and always returned before
the real no-interleave fallback. Now stops at `n - 1`. Same answers, but the
fallback is live.

**Real bar, end to end:** 9 of 15 containers fit a 1092mm bar; top 2 are
PLS12103 and FLC12102 at 60 each; the custom box also gives 60, so
`custom_beats_catalogue()` is **False**. The right answer — stock is already
optimal for that part and the tool declines to invent a custom-box upsell.

**PLANNING.md corrected** (§4 rewritten with the lattice model and the measured
pitch table, §5 items 6 and 7 resolved from the seed data, §2's "delete
`packing.js`" rescoped). Details in the two entries above.

**`frontend/src/lib/packing.js` NOT deleted,** against PLANNING §2. Only
`partsPerBox` and its private helpers encode the cuboid assumption; the other
five exports are insert-drawing and truck-load helpers that §2 separately says
to keep. And nothing can be deleted before there is an endpoint to call
instead. Rescoped in §2.


### 2026-09-09 (fourth) — B3-1, the solve endpoint

`engine.solve()` is now reachable over HTTP, in the worker. Ticket B3-1 →
`backend` agent, then integration review by the PM. Seven suites green.

```
POST /api/parts/{id}/solve   -> 202 {solve_job_id, status}
GET  /api/solve-jobs/{id}    -> catalogue / custom / truck / warnings
```

| File | Change |
|---|---|
| `app/models.py` | `+SolveJob` — a new **table**, not a column. `create_all` makes tables for free; a column needs a manual `ALTER TABLE` on every dev.db (no Alembic). |
| `app/worker.py` | `+solve_part` task `+run_solve`, mirroring `extract_step`/`run_extraction` including the thread-fallback split. |
| `app/main.py` | `+POST /api/parts/{id}/solve`, `+GET /api/solve-jobs/{id}`. |
| `app/schemas.py` | `+SolveIn`, `+LayoutOut`, `+BoxDesignOut`, `+TruckFitOut`, `+SolveResultOut`, `+SolveJobStatusOut`. |
| `tests/test_solve_api.py` | **New.** The wheel's 48 = (3,2,8) reproduced *through the API*. |

The acceptance check is the Phase 2 CAD-only result routed through HTTP —
**48 in PLS12803, grid (3,2,8)** — so the endpoint is pinned to the same
shipped proposal the engine is, not to a snapshot of its own output.

#### Calls made

**Manual parts get 422, not a cuboid answer.** A hand-entered part has no CAD,
so there is no interleave to measure and the answer collapses to the cuboid
one — 8 for the Mubea bar where 40 ships. An endpoint returning that quietly
would be wrong by 32 parts and look authoritative doing it. It refuses and says
to upload the .stp. A clearly labelled `method: "cuboid"` result is a fine
future feature; it needs an app-side cuboid function (`tests/ground_truth.py`
is the contract — do not import test code into the app) and a UI that shows the
label.

**No truck numbers without a tare.** `tare_kg` is optional in the request, but
when it is absent `truck` is `null` and a warning names tare and the
+62%/box → +4%/truck finding. Never a truck number on an assumed tare: at
tare=0 the Mubea ranking inverts outright.

**No result cache.** The key would need `tare_kg` + `vehicle` + `top_n` or it
serves a wrong truck number. Not worth it before anyone has used the endpoint.
`ponytail:` comment in `main.py`.

**Vehicle validated at POST, always** — not only when tare is given. Keeps the
failure at the synchronous boundary instead of surfacing later as a failed job.
The implementer's call, and the right one.

#### Three defects found in integration review, all fixed

All three were in `run_solve`, and the first is the one that mattered.

**The request used two different catalogues.** `engine.solve()` defaults to
`assets=None` → `containers()` with **no db** → the `seed_data` constants. But
the truck lookup read `containers(db)` — the DB, filtered to
`kind == "container" AND status == "checked"`. Two sources in one request, with
two consequences:

- *Functional, and active:* a box the team adds via `POST /api/packaging` and
  then verifies would **never be ranked**. The `packaging` table was decorative
  on the hot path, and the `status == "checked"` filter the last review added to
  `containers(db)` was dead code there. Proven: 16 rows in the DB, 15 rankable.
- *Latent crash:* diverge the other way — flip a row to draft, or load the
  remaining 34 assets — and `next(c for c in containers(db) ...)` raises
  `StopIteration` into an opaque `status="failed"` job with `error=""`.

Fixed by passing `assets=containers(db)` into `solve()` and reusing that one
list for the truck lookup. Pinned twice: every ranked asset must exist in
`containers(db)`, and a DB-only verified box (1150×950×1400) must reach the
ranking *and win* — it takes 11 layers of the wheel at its 120mm pitch, so
**66** beats the stock 48. That second check fails if `solve()` is ever pointed
back at the seed constants.

**Extraction warnings were dropped.** `ExtractionResult.warnings` carries the
reader's own caveats — INCH-declared STEP, non-watertight shell, absurd dims —
and none reached the solve result. A part whose CAD declared inches got a
confident parts-per-box number with nothing attached to it. Now prepended to
`warnings`; the wheel's response correctly carries its 2-solid and
not-watertight notes alongside the 48.

**`extraction.result_json["candidates"]` had no None guard.** Unreachable today
(`create_part` only sets `glb_path` in the `source == "stp"` branch, which
requires `job_id`), but it would surface as `AttributeError: 'NoneType'` rather
than a legible error. Now raises with the part id.

#### `/code-review high` — 9 findings: 7 fixed, 2 rejected on evidence

**HIGH — the app would not start against any existing database.** `Packaging.kind`
landed last session; `create_all` creates tables but never ALTERs them, and
`main.py` seeds at *import* time with a SELECT over every column. Reproduced on
this repo's own dev.db:

```
INTAKE_DATABASE_URL=sqlite:///dev.db venv/bin/python -c "import app.main"
OperationalError: no such column: packaging.kind
```

Same for any docker-compose Postgres volume. The only warning anywhere was a
prose note in a docstring. Fixed with `main._ensure_added_columns` — a narrow
`ALTER TABLE ADD COLUMN` shim driven by a table→column map, run after
`create_all`. Verified on the real dev.db (adds the column, logs it, second
import is a no-op). **Any future column needs an entry there until Alembic
lands**; noted in CLAUDE.md.

**HIGH — a redelivered task silently answered a different question.** Request
params were stashed in `SolveJob.result_json`, then `run_solve` overwrote that
same column with the result. `task_acks_late = True`, so a worker lost
mid-flight *is* redelivered — and on redelivery `params` was `{}`: tare lost
(so `truck: null` plus a bogus "no tare weight" warning), `top_n` back to 2,
`vehicle` `None`. Params now travel as task arguments. The test no longer
supplies them either — it records what the endpoint hands `.delay()` and
replays that, so the endpoint→task contract is itself pinned.

**MEDIUM — `lattice_count`'s weight cap under-counted by 30%.** The cascade
dropped layers, then rows, but never trimmed the long axis:

```
geometry 7x2x1 = 14, cap 10 parts
  cascade -> 7  (7,1,1)
  correct -> 10 (5,2,1)   # exactly 600kg
```

Both satisfy `count == product(grid)`, so the §7 invariant could not see it —
and `test_weight_cap`'s own expectations encoded the under-count (it asserted
`(3,1,1)` where `(2,2,1)` fits). Replaced with a search over the grid, ties
going to the fullest footprint because a full bottom layer is what an insert
is. Ground truth unaffected: 40 and 48 both hold.

**MEDIUM — the truck number depended on `seed_data` ordering.** `rank_catalogue`
sorts on `-count` and is stable, so a tie on parts-per-box left catalogue order
choosing the truck answer — and for the wheel PLS12803 gives 2304 parts/truck
against PLS12103's 1728. Reorder the seed list, headline drops 25%, nothing
else changes. That is this project's own thesis used backwards. Now every ranked
option is scored to the truck and the best wins, with a warning when the
per-box winner and the per-truck winner differ. Pinned, plus a non-vacuity
check that the options really do differ (2304 vs 1728).

**MEDIUM — `confirmed_orientation` was ignored.** The engineer confirms a
resting pose in the viewer; the winning layout can be in a different one, and
`pose_label` was the only clue. Now warns when they differ. Deliberately does
*not* restrict the pose set — whether a confirmation binds the packing pose is
a DOMAIN.md question, not one to answer silently (hard rule 2).

**MEDIUM — every user-added packaging row was a container.** `PackagingIn` had
no `kind`, so `POST /api/packaging` fell through to the model default. Add a
rack, flip it to checked, and `containers(db)` hands it to the nesting engine —
precisely what `models.py` says the column exists to prevent. `kind` is now a
`Literal` field on the schema.

**LOW — a `done` job with nothing in it did not say why.** `best_count: 0` with
no explanation could mean too big, too heavy, or no containers loaded. Now
warns with the container count and the part weight.

**REJECTED — "clearance is never applied between the part and the asset wall."**
True, and deliberate. Mubea ships `190 + 9 × 66.67 = 790.0mm` of parts inside a
790mm inner: exactly zero wall clearance on the stacking axis. Charging even
1mm/side takes that case from **40 to 27**:

```
as shipped         (40, (1, 4, 10))
1mm/side wall      (27, (1, 3, 9))
```

The shipped proposals measure inner height as the height the lattice gets. The
real datum is a DOMAIN.md question; it is not a bug to fix here. Now documented
in `lattice_count`'s docstring so it is not re-raised.

**REJECTED — "a partial fixture directory should degrade, not fail."** Without
the steering wheel this suite has no external ground truth at all, only
snapshots of our own output — and an earlier version printed "all checks
passed" in exactly that state, which is the overclaim its docstring exists to
prevent. Kept loud; docstring now says "empty" skips and "partial" fails.

### 2026-09-09 (fifth) — F2-1, the frontend on the real engine

**`partsPerBox` is gone.** `PackingRecommendation` now calls
`POST /api/parts/{id}/solve` and polls `GET /api/solve-jobs/{id}`. The client no
longer has an opinion about how many parts fit a box.

Acceptance check: `node frontend/src/lib/packing.check.mjs` feeds `layoutToFit` a
real backend layout and asserts **48 / (3,2,8) / 6 pockets** — a shipped TRW
proposal, not a snapshot of our own output. Plus `npm run build` clean and
`grep -rn partsPerBox src/` empty.

#### The adapter is the whole trick

Every shipped proposal is a regular lattice of one part, so a `Layout`'s
`grid` + `pitch_lbh` regenerate the 2D placements the insert-drawing helpers
already wanted:

```js
placements[i*ny + j] = { x: i*pitch[0], y: j*pitch[1], w: extent[0], h: extent[1] }
```

~30 lines in `frontend/src/lib/solve.js`, and `trayGeometry` / `groupInserts` /
`layerSummary` / `InsertIso` / `InsertTray3D` are untouched. Nothing adds
clearance or wall on top of the measured pitch: the wheel's pitch is 377.0
against a 372.0 extent, so the 5mm of real space is already measured from the
geometry. Adding the old 7.5 + 10 back on top walks 48 toward the cuboid answer.

Consequence, decided deliberately: `clearance` / `wall` / `foam` are now
**drawing-only** and relabelled in the rail as such. They size the tray; they
cannot change a count.

`thisSideUp` was **deleted**, not reimplemented. The endpoint has no such
parameter — the solve warns when the winning layout's pose differs from the
confirmed one and deliberately does not restrict. A checkbox that silently does
nothing is worse than no checkbox.

#### Three defects found by running the endpoint over real HTTP

The suite drives route functions directly, so nothing had ever exercised
FastAPI's response serialisation. Doing that found two schema defects and cost
one lesson.

**`BoxDesignOut` silently dropped the custom box's geometry.** It declared six
fields; the worker serialises eight. Pydantic drops unknown keys with no error,
so `extent_lbh` / `pitch_lbh` never reached the client — and the insert drawing
is built from extent + pitch. The custom design was undrawable, and the only way
to draw it would have been the cuboid math this ticket removes.

**A winning custom box could not say which pose it was for.** `engine.solve`
iterates poses, synthesises per pose, keeps the best — and discarded the label.
Every catalogue card reports `pose_label`; the custom card structurally could
not. `BoxDesign.pose_label` added, stamped on the winner via
`dataclasses.replace`.

**`TruckFit` did not name the asset its numbers belonged to.** The worker scores
every ranked option and keeps the best per truck, which is *not* always
`catalogue[0]` — that was last session's fix. But the result only revealed the
winner inside a warning string, so the frontend drew the load plan from
`catalogue[0]`'s outer dims while the numbers belonged to a different box: the
right number under the wrong label, which is the defect class this project
exists to kill. `TruckFit.asset_name` added (`"custom"` for the synthesised
design), and `TruckSection` now keys the header, `floorFit`, `TruckLoadIso` and
the PNG filename off it.

#### The truck test was vacuous and is not any more

`test_solve_api` asserted the truck block picks the best ranked option — but the
wheel's per-box and per-truck winners are the **same box**, so every one of
those assertions passed against a `catalogue[0]` hardcode. New block 2d uses the
`ZZ-TEST-TALL` box already in the suite, which wins per box and loses per truck:

```
fixture really does split per-box and per-truck winners:
    box=ZZ-TEST-TALL truck=PLS12803 {'ZZ-TEST-TALL': 1188, 'PLS12803': 2304}
truck block names the per-truck winner, not catalogue[0]
warning names both the per-box and per-truck winner
```

A non-vacuity guard asserts the two really disagree, so the test cannot quietly
stop testing anything. The 1600mm outer only stacks one high — 18 boxes x 66 =
1188 against 48 x 48 = 2304.

#### Verified live, end to end

Real uvicorn + a real Celery worker, real STEP upload, then the JSON piped
through the actual frontend adapter (`scratchpad/wire_adapter_check.mjs` — a
probe, not a committed test; the fixture check and `test_solve_api` are the
regression contract):

```
  PLS12803        48 parts  grid 3x2x8   layers "8 x 6"
  PLS12103        48 parts  grid 3x2x8   layers "8 x 6"
  PLS12101        42 parts  grid 3x7x2   layers "2 x 21"
  FLC12102        42 parts  grid 3x2x7   layers "7 x 6"
  PLS12801        36 parts  grid 3x2x6   layers "6 x 6"
  custom          48 parts  grid 3x2x8   layers "8 x 6"
  truck -> PLS12803: 48 boxes, 2304 parts, volume-bound
```

#### Calls made

- **`top_n: 5`.** Solve cost is per-pose, not per-asset, so more options are
  nearly free, and the ranked comparison is the core screen. The UI does not
  re-sort — the backend already scored them.
- **Solve on mount + explicit Re-solve.** Never on a keystroke; it is an ~11s
  server job.
- **`runSolve` caps its poll at 160 attempts (~4 min)** and says what a stuck
  job usually means. Found the hard way — see the landmine below. An uncapped
  `for(;;)` also spun silently on a 404, where the body has no `status` and
  neither branch fires.
- **Draft parts get "Save this part first"**, not a crash: the intake tab's
  draft has no `id` until saved. Flagged rather than assumed — worth someone
  confirming that beats hiding the panel.
- **My D1 was wrong and the implementer said so.** I claimed `uniformGrid` /
  `bestFill` / `gridDesc` were private to `partsPerBox`; `floorFit` calls
  `bestFill`, so deleting them breaks `loadPlan` and `LoadCalculator`. Only
  `partsPerBox` was deleted. Right call.

#### New environment landmine: a stale worker eats your job silently

A Celery worker left running from an **earlier session** is still subscribed to
the same redis queue. It consumes your task, fails to find the job id in
whatever database *it* was started with, logs "Job not found" to its own log, and
returns. The API reports `pending` forever with `error: null` and nothing wrong
on your side. Cost 20 minutes. Now `CLAUDE.md` landmine 2, with the fix:
`ps aux | grep celery` before believing a hung job, or run against another redis
db.


---

### 2026-09-09 (sixth) — F2-2, closing the code review on F2-1

`/code-review high` on the F2-1 change set returned **ten findings. All ten
were real** — I verified each against the code before touching anything, and
none was a false positive. That is a worse hit rate than it sounds: F2-1 had
already been through my own integration review, which found three defects the
implementing agent's passing checks missed. Two review passes, thirteen real
defects. The lesson is not "review harder", it is that **a passing check on
the piece you changed says nothing about the seam you changed it across.**

#### The two that were wrong answers, not wrong drawings

**`box_winner` was always `catalogue[0]`.** `options` in `run_solve` is
`[*catalogue, custom]`, so reading the per-box winner off `options[0]` names a
catalogue row even when the custom design beats it — the exact case the UI
badges "beats catalogue". Demote every stocked box to draft except one that
holds a single wheel, and the old code emitted:

    Best per box is ZZ-TEST-TINY, but best per truck is custom (2304 vs 276)

naming a box that holds **one** as the per-box winner over a custom that holds
48, and warning about a split that does not exist. Now `max(options, key=count)`,
and the comparison figure follows the winner instead of being `fits[0]`.

**A volume/weight tie was reported as "volume".** `by_weight < by_volume` puts
equality in the `else`. A load simultaneously at payload and at cube told the
engineer to look for height when height buys nothing. Now "weight and volume",
which the frontend prints verbatim after "bound".

#### The truck drawing was computed by a different algorithm than its number

`TruckSection` called `floorFit` → `bestFill`, which allows mixed-orientation
split strips; `engine.parts_per_truck` uses a uniform fit. So the drawing found
21 boxes per floor where the backend had assumed 18, and
`ceil(truck.boxes / floor.count)` then under-reported the stack. The caption
described a pattern the parts/truck figure was never derived from.

The fix is the same one as the two F2-1 schema drops: **the backend already
computed it, so ship it.** `TruckFit` gained `floor_grid` and `floor_rotated`,
straight out of the expression that produces `truck.boxes`, and the frontend
draws from those. `floorFit` is gone from the component. It stays in
`lib/packing.js` — `LoadCalculator` still uses it via `loadPlan`.

Invariant that keeps them honest, now asserted both sides:
`floor_grid[0] * floor_grid[1] * (cargo_h // outer_h) == boxes`.

#### The insert drawing was unmanufacturable for interleaved parts — and now it is absent

`layoutToFit` spaced pockets at the measured pitch but sized them at the full
part extent. For the wheel that is harmless (pitch 377 > extent 372). For the
**Mubea bar it draws pockets overlapping by 160mm** (pitch 140, extent 300) —
an impossible tray next to a correct count of 40.

`layoutToFit` now flags `interleaved` when in-plane pitch is under the extent
(Z excluded: `pitch[2] < extent[2]` is ordinary nesting depth), and the panel
renders a note instead of a tray. **I deliberately did not invent a comb/slot
drawing** — we have no real proposal drawing to check one against, and a
plausible-looking wrong tray is worse than an honest gap.

Running it live surfaced the consequence, which is bigger than the defect:

    Mubea bar, every ranked option:  INTERLEAVED — no tray drawn
    TRW wheel, PLS12101:             INTERLEAVED — 19mm overlap, wheels on edge

So for the founding part class **the UI now shows no insert drawing at all**,
where before it showed a wrong one. That is the right trade, but it moves the
interleaved-tray drawing from "someday" to the top of the queue — see `## Next`.
The wheel case was a surprise: stood on edge in "Alternative 2" the wheels dish
into each other by 19mm, so this is not a long-parts-only problem.

#### Draft boxes were dropped in silence

`containers()` filters `status == "checked"`; `POST /api/packaging` hard-codes
`status="draft"`, which is what the UI's "+ Custom box" button creates. An
engineer adds the box the customer actually stocks, re-solves, and sees no
card, no warning, no reason why. Unverified dims should not be ranked — being
dropped without a word is the defect. `catalogue.excluded_drafts()` sits next
to the filter it mirrors, and the worker names them.

#### Smaller

- **`np.take(grid, range(...))` is fancy indexing** — it copied the whole
  ~760k-cell grid twice per probe, ~1500 probes per axis. Basic slicing returns
  views. Same answers (`test_nesting` unchanged), less of the 2.4s/pose.
- **A 422 rendered as `⚠ [object Object]`** — FastAPI puts a *list of dicts* in
  `detail`. `errorDetail()` handles both shapes, at both call sites.
- **Every mount enqueued an uncancellable solve, doubled by StrictMode** —
  clicking through five parts queued ten ~11s voxelisation jobs. `AbortController`
  now, wired into both fetches and the inter-poll wait.
- **`test_cad_import` printed "all checks passed" with drift baselines
  unmeasured** — a missing fixture was `print SKIP; continue`, which is exactly
  what the module docstring promises it will not do. Now `check(False, ...)`;
  verified by injecting a nonexistent baseline (rc=1), not by assuming.

#### Two of my own checks were vacuous before they were useful

Worth recording because it is the third session running that this has bitten.

My `runSolve` abort check stubbed `fetch` and discriminated on `'/solve'` — but
the status URL is `/api/solve-jobs/{id}`, which *contains* `/solve`. So every
poll got the POST body back, the poll counter stayed at 0, and
"abort stops polling" passed **without a single poll ever happening.** Fixed to
match `/solve-jobs/`, plus a `pollsAtAbort > 0` guard.

Then my `removeEventListener('abort', abort)` passed a different function than
the anonymous wrapper actually registered, so it removed nothing.

The pattern in all three sessions: *the assertion is about the thing I built,
and the stub/fixture around it is where the bug hides.* Guarding non-vacuity
explicitly — assert the fixture really exhibits the condition — is the only
thing that has caught these.

#### The ticket was wrong again, and the agent said so

F2-2 told the `frontend` agent to reuse "the existing stale-params affordance".
There is no such thing — it grepped `stale|outdated|dirty|mismatch|changed`
across `src/` and got nothing, then built a minimal one and flagged that it was
new code rather than a reuse. Same as F2-1's D1, where I claimed three helpers
were private to `partsPerBox` and `floorFit` in fact calls `bestFill`.
**Two tickets, two invented facts about code I had read.** Worth stating plainly
in the next ticket: verify my claims about existing code before implementing them.

#### Verified

All seven backend suites pass; ground truth **40 / 48** unchanged. Frontend:
four check blocks and a clean build.

    packing.check.mjs: 48 / 3,2,8 / 6 pockets
    packing.check.mjs: interleave 40 / 1,4,10 / interleaved: true
    packing.check.mjs: 422 detail readable, abort stops polling (1 poll, none after)
    packing.check.mjs: truck floor plan 8 x 3/floor x 2 = 48 boxes

Live through real uvicorn + Celery + HTTP, both ground-truth parts, payload
piped into the real `layoutToFit` / `floorPlanFromTruck`:

    TRW wheel   PLS12803 48 (3x2x8) 6 pockets  ...  PLS12101 48 INTERLEAVED
                truck -> PLS12803: 48 boxes, 2304 parts, volume-bound
                drawing: 8 x 3/floor x 2 layers = 48 slots for 48 boxes
    Mubea bar   PLS12103 60, FLC12102 60, PLS12803 48 — all INTERLEAVED
                truck -> PLS12103: 27 boxes, 1620 parts, weight-bound
                drawing: 9 x 2/floor x 2 layers, rotated true (27 of 36 drawn)

`floor_rotated: true` and a partially filled top layer both got exercised live.

#### Calls made

- **Interleaved trays: a note, not a guessed drawing.** Needs a reference
  drawing from the packaging engineers, so it is their input, not my invention.
- **Drafts stay unranked, but named.** Ranking unconfirmed dimensions is worse
  than excluding them; silence was the bug.
- **`floorPlanFromTruck` moved from the JSX into `lib/solve.js`** — it is pure
  math, and in the component node could not reach it, which is precisely why it
  had no check.
- **The 200kg `tare_kg` cap left alone.** A steel returnable's tare can
  plausibly exceed it; that is a domain question, in `PROGRESS.md ## Open` now.
- Left the user's three August Celery workers (pid 10375/10381/10382) running
  and worked on redis db 9 / port 8011 instead. Mine are torn down.

### 2026-09-09 (seventh) — the browser, the UI rebuild, and the insert BOM

Rahul opened the UI in a browser for the first time. **Item 4 of the last
entry's Next list said "This one genuinely needs a human — it is not something
I can do." He overrode that in two words: "open in browser."** I brought up
uvicorn + Celery + Vite on redis db 9, preloaded both ground-truth parts
through the real API, and opened it. Six defects came back in one message, and
every one was real. Nothing in seven sessions of green checks had caught any of
them.

#### What a browser found that 20+ automated checks did not

1. **Save & calculate did nothing visible.** Root cause was not the button: no
   `id` ever reached the results panel, so the solve had nothing to solve. The
   panel had never been rendered with a saved part in the whole of Phase 2.
2. **No pending state** on a 10-second solve. It read as a dead button.
3. **Horizontal scroll in every form.** Grid and flex items default to
   `min-width: auto` (min-content) and a bare `<input>` carries ~180px of
   intrinsic width from `size=20`. Fixed at `.field` scope, not per-grid —
   the same bug had been about to be fixed three times in three places.
4. **"UI is still the old one and seems very basic"** — the documented design
   system (Fira Sans / Fira Code, `#1E40AF`, `#D97706`, `#F8FAFC`, 8px rhythm)
   had been written down in CLAUDE.md and never implemented.
5. **No way to choose a box.** The engine ranked all 15 and the engineer could
   not say "price me a PLS12801".
6. **"The whole screen contains so much content that it seems very confusing."**

UI-1, UI-2 and UI-4 closed all six. The lesson is not "test in a browser"; it
is that **I asserted the rendering could not be verified by me, and it could.**

#### UI-2: named boxes rank regardless of draft status

`assets: ["PLS12801"]` on the solve. One injection point — `containers(db)` in
the worker — so `containers_named(db, codes)` sits next to it and queries by
`item_code IN codes` **without** the `status == "checked"` filter. That filter
exists to stop unverified dims being ranked *silently*; a box the caller named
explicitly is not silent. The blanket "N unverified box(es) not ranked" warning
is skipped entirely when specific boxes are named — it would list drafts the
caller never asked about — and replaced with two scoped warnings: codes not
found, and named boxes ranked with unconfirmed dims.

#### P4-1: the insert BOM, and the claim that was wrong

The last entry said the interleaved insert drawing was **"blocked on a
reference drawing from the packaging engineers"** and listed it under `## Open`
as their input.

**That was wrong, and it had been wrong for two sessions.** `ground_truth.py`
already carried the shipped dunnage BOM for both proposals, and PLANNING §7
names the six Mubea elements and says outright that the BOM is "generatable,
not free text". I had read that file. Nothing was blocked.

`app/dunnage.py` now generates it, and `tests/test_dunnage.py` regenerates both
shipped BOMs from their own measured lattice — 52 element-level checks, wired
into `ground_truth.py.__main__`, at the same status as the 40/48 counts.

**Two archetypes are one rule.** The Mubea and TRW decks look like different
systems and are not — they are the same predicate answered differently:

    parts interleave in plane  ->  layer bars + separators   (Mubea)
    they do not                ->  pocket tray + sheets      (TRW)

**What is *not* derived is the point of the module.** Every element carries a
`basis` of `derived` / `pattern` / `unknown` and an `unknown` list naming each
gap, and anything with no basis ships as `None` with "needs deck" rather than a
plausible number. The MS Rod is the worked example: the shipped rod is d8×805,
805 matches no dimension we hold (nearest is the 800mm outer breadth, off by 5)
and its qty of 6 follows from nothing we measure — so it ships blank on all
three fields. Bar widths (60/70) ship as `?`. Foam densities and GSM are
flagged. Only the top separator is `pattern`: a 50mm inset and 30mm thickness
from one sample, and 30mm is not even in the §6 sheet list.

#### The height budget, and two corrections of my own arithmetic

Ticket P4-1 gave the bottom separator's thickness as
`inner_H - layers * pitch_H` (790 − 10×66.67 = 123.33 → 123, matching the
deck). **Wrong formula, right answer by coincidence** — it only matches because
the Mubea stack fills the box exactly. The correct expression is the vertical
nest depth, `extent_H - pitch_H`: the depth each part sinks into the row below.
The bottom row has nothing to nest into, so the separator provides that depth
and contributes **zero net stack height**. That is why a 732mm stack in a 790mm
box still carries 75mm of bottom separator without overflowing.

**Then my proof of the correction was itself wrong, and the agent said so with
the arithmetic.** I claimed 732 + 133 = 865 overflows 790. That charges the
separator as fully additive, contradicting my own explanation that the bottom
row sinks into it: `gross = (T - sink) + stack = 58 + 732 = 790`, exactly the
inner height. `inner_H - layers*pitch_H` is *defined* as the thickness that
makes the build exactly fill, so it can never overflow. The real objection is
that it manufactures 58mm of dead foam and makes a part-and-pose property
depend on which asset was picked. Formula choice right, reasoning wrong — and
the consequence is that **the fit assertion I asked for cannot discriminate the
two formulas**, so it does not appear in the test.

#### A defect P4-1 surfaced and did not silence

`synthesis.synthesise` sizes the custom box's inner height to exactly the parts
stack, so the wheel's custom box now reports `fits: False` — build 990.0 into
inner 987.0. The 3mm sheet under the bottom tray has nowhere to go. Physically
there is room (`BASE_SEPARATOR_MM = 35` goes on the *outer* height), so this is
a datum question, not a count bug. Left reporting honestly. In `## Open`.

#### The packing animation: "make this a packing enginner, not just a lame insert designer"

Asked for the level of detail the proposal decks show — foams, cuts — and
suggested a GIF, with Gemini's API key offered for it.

**I did not use Gemini for the animation, and that was the whole call.** A
generative model would render plausible geometry that came from no lattice —
the exact fabrication this engine exists to replace. Instead the animation is a
**voxel union of the real customer mesh** in its confirmed resting pose, at the
engine's own lattice positions. Because it is real occupancy rather than an
extruded silhouette, the vertical interleave shows as one interlocked mass,
which is the only thing the animation exists to show.

Four bugs, each caught by looking at the output:

- `simplify_quadric_decimation` needs `fast_simplification`, absent — and 42014
  faces × 36 copies was never going to work. Abandoned meshes for voxel rasters.
- Dunnage painted **in front of** the parts: one depth key per cuboid meant the
  bottom separator covered the entire box. Fixed by rasterising dunnage into the
  **same** voxel volume as the parts, so one far→near ordering governs
  everything and no z-buffer is needed.
- **Bars were 90° out.** The BOM says the bar's length is the asset inner
  breadth (750), so bars run *across* the breadth, spaced along the length. Only
  that orientation reconciles with the deck's 20 side bars and 11 centre bars.
- Opaque bars walled the parts off completely. Per-face RGBA at alpha 0.26 —
  one collection, not two, so far→near still composites.

Then, on "can we make the gif a bit 3d" and "slow it down" plus a request for a
component breakdown: isometric projection (camera at +1,+1,+1, depth = x+y+z,
camera-facing faces only), ~1s per step, and shading by face normal — per-voxel
edges read as graph paper at 96×63 cells, and `edgecolors="none"` leaves
antialiasing seams, so the edge takes the face colour.

**The exploded component breakdown takes its every dimension, qty, spec and
basis from `dunnage.bom()` rather than retyped constants.** That is not tidiness:
wiring it to the BOM immediately caught the animation drawing **8 centre bars
where the BOM says 10** — they go one per layer *boundary*, `layers + 1`, so
there is one above the top row. The picture had looked entirely plausible. The
script now asserts its own drawn counts against the BOM's `qty` and refuses to
render if they disagree: parts 36/36, side bars 18/18, centre bars 10/10.

#### Gemini

The key works (`x-goog-api-key`, 40 models including `gemini-3.1-pro-preview`,
image models, Veo). One legitimate use: proposal prose over our own numbers,
which PLANNING line 14 already scopes. One needing Rahul's explicit
go-ahead: reading the NDA decks, which the fixtures' own README forbids
uploading. One where it would actively hurt: the animation.
**Both tokens passed through a chat transcript and the live one should be
rotated.** In `.env`, gitignored.

#### Verified

All seven backend suites pass, ground truth **40 / 48** unchanged, plus the new
`test_dunnage` — 52 element checks reproducing both shipped BOMs. Two of those
suites had to be re-run through `sed 's/\x1b\[[0-9;]*m//g'` because `tail -1`
was showing nothing but an ANSI colour reset.

Live over real HTTP, box picker included: `assets: ["PLS12801"]` on the Mubea
bar returns exactly that card — 36 parts, grid 1×4×9, pitch (1097, 145, 73).
That is the layout whose "pitch 145 < part width 300" note started the session.

#### Calls made

- **No generative model in the geometry path.** A GIF that looks right and came
  from no lattice is worse than no GIF.
- **Named boxes rank even as drafts.** The checked filter stops *silent*
  ranking of unverified dims; naming a box is not silent.
- **Bottom separator = nest depth, not leftover height.** A part-and-pose
  property must not depend on which asset was picked.
- **The insert drawing renders in the Celery task, not on request.** The worker
  already holds the mesh and the BOM, so it writes the PNG and ships a
  `drawing_url` — no new endpoint, no cache invalidation, heavy geometry stays
  in the worker (hard rule 7), and the drawing cannot drift from the layout it
  shipped with (hard rule 9).
- **Exploded PNG in-app, GIF not yet.** 15× the bytes and it needs a player.
- **`PROGRESS.md` says the interleaved drawing was never blocked.** Correcting
  the record matters more than the two sessions it cost.

---

## 2026-09-10 — The decks arrive, and they validate the engine

Four proposal decks and one xlsx are now on the machine. The headline: **the
decks did not correct the tool, they confirmed it.** Every derivable number in
the Mubea slide-4 dunnage table matches what `dunnage.bom()` computes from the
measured lattice — size *and* quantity, including the qty 11 and 20 that the
first prototype drew as 8 and 18. That bug was only ever visible because the
drawing reads `dunnage.bom()` instead of retyped constants.

The 60 mm and 70 mm bar widths, carried for two sessions as admitted draw-only
inventions and deliberately kept out of every label, turn out to be exactly what
shipped.

#### The 36 that should have been 40

The project's one headline metric was wrong at shipped defaults and no test
caught it. `DEFAULT_CLEARANCE_MM = 5.0` was charged on all three axes, so
between-layer air was added where the layers physically rest on the EVA bars the
BOM already counts: `148 + 9×68 = 760 ≤ 790` gives 10 layers and 40 parts;
`148 + 9×73 = 805 > 790` gives 9 and 36.

Two checks should have caught it and both were rigged:

- `ground_truth.py.evaluate()` is fed a **stub engine**
  (`lambda lbh, a: {(1085,190,285): 40, ...}`), so it only ever verified the
  fixture arithmetic. The real nesting engine was never on the hook for 40.
- `test_nesting.test_real_bar` reached 40 **by hand-passing
  `clearance_mm=0.0`** — a test proving the engine *could* hit 40 while shipped
  defaults returned 36. The same trap CLAUDE.md warns about with fixed
  orientations.

Fix: `DEFAULT_STACK_CLEARANCE_MM = 0.0` separate from the in-plane 5 mm. One
constant was serving two different physical quantities. `min_pitch` stays
axis-agnostic; the caller picks the clearance. New `tests/test_clearance.py` is
the file `ground_truth.py` was always supposed to be — real CAD, real engine,
**defaults only**, and it reproduces 40 and 48 end to end for the first time.

#### Per-asset tare changes the truck answer

`Packaging.tare_kg` / `.material`, populated from the SCS report for 15 assets.
The previous entry's open question — "tare inverts the Mubea truck ranking
(1560/1625)" — is superseded: 1625 came from assuming 30 kg for both boxes.
With PLS12103's real 39 kg it is an exact tie.

```
PLS12801 (what shipped)   40/box, 30kg tare  ->  39 boxes/32ft  =  1560 parts
PLS12103 (best on count)  65/box, 39kg tare  ->  24 boxes/32ft  =  1560 parts
```

**A +62% gain per box is +0% per truck.** `test_catalogue` asserts the tie and
that PLS12801 never ranks *below* — the tie is the stronger finding, so the
assertion is `>=`, not `==`.

#### Two seeding defects of the same shape

1. **The tare column was added and never populated.** `create_all` cannot
   ALTER, so `main._ensure_added_columns` adds the column — but
   `seed_master_data` only inserted on an *empty* table, so every existing
   dev.db and compose volume kept NULL forever. Tare reads as unknown, the truck
   tier silently reports nothing, and it looks like the feature is broken. The
   agent's own testing did `rm -f dev.db` first, which is why it looked clean.
2. **Then the identical thing happened to whole assets.** PLS1280 and PLS12804
   were appended to `PACKAGING` and never appeared on any non-empty database —
   a *ground-truth asset* simply missing from the catalogue, nothing in the
   logs. Every check in `test_catalogue` builds a fresh in-memory DB, so the
   empty-table path is the only one they ever ran.

`seed_master_data` is now per-code: insert what is absent, fill NULLs on what is
present, never overwrite (a set value is either seeded or a user edit, and
neither is ours to clobber). `test_catalogue` check 7 seeds onto a non-empty
table and was verified to **fail** under the old rule.

#### A SolidWorks upload gave the wrong error

Bharat Forge ships `.SLDPRT`. Hard rule 5 says these fail loudly with guidance,
and `extract_dimensions` did — but the gate lived only there. `extract_part`,
**the function upload actually calls**, fed the binary to cascadio, failed,
retried the OCP fallback, failed again, and surfaced
`OCP could not read STEP file: 1.SLDPRT` — which reads as our bug rather than
"this format has no reader, ask for a STEP export".

`test_cad_import` passed anyway: it accepted any message containing
`"step"`, and *"could not read **STEP** file"* contains it. The guard now lives
in `extract_part` where all callers route through, the duplicate is deleted, and
the check requires the guidance itself (`"unsupported cad format"` **and**
`"export"`) from **both** entry points.

#### Every insert drawing in `test_solve_api` was failing silently

Found while chasing a false FAIL in a sweep script. `worker.render` writes the
PNG without creating its parent directory, and the surrounding `except
Exception` swallows render failures **on purpose** — a drawing must never fail a
solve. Together those mean a missing storage directory makes *every* drawing
vanish with nothing in `warnings` and nothing wrong on the caller's side. It
only ever showed as a silent no-op because upload happens to create the
directory first in production.

`test_solve_api` did not notice because it never mentioned `drawing_url` — the
exact failure hard rule 9 describes ("check it over real HTTP, because direct
route-function tests never exercise serialisation"). It now asserts every ranked
layout ships a `drawing_url` **and** that the PNG behind it is on disk. Before
the one-line `mkdir(parents=True, exist_ok=True)` that check reads 0/2.

#### Four new ground-truth cases

`TP_Tata Autocomp _ 1574 & 1578` ships four cases *with their STEP files* —
tripling the validation set. `PLS12804` (inner 1150×750×580, outer
1200×800×790, 25 kg) is seeded; like PLS1280 it appears in no other source.

| Case | Asset | Deck | Matrix |
|---|---|---|---|
| X104 DSL Intercooler | PLS12801 | 60 | 1×12, 5 inserts |
| Nexon EV Radiator | PLS12804 | 18 | 9×1, 2 inserts |
| P118 Radiator | PLS12804 | 10 | 10×1, 1 insert |
| P125/P126 Intercooler | PLS12801 | 65 | 12×1(1), 5 inserts |

Two things came out of running the real engine on all four STEP files
(41-511s each; the 46MB P118 assembly is the slowest CAD we have touched).

**The extraction pipeline is right on four files it had never seen.** CAD
against the decks' stated part dimensions, all four within 3mm:

```
deck 668x128x90     cad 667.6x127.8x90.1
deck 714x122x246    cad 711.1x245.3x122.4
deck 681x98x457     cad 680.9x457.3x99.2
deck 625x130x89.7   cad 624.6x129.5x89.7
```

**The counts disagree, and the reason is pose, not arithmetic.** Fed each
deck's own pocket pitch, `lattice_count` reproduces every shipped number and
every matrix exactly — 60 as 12x1x5, 18 as 9x1x2, 10 as 10x1x1, and P125/P126's
60 of its 65. Left to choose freely the engine picks a different pose:

| Case | Engine (free pose) | Deck | Engine's grid |
|---|---|---|---|
| X104 intercooler | **64** | 60 | (8,1,8) pitch (137, 673, 96) |
| Nexon EV radiator | **18** | 18 | (9,1,2) pitch (125, 717, 244) |
| P118 radiator | **12** | 10 | (2,1,6) pitch (465, 689, 92) |
| P125/P126 intercooler | **64** | 65 | (8,1,8) pitch (137, 629, 92) |

Every one of these decks says *"component will be placed in Vertical
orientation"*; the engine lays these parts flat, gets a smaller vertical pitch
and buys layers. So on two cases it claims **more** than shipped (+4 and +2) and
on one it falls one short. **The open question is whether that flat pose is
available at all** — the same decks also say "TATA logo to be printed on the
assets", so the orientation may be a customer requirement rather than an
optimum. Until that is answered the engine must not be tuned to these four
numbers: matching a pose somebody else chose is the same flattery CLAUDE.md
warns about with fixed orientations, pointed the other way.

New `tests/test_tata.py` pins the half that is settled (the arithmetic, from the
decks' pockets) and records the half that is not. It is wired into
`ground_truth.py`'s sweep. It is deliberately *not* in `ground_truth.CASES`:
all four are near-cuboid heat exchangers whose shipped counts sit **below** the
cuboid baseline, because a PP-flute insert has walls — every pocket in these
decks is 5-25mm larger than the part. `_selftest` asserts
`baseline < achieved` precisely to catch a wrong baseline, and that guard should
not be weakened to admit a different species of case. The file's own
non-vacuity check measures the pocket-wall effect: part-tight lattices give
72/60, 18/18, 11/10 and 72/65.

**P125/P126 is not a regular lattice.** Its deck reads matrix `12x1(1)`,
parts/insert 13 — twelve in a row plus one more, five times over. A lattice
gives 60 and the deck ships 65. The five extras are real and cannot be placed
from the deck text: 12 x 90.4 = 1085 leaves 65mm across, too little for an
89.7mm part, but five layers at 135mm leave 115mm of height, enough for one
lying flat. Recorded as a gap in what the engine models (regular lattices only),
not absorbed into a fudged pitch.

Bharat Forge (suspension arm D4927/D4926, 65/PLS12801, 1×13) is **blocked on a
STEP export** — SolidWorks native is all we have. Its deck is `R5_After Trial`,
so it also documents a *revised* design, which is the second such example.

#### Source conflicts, reported not resolved

PLS12801's **outer** height now has five answers: 960 (BFL), 980 (Tata), 986
(seed), 990 (SCS), 1000 (Mubea/TRW). Its **inner** height is 790 in every single
source. Nesting only reads inner, so this conflict is cosmetic until the truck
tier stacks boxes. User: *"Don't focus on the PLS12801 outer height too much,
correct data will be provided to you."* Also open: six CRT crates rated 20 kg
vs 14, and FSC inner H 580 vs 410 (the Tata deck's PLS12804 independently says
580).

The pallet tier is **not** unblocked by the SCS report: `Can Be Palletized` is 1
for only 7 of 49 rows and pallet L/B/H read 0 for all 49.

#### The exploded render

Slide 4 of the TRW deck is the visual target — user: *"that's the direction to
take"*. The render now composites translucent dunnage over opaque parts in a
single far→near ordering across concatenated volumes (alpha needs strictly
back-to-front; painting one volume after another does not give it), draws the
box as a wireframe with a base slab, and calls out `inner H 1000 mm /
build 978 mm FITS` on the box edge. Drawn counts come off the **lattice**, never
`element.qty`, and `_check_count_bites` bumps a BOM qty by 2 and requires the
render to refuse — because the obvious implementation, `range(element.qty)`,
makes the count assertion vacuous.

Parts were recoloured warm against cool dunnage. Blue parts inside a blue tray
read as one mass however good the alpha is, which defeats drawing them
translucent at all. Still open: graph-paper crosshatch on translucent faces
(restroking a 26%-alpha face darkens the cell boundaries) and the leader angles.

#### The review CLAUDE.md has demanded since Phase 2

`/code-review` was never run on any of the eleven tickets because **the command
does not exist** — nothing in `.claude/commands/`. CLAUDE.md has listed it as
step 4 of the development cycle with nothing behind it. Ran it properly instead,
three scoped reviewers over 19 modified files and 21 untracked ones.

Six confirmed defects, all fixed:

| Defect | Why it survived |
|---|---|
| `LoadCalculator.jsx` threw the raw FastAPI `detail` — a **third** `[object Object]` site the F2-2 fix never reached | the `errorDetail` docstring claimed "both call sites"; there were three |
| **Hard rule 1 violated**: `setScalar(1000)` in two files, so a unit fix in `glbModel.js` would silently miss the orientation viewer | CLAUDE.md claims "a contract test enforces this". There was no such test. |
| `truck.tare_kg` shipped and was never rendered — the number that decides the ranking, and it can differ from what the user typed | added by C-TARE for the frontend, never wired to it |
| **Hard rule 9, fourth instance**: `PackagingOut` declared neither `tare_kg` nor `material`, so `GET /api/packaging` reported no tare *at all* — absent, not blank | no test for that endpoint existed in any form |
| `assets: []` validated, failed the worker's `if requested:`, and fell through to the unrestricted catalogue — "rank none" silently became "rank all" | only `["PLS12803"]` and `None` were ever tested. The frontend has a comment saying "never send an empty array" — someone coded around it instead of fixing it. |
| `catalogue.py` told the reader to **drop dev.db** for a column `_ensure_added_columns` has ALTERed automatically since C-TARE | the comment predated the shim and was never revisited |

Plus one misleading message: a real row of the wrong kind was reported as
`not found`, sending an engineer hunting a typo that does not exist. Now reads
`RACK-01 (exists, but kind=rack)`.

Two new checks close the holes the defects came through. `scale.check.mjs` is
the rule-1 contract test that was claimed but absent, verified by reintroducing
the bare literal and watching it fail. `test_catalogue` check 8 asserts
**serialisation**, not the ORM — reading `row.tare_kg` off the model proves
nothing about what leaves over HTTP, which is exactly how rule 9 has now been
broken four times.

The frontend reviewer also verified its own checks are not tautological by
mutating `bom.js` and confirming `bom.check.mjs` fails. That habit is worth
keeping: three of this project's worst bugs hid behind green checks.

#### Housekeeping

Both `G-DRAW2` and `E-DECK` died mid-edit on a session-wide rate limit; two
background commands were killed for low memory. Nothing was corrupted — all six
touched files parsed and every suite passed — but `dunnage.py` carried a
docstring claiming the MS Rod was filled in while the element still emitted
`None`. Finished by hand rather than re-dispatched into the same limit.

The three Celery workers from 18 Aug are still running pre-change geometry code
(landmine 2). **Killing them was blocked by the permission classifier** — it
needs `! kill 10375 10381 10382` from the user.

#### Calls made

- **An element is only as trustworthy as its weakest number.** The bars' length
  and height are derived, but the 60/70 mm width came off a deck, so they read
  `basis="pattern"`, not `derived`. A "DERIVED" chip on an element containing a
  number we cannot re-derive is exactly the overstatement `basis` exists to
  prevent.
- **The MS Rod check was inverted, not deleted.** It used to assert the rod's
  size is *never emitted*, which was right when we had no source. It now asserts
  the rod is `pattern` and **never** `derived`, so a different part in the same
  box cannot inherit d8×805 qty 6 by implication. 805 mm matches no dimension we
  hold (nearest is the 800 mm outer breadth, off by 5).
- **The guard goes where the callers route through.** Hoisting the format gate
  into `extract_part` is a smaller diff than adding it to each entry point, and
  patching only the path the report named would have left upload broken.
- **A memory file for shared data.** Repeatedly asking for files already on disk
  is the failure the user named. `~/.claude/.../memory/shared-test-files.md`
  lists every deck, CAD file and report, what each is for, and that the Bharat
  Forge parts need a STEP export.

#### The geometry review, and the three defects I introduced myself

The geometry reviewer was the last of the three to land and by far the worst:
nine confirmed, three plausible. **Three of the nine are damage from this very
session** -- which is the argument for step 4 of the cycle, made against me.

The clearest is the MS Rod. E-DECK filled its size in from the deck, and that
made `insert_drawing`'s "every element with a size and a qty must be drawn"
warning fire on an element that is label-only by design. So the self-check has
been exiting 1 -- and `assert not catch.msgs` sits *before* `_check_count_bites`,
so **the non-vacuity probe added to prove the count check bites has not run
since the moment it was written.** A check that cannot be reached is worth
exactly as much as a check that cannot fail. The exemption is now a declared
one-entry map, not a blanket "undrawn elements are fine": an undrawn
`Corner Angle` still warns, and the self-check proves it.

Same shape for the bar width. Threading `centre_bar_w_mm` through the BOM made
the drawing's own copy of the width stale, so a non-default width printed one
number and drew another. The drawing now reads the width off the BOM element it
is drawing -- one expression, one number, hard rule 9 pointed inward.

And the parts assertion counted loop iterations, not voxels, so it passed
whether or not anything landed in the volume. It now counts occupied cells read
back out of the volume, and both failure modes are proved: a part that occupies
no voxel at a 12mm cell, and occupancy placed outside the volume ("rasterised
0 of 40 part instances").

#### Coplanar bars: two bars billed for one bar of height

`bom()` **sums** `net_height_mm` over the elements, so any single element's
value is only correct relative to what every other element claims. The centre
bar and the two side bars share a layer boundary -- same height, side by side
across the breadth -- and each charged its own `max(0, bar_h - nest_depth)`.
Two bars of height for one bar of geometry.

It survived because it is invisible on both shipped cases: Mubea's `bar_h` (68)
is under its nest depth (80), so both terms are zero, and TRW is the
pocket-tray archetype with no bars at all. It bit only the poses with little or
no vertical interleave -- exactly where it reported DOES NOT FIT for a stack
that fits. The synthetic zero-interleave case went from build 1110 to 1050.

The right total is one bar, the **topmost**: interior bars lie inside the pitch
by construction (`bar_h = floor(pitch_H)`), so the parts stack has already paid
for them. The two candidate formulas turn out to be the same expression --

        layers*pitch_H + bar_h - stack  ==  bar_h - nest_depth

with `stack = ext_H + (layers-1)*pitch_H` -- which is why the fix is to charge
the bar contribution **once** rather than to change the formula. Mubea's build
height is unchanged at 790.0/790, so this was provably free on the one fact
that drives everything.

Three checks guard it, and the third matters most: the side bar must charge no
height **and still have one**. `net_height_mm == 0` alone would pass for a side
bar that had lost its geometry entirely. Reintroducing the double-count fails
two of the three.

#### The diagonal the collision check never looked at

`min_pitch` is a sliding-window probe: every multiple along one axis must be
clear. That is correct, and it is also only 3 of the lattice's directions. A 3D
lattice contains `(i*px, j*py, k*pz)`, and nothing checked the mixed vectors --
reachable whenever two axes have `pitch < extent`, which is the Mubea
archetype, i.e. the whole reason this project exists.

I deliberately scoped this as **measurement, not enforcement**, and the
measurement is why. Ticket R3, at 4/2/1mm voxels on both shipped designs:

| case | mixed vectors | worst overlap 4mm | 2mm | 1mm |
|---|---|---|---|---|
| YXA bar, 40/PLS12801 | 54 | 10 cells, 0.061%, 640 mm3 | 0 | 0 |
| QY2i wheel, 48/PLS1280 | 102 | 0 | 0 | 0 |

The wheel is *structurally* immune, not lucky: its pitch >= extent on both floor
axes, so it interleaves on one axis only and no mixed vector is reachable. The
bar has exactly one offending vector out of 54, and probed at its raw touching
pitch the cell count stays flat (28 -> 29 -> 10) while the volume collapses
1792 -> 232 -> 10 mm3, ~8x per halving. **That is the signature of two surfaces
touching along a contact patch inflated outward by voxelisation, converging to
zero measure.** Real interpenetration holds its mm3. The overlap is where
`occupancy`'s outward rounding puts skin that isn't there.

So: had I let the reviewer's instinct stand and tightened `min_pitch`, the cost
is measured -- grow the y pitch to clear it and the bar drops to 30, grow z
instead and it drops to 32. **Against the 40 that ships.** Eight parts a
shipment, in the expensive direction, surrendered for 640 mm3 of voxel skin.
Ground truth is not a target to be satisfied when convenient; it is the thing
that told us the check was wrong.

The defect is real all the same, and R3 proved it by **pigeonhole** rather than
by argument: two 1-cell blocks on the plan diagonal, built as a numpy grid so
there is no rounding to blame, have both per-axis probes genuinely clear while
`(1,1,0)` collides on half the part. `lattice_count` returns 9 in a (3,3,1)
grid -- 9 parts x 2 cells = 18 cells inside a 16-cell region. The honest answer
is 6. Minimality is asserted too: a 1-cell part has `pitch == extent` on every
axis, so no mixed vector is reachable at all.

`tests/test_lattice_vectors.py` records all of it, including the artefact
conclusion as an assertion (overlap volume must shrink with the voxel). Nothing
in `nesting.py` changed. The recommendation on file, which I am taking, is that
a mixed-vector rule should be a **tolerance** (the shipped designs sit at
0.061%, the synthetic collision at 50% -- three orders of magnitude of daylight)
and that it should not be built before `DOMAIN.md`, because the real weakness is
that a 4mm occupancy model cannot tell 640 mm3 of contact from 640 mm3 of
steel. `nesting.py` already concedes that 4mm landing on the deck is luck: 2mm
predicts 11 layers where 10 ship. Bolting a purity check onto an uncalibrated
quantisation model produces false rejections at exactly the interleave ratios
this project exists to find.

#### The contract test CLAUDE.md claimed for three phases

Hard rule 1 says "a contract test enforces this". It did not exist. Writing it
found that there are **three** scaling sites in two languages, not the two the
rule describes: `step_fallback.py` applies x0.001 to make the GLB metric in the
first place, so it must be the exact reciprocal of `geometry.py`'s x1000. If
those two drifted, the cascadio path and the OCP fallback would disagree with
*each other* and only one would match the frontend -- invisible to any test
that looks at one language. `tests/test_scale_contract.py` asserts all three
plus the reciprocal; a one-digit drift trips three assertions.

Also removed `clearance_mm=0.0` from `test_nesting.test_real_bar`. That was the
override that let a test reach 40 while the shipped product returned 36 -- a
test proving the engine *could* hit the ground truth while the product did not.
E-CLEAR fixed the engine and the override became dead weight. At defaults the
test now reports 40 in (1, 4, 10), agreeing with `test_clearance`.

#### G-UNIT: the docstring was right, the reporting was lying

`iges_to_glb` asserted that OpenCascade honours the IGES global-section unit
flag and normalises to mm, and `geometry.py` hardcoded `"mm"` for every IGES on
the strength of that one sentence. Two very different things could have been
true: the claim is right and only our reporting lies, or the claim is wrong and
every inch-authored IGES has been out by 25.4x in silence. **Nobody had ever
run the non-mm path** -- all four IGES fixtures are mm, and IGES is 4 of our 5
real customer files.

Settled empirically rather than from documentation. OCP writes a cube that is
1x1x1 *in the file's own declared units*, twice, and our own `extract_part`
reads both back:

| file declares | global-section max coord | `extract_part` |
|---|---|---|
| MM (flag 2, UnitValue 1.0) | 1.0 | **(1.0, 1.0, 1.0) mm** |
| INCH (flag 1, UnitValue 25.4) | 1.0 | **(25.4, 25.4, 25.4) mm** |

25.4x apart, so the reader does convert: no inch IGES was ever wrong. Isolated
further by reading the *same* INCH file with `xstep.cascade.unit` set to INCH,
which yields 0.03937 -- the raw file value. So the conversion is file-unit to
cascade-unit, and the mm contract depends on that global static being MM.

**The trap in my own ticket recipe.** I proposed writing `MakeBox(1,1,1)` as an
INCH file and expecting 25.4 back. That would have been wrong:
`IGESControl_Writer` converts too, so a 1mm solid written as INCH lands in the
file as 0.03937in, reads back as 1mm, and **looks exactly like the reader not
converting.** A double conversion masquerading as no conversion. The test
therefore asserts the file's own max-coordinate field first, pinning the writer
before it trusts the reader. Worth remembering: with a symmetric round trip,
"no effect" and "two cancelling effects" are the same observation.

So the fix is truthfulness, not arithmetic: the unit now comes from
`IGESControl_Reader().WS().Model().GlobalSection()`, and a non-mm IGES warns
that the values shown are *already* converted. `UnitValue()` is read rather
than the unit flag mapped, because flag 3 is user-defined. And
`_pin_cascade_unit_mm()` now sets the static the whole mm contract rests on
instead of assuming its default.

Three things I took further than the ticket:

- **The pin was on one of two paths.** Its own docstring says
  `_shape_to_glb`'s x0.001 depends on it, and `_shape_to_glb` is shared with
  the STEP fallback, which did not pin. Same dependency, same risk, so it now
  pins at both readers -- the guard belongs where every caller routes through.
- **`detect_step_length_unit` can return `'m'` and `'foot'`, and
  `extract_part` branched on `== "inch"`.** The identical silence, in the other
  format, at 1000x instead of 25.4x. Now `not in ("mm", "unknown")`, matching
  the IGES branch so the two cannot drift apart again. Its docstring also
  omitted `'foot'` from the four values it claimed to return.
- **A readable label.** `.upper()` on `'m'` renders as "declares M units",
  which reads like a typo rather than a unit.

The metre case cannot be built the way the IGES cubes were:
`SetCVal_s("write.step.unit", "M")` is **silently rejected** by this OCC build
-- it reads back as `"MM"` and the file still says `SI_UNIT(.MILLI.,.METRE.)`.
INCH does take, as a `CONVERSION_BASED_UNIT`. So the metre file is an OCP-written
mm STEP with its one `SI_UNIT` line rewritten, and the check asserts the
reporting only, since a file declaring metres while holding mm-magnitude
coordinates has no meaningful dimensions by construction. Reverting the
predicate to `== "inch"` fails it.

Every real fixture is byte-identical before and after -- Housing
(590.0, 132.62, 105.08), Rack (663.5, 109.77, 27.59), IBJ
(338.57, 38.45, 37.13), YXA bar (1091.58, 298.25, 142.57), wheel
(371.0, 353.9, 137.71) -- and the bar is an IGES file, so 40/PLS12801 was
directly in the blast radius and did not move.

Left alone deliberately: `declared_unit` is not a field on `ExtractionResult`.
Adding it would pull `schemas.py` and the frontend in under hard rule 9 to
carry information the warning already delivers.

### G-POSE: a UI constant gating the engine, and a ranking key nobody checked

`max_candidates=4` costs **zero parts on all six cases we can compute.** That
is the answer, and it is the negative result the ticket explicitly welcomed.

`generate_orientation_candidates` builds six resting poses, dedups them, sorts
by **footprint area**, and truncates to 4. The near-duplicate flip of each pose
survives dedup (its CG sits at a different height), so on five of six cases the
four slots hold only **two** distinct footprints and the third — always the
tallest, smallest-area one — never reaches the nester.

| case | gen | searched | footprint cut | cap 4 | no cap |
|---|---|---|---|---|---|
| Mubea bar / PLS12801 | 6 | 4 | (298, 143) — worth **0**, h 1092 > 790 inner | **40** | **40** |
| TRW wheel / PLS1280 | 6 | 4 | (354, 138) — worth 36 | **48** | **48** |
| Tata X104 / PLS12801 | 6 | 4 | (128, 90) — worth 56 | **64** | **64** |
| Tata Nexon EV / PLS12804 | 6 | 4 | (245, 122) — worth **0**, h 711 > 580 | **18** | **18** |
| Tata P118 / PLS12804 | 6 | 4 | (457, 99) — worth **0**, h 681 > 580 | **12** | **12** |
| Tata P125/P126 / PLS12801 | **4** | 4 | none — dedup merged a flip pair | **64** | **64** |

Replayed across the whole 17-asset seeded catalogue plus the synthesised custom
box, because the poses feed `rank_catalogue` and `synthesis.synthesise` for
every asset and the per-asset table above is not sufficient on its own:
**65/65, 48/48, 80/80, 36/36, 22/22, 80/80.** No difference anywhere.

**The premise was right, the feared consequence does not occur.** Three of the
five cut poses cannot fit any asset at all (height exceeds inner height), so
their loss is provably nil. The other two are real but inferior. The narrowest
margin is X104, where the cut pose reaches **56 — better than the 55 from a
pose the cap keeps.** The cap is already discarding a strictly better pose than
one it searches; it just is not discarding the best one. On P118 catalogue-wide
the cut pose **ties** the winner at 22.

**Footprint area is the wrong key, and it mis-ranks two real files.** Not a
theoretical objection:

- **Nexon EV radiator** — pose 0, the largest footprint (711x245, area 174,459)
  reaches 16. Pose 2 (711x122, *half* the area) reaches **18**. The shipped
  answer of 18 comes from rank 2, not rank 0.
- **P118 radiator** catalogue-wide — pose 0 (area 311,391) reaches 20; poses 2
  and 4 (areas 67,565 and 45,382) reach **22**.

The mechanism: count is `prod floor((inner - extent)/pitch) + 1`, and area sees
no pitch at all — so the key is blind to the vertical interleave that produces
the entire Mubea gap. On the headline case the bar's pose 0 has **2.09x** the
footprint area of pose 2 but only **1.11x** the count (40 vs 36), because pose
2's interleave of 0.474 buys back nearly the whole area deficit. Area happens
to be right on 4 of 6, and that coincidence is what kept the cap invisible.

Worse than the key: **whether the cap bites at all depends on a 0.5mm CG
tolerance in dedup.** A near-symmetric part (P125) collapses its flip twins and
gets all three footprints searched; an asymmetric one keeps both twins and gets
two. That is an accidental, part-dependent search space.

**Recommended fix, not made:** not "lift the cap" — lifting it adds a full
voxelisation per extra pose, measured at +6s on the bar but **+56s on the Nexon
and +183s on the P118**, which already runs 564s. Instead **drop the flip twins
before measuring and give the engine every remaining pose.** Twins are
identifiable for free from `(axis, flip)` without touching the mesh, and across
all six cases no twin pair ever produced a different count (they differ only by
a voxel of quantisation noise — bar pitch 69 vs 73). Three real poses instead
of four slots: a *fuller* search that is also ~25% *cheaper*. Keep
`max_candidates=4` on the UI list where it belongs; stop letting one constant
serve both.

That needs a failing test first, and the honest one is not in the repo — both
in-repo fixtures win on pose 0, which is exactly why `--cap 1` does **not**
fail. It needs the Nexon, or a synthetic part whose best pose ranks third by
area.

**One correction to the audit's own advice.** It notes that poses which cannot
fit any asset still cost a full voxelisation (the bar's poses 4 and 5 are 6s of
its 18s; the equivalent on P118 is ~183s) and calls a height-vs-tallest-inner
reject "free". It is not free as described. `synthesis.synthesise` is called
**per pose** and carries its own bound (`extent[2] > max_inner_height_mm`,
`synthesis.py:127`), which can exceed any catalogue asset — so a reject gated on
the catalogue alone would silently delete the tall poses synthesis is entitled
to build a box around. The bound has to be
`max(tallest catalogue inner, max_inner_height_mm)`. Cheap, still worth doing,
but not the one-liner it looks like.

**The check:** `backend/tests/test_pose_search.py` — measures every generated
pose on both in-repo fixtures and asserts the cap-4 answer equals the no-cap
answer, for the ground-truth asset and across the full catalogue. It also
asserts the shipped candidate list really is the area-sorted prefix (otherwise
the comparison measures the wrong thing) and that some case actually truncated,
so the equalities cannot pass vacuously. It reads the cap out of the function
signature with `inspect`, so it follows the shipped value instead of testing a
stale copy. Tata numbers are recorded in the docstring, not run — those files
are not fixtures and cost ~14 minutes.

Verified by me, not taken on report: `ground_truth.py` still EXIT=0 at **40 in
PLS12801 (1,4,10)** and **48 in PLS1280 (3,2,8)**; the test passes at exit 0;
and `--reverse` (worst-area-first, fault injection) fails at exit 1 with
`max_candidates=4 gives 36 in PLS12801 but searching all 6 poses gives 40 --
the cap is now costing 4 parts`. Not wired into the `ground_truth.py` sweep —
that is the mandatory regression path and this was an audit. One import line
and ~30s if we want it enforced.


### First real end-to-end run, and the runner-up nobody checked

Rahul killed the stale workers (pids 10375/10381/10382), so for the first time
the whole stack ran end to end over real HTTP: upload -> extract -> confirm ->
solve, API on `127.0.0.1:8011`, worker on redis db 9, vite on 5173. Three things
worth recording, one of them a defect.

**The plumbing is honest.** The TRW wheel went through **Celery**, not the
in-process fallback (landmine 6 -- confirmed in the worker log, `extract_step
succeeded in 2.83s`, and no fallback line in the API log). `canonical_dims_lbh`
came back `[371.0, 353.9, 137.71]`, byte-identical to the fixture. Hard rule 9
holds on the solve payload: every ranked entry carries `asset_name`,
`extent_lbh`, `pitch_lbh`, `grid`, `dunnage`, `drawing_url`, `silhouette`,
`interleave`, `limited_by`, `pose_label`, and `truck` carries its own
`asset_name` rather than leaving the drawing to guess. Hard rule 4 holds too:
`mesh_volume_mm3` is **null** because the model is not watertight, and only
`obb_volume_mm3` (plain L x B x H) is reported, with a warning that says so.

**G-POSE is visible in the live payload.** `candidates` comes back with **four
entries and only two distinct footprints** -- `[371, 353.9, 137.71]` twice,
`[371, 137.71, 353.9]` twice -- and the third pose absent. Exactly the flip-twin
duplication the audit measured, now confirmed in the JSON the UI consumes.

**The defect: equal counts were ranked by seed order.** For the wheel, three
assets tie at 48 per box, and the tie was broken by nothing but position in
`seed_data.PACKAGING`:

| asset | inner L x B x H | outer volume | per box | parts/truck |
|---|---|---|---|---|
| PLS12803 | 1150x750x1000 | 1,148 L | 48 | **2304** |
| PLS12103 | 1150x**950**x1000 | **1,435 L** | 48 | **1728** |
| PLS1280 | 1150x750x1000 | 1,152 L | 48 | **2304** |

PLS12103 is a 25% larger crate holding the same 48 parts -- the wheel's 3x2
floor grid never uses the extra 200mm of breadth -- and it was presented as the
**runner-up recommendation**, 576 parts/truck worse than the box it displaced.

**Correction to my own first reading of this.** I initially called it 25% worse
advice. The truck *number* was never wrong: `tests/test_solve_api.py` §2a and
§2d already pin that the truck block reports the best ranked option and names
it, and both passed throughout. What was wrong is narrower and still worth
fixing -- the **order of the comparison cards**, which CLAUDE.md calls the core
screen. An engineer comparing crates saw a strictly dominated one ranked above a
better one.

`rank_catalogue` ended with `best.sort(key=lambda l: -l.count)`. Python's sort is
stable, so equal counts kept catalogue order. Now:

    vol[asset.name] = d[0] * d[1] * d[2]        # outer, falling back to inner
    best.sort(key=lambda l: (-l.count, vol[l.asset_name]))

This only ever reorders equals, so it cannot change which count wins. Confirmed
over HTTP after restarting both services (landmine 2 -- uvicorn has no
`--reload` here and Celery never hot-reloads): PLS12103 moved from **rank 1 to
rank 2**, behind PLS1280, and `best_count` stayed 48.

**What the fix does NOT do**, and this is the interesting half. Ranking is still
by parts-per-box, and count order still disagrees with truck order wherever the
counts differ -- measured across the whole catalogue for this wheel:

| asset | per box | parts/truck |
|---|---|---|
| PLS12101 | 42 | 1512 |
| PLS12801 | 36 | **1728** |
| PLS12802 / PLS12804 | 24 | **1728** |
| FLC12101 | 36 | 1296 |
| CRT6435 | 2 | 1152 |
| CRT6418 | 1 | **1248** |

A 42-per-box crate shipping fewer parts than a 24-per-box crate is not a bug --
the engine already scores every option on the truck and warns when the two
winners split (§2d, `Best per box is X, but best per truck is Y`). Whether the
*ranking itself* should be by truck throughput is a real question and it is not
mine to answer: it depends on tare, vehicle and whether the customer buys a
crate or a shipment. Left for `DOMAIN.md`.

**The fix broke a test, and the test was right.** §2a's non-vacuity guard
("ranked options really do differ on parts/truck") had been relying on the
PLS12803/PLS12103 tie for its divergence -- i.e. on the very behaviour just
removed. Re-pinned to a divergence that survives the tie-break, from
**different** counts: restricted to `["PLS12101", "PLS12801"]`, ranking puts
PLS12101 first at 42/box while PLS12801 ships 1728 against 1512.

That re-pinning exposed a second gap in the same check. The engine scores
`[*catalogue, custom]`, but `assets` restricts only the catalogue half, so a
restricted request can leave the **synthesised box** the outright winner -- and
it does here, 2304 against 1728. The check's option list omitted `custom`, so it
was comparing the engine's answer against a smaller option set than the engine
had, and calling the engine wrong. Now scores all three:
`{'PLS12101': 1512, 'PLS12801': 1728, 'custom': 2304}`.

**The check:** `tests/test_nesting.py::test_ties_go_to_the_smaller_box`. Two
containers with identical inner and different outer, listed roomy-first so the
order cannot come from input order; asserts equal counts rank smaller-outer
first, and that a genuinely higher count still outranks a smaller box. No CAD
needed -- `Pose` takes bare numbers the way synthesis builds them, so it drives
`rank_catalogue` with `mesh=None` and a synthetic pose. Proved to fail on
reverting the sort: `tie not broken by outer volume: ['ROOMY', 'TIGHT']`.


## Open

| Item | Owner | Blocks |
|---|---|---|
| **Should the ranking be by parts-per-truck, not parts-per-box?** Ties are now broken by box size, but where COUNTS differ the two orders still disagree: for the TRW wheel PLS12101 takes 42/box and ships 1512, while PLS12801 takes 36/box and ships 1728. The engine scores both and warns on a split (§2d), so nothing is wrong — but the card order is still per-box. | packaging engineers | Nothing today. It decides what the core screen ranks by, and the answer depends on tare, vehicle, and whether the customer buys a crate or a shipment. `DOMAIN.md`. |
| ~~**Tare weight per asset**~~ | — | **Done** — C-TARE. `Packaging.tare_kg`/`.material` from the SCS report, 15 assets. The 1560/1625 inversion was an artefact of assuming 30kg for both boxes; with PLS12103's real 39kg it is an exact tie, 1560/1560. |
| Why did Mubea ship in a PLS12801 when a PLS12103 holds 65? | team | Mostly answered by per-asset tare: the two tie at 1560 parts/truck, so there was no per-truck gain to capture. Still worth confirming nothing else drove it. |
| Real envelope dims for Housing / Rack / IBJ / YXA bar | team | IGES accuracy is unaudited. `TODO(team)` in the test. |
| `DOMAIN.md` — clearances, wall thicknesses, foam rules, and the inner-height datum question above | packaging engineers | The provisional constants in `synthesis.py` and the 5mm clearance default in `nesting.py`. |
| Remaining 32 catalogue assets untyped and unloaded (17 of 49 seeded) | PM | Full coverage. PLS1280 and PLS12804 came from decks and are in the SCS report at all — a reminder that the 49 rows are not the whole catalogue. |
| ~~No API endpoint for `engine.solve()`~~ | — | **Done** — B3-1, `POST /api/parts/{id}/solve`. |
| ~~**Interleaved-pose insert drawing**~~ | — | **Done** — P4-1, `app/dunnage.py`. It was never blocked: `ground_truth.py` carried both shipped BOMs and PLANNING §7 names the elements. |
| ~~**Insert BOM gaps the decks would close**~~ | — | **Done** — E-DECK. The Mubea deck closed every one and *confirmed* the 60/70mm widths we had invented. One gap survives: the TRW separator's EVA density, which its own deck does not state. |
| **Custom-box inner-height datum**: `synthesise` sizes inner H to exactly the parts stack, so the wheel's custom box reports `fits: False` (build 990.0 > inner 987.0) — the 3mm bottom sheet has nowhere to go, though `BASE_SEPARATOR_MM = 35` on the outer height means there is physically room | packaging engineers | The custom design for any layout whose dunnage adds net height. Reporting honestly, not silenced. |
| **Is the flat pose available for the Tata heat exchangers?** All four decks say "vertical orientation, TATA logo printed on the assets"; the engine lays them flat and claims 64 vs 60 (X104) and 12 vs 10 (P118). | packaging engineers + customer | Whether those two cases are a **+4 and +2 real gain** or an unmodelled customer requirement. Nothing is tuned to them meanwhile — see `tests/test_tata.py`. |
| **Bharat Forge is blocked on a CAD export.** `1.SLDPRT` / `2.SLDPRT` are SolidWorks native and no open-source reader exists. Suspension arm D4927/D4926, 65/PLS12801, matrix 1x13. | team | That whole case. The upload now says exactly what to ask for. Its deck is `R5_After Trial`, so it also documents a revised design. |
| **Irregular lattices**: P125/P126 ships 65 as `12x1(1)` — twelve in a row plus one extra per insert. The engine models regular lattices only and gets 60. | PM | 5 parts on that one case, and any other proposal that fills a leftover pocket. Not a bug; a modelling limit, recorded in `test_tata.py`. |
| **PLS12801 outer height has five answers**: 960 (BFL), 980 (Tata), 986 (seed), 990 (SCS), 1000 (Mubea/TRW). Inner height is 790 in *every* source. | team | Nothing until the truck tier stacks boxes — nesting reads inner only. User: "Don't focus on the PLS12801 outer height too much, correct data will be provided to you." |
| Six CRT crates rated 20kg vs 14, and FSC inner H 580 vs 410 (the Tata deck's PLS12804 independently says 580) | team | Ranking those assets. Reported, not resolved. |
| **The lattice is only collision-checked along single axes** | geometry / packaging engineers | **Measured (R3), deliberately not enforced.** Real defect, proved by pigeonhole on a synthetic 2-cell diagonal part (engine says 9 in a 16-cell region; honest answer 6). But **not reachable on any customer part we hold**: the wheel is structurally immune (interleaves on 1 axis), and the bar's single offending vector is voxel slop — cell count flat, volume collapsing ~8x per halving to 10 mm3. Enforcing it would cost the bar 8 of its 40. Blocked on `DOMAIN.md` and a calibrated occupancy model, then a **tolerance** (~1%), never a predicate. Diagnostic: `tests/test_lattice_vectors.py`. |
| ~~**Dunnage charges bars height they do not occupy**~~ | — | **Done** — R2. My first diagnosis had two details wrong: it is not "three element groups each claiming their full width", it is the centre bar and side bars being **coplanar** at one layer boundary while `bom()` sums their heights; and the real-CAD bar's nest depth is 80mm, not 218. The two candidate formulas reduce to the same expression, so the fix charges the bar once rather than changing the arithmetic. Mubea unchanged at 790.0/790. |
| **No in-plane budget check at all.** `lattice_count` charges zero wall clearance, then the BOM emits two 35mm side panels: 1085 + 70 = 1155 in an inner L of 1150, and `Bom.fits` reports True because it only budgets H. | packaging engineers | Nothing yet — the shipped deck has the same tension, so this is probably the DOMAIN.md wall-clearance datum rather than a bug. But nothing detects or reports it, and the drawing paints only into empty cells so the picture hides it too. |
| ~~**`max_candidates=4` now gates the engine's search.**~~ | — | **Done** — G-POSE. Audited on all six computable cases: the cap costs **zero parts** (40/40, 48/48, 64/64, 18/18, 12/12, 64/64, and identical catalogue-wide). The premise held — 4 slots, 2 distinct footprints — but every cut pose is either unfittable or inferior. The real finding is that **footprint area mis-ranks poses on two live files** (Nexon 16 vs 18, P118 20 vs 22): area cannot see the interleave. `tests/test_pose_search.py` pins it. |
| **The UI's "most stable" pose is not the engine's best pose.** `extract_part` hands ONE ranked list to both the orientation picker and the nester, so the screen presents rank 0 as the recommendation while the count can come from rank 2 — on the Nexon (16 vs 18) and the P118 (20 vs 22) it does. Same class as the IGES unit bug: the computation is right, the reporting is not. | frontend + packaging engineers | Nothing numerically — the shipped count is the best over all four searched poses. It decides what the picker should show: the most stable pose, the highest-count pose, or both with the difference named. |
| ~~**IGES units rest on an untested docstring**~~ | — | **Done** — G-UNIT. The claim was true: OCC converts on read, measured 1.0mm vs 25.4mm on cubes that are 1x1x1 in their own file units. So no geometry was ever wrong; the reporting was. Unit now read from the reader's global section, non-mm warns in both formats, and the cascade-unit static is pinned at both readers rather than assumed. No real fixture dimension moved. |
| **Clearance baked into the pitch can flip the dunnage archetype.** `archetype_of` tests `pitch < extent`, but the pitch already carries the 5mm in-plane clearance, so a part with a genuine 3mm interleave reports `pitch = extent + 2` and gets `pocket_tray` — the wrong dunnage system from the wrong generator. | geometry | Unproven; no real fixture exhibits it. |
| **Exploded-drawing leader angles** — the dotted leaders run from an evenly-spread label column back to component mid-heights, so several cross the drawing at a shallow angle and read as pointing at the wrong component. User: "pointing in the exploded view is not at the right angle, we'll work on that but for now this will do." | PM | Nothing — cosmetic. Fix is the leader routing in `insert_drawing._labels`, not the geometry. |
| Is 200kg a sane `tare_kg` ceiling? A steel returnable may exceed it | packaging engineers | Nothing yet — over the cap the UI now shows a readable 422 instead of "[object Object]". |
| Does a confirmed resting pose BIND the packing pose, or only the dimensions? | packaging engineers | Nothing — the solve warns when the winning layout differs from the confirmed pose. Answer decides whether it should restrict instead. |
| Manual (no-CAD) parts get a 422, so the endpoint serves STEP/IGES parts only | PM | Any manual-entry workflow. A labelled `method: "cuboid"` result would need an app-side cuboid function and UI support. The UI now shows the 422 detail verbatim. |
| Does the intake tab's unsaved draft deserve a solve at all? | PM | Nothing. It shows "Save this part first" — the alternative is hiding the panel until saved. |
| Pallet tier skipped — box → truck only | PM | Phase 3. None of the 15 loaded assets carries a pallet spec. |
| Mirrored/interleaved pairs not implemented (would overshoot ground truth) | PM | Nothing. Reasoning in `test_nesting.py`. |
| `extract_part` leaks one GLB per call via `tempfile.mkstemp` | backend | Nothing yet. Pre-existing — `extract_dimensions` is now wrapped, `extract_part` is not (its callers want the GLB for the viewer). ~99 stale files in `$TMPDIR`. |
| Split commit `112d4e7` — swept pre-existing WIP in with the planning docs | PM | Nothing. Cosmetic. |

## Next

1. ~~Wire `engine.solve()` to an endpoint~~ — **done**, B3-1.
2. ~~Retire `partsPerBox` in the frontend~~ — **done**, F2-1.
3. ~~Close the `/code-review` findings on F2-1~~ — **done**, F2-2.
4. ~~The interleaved-pose insert drawing~~ — **done**, P4-1.
5. ~~`/code-review` on the eleven tickets~~ — **done**, and it needed the
   command file written first. 15 confirmed defects, all fixed; four of them
   were damage from the same session that produced them, which is the whole
   argument for step 4 of the cycle.
6. ~~Commit~~ — **done**, six commits, `c650fcf` through `4d78f95`. The entire
   engine had been living in the working tree, uncommitted since `edb8d34`, and
   that was the one thing here not recoverable by rerunning something.
7. **← START HERE. Open a browser.** Nobody has seen UI-4's restructure, the
   BOM table or the Explode modal rendered. The last browser session found six
   defects that 20+ green checks had missed, and the frontend reviewer asked
   specifically for a human eye on the Truck Load tab and the Load Calculator
   result card. **No longer blocked** — the stale workers are gone and `## Now`
   has the three commands. Look first at the ranked-solution cards: the order
   changed this session (`4d78f95`) and was verified only in JSON, never on
   screen. It should read PLS12803, PLS1280, PLS12103.
8. ~~IGES unit detection~~ — **done**, G-UNIT, and the answer was the
   reassuring one: OCC really does convert on read, so no customer dimension
   was ever wrong. Only the reporting lied. The same silence turned out to
   exist on the STEP side for metres and feet, and is closed too.
9. **Answer the Tata pose question**, then decide whether the engine's +4/+2
   stand. The difference between "we reproduce the decks" and "we beat them",
   and it is one conversation with the packaging engineers.
10. **Get a STEP export for the Bharat Forge suspension arm** — the only
    shared case we cannot compute at all. `.SLDPRT` is unreadable and we
    support STEP only.
11. ~~**Audit `max_candidates=4`**~~ — **done**, G-POSE: costs zero parts
    on all six computable cases. What it turned up instead: **footprint
    area is the wrong ranking key** — it mis-ranks the Nexon (16 vs 18) and
    the P118 (20 vs 22), because area cannot see the vertical interleave.
    Fix is to drop the flip twins rather than lift the cap (a *fuller*
    search, ~25% *cheaper*), but it needs a failing test first and neither
    in-repo fixture can provide one — both win on pose 0. Needs the Nexon
    as a fixture, or a synthetic part whose best pose ranks third by area.
12. **Decide what the orientation picker should recommend** — it labels rank 0
    "most stable" while the count may come from rank 2. Pairs with item 7.
13. ~~**Ranking ties fell to `seed_data.PACKAGING` order**~~ — **done**,
    `4d78f95`. Equal counts now go to the smaller box. The open half is
    whether the ranking should be by parts-per-truck at all where counts
    *differ* — see `## Open`; that one is `DOMAIN.md`.
14. **Phase 3** — catalogue cleanup (7 defects, remaining 32 of 49 assets),
    then the pallet tier. Still blocked on pallet dims: `Can Be Palletized` is
    1 for 7 of 49 rows and pallet L/B/H read 0 for all 49.
15. ~~**Kill the stale Celery workers**~~ — **done** 10 Sep 2026: Rahul ran
    `kill 10375 10381 10382`. The stack then came up clean on redis db 9 and
    port 8011 and was driven end to end for the first time. **Services were
    stopped again at session close — nothing of ours is listening.**

Still needed from the packaging engineers or the customer: the correct PLS12801
outer height; the CRT crate load rating (20 vs 14) and the FSC dims; and
`DOMAIN.md`, which now carries the in-plane clearance budget, whether clearance
can flip the dunnage archetype, the mixed-vector tolerance, and the
rank-by-truck-throughput question.

Nothing on the Gemini key — **Rahul's call, 10 Sep 2026: keep it, rotate when
he has to.** It is in `.env` (gitignored), the value is not in any tracked file
or in git history, and no code in the repo reads it — the only references
anywhere are `.env` itself and this log. Format note for later: the AI Studio
key is the newer `AQ.`-prefixed kind, not `AIza`.

## 2026-09-10 (later) — Whole-approach audit, no code changed

Asked for: audit the approach and find the flaws. Read PLANNING, the engine
(nesting/engine/synthesis/dunnage/geometry/worker), the contract tests and
the frontend adapter. Two measurements, both with the venv:

**The 40 is a voxel-size artefact.** YXA bar in PLS12801, defaults otherwise:

| VOXEL_MM | count | grid | pose extent |
|---|---|---|---|
| 3 | 44 | (1,4,11) | 1095x303x147 |
| 4 | **40** | (1,4,10) | 1092x300x148 |
| 5 | 36 | (1,9,4) | 1095x145x305 (different pose wins) |
| 6 | 36 | (1,9,4) | 1098x150x306 |

Nothing physical sets the vertical pitch; the deck's is the 66mm bar. The
constant is standing in for DOMAIN.md. And the YXA bar is not the Mubea part
(298 wide vs 190), so the CAD contract reproducing 40 is coincidence.

**PLANNING §4's EPS claim is false.** `lattice_count` with pitch 186.7 instead
of 560/3 gives 30, not 40; EPS=1e-6 cannot absorb a 0.03mm typo. Documented
safety net does not exist.

Ranked flaws recorded in the session summary (Rahul has it): pitch derived
from the part alone with dunnage designed afterwards (root of the Tata 3/4
miss, the 1155>1150 side separators, custom `fits: False`); N=2 calibration
with >=5 knobs; ground-truth run passes vacuously without NDA fixtures;
custom box height overhead 40mm vs catalogue 196mm biases the truck card;
pose search is accidental (area key, cap 4, 0.5mm dedup); confirmed pose and
dims never reach the engine; two truck calculators and three sets of dunnage
constants still live (engine, packing.js loadPlan/trayGeometry); frontend
poll cap 240s vs worker limit 600s so the UI reports "no worker" on a live
solve of any radiator-class part.

## 2026-09-10 (deploy session) — part-intake is on GCP

Separate session, working from `deploy/BRIEF.md`. No application source
touched; nothing committed.

**Live:** https://34-47-230-213.sslip.io — HTTP basic auth site-wide
(user `yantra`; password in Secret Manager `part-intake-basic-auth`), Let's
Encrypt cert via Caddy on the sslip.io name. Project `yantra-part-intake`
(new, org trakkia.com, gcloud configuration `part-intake`), VM
`part-intake-vm` in `asia-south1-a` (e2-standard-2, 50 GB, Debian 12), static
IP 34.47.230.213, SSH only through IAP. Images in Artifact Registry
`asia-south1-docker.pkg.dev/yantra-part-intake/part-intake/{backend,frontend}`
tagged `latest` + `20260910-101504`. Daily boot-disk snapshot at 20:00 UTC,
7-day retention. Secrets: `part-intake-basic-auth`, `part-intake-db-password`.
Runbook: `deploy/README.md`. Ship: `deploy/deploy.sh`.

**Verified over real HTTPS:** `/api/packaging` returns the seeded catalogue;
`/` returns the SPA; no credentials → 401 on both; all five compose services
Up, worker log `celery@1b507d5b2959 ready` on `redis://redis:6379/0`. End to
end with the QY2i steering-wheel STEP: upload 13 MB in 3.5 s → job `done` in
~15 s, `dims [371.0, 353.9, 137.71] mm`, and the **worker** log shows
`Task extract_step[...] succeeded in 10.2s` with zero in-process-fallback
lines in the API log. Part created → solve `done` in 25 s, `solve_part`
succeeded in the worker log, catalogue **PLS1280 count=48 grid=[3,2,8]**
(ground truth) and PLS12803 also 48. `deploy.sh --no-build` rerun is clean.

**Not verified:** a snapshot has not yet been taken (first one runs 20:00
UTC) so the restore procedure in the README is untested; no browser session,
only curl; cert renewal (Caddy handles it, first renewal ~Nov 2026).

**Caveat for the PM:** Cloud Build uploads the *working tree*, not HEAD. At
10:15 UTC the tree was at 97fcc33 with only PROGRESS.md modified, but by the
end of this session 23 source files under backend/app, backend/tests and
frontend/src showed uncommitted edits from another session. The source
tarball was uploaded at 15:45:04 IST; `backend/app/{main,worker,engine}.py`
were modified 15:42–15:43 IST, so those uncommitted backend edits ARE in the
live backend image. `frontend/src/lib/{packing,solve}.js` (15:49, 15:52) are
not. Commit, then rerun `deploy/deploy.sh`, to know exactly what is live.

**Files added:** `frontend/Dockerfile`, `deploy/Caddyfile`,
`deploy/docker-compose.prod.yml`, `deploy/cloudbuild.yaml`, `deploy/deploy.sh`,
`deploy/vm-startup.sh`, `deploy/README.md` (plus the PM's `deploy/BRIEF.md`).
Also: compute default SA granted `roles/cloudbuild.builds.builder` and
`roles/artifactregistry.reader` (new projects grant it nothing, first build
failed on `storage.objects.get`); default `allow-ssh`/`allow-rdp` firewall
rules deleted. Cost ≈ $70–75/month.

## 2026-09-10 (evening) — Audit fixes: dunnage feeds the count, the engine reports its own uncertainty

Goal set by Rahul: fix the audit's flaws and make this the best engine we can.
Deployment handed to a second session via `deploy/BRIEF.md` (untouched here).
Everything below is in the working tree, **uncommitted**; `/code-review` ran
(three reviewers) and every CONFIRMED finding is fixed with a check behind it.

**The root fix — dunnage now feeds back into the count.** `dunnage.dead_height_mm`
(pure function of extent, pitch, archetype; grid-independent) is charged
against the inner height by both `nesting.layouts_for` and
`synthesis.synthesise`. A pocket tray's 3mm sheet, a bar or top separator
taller than the nest depth, come out of the room the lattice gets. Mubea and
TRW do not move (dead 0 and 3mm), and `Bom.fits` is now true on H by
construction for anything the engine returns. `test_self_consistent` recomputes
on inner-minus-dead — the review caught the first version of this putting a
layer back on the taller inner (42 reported in a box solved for 21).

**Also landed:**
- **Pose search complete by construction.** One pose per OBB axis (lower-CG
  sign); the flipped twin has the same footprint, height and — in exact
  geometry — pitch. On the raster the two signs differ by up to a voxel (bar
  axis 1: (69,144) vs (73,140), 60 vs 55 catalogue-wide) — quantisation noise
  the `count_upper` band covers, recorded in the docstring. 3 poses searched,
  nothing cut; ~25% cheaper.
- **`count_upper`** on every layout and the custom design: `lattice_count` one
  voxel tighter. YXA bar 40 → ceiling 44, which 3mm and 2mm rasters return
  outright. The single constant no longer decides the answer silently.
- **Archetype sees the parts, not the air — and needs both interleaves.**
  `archetype_of(…, clearance_lbh)` subtracts the baked-in clearance, and
  `bar_and_rod` now requires interleave in plane AND vertically, each beyond
  one voxel (`INTERLEAVE_MIN_MM = 4`). The first cut required in-plane only:
  the Nexon radiator's 4mm raster-noise overlap became "bars", the new dead
  height charged a 244mm bar, and the pinned 18 fell to 9 — caught by
  `test_tata --cad`, which is exactly what the fixtures were added for. In
  plane only is a slotted tray (pocket-tray structure); vertical only is TRW.
  Worker's drawing recomputes the BOM with the job's own clearance.
- **In-plane budget reported.** `Bom.slack_lbh` per axis; Mubea's own deck
  reads −5 on L (1085 + 2×35 in 1150). Solve warns with the number, on any axis.
- **Custom box outer H = inner + 196** (catalogue overhead), not + 40.
- **Pose lock**: `SolveIn.confirmed_pose_only`; `poses_searched` echoed.
- **Clearance is a parameter**: `SolveIn.clearance_mm` (default 5), echoed
  back. The Tata decks pack their dense axis at 0.3mm (X104) and 10.3mm (P118);
  no constant is right for both.
- **One truck calculator.** `POST /api/truck-fit` runs `engine.parts_per_truck`
  (now `max_stack`, `by_volume`, `by_weight`, `stack`); `loadPlan`/`floorFit`/
  `bestFill` deleted from the frontend. No-weight is labelled "volume", not a
  tie (review).
- **Frontend poll**: pending 90s → "no worker"; processing up to 660s on its
  own counter (review: a shared counter charged pickup time to the run).
  `interleaved` from `dunnage.archetype`; tray drawing takes pocket depth and
  sheet from the BOM; `count_upper`, pose checkbox, clearance input, small print.
- **Contract fails without fixtures.** `test_clearance` raises when the NDA CAD
  is absent (`INTAKE_SKIP_NDA=1` skips and says so). PLANNING §4's false EPS
  claim corrected.
- **Tata STEP files are fixtures** (gitignored). `test_tata.py --cad` runs the
  engine on them, all poses and the deck's pose, both pinned:

  | case | all poses | deck pose | deck ships |
  |---|---|---|---|
  | X104 | 64 (8,1,8) | 55 (11,1,5) | 60 |
  | Nexon EV | 18 (9,1,2) | 18 | 18 |
  | P118 | 12 (2,1,6) | 11 (11,1,1) | 10 |
  | P125/P126 | 64 (8,1,8) | 55 (11,1,5) | 65 (60 regular) |

  In the deck's pose the remaining gap is the clearance constant, not geometry.

**Verified:** `ground_truth.py` full sweep (40/PLS12801, 48/PLS1280 from CAD,
Tata arithmetic, dunnage), `test_solve_api` through the response models
(count_upper, slack_lbh, poses_searched, clearance_mm, truck bounds,
confirmed_pose_only → one pose, truck-fit 39/24/404), tester over real HTTP on
:8011 (every field, in-process fallback), `test_pose_search`,
`test_lattice_vectors`, `npm run build` + four check scripts.

**Open, from the reviews and the tester:**
- `.delay()` against a live redis with no worker succeeds and the job hangs at
  pending; the dev fallback only fires on a refused connection. The UI now says
  "no worker" after 90s; the API still cannot tell. Landmine 2, unchanged.
- In-plane-only interleave gets the pocket-tray BOM (cells at the pitch, so
  the "pockets" overlap in plan — a slotted tray). The frontend's tray drawing
  refuses that case via `interleave` ratios; a real slotted-tray generator is
  DOMAIN.md.
- `count_upper` is an estimate of the ceiling (extent can be over by two
  voxels), documented as such.
- Nothing seen in a browser this session. Item 7 of `## Next` stands.

### Same day, later — CI/CD: push to `main` deploys

Repo created: **github.com/yantrakshadmin/part-intake** (private; created under
yantra-logistics first, transferred to yantrakshadmin where every other repo
lives). Local branch renamed `master` → `main`, remote `origin` added. Cloud
Build 2nd-gen GitHub connection `part-intake-github` (asia-south1), trigger
**`part-intake-main`** on `^main$` running `deploy/cloudbuild.yaml` with
`_TAG=$SHORT_SHA`, service account = compute default SA.

`cloudbuild.yaml` now builds → pushes → deploys: the `deploy` step scps
compose + Caddyfile and runs `docker compose pull && up -d` over IAP ssh.
Two things it took to get right: (1) Cloud Build runs as root and the guest
agent refuses ssh keys for root → ssh as `deploy@vm` with
`--ssh-key-expire-after=1h`; (2) a build with an explicit SA must set
`logging: CLOUD_LOGGING_ONLY`. Compute SA gained `compute.instanceAdmin.v1`,
`iap.tunnelResourceAccessor`, `iam.serviceAccountUser`; Cloud Build service
agent gained `secretmanager.admin` (connection stores the GitHub token).

Proof: push of `1344cd0` → build 8812c5f4 SUCCESS, images tagged `1344cd0`
+ `latest`, VM containers restarted on the new image, site 401/200 as before.
`deploy/deploy.sh` still works by hand and runs the identical pipeline.

Committed only the deploy files (three commits on top of 97fcc33). The other
session's 23 modified source files are still uncommitted — the next push to
`main` deploys whatever is committed, so review then commit them.

### Same day — DNS: https://packit.trakkia.com

trakkia.com is Cloud DNS zone `trakkia-com` in project `trakkia-uat` (live NS
match). Added A `packit.trakkia.com` → 34.47.230.213 (TTL 300). VM `.env`
`SITE_HOST=packit.trakkia.com`, caddy recreated, Let's Encrypt cert obtained.
The sslip.io name is no longer served (single-host Caddyfile); to bring it
back set SITE_HOST to it. Runbook updated.

## 2026-09-10 — Packing-sequence GIF in the Explode view; PRD for the next goal

Goal: "Add that explode and gif view which actually shows how to pack the part
shared, use actual part; Gemini for design purposes." Done, verified over HTTP.

**What shipped**
- `insert_drawing.build_gif(...)`: packing sequence rendered from the real
  customer mesh voxels at the engine's own lattice (same `_place` the exploded
  PNG uses — one placement expression, not a second one). Step rule
  `_dun_step`: dunnage at height z0 lands at step `2·#{layers below z0}`,
  parts layer k at step `2k+1`, top dunnage after the last layer. Pacing
  600 ms dunnage / 1500 ms parts / 3000 ms final hold, progress bar at the
  bottom, opaque caption panel "Layer k of N · Place q × Name", step-0 caption
  "Base dunnage" (or "Empty box" when nothing lands there).
- Worker `_render_drawings` → `build_{job}_{i}.gif` / `build_{job}_custom.gif`,
  each artifact in its own try (a GIF failure never fails the solve; `dunnage.bom`
  now guarded too). `gif_url` beside `drawing_url` on every catalogue entry and
  on custom, in `result_json` and on `LayoutOut` / `BoxDesignOut` together
  (hard rule 9). `test_solve_api.py` asserts every ranked layout ships a
  `gif_url` whose file exists and opens with PIL.
- Frontend `ExplodeModal`: "Packing sequence | Exploded view" toggle
  (`aria-pressed`), "▶ Pack" button on the BOM; `.view-toggle` sits on the 8 px
  rhythm; solve-time copy now says "about a minute".
- Sizes: 736×528, bar 22 frames 1.11 MB, wheel 18 frames 1.20 MB. Render time
  per GIF 4.5–6.0 s (PNG 0.6–1.0 s). A full solve with drawings + GIFs is now
  ~40–50 s wall-clock (bar 50.7 s, wheel 38.3 s) against ~11 s before.

**Gemini** (`gemini-3.1-pro-preview`, key in gitignored `.env`, rotate it):
used for design critique only. It was shown fully synthetic renders with
made-up numbers (`scratchpad/gemini/synth.py`), never customer CAD or decks.
Fourteen items; adopted: part edge strokes, lower dunnage alpha, solid
dog-leg leaders, dashboard palette + Fira, solid build-height dimension line,
variable pacing, progress bar, caption panel, instructional wording,
highlight-on-placement frame. Rejected: removing PATTERN/MEASURED/DERIVED
tags (they say which numbers are measured vs derived — hard rule 2 in
picture form), a fake vertical "explode" offset on the static render (the
GIF is the explode now), fade-out loop frames (adds 400 ms of nothing), and
moving the title block into React (the PNG is downloaded standalone).

**Code review** (`/code-review`) found and we fixed: frame 0 painted the whole
box orange because `dun_step == 0` also matched every never-written cell —
`_step_masks` gates on the label volume and the self-check asserts
`gated0 < raw0`; `dunnage.bom` had drifted outside the try; label column
lines overlapped (now per-block measured line heights); leaders pointed at
empty space (now anchored to the nearest-camera solid instance of the row,
dropped when a row draws nothing, e.g. MS Rod); `__main__` default outdir
was a hard-coded scratchpad path (now `tempfile.gettempdir()`).

**Ranking note, not a bug:** an unrestricted Mubea solve ranks PLS12103 /
FLC12102 at 65 parts above PLS12801 at 40 — parts per box is the key.
40/PLS12801 is the answer when `assets=["PLS12801"]`, which is what the
ground truth and `test_engine.py` do.

**Checks run this session, all pass:** `tests/ground_truth.py` (40 / 48 and
the Tata decks), `python -m app.insert_drawing <dir>` (incl. new step-0
assert), `tests/test_solve_api.py` on redis db 9, `npm run build`.

**Still owed:** nothing seen in a browser this session — the Explode modal
toggle and GIF playback must be walked through with screenshots next time.
`deploy/` and `frontend/Dockerfile` untouched (deploy session owns them).
Nothing committed.

**PRD.md written** (project root) after cross-checking the Fitsol-derived
`Green_Packaging_Optimization_PRD.md`: adopt the shape (project + status,
baseline, why-panel, alternatives, proposal report), keep our substance (CAD
nesting, generated insert, no sliders, no invented numbers). Delivery order
F1 project entity → F2 shell → F3 baselines → F4 reasons → F5 runs → F6
trips/year → F7 proposal PDF. Next goal starts there.

Session close (2026-09-11): stack was brought up for Rahul's browser test
(API 127.0.0.1:8000, worker on redis db 9, Vite 5173) and torn down again.
Browser findings not yet recorded — start the next session by asking what
the Explode modal / GIF looked like. Working tree uncommitted, PRD.md ready.

## 2026-09-11 — F1 project entity + F2 app shell: the app reads as a product

Goal (`/goal`): "read the PRD and start making the part intake and steps look
more like a product rather than just a packaging calculator." Started PRD §8
delivery order at F1 and F2, and pulled F5 (runs as scenarios) forward because
without it the Packaging tab re-solved on every visit. Engine untouched
(PRD principle 4). Ground truth 40 / 48 passed after the last backend change.

**F1 — backend** (`models.py`, `schemas.py`, `main.py`, `tests/test_projects_api.py`)
- `projects` table (customer, part_number, part_name, status ladder
  `draft → solved → proposal_sent → trial → approved → archived`, owner, notes,
  annual_volume, route_km, vehicle_id, customer_count, customer_box,
  cost_per_trip, emission_factor_kg_per_km, recommended_run_id, timestamps).
  `part_profiles.project_id`, `solve_jobs.project_id` + `inputs_json` added
  via `_ensure_added_columns` (landmine 5); the real dev.db booted and logged
  the three column adds. One part per project: a second `POST /api/parts`
  with the same project_id is a 409.
- Routes: `POST/GET /api/projects` (`?q=` search), `GET/PATCH /api/projects/{id}`,
  `POST /api/projects/{id}/solve`. `_enqueue_solve` is the single solve path
  for both the old part route and the project route; it derives project_id
  from the part, so old-route solves list under the project too.
- `RunOut` summarises a solve server-side (hard rule 9): best_count/best_asset
  always describe the SAME object (`best_asset == "custom"` when the
  synthesised box wins), plus catalogue_count/catalogue_asset, custom_count,
  truck_boxes, truck_vehicle, clearance_mm, poses_searched, inputs, part_id.
  Runs ordered created_at desc with the uuid as a deterministic tiebreaker.
- 26 checks in `test_projects_api.py` incl. a real landmine-5 proof (old
  schema built by raw DDL, shim adds the columns).

**F2 (+F5) — frontend** (`App.jsx` = shell + 20-line hash router; new
`NewProject.jsx`, `ProjectsList.jsx`, `ProjectPage.jsx`, `Assets.jsx`,
`lib/router.js`, `lib/api.js`; `PartList.jsx` deleted)
- Left nav: Projects · New project · Assets · Tools › Load calculator.
- Projects list with search; New project = old intake flow + customer and an
  optional collapsed "Customer's current pack" fieldset; creates the project,
  attaches the part, lands on Packaging. Retry after a failed part save reuses
  the created project (no orphan duplicate).
- Project page: header (status `<select>` and owner PATCH inline), tabs
  Overview · Packaging · Truck · Runs · Proposal. Overview hero reads the
  recommended run (else newest done). Packaging/Truck share ONE
  `PackingResults` and show the STORED run (`GET /api/solve-jobs/{id}`);
  a new solve only starts from "Re-run with these parameters", whose rail is
  mirrored from the viewed run's `inputs` and titled "Parameters of this run".
  Runs table: row click → `?run=<id>`, "Mark recommended" → PATCH.
  Proposal is an empty state (F7). Assets is read-only (F8 adds edit).
- `runSolve` takes `projectId` and returns `{result, solveJobId}`.
  `feedback.html` deep links moved to the hash routes.

**Verified** — tester over real HTTP (port 8011) and three headless-Chrome
walks driven over the DevTools protocol (Node 22 WebSocket + Chrome.app;
recipe saved to memory — always pass `--user-data-dir`, or a launch hands
the URL to Rahul's open Chrome and pops a visible tab, which happened once),
screenshots read back: no console errors in passes 1–2 (pass 3 sampled only
the tabs left open at the end), no solve on mount or tab switch (run_count
flat while clicking around), hero numbers equal the API's RunOut, network
log shows zero fetches of the old recommended run after a re-run, the
processing state renders within 1.6 s of the POST and resolves without a
reload, a blocked part save leaves exactly one project and the retry
attaches the part to it. Solves 18–25 s on the wheel, ~60 s with GIFs at
top_n 5. `top_n` is a fixed 5 in the frontend and is not in the rail.

**Defects found and fixed this session** (tester + `/code-review`): old
route dropped project linkage; best_count paired with catalogue asset name
(65 "in PLS12801" when 40 fit); Truck tab mounted a second solve; Packaging
re-solved on every visit; Re-run used pristine rail defaults instead of the
viewed run's; recommended-run flash after re-run; pending stored run shown
red; orphan project on part-save failure; create_project skipped the
vehicle_id check; runs order had no tiebreaker; feedback.html links dead.

**Known / deliberate**
- Legacy rows with `project_id NULL` (2 parts, 7 solves in dev.db) are not
  backfilled and are invisible in the new UI. Re-upload the Mubea/TRW files
  as projects.
- Rail params are mirrored from the run on screen — solver parameters, not
  part dims; hard rule 2 stands for the part.
- StrictMode double-mount may enqueue one extra dev-only solve on the very
  first (no stored run) mount; the first is aborted client-side.

**Next**: F3 baseline fields (cuboid_count, customer_count gain) on the solve
result + Overview hero; F4 `reasons: list[str]`; F6 trips/year; F7 proposal
PDF. Commit: the whole tree (today's F1/F2 plus the earlier GIF session) is
still uncommitted; a push to main deploys.

## 2026-09-11, later — F3 baseline, F4 reasons, F6 trips, F7 proposal PDF

Goal (`/goal`): "pick f3,f4 till f7". All four shipped and verified; ground
truth 40 / 48 on the final code (`gt_final.log`: Mubea 8 → 40 5.0×, TRW 42 →
48 1.1×). Engine math untouched — fields, stamps and read-time helpers only.
Nothing committed.

**F3 — baseline and gain.** `engine.cuboid_count(part_lbh, inner, part_kg,
max_weight_kg)` (moved from ground_truth.py, which now cross-checks it and
keeps its own `base < achieved` assert). `Layout.cuboid_count` and
`BoxDesign.cuboid_count` stamped in `engine.solve`. `RunOut`/`ProjectSummaryOut`
gain `cuboid_count` (of the same object as best_count), `customer_count`,
`gain_vs_cuboid` (ratio, 2 dp), `gain_vs_customer_pct` — computed read-time
in `runs._run_out` (moved out of main.py), so editing the customer's pack
updates the gain without a re-solve. UI: hero lines "65 vs 18 cuboid (3.61×)"
/ "65 vs 32 today (+103.1 %)", a Project inputs card (annual volume,
customer parts/box, customer box, route km — PATCH on blur, "not provided"
when blank), "vs cuboid" column on the list, "cuboid N" chip on ranked cards.

*Fact learned:* the CAD-measured Mubea baseline is **10**, not the deck's 8 —
the resting extent is 1092×298×143 against the nominal 1085×190×285.
CLAUDE.md now says both. The 40 stands.

**F4 — reasons.** `app/reasons.py: reasons_for(...)` composes ≥ 3 sentences
per layout/design from existing fields (pose, interleave %, geometry- or
weight-cap, raster band, clearance, cuboid, dunnage fit); stamped in the
worker; `LayoutOut`/`BoxDesignOut`/`RunOut.reasons`. UI: "Why this design"
card on the Overview, "Why" disclosure on every ranked card. Self-check
`python -m app.reasons`.

**F6 — demand → trips.** `engine.trips_per_year(annual_volume, parts_per_truck)`,
read-time on `RunOut` (`parts_per_truck`, `trips_per_year`), not stored, so
the volume can be entered after the solve. Truck card shows both; "enter
annual volume" when null.

**F7 — proposal PDF.** `proposals` table + `ProposalOut`; `POST
/api/projects/{id}/proposal` (202; 422 without a finished, non-empty run),
`GET /api/projects/{id}/proposals`, `GET /api/proposals/{id}`; Celery task
`render_proposal` → `worker.run_proposal` with the same dev fallback as
solves. `app/proposal.py: build_pdf(project, part, run, run_out, out_path,
db, png_dir)` — matplotlib PdfPages, A4 landscape, 9 pages: cover, part &
pose, ranked comparison, insert BOM, exploded PNG, GIF first/middle/last
strip, truck load to scale inside the cargo bay, baseline & gain + reasons,
assumptions. Returns `numbers` (~24 printed values) which
`tests/test_proposal.py` (22 checks) compares to `RunOut`/`result_json`,
plus a negative proof that a corrupted truck value changes them. Run
selection lives once in `runs.proposal_run_for` (recommended, else newest
done with content) for the route and the `python -m app.proposal <id>
[--png]` CLI. Renders in ~1.3 s on the worker. `/api/files` now answers HEAD.
UI: Proposal tab (generate, list, status pill, Download PDF; 2 s interval
poll), Overview's Generate button live via `?generate=1`, stripped so a
reload does not re-fire.

**Verified.** Tester walks over real HTTP and headless Chrome (CDP) for
F3/F4/F6 (Mubea) and F7 (TRW), all numbers equal between API, PDF `numbers`
and screen, no console errors. Defects found by testers and `/code-review`
and fixed: proposal polling that stopped after one cycle; "cuboid 0" chip on
legacy runs; PDF crash on a run with no box; truck page drawn from seed data
instead of the DB box; `numbers` covering 9 of ~25 printed values; CLI
ignoring the recommended run; HEAD 405 on files; tables clipping text; truck
page without the bay outline.

**Known / deliberate.** `cuboid_count` uses the first pose's extent for every
layout (one wheel pose differs by a voxel; no seeded asset changes count).
No Fira Sans on this machine → DejaVu in the PDF; install the font on the VM
for the real look. `top_n` is fixed at 5 in the frontend. Legacy runs show
"Reasons ship with the next solve".

**Next:** F10 stage ladder while solving, F8 assets edit UI, F9 cost/CO₂
(inputs exist on the project), F11 activity. Then commit and deploy — the
whole tree is still uncommitted and a push to main deploys.

Deployed: commit 488df8f pushed to main; Cloud Build 7468e02a SUCCESS at
13:22 IST (build → push → compose up on the VM); packit.trakkia.com answers
401 (basic auth) as before. Postgres on the VM gained the three columns via
`_ensure_added_columns` and the projects/proposals tables via create_all.

Hotfix after first live use (VM e2-standard-2): a solve took ~5 min because a
GIF was rendered for all 5 ranked boxes + custom (~40 s each on the VM) and
leaving/returning to Packaging during a pending solve enqueued a second one.
Now: GIF only for catalogue[0] + custom (`worker.GIF_FOR_TOP_CATALOGUE_ONLY`);
`pickedRun` falls back to the newest pending/processing run and polls it;
`onSolveStarted` reloads the project so a remount sees the pending job; the
vehicle select defaults to 32_ft_sxl.

Session close (2026-09-13): hotfix 224d994 deployed and confirmed on the VM
(worker restarted with GIF_FOR_TOP_CATALOGUE_ONLY=True). Rahul has the site
credentials (Secret Manager `part-intake-basic-auth`). Open for next session:
browser findings from Rahul on the live site (4 console errors seen in
DevTools, not yet identified); F10 stage ladder, F8 assets UI, F9 cost/CO2,
F11 activity; consider a background artifact task so the count returns
before the drawings on the 2-vCPU VM.

## 2026-09-14 — Live IGES outage: libGL missing from the backend image

Rahul: nothing parses post upload. Worker log on the VM: every IGES job died
with `ImportError: libGL.so.1` from `step_fallback.iges_declared_unit`.
`python:3.12-slim` has no apt packages and cadquery-ocp links libGL/X11 even
headless — OCP has never been importable in the container. STEP hid it
(cascadio is self-contained); only IGES and the OCP STEP fallback import OCP.

Fix `e10abc7`: apt-install `libgl1 libglu1-mesa libxext6 libxrender1 libsm6
libfontconfig1` + a build-time `RUN python -c "import OCP…"` guard. Cloud
Build SUCCESS 11:13 IST; tester verified on the VM (import ok, 0 ImportError,
`sample.igs` upload → done, 916/916 faces). That test upload left one stray
part on prod — no DELETE route exists yet — remove via psql or leave it. Still open: Rahul's 4 console errors,
F10/F8/F9/F11, count-before-drawings on the 2-vCPU VM.

## 2026-09-14 — Ticket 1: solution-first Packaging screen + `packed_url`

Rahul's live-use feedback (docs/AUDIT_RECONCILED.md R1–R3): the first screen
after a solve must be the answer, not the box list; fewer yellow warnings,
less text; the GIF/exploded view were buried. Now the Packaging tab leads
with a hero — count, box, pose, a Packed / Exploded / Packing-order image
stage and a certificate card of the backend's own numbers — then the ranked
list. Warnings collapse to one "N notes ▸" line. Hero = the run's best
layout via `custom_beats_catalogue` (rule 9), never `options[0]`.

Backend: `build_gif` also returns the hold frame as PNG; worker writes
`<stem>_packed.png` and stamps `packed_url` on catalogue/custom dicts;
`LayoutOut`/`BoxDesignOut` declare it. The `_selfcheck` now asserts the
packed PNG is pixel-identical to the GIF's last frame (the first version of
that check could not fail — reviewer caught it). No fake "Packed" tab when
`packed_url` is null. ErrorBoundary around the 3D preview: WebGL failure
shows a notice, the form and Save & calculate stay live. ≤1180 px stacks
results above parameters. `test_solve_api.py` covers packed_url over real
HTTP (57 PASS); ground truth 40/48 holds; headless-Chrome shots at 1440×900
and 1100×800 in the session scratchpad.

Next in build order: #2 count before pictures (split render task), ticket 3
warnings/text ("not watertight" fires on 16/16 customer files — drop it from
the UI), R4 exploded view v2, R5–R7 pose honesty/stability/angle ladder.

## 2026-09-14 — Ticket 2 count-before-pictures; ticket 3a warning noise

Ticket 2 (audit #2): `run_solve` commits the counts and marks the run `done`
in ~3 s, then enqueues `render_run(job_id)` on a new `render` queue; a
second compose service `worker-render` (`-Q render --concurrency=1`) draws
PNG/GIF/packed/PDF and stamps the URLs into `result_json` in place.
`SolveResultOut.render_status` pending|done|failed (+ `render_error`),
default "done" so old runs still read as rendered. Broker down: nested
in-process fallback inside the existing background thread (landmine 6).
Frontend keeps polling while `status=done && render_status=pending`; the
hero shows the count with a "Drawing the packed box…" placeholder and the
Packed image lands on the same page without reload. New opt-in real-HTTP
test `test_solve_api.py --http` (real uvicorn + celery) proves the count
poll precedes the drawing poll; shown to fail against the old single-phase
worker. Review clean on drift/stuck-pending/race/compose.

Ticket 3a: the "not watertight" warning fired on 16/16 customer files and
the multi-solid one on 9/16 — both deleted from `warnings`; the facts were
already on `ExtractionResult`/`ExtractionResultOut` as `watertight` and
`solid_count`. Remaining warnings: unit not mm, unit unreadable, unusual
dims. `test_open_shell_is_a_fact_not_a_warning` bites.

Deploy note: `up -d` creates `worker-render` on the next deploy; check
`docker compose ps` shows three backend containers. Open: Rahul to click
Re-solve on prod project 1 to see the Packed tab on a fresh run; ticket 3b
(frontend text cuts, hide Manual entry, route_km), R4 exploded view v2,
R5–R7 pose.

## 2026-09-14 — Ticket 3b prose cuts (79108e4); R4 exploded view v2

3b: manual entry behind a text link; draft-box message says what draft
means (global catalogue, excluded from default rankings — Packaging has no
project scope, flag if per-project boxes are wanted); Route (km) input
removed (backend only echoes it); long helpers cut to one line.

R4 (`insert_drawing.py` only): dark slate ground, count at 40 pt, layers
exploded along Z by build step (half a pitch, capped at +60% box height),
faint centre guide, leader lines to a tabular spec column. New
`_check_spec_column` pulls every number back out of the column strings and
asserts it is a BOM/layout field; shown to bite. GIF + packed PNG
byte-identical to HEAD. `dunnage.CAVEAT` cut from 7 lines to one sentence
(shared by the drawing footer, API and proposal PDF). Height now varies
(wheel 1642 px, bar 1474 px); the hero letterboxes at 480 px.
Follow-ups: proposal PDF pastes the dark PNG on a white page — needs a
panel; run-id footer needs worker.py to pass the id; detail inset skipped.
Next: R5–R7 pose (hide near-duplicate candidates, drop "(most stable)",
support-polygon stability, 45° ladder); #1 proposal takes run_id; #4/#5
bounded async, /api/health; per-project custom boxes?

## 2026-09-14 (session close) — R5+R6 stability, #1 proposal run_id: committed locally, NOT pushed

Deployed today (all SUCCESS on packit.trakkia.com): ac925b6 solution-first
screen + packed_url; baca966 count-before-pictures + worker-render service
+ warnings cut; 79108e4 prose/dead ends; e6d3572 exploded view v2.

Committed locally at session end, awaiting push (push = deploy):
- R6 real stability: `support_polygon()` = hull area of vertices within
  max(2 mm, 0.5% of footprint short side) of the floor / OBB footprint;
  `support_area_ratio`, `cg_height_mm` (surface centroid, rule 4),
  `tip_ratio`, `support_polygon_mm` on candidates + response model
  (Optional/None so old jobs read "unknown"); `rank_basis`. Candidates walk
  all six OBB faces, sort by (-ratio, cg), dedupe within 1% dims and 0.05
  ratio, `_keep_every_footprint` truncation (tested, bites). Labels: no
  "(most stable)", no verdict words (calibrated wrong on real parts:
  rank-0 ratio 0.07–0.57 across 7 files); "(other end down)" for a kept twin.
  Axle casing now ranks upright (0.565) first; side-lying is 0.020.
  Ceiling: curved contact reads sqrt(2·tol·R) — comment in geometry.py.
- Engine `measure_distinct_poses`: flip twins reuse the measurement (wheel
  4 cards / 3 voxelisations). Inverter worst case ~430 s at 8× VM — near
  the 570 s soft limit; watch it.
- R5 frontend: "How it rests" + one-line "solver tries every pose", "Only
  this pose" checkbox, `contact NN%` tag, notes grey not amber,
  orient-chip ellipsis.
- #1 proposal: `POST /api/projects/{id}/proposal?run_id=` (404 other
  project, 409 not done, 409 render pending, 409 render FAILED naming the
  error); cover "Run #<8> · date"; dark panel behind exploded PNG;
  frontend passes the viewed run's id, resets it on project change,
  shows "Generated from Run #…", grey line for 409.
Verified on the final tree: 15/15 backend suites exit 0, 7 geometry tests,
ground truth 40/48 (geometry agent's run on the final geometry state; the
combined run was killed by low memory after suite 15 — re-run
`ground_truth.py` once before pushing). Frontend build + 3 checks clean.

Next session: 1) `venv/bin/python tests/ground_truth.py` → push → watch
Cloud Build → tester on live (needs a Re-solve on prod project 1, which
the classifier blocks me from triggering — Rahul clicks it). 2) Open:
`poses_searched` still lists 4 labels (worker.py:502, one line); Packaging
table has no project scope (draft boxes are global); proposal default path
409s during the render window instead of falling back to an older rendered
run; stray `sample.igs` part on prod; R7 45° ladder; #4/#5 bounded async +
/api/health; contact classification (face/line/point) for the stability
score. Rahul's "4 console errors" did not reproduce on the new bundle.

## 2026-09-15 — user feedback logged (no code)

Rahul brought three pieces of user feedback; written up as tickets F1–F3 in
PLANNING.md §10: (F1) too much on one screen, (F2) exploded view / GIF not
zoomable and unreadable at hero size — open the existing ImageStage modal on
click, fit to viewport, (F3) dark ground on the exploded view is
uncomfortable — revert to the light card (undoes the R4 dark ground and the
proposal.py dark panel). F3 is the smallest and should go first; F2 next;
F1 is the redesign and wants a ticket per section.

Later the same day: ground_truth.py 40/48 exit 0 → pushed 4297c92.
Then shipped F3 + F2 + a GIF bug Rahul spotted:
- GIF drift: `build_gif` used `fig.tight_layout()`, which sized the axes to
  each frame's caption, so the box slid ~30 px between a dunnage step and a
  parts step. Now `subplots_adjust(0,0,1,1)`; `_selfcheck` asserts the box's
  leftmost/topmost dark pixel is identical across frames (proved to bite on
  X by reintroducing tight_layout; Y added after review showed an X-only
  check passed a 16 px ylim jitter). Side effect: GIF/packed PNG bytes
  changed vs HEAD, so F3's "byte-identical" clause was checked against the
  post-fix render, not HEAD.
- F3: `D_*` dark palette deleted, explode_png back on white; `_paint`/
  `_draw_asset` lost their R4 colour params; proposal.py dark panel gone.
  Centre guide on C_LEADER (C_BASE is near-black on white). All corners of
  all four demo PNGs (255,255,255). Ground truth 40/48 on the final tree.
- F2: hero image is role=button/zoom-in, opens the existing ExplodeModal on
  the clicked view; modal generalised to `views`; Fit / 1:1 toggle; card
  96vw×96vh. Two CSS layers bit: the modal is nested in `.hero-image`, so
  the 480 px thumbnail cap won by specificity (now `.hero-image >`), and
  max-* never grows a 736×528 packed PNG/GIF (now `height: 88vh` in Fit).
  Tester in headless Chrome at 1440×900: all 7 checks PASS, 0 console errors.
Follow-ups: GIF is rendered at 736×528 (80 dpi) and is upscaled 1.5× in
the modal — raise dpi if it reads blurry (size gate is 1.5 MB, bar is at
1.17). Modal does not return focus to the trigger on close (pre-existing).
Next: F1 one-question-per-screen (ticket per section); then yesterday's list.

## 2026-09-15 — F4 live packing animation, F5 profile, Gemini UX audit

Rahul picked the live Three.js animation over the GIF from a side-by-side
sample ("animation looks sexy"), so F4 shipped this session; he also said
solving "takes too long" (→ F5, measured, not yet built) and asked for a
Gemini-driven UI/UX audit with honest triage.

- **F4 backend** (`insert_drawing.py`, `schemas.py`, `worker.py`): `_place`
  records the per-step dunnage solids and part origins it draws, so the JSON
  `sequence` and the GIF come off ONE placement call (hard rule 9). Captions
  moved into `_captions()`, shared by `build_gif.frame` and `_sequence`;
  GIF/PNG bytes are hash-identical to HEAD (geometry reviewer, three
  fixtures + both explodes). `build_gif` returns `(gif, packed, sequence)`
  and takes the pose 4×4; `run_render` stamps `layout["sequence"]` and
  `custom["sequence"]`; response models declare every field. Self-check
  `_check_sequence` now asserts the origins ARE the engine lattice (a
  cell-index or metre implementation fails — proven), parts per step ==
  grid, and round-trips the dict through `BuildSequenceOut` (dropping a
  schema field fails — proven).
- **F4 frontend** (`PackAnimation.jsx`, `PackingRecommendation.jsx`,
  `index.css`): GLTFLoader + `MM_PER_M` (scale.check now expects exactly 3
  sites), pose matrix applied alone inside one Z-up→Y-up `world` group,
  InstancedMesh per GLB primitive, seekable timeline, caption card,
  Play/Pause/speed. Default hero view when `sequence && glb_url`; "▶
  Animate" in the Insert BOM; opens in the F2 modal. Tester (real TRW run
  over HTTP, CDP): 17 steps / 48 origins / 4×4 pose on the wire, canvas
  mounted, "48 of 48 parts placed" at seek end, 0 console errors. Tester
  bugs fixed: camera aimed at +By/2 but the world flip puts the box at
  −By/2 (a third of the box was off-frame — re-shot centred), and the
  animation view had no click-to-zoom. Reviewer fixes: no render while
  paused, pose_matrix guard, modal sizes the canvas.
- **F5 — solve speed, measured** (PLANNING §10): count in ~6 s; the ~26 s
  tail is 2 GIFs (18.5 s) + 6 explode PNGs (6–7 s), rendered serially.
  Ticket lists S1 (GIF only when no GLB), S2 (hero first), S3 (parallel
  renders). Not started — same files as F4, do next.
- **`tools/ux_audit.py`**: headless Chrome captures 10 routes, sends them
  with the brief to `gemini-pro-latest`, writes `docs/ux-audit-<date>.md`.
  Run only on the non-NDA sample part. Triage in PLANNING §10: accepted
  hero hierarchy / Re-run placement / multi-select label (→ F1 shape),
  "1 boxes" (fixed), Truck tab shows the Packaging hero (→ F6, confirmed
  from the screenshot); rejected textarea, badge, sub-tab-removal and
  duplicate-button claims with reasons.
- **Dev landmine fixed**: `render_run` is routed to the `render` queue, and
  the documented `celery -A app.worker worker` command only consumed
  `celery`, so dev renders sat at `render_status=pending` forever. Worker
  now declares both queues; prod's `-Q render` still narrows.
- **Process**: five agents ran in parallel (tester ×2, reviewer ×3). Two
  early agents collided on redis db 9 / port 8010 — every parallel agent
  now gets its own port, redis db, celery `-n` and sqlite (memory note).
- Ground truth on the final tree: Mubea 40 / PLS12801, TRW 48 / PLS1280,
  `test_solve_api` all checks passed, insert-drawing self-check passed.

- **F5 S1 shipped** (commit "backend: worker skips the animated GIF
  frames"): render tail 26 s → ~1 s on TRW over HTTP (tester: POST →
  render_status=done 9–12 s including the solve). Simpler than ticketed:
  the "GIF for no-GLB parts" branch was unreachable — `run_render` loads
  the mesh FROM the GLB, so a part without one cannot render at all (a
  test that nulled `glb_path` proved it). The worker always passes
  `frames=False`; the GIF path lives on in the self-check. S2/S3 are
  moot at a 1 s tail — leave them unless prod says otherwise.
- **F1 shipped** (commit "ui: one question per screen on Packaging"):
  hero grid gone (rows live in Box Packing Analysis, which now follows
  the hero box until a card is clicked — reviewer caught the
  custom-beats-catalogue mismatch), inline `why` / `N notes`
  disclosures, Re-run in the rail, codes in the boxes-to-rank summary,
  ranked heading at the 1440×900 fold with the viewer at 320 px. Lesson:
  the first pass hit the fold by shrinking the animation to 200 px — the
  hero viewer is the product, reclaim space from chrome, never from it.
- **F6 shipped** (commit "ui: Truck tab opens on the truck plan"): same
  mounted PackingResults, hero hidden not unmounted, TruckSection reused
  in its place, sub-tab dropped while it would repeat. CDP: canvas count
  1 across the round trip, timeline keeps advancing, no refetch.

Open: GIF caption card overlaps the box top edge in frame 1
(GIF now only in the self-check, so low priority); `voids` not in the
sequence (tray reads as a translucent slab, alpha 0.30); the animation's
"Empty box" caption card touches the box top edge at t=0 (cosmetic).

Deployed: 6d78736 pushed to main; Cloud Build 6f54655e SUCCESS (asia-south1).
Prod workers keep explicit `-Q celery` / `-Q render`, so the dev-only
`task_queues` declaration in worker.py changes nothing on the VM. gcloud auth
on this Mac has expired (`gcloud auth login` needed to read build logs); the
GitHub check run on the commit is the deploy signal meanwhile.

## 2026-09-15 (later) — SX4 cover "what a failure": F7/F8/F10 landed, sweep built

Rahul picked one random STEP file (Maruti SX4 floor side cover, NDA, local
only) and the animation laid parts across each other and out of the box.
Root cause F7: layouts that win footprint order (1,0,2) were posed with the
raw resting rotation. Landed today, in order:

- 4db3277 drawing: F7 — `Layout.turned`, `IN_PLANE_TURN` (Rz+90, paired
  with `np.rot90` silhouette; the old transpose was a mirror), one
  `rotation_for` expression behind voxels and `pose_matrix`. Posed AABB ==
  turned extent; no "clipped by the volume" on the cover.
- 779813b ui: F8 — camera fits projected corners at any aspect. Review
  caught the two-pass fit oscillating; damped fix verified in a three.js sim
  on all 17 catalogue boxes at three stage sizes.
- 941d67d ui: F10 — client-derived insert tray and the "slotted comb" note
  deleted; Insert tab shows the worker's BOM only.
- 5adecb2 backend: `tests/sweep.py` — every CAD file (16 in ../Rahul + 9
  fixtures), engine + drawings, flags table. First full run, on the F7 tree:
  0 extract/engine/render failures, GT 40/48 hold, 16 of 25 winners are
  TURNED (so F7 affected most real parts, not one), 1 CLIPPED (F12), 4
  BELOW_CUBOID (E2), 5 NO_NEST. Table at the session scratchpad
  `sweep/sweep.md`; ~25 min wall on this Mac.

Re-judged F9 after F7: geometry right, still unreadable at 13 layers —
respec'd in §10. New tickets F11 (pocket BOM narrower than the part on
interleaved poses), F12, E2, S2 (sweep is part of the cycle). E1 stays open.

Ground truth: 40/PLS12801, 48/PLS1280, Tata decks PASS. Frontend build and
six lib checks pass.

Process notes: four parallel agents plus headless Chrome overran the 8 GB
Mac (1.1 GB test process, 580 % CPU swiftshader) — two agents at a time from
now. The session hit the API limit at ~15:20 (reset 15:30); the geometry
reviewer and sweep agent died mid-flight and the PM finished both reviews
by hand. Research: Fraunhofer/scapos PackAssistant already does our core
problem including nested stacks (E1) with a JSON API — recommend a trial
benchmark on the 16 files before more engine work. Rahul also shared
`Pack_Studio_UI_UX_Workflow_Analysis.docx` (competitor demo analysis):
their differentiator is the connected chain part → orientation → box →
protection → pallet → shipment → cost → report with a binding-constraint
explanation; we have most of the chain except economics and pallets, and
their "recommendation card + binding constraint in plain words" matches
Rahul's taste memory exactly.

Not pushed (Rahul pushes on request; f3b018c onward unpushed).

## 2026-09-15/16 — F11/F13/F14/F15/F17 landed: legible pictures, no-prose results page, the insert drawing

Rahul's verdict on the packed/exploded voxel PNGs ("looks like shit … I am
not able to understand anything") and on the Insert tab ("what is the use of
this whole process if I am not able to tell the person what insert to get
manufactured") set the day. Landed, in order:

- 115bcd0 ui: F14 — packed box from the real mesh, cardboard on white,
  layer slider, hide-dunnage, Play-from-rest fix.
- 343c4e1 drawing: F15 — Front/Side/Top silhouettes for the report. Snap of
  dunnage rectangles to the cell lattice was the root fix for the stray line
  (four coincident edges per layer); review added a clamp to the box.
- aa4eb70 ui: F13 — results page = 3D view + key/value list from
  `resultRows()` + cards with three numbers + plain BOM table. Every chip,
  badge, caption, tab and sentence deleted (two rounds; round two removed
  the 3D step overlay, the misleading `warnings[0]` line, the BOM caveat and
  the parameter-panel helper text). Screens at scratchpad `f13/*_r2.png`.
- d5c9115 ui: render_error shown whenever set.
- 8d7fc9a backend: F11 — `layer_sheets` archetype for interleaved poses,
  invariant `_assert_cells_hold_the_part` inside `bom()`. Review (three
  rounds) found the real defect: sheets the parts do not nest into cost
  height nothing charged, so a 420-part layout shipped where 378 fit. Now
  `Bom.layer_step_mm` is what the engine solves layers on, the BOM charges
  and the drawing stacks at (one field, three consumers). Full 25-file
  sweep: byte-identical winners, 8 parts now `layer_sheets`, 40/48 hold.
- 940a44b docs. f2a5660 drawing + 179d23a ui: F17 — `insert_sheets_png`,
  one sheet per BOM element (plan with pockets at `_place`'s positions,
  section with depth/step/nest, title block), on the BOM rows and in the
  PDF (+2 pages for TRW). Self-check harvests every dimension text on every
  sheet and refuses any number not from the BOM / inner / an origin.

Ground truth 40/PLS12801, 48/PLS1280, Tata PASS on every run. Frontend
build + 6 lib checks pass.

Tickets added: F11a (bom() raising fails the whole solve — should degrade
the BOM), F11b (no noise floor on the interleave test; the implementer
argues against one), F13a (backend emits the per-layout binding-constraint
sentence). Open for Rahul: E1 (do interleaved parts need a comb, or are
sheets enough?), E2 (cuboid baseline clearance), PackAssistant trial, push.

Process: two agents at a time held; every ticket got a scoped review and
every confirmed finding a bite proof before landing. One agent stalled on
its last item (harness watchdog) — the item was already on disk and passing,
PM proved the bite and committed. One agent created `scratchpad/` inside the
repo (353 MB incl. a dev.db with an NDA upload) — moved out to the session
scratchpad before commit; give agents the absolute scratchpad path and say
"never inside the repo".

Not pushed (Rahul pushes on request; 4982a9f deployed; 10 commits unpushed).

## 2026-09-16 — Three-model packaging debate (Fable / Opus / Gemini), paused before the second sign round

Rahul's ask: archetype 3 (`layer_sheets`) counts parts held by nothing; die-cut
foam is often too expensive; retention against transit damage must be part of
the maths. Rather than design it ourselves, a three-agent critique debate
(Fable 5.1, Opus 5, Gemini 3.1 Pro) ran on a stdlib harness, unlimited
rounds, PM injecting engine facts as non-debater notes. Steered from material
pricing back to packaging maths after round 2 on Rahul's instruction.

**Durable copy (session scratchpad will not survive):**
`~/.claude/projects/-Users-rahulsharma-PycharmProjects-part-intake/debate-2026-09-16/`
— `debate/` (harness, brief, state.json, transcript.md 213 KB, verdict.md,
PM notes, `resign.py`, `pm_addendum.md`) and `diag/` (diagonal-collision
spike: script, report.md, results). Local only; diag names customer CAD.

**Status: converged in round 5, chair Gemini drafted, both signatories
DISSENTED on drafting defects, not on the design.** Fable: cost column mixes
per-box and per-element (Row 4 comb is ₹9.7k/box, not ₹350); PU40 slab cannot
deliver 706 N (needs PU60–80 or the tag must say UNACHIEVABLE), PU compression
set caps slab life ~20 trips, slab height is 33 mm not 25; HDPE strips cannot
be welded to a PP base; return-freight footprint count unstated (16 vs 24);
no per-row outcome table. Opus: friction freeze must be judged at the top
layer of the stack (min over layers), non-folding inserts raise `h_fold` in
the return-freight divisor (~₹180/box/trip, not a ₹20 strap), tooled elements
must be sized at the pessimistic end of the pitch band.

**What the debate settled (all three signed these):**
- Retention family ordered by DOF removed per mm of stack-up and return cube,
  not ₹/kit: perimeter pads → preload slab + PP face plate → soft interleaf →
  follower + 2 straps + hard stop → interrupted-strip comb → (peg board, foot
  pockets, thermoform outside the six rows). Zero die-cut, zero tooling on
  all six brief rows.
- `dunnage.sheet_step_mm = max(0, sheet − nest_depth)` is a CONFIRMED engine
  defect: a rigid sheet cannot hide in a vertical nest. Flat shell 65 → 60
  (55 with 5 mm EPE for Class A). Ticket regardless of verdict.
- Loadability: Δ_proj = p_sil − p_vol; 0 → straight descent, pre-load
  elements legal; >0 → oblique sweep → tilt sweep → REFUSE "no load path".
- Retention computed, not assumed: 8-direction 1 g drift relaxation with
  friction freeze, free-stand test (r_min ≥ 20 mm, tip ≥ 15°), `compact()`
  displacement-controlled jam test for the follower.
- Gain-noise gate (refuse gains inside the ±4 mm voxel band: block part
  150 vs 144 refused), weight gate, missing-input dual branch + `RANKING
  UNRELIABLE` banner, catalogue needs fold_type / folded_void_h / lid_void.
- Row verdicts: shell flat 60/55, stamped bracket 60, flat bracket 48 (comb),
  slender bracket 840–900 band (follower or comb per `compact()`), shell on
  edge and block part refused.

**Diagonal-neighbour spike (geometry agent, read-only, 24 files):** the
engine tests only single-axis offsets. 7/24 parts collide on a multi-axis
offset; 4 are real crashes by a 2 mm re-voxelisation test (cell count grows
3–3.4×): rear shroud 60→50, front fairing 48→32, visor 408→297, motor cover
810→675. **Stabiliser bar 40 collides on 28 grazing cells (ratio 1.04×,
volume falls 8×) — a strict check drops the deck-proven 40 to 30.** The
mandated full-offset check therefore needs a contact tolerance (collision
only if it grows under refinement). TRW 48 has no in-plane interleave and is
immune. Brick bond rescues one part (ZB 3000, s = 128 mm). Written into
`pm_addendum.md` with the voxeliser-bias confirmation (`occupancy` is
surface-subdivide → both grids dilated → pitch biased high; settles the
900-vs-840 dispute in Opus's favour).

**Resume (Rahul paused for token budget):**
```
cd ~/.claude/projects/-Users-rahulsharma-PycharmProjects-part-intake/debate-2026-09-16/debate
/Users/rahulsharma/PycharmProjects/part-intake/backend/venv/bin/python resign.py --max-cycles 3 > resign.log 2>&1 &
```
`resign.py` archives verdict.md as verdict_vN.md, has the chair (Gemini)
redraft from the draft + both dissents + pm_addendum.md, then Fable and Opus
sign again; loops up to three cycles. Needs Gemini key in env or repo `.env`.
Then: Rahul reviews verdict.md; tickets T1–T7 (diagonal check with
tolerance, sheet_step_mm, drift relaxation, free-stand, Δ_proj/oblique/
compact(), mesh-refined pitch + foot thickness, missing inputs + catalogue
fields).

Working tree untouched this session except this entry. Still not pushed
(11 commits). Open: E2, PackAssistant trial, F13a/F11a/F11b/F12/S2/F16,
PLS fold type / lid void question for Rahul.
