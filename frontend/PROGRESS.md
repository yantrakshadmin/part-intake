# Frontend progress notes

Read `../CLAUDE.md` and `../PLANNING.md` first. This file is "where things
stand" for the frontend so a session can resume quickly.

## Local dev

```bash
cd frontend && npm install && npm run dev   # :5173, proxies /api -> :8000
```
Backend must be running (see `../backend/PROGRESS.md`).

## What's implemented (commits b0e0077, 0b794c4, 2f58217)

- `src/lib/glbModel.js` — **single source of truth** for the Z-up -> Y-up
  basis swap (`zUpToYUp`), `candidateMatrix(candidate)`, `loadOrientedModel()`
  (loads GLB, scales x1000 mm, applies candidate pose), and
  `computeTightBounds(object)` (vertex-accurate AABB — see below). Any new
  view component should reuse these instead of re-deriving the transform.
- `src/components/OrientationViewer.jsx` — isometric 3D view (OrbitControls)
  with a blue wireframe OBB box (`Box3Helper`) showing the resting-pose
  bounding box for the selected candidate.
- `src/components/Drawing2D.jsx` — orthographic Front view (X width x Y
  height) and Top view (X width x Z depth), each 300x300px, with SVG
  dimension-line overlays (extension lines, double-headed arrows, numeric
  labels) styled like a CAD drawing.
- `src/App.jsx` — renders Isometric + Front + Top side by side, all driven
  by the same `result.candidates[selected]`.

## Bug fixed this session: bounding-box overestimate (commit 2f58217)

`THREE.Box3().setFromObject()` unions each mesh's *local* AABB after
transforming its corners by the world matrix. For an assembly where
sub-meshes carry their own internal rotations (e.g. a pulley mounted at an
angle), that overestimates the true extent by 15-25%. This caused the 2D
view dimension labels to disagree with the extracted `dims_lbh` for a real
multi-solid part (M&M pump, 10 solids) — NOT a backend bug.

Fix: `computeTightBounds()` in `glbModel.js` walks actual transformed
vertices instead. Now used by both `loadOrientedModel` (2D views) and
`OrientationViewer`'s wireframe OBB. Verified against:
- `tests/fixtures/sample.step` (single solid): 127.00 x 63.50 x 34.92 mm,
  matches across isometric/front/top/form for all 4 candidates.
- M&M pump (10-solid assembly): 155.91 x 134.20 x 86.29 mm, matches across
  all views/candidates after the fix (was off by ~15-25% before).

## Verification scripts (NOT in repo — scratch, in /tmp/pw-check)

Playwright (chromium-only, installed via `npx playwright install chromium`)
was used for headless browser checks, kept outside the repo per the
"keep frontend deps short" rule:
- `/tmp/pw-check/browser-flow.js` — full upload -> candidates -> save flow.
- `/tmp/pw-check/views-flow.js` — screenshots all 4 candidates for
  sample.step (isometric/front/top).
- `/tmp/pw-check/mm-views.js` — same, but for the real M&M pump file, plus
  reads SVG dimension-label text and compares to `dims_lbh`.
These can be re-run or recreated if needed; they're disposable.

## UI restyle + part list + load calculator (2026-06-11, later)

- `src/index.css` (new): light SaaS theme — Inter, cards, segmented controls,
  dropzone, tables, badges. Old inline-styled UI replaced.
- Header nav: New part | Parts | Load calculator (STEP/Manual toggle moved
  inside New part view).
- `components/PartList.jsx`: saved parts table + search (TODO #3 done).
- `components/LoadCalculator.jsx` + `lib/packing.js`: standalone box→vehicle
  calculator. Floor fit = 0°/90° grid + split-strip hybrids, stacking by
  height (optional max-stack cap), payload check, binding-constraint chip,
  SVG top-view floor plan. Custom box entry, saveable to master list as draft.
- packing.js is pure — reuse it for the post-save part flow
  (parts/box × this = parts/truck), which is still TODO.

## Packing recommendation v2 (same day, after user feedback + reference images)

- `PackingRecommendation.jsx`: post-save and on part-click in the list.
  Box cards grid (replaces table), click a card → specs + insert design.
- packing.js `partsPerBox`: orientation set with **this-side-up lock**
  (default on; untick allows on-side/on-end), **mixed-orientation layers**
  (split-strip hybrid, matches the 4+1 gear-shifter reference photo),
  shared-divider pocket pitch, dummy pocket count when weight-bound.
- `insertType(part)`: aspect ≥ 5:1 → comb/slot, else pocket/cell
  (PLANNING Phase 3 archetypes).
- `InsertLayout` SVG: top view of one layer — green filled pockets, dashed
  dummy pockets pushed to one side, comb slats for long parts.
- Custom box add (inner+outer dims) from the recommendation panel → POST
  /api/packaging as draft. Outer defaults to inner +50/50/120 if blank.
- Reference images for insert styles: WhatsApp Image 2026-06-11 at 11.*.jpeg
  in ~/Downloads (Yantra Packs pocket/cell + comb/slot examples).

## v4: light theme restored, 3D foam insert tray (same day)

- **Dark theme reverted** — user wanted the reference screenshot for content
  only. Light SaaS theme is the keeper; do not theme-flip again without asking.
- Insert viz replaced with **InsertTray3D.jsx**: rotatable Three.js foam tray
  (floor slab + outer walls + dividers, pockets = voids, amber foam material)
  ported from the user's ~/Downloads/binpacking_v3.html reference
  (computeInsertDims/buildInsertScene). Per-orientation insert tabs.
- `insertDimsFor()` in packing.js: slab spans box cavity, centered pocket
  grid, equal padding; spec rows = Insert Slab Dimensions + Pocket Dimensions
  (hollow cutout), matching the reference's ins-panel.
- My earlier SVG Top/Side/End dimensioned views were deleted (user disliked).

## v3: dark theme (REVERTED in v4), mixed layers, dimensioned views (same day)

- **Dark engineering theme** (user reference: WhatsApp Image ... 17.06.26.jpeg
  dashboard). Tokens flipped in index.css; 3D/2D viewer canvases stay light
  ("paper") deliberately — CAD drawings read better on white.
- `partsPerBox` v3: **greedy per-layer orientation stacking** — each layer
  picks the best orientation fitting the remaining height, so e.g.
  3 × B_H_L=42 + 1 × L_B_H=24 (matches reference layer table). This-side-up
  checkbox (default on) restricts to upright; that's why solutions show one
  orientation unless unticked.
- Save errors fixed: FastAPI 422 detail arrays rendered readable (was
  "[object Object]"), plus client-side required-field messages.
- **Live packaging fit under the part form** — shows as soon as dims+weight
  are valid, no save needed.
- BoxDetail v2: Insert Layer Configuration table (per-layer orientation chips,
  parts, dummies) + Box Packing Analysis facts + **dimensioned Top/Side/End
  views** (CAD-style tick dimension lines, green pockets, dummy pockets
  dashed on one side, foam sheets in elevations).
- Box cards v2: layer breakdown (42+42+42+24), weight/volume utilization bars
  (amber = binding constraint), parts/truck.

## v5: dimensioned isometric insert drawings, multiple inserts (2026-06-12)

User feedback: post-dimension flow "looks worse"; sent 4 SketchUp-style
reference renders (cream/blue trays, dimension callouts). Changes:

- `components/InsertIso.jsx` (new): static isometric **SVG** drawing of an
  insert tray with CAD dimension callouts (overall L × B × H + pocket
  cutouts), 45° architectural ticks, bold labels — matches the references.
  Pure SVG → no WebGL context limit, so any number of inserts can be on
  screen at once, and it prints. Occlusion trick: in orthographic iso,
  everything visible inside a pocket projects within the opening polygon,
  so pocket interiors (floor + the two min-x/min-y walls) are clipPath-ed
  to the opening — correct for any depth, no z-sorting.
- `lib/packing.js`: `insertDimsFor()` **deleted** — it recomputed a uniform
  grid that could disagree with `bestFill`. Replaced by `groupInserts(fit)`
  (distinct trays by orientation code) + `trayGeometry({box, group, ...})`
  which builds pockets straight from `fill.placements`, so the drawing
  always matches the parts-per-box math (incl. rotated end/side strips).
- `PackingRecommendation.jsx`: InsertPanel tabs removed — **all tray types
  render as stacked cards** (Insert 1 of 2 …), each with the iso drawing,
  spec rows (tray dims, cutout sizes × counts, wall/floor, parts/layer) and
  a per-card "Rotate in 3D" toggle. Dummy pockets shaded grey, pushed to
  one side, count clamped to one layer.
- `InsertTray3D.jsx`: rewritten to consume the same `trayGeometry` object —
  wall lattice is now an ExtrudeGeometry with pocket holes (handles rotated
  strips), on a foam floor sheet. 3D and drawing can no longer disagree.
- Verified with Playwright (`/tmp/pw-check/iso-*.js`): 170×150×70 part →
  PLS12103 shows 5×5 + 4-rotated tray matching reference style; free
  orientation → 2 insert cards; heavy part → grey dummy floors; small part
  → CRT crate render matches reference image 3.

## v6: blue palette, readable numbers, less scrolling (2026-06-12, later)

User feedback on v5: dull colours ("use blue pallet"), "22+22+22…" cryptic,
overlapping layer table, too much scrolling, upload broken (backend — see
backend/PROGRESS.md: INTAKE_REDIS_URL + thread fallback).

- `partsPerBox` now **drops zero-part layers** after the weight cap and
  records `filled` per layer — no more "10 layers, 7 empty, 175 dummy".
  `remainingHeight`/`dummies` recomputed from used layers only.
- `layerSummary(fit)`: "2 × 22 + 1" style run-grouped text; box cards say
  "2 × 22 + 1 across 3 layers" instead of "22 + 22 + 22 + …".
- `groupInserts` splits a **partially-filled top layer into its own insert
  card** (same cutting, different loading) so dummy shading doesn't smear
  over full layers; per-card "N parts + M dummy pockets (grey — weight cap)".
- InsertIso + InsertTray3D: **blue palette** (royal-blue walls, light pocket
  floors, grey dummies) per reference images 2–4; iso figure bg soft
  blue-white gradient.
- UX: box-card grid capped at 6 with "Show all N boxes"; **auto-selects the
  best box** (clicking selected no longer toggles closed); summary chip
  strip (parts/box · layers · weight% · volume% · parts/truck) tops the
  detail; layer table slimmed to Layer/Orientation/Parts/Dummy/Height (the
  repeated L×B columns are gone — that was the overlap); "best" badge only
  on the top card (was on every tie); orient-chip nowrap.
- Verified via /tmp/pw-check/v6*.js: STEP upload e2e green again, piston
  case shows "45 parts/box · 2 × 22 + 1 across 3 layers", 2 insert cards.

## v7: full-screen workspace, truck loading plan, downloads (2026-06-12)

User: use full screen (inputs left / results right), calc on New part screen
(button → "Save & calculate"), truck cards all show same number (it's
correct when weight-bound — now shows "N boxes · M / truck" so it reads
sanely), bigger cards + hover, animated truck loading plan, download
buttons on figures.

- **Workspace layout**: `.workspace` grid — sticky 400px `.rail` (inputs) +
  `.stage` (results); shell widened to 1720px. Applied to BOTH New part and
  Parts pages; collapses to one column < 1180px.
- `PackingRecommendation.jsx` split into exports: `usePackingData()`
  (packaging+vehicles fetch, owned by App), `defaultPackingParams()`,
  `PackingParams` (rail card: clearance/wall/foam/vehicle/this-side-up +
  custom box form) and default `PackingResults` (cards + BoxDetail).
  App owns params for intake; PartList owns its own for Parts page.
- App.jsx: intake = rail (mode toggle, compact dropzone, part form with
  **Save & calculate**, params) + stage (orientation confirm card, live
  PackingResults, empty-state placeholder). PartList = rail (search +
  compact part rows, auto-selects first) + stage results.
- **TruckLoadIso.jsx** (new): animated isometric SVG of the loading plan —
  cargo as dashed wireframe + light far walls, boxes from
  `loadPlan().floor.placements × layers` capped at plan.total (240 drawn
  max), painter-sorted back-to-front = loading order, staggered
  `truckBoxIn` CSS animation (45 ms/box), Replay button, L/B/H dims, chip
  row (boxes, parts/truck, cube %, binding). Lives in `.truck-section`
  (grid-column 1/-1) of BoxDetail when a vehicle is picked.
- InsertIso now exports `isoProject/isoPath/fmtMm/isoDimension` (reused by
  TruckLoadIso — keep them in sync).
- **lib/download.js**: `downloadFigure(el, name)` — SVG→canvas→PNG at 2×,
  or WebGL canvas capture (InsertTray3D renderer now has
  `preserveDrawingBuffer: true`). "⤓ PNG" buttons on each insert card and
  the truck plan.
- Box cards: min 210px, larger count, truck pill ("23 boxes · 7337 /
  truck"), hover lift already in v6.
- Verified (/tmp/pw-check/v7.js): workspace renders, 32 ft sxl plan draws
  23 boxes (9000 kg payload bound — math cross-checked), insert PNG
  download produces a file, Parts page auto-selects and renders.

## Pending / next steps

- Remaining Near-term TODO (CLAUDE.md, stop before Phase 2):
  - [x] #3 Part list page UI — done (rail list + search, v7).
  - [x] #4/#5 Dimension labels + wireframe OBB — done.
  - [ ] #6 Delete/edit part profile endpoints + UI.
- Insert figures (InsertIso) and truck plan (TruckLoadIso) share the iso
  helpers exported from InsertIso.jsx — keep them in sync if either moves.
- Possible next polish (not requested yet): box tare weight field (truck
  math currently uses content weight only — flagged in the custom-box
  form), per-truck-vehicle comparison row, STEP/DXF export of trays
  (that's Phase 3 territory).
- Dev servers as last verified: vite :5173, uvicorn :8000 + celery worker
  (INTAKE_REDIS_URL=redis://localhost:6379/0), redis via brew services.
