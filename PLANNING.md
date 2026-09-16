# Part Intake & Insert Designer — PLANNING v2

Internal tool for the Yantra Packs projects team. Not a product, not for sale.
**Goal: cut the time spent on fitting analysis.** That is where the hours go.

v1 of this plan is preserved in `PLANNING.v1.md`. Read this one.

---

## 1. What the team actually does

A part arrives (3D model, 2D drawing, or bare dimensions). An engineer works out
how to fit the most parts into one of our returnable assets, designs the insert
that holds them, and writes a technical proposal.

The value is **not** in fitting a box inside a box. It is in the cases where
the part is *not* a box.

### Ground truth (from real proposals — these are the acceptance targets)

| Job | Part (mm) | Asset (internal) | Cuboid math | **Shipped** | Gain |
|---|---|---|---|---|---|
| Mubea stabiliser bar | 1085×190×285, 5kg | PLS12801, 1150×750×790 | 8 | **40** (4/layer × 10) | 5.0× |
| TRW steering wheel | 370×360×135, 2.5kg | PLS1280, 1150×750×1000 | 42 | **48** (3×2 × 8 inserts) | 1.14× |

Two archetypes, confirmed against real work:
- **Compact prismatic** → pocket/cell matrix. Near-cuboid, small gain.
- **Long/bent tubular** → interleaved comb/slot on MS rods. Enormous gain
  (10 layers of a 285mm-tall part inside 790mm — the bars nest into each other).

**Any engine that models parts as cuboids loses Mubea 32 parts per PLS.**
This single fact drives the whole architecture.

---

## 2. What survives from v1

This is a re-foundation, not a rewrite.

| Component | Verdict |
|---|---|
| `backend/app/geometry.py` — OBB + resting-pose candidates | **Keep.** Becomes the *outer loop*, not the answer. Needs the fixes in §3. |
| FastAPI + Celery + Postgres skeleton | **Keep.** Unchanged. |
| Frontend shell (rail + stage), `OrientationViewer` | **Keep.** Evolve, don't rebuild. |
| `InsertIso` / `TruckLoadIso` drawing code | **Keep.** Feed it real placements. |
| `frontend/src/lib/packing.js` — client-side cuboid packing | **Split, don't delete wholesale** (rescoped 2026-09-09). Only `partsPerBox` and its private helpers `bestFill`/`uniformGrid`/`gridDesc` encode the cuboid assumption — `partsPerBox` greedily stacks layers with `remaining -= layerHeight`, which is the Mubea error exactly. The other five exports (`layerSummary`, `groupInserts`, `trayGeometry`, `insertType`, `loadPlan`, `floorFit`) are insert-drawing and truck-load helpers that *consume* a fit result, and the InsertIso row below says keep those. Blocked on an API endpoint serving `nesting.nest()` — nothing can be deleted before there is a replacement to call. |
| "part = cuboid" everywhere else | **Delete.** |

---

## 3. Input pipeline — what five real files taught us

Tested against: QY2i steering wheel (.stp), YXA stabiliser bar (.igs),
Y2V_YK9 Rack / IBJ / Housing (.igs), radiator assembly (.SLDASM).

**Zero of five matched what v1 was validated against** (a SolidWorks solid demo part).

| Reality | Requirement |
|---|---|
| All are **surface models** — open shells, no `MANIFOLD_SOLID_BREP` | Hull/OBB path is fine. Volume and watertight checks are not — never report mass properties from these. |
| 4 of 5 are **IGES** | cascadio is STEP-only. Need the fuller OpenCascade binding. Two readers total: STEP (`.stp` and `.step` are the same format) and IGES. |
| **Stray reference geometry** | Real, but *not* present in these five fixtures. The YXA bar carries 44 datum points spanning 736mm against a 298mm part; the Housing fixture is a re-export named `WITHOUT_REF_HOUSING_7` with the reference geometry already stripped. Meshing `TopAbs_FACE` only excludes points and free curves by construction — that is the filter, and it is already in place. |
| `.SLDASM` is proprietary binary, 48MB | **No open-source reader exists.** Fail loudly with guidance ("request STEP/IGES from customer"). Do not silently accept. |

### Pipeline
```
STEP / IGES ─┐
2D drawing ──┼─> extract ─> largest-body filter ─> hull ─> min OBB
bare dims  ──┘                                        ─> resting-pose candidates
SLDASM ──────> explicit unsupported error            ─> per-pose silhouette
                                                            │
                                              HUMAN CONFIRMS (never skipped)
                                                            ▼
                                                      Part Profile
```

Rules:
1. **Faces-only is the stray-geometry filter.** A connected-body threshold was built
   and measured: it breaks real parts. IGES faces share no topology, so raw
   `trimesh.split` gives 48,960 components on the IBJ (largest 7.96%); after
   `BRepBuilderAPI_Sewing` the largest threshold that is a no-op on all five files is
   0.01%, and at 0.10% the Rack loses a genuine 124.7mm panel. Revisit only with a
   file that actually exhibits the problem.
2. **Human confirm stays.** Not because automation is wildly off — the one externally
   checkable file lands within 2% — but because nothing in the file tells us which
   body is the part.
3. 2D drawings and PDFs: use a vision model to propose dims *and profile outlines*,
   then the same confirm step. Do not write a CAD-drawing parser.
4. **A part entered as bare dimensions can only ever get the cuboid answer.** The UI
   must say so explicitly — "cuboid fit only, no nesting gain available" — or the
   team will trust a number with no upside in it.

---

## 4. The engine

Inserts are **layered** (parts, foam/PP sheet, parts), and every shipped proposal
is a **regular lattice of one part** — Mubea 4x10, TRW 3x2x8. So the question is
not "how many boxes fit in a box" and not "how do irregular outlines tile a
sheet". It is: **how close can two copies of this part sit on each axis?**

> **Corrected 2026-09-09.** This section previously said "2D irregular nesting
> per layer, then stack". That is wrong, and wrong in the expensive direction:
> `stack()` divides the asset height by the layer height, which reintroduces the
> cuboid assumption on the vertical axis — the exact assumption this project
> exists to remove. It loses 8 of Mubea's 10 layers. Measured pitch vs bounding
> extent, which is what the engine now solves for:
>
> | | lateral | vertical |
> |---|---|---|
> | Mubea, implied by the deck | 285 → 155mm (1.8x) | 190 → **66.7mm (2.8x)** |
> | TRW, stated in the deck | 370 → 376.6mm (none) | 135 → **120mm (1.1x)** |
> | YXA bar, measured from CAD | 300 → 140mm (2.1x) | 148 → **68mm (2.2x)** |
>
> A 2D nester cannot see the right-hand column at all. TRW is the *same*
> phenomenon as Mubea, not a second archetype: a 135mm wheel on a 120mm pitch
> because it sits **into** its 117mm tray.

```
for pose in candidate_resting_poses(part):        # existing geometry.py
    grid  = occupancy(part, pose)                  # 3D boolean, voxelised
    pitch = [min_pitch(grid, axis) for axis in (L, B, H)]
    for asset in catalogue:                        # containers only, enumerable
        count = lattice_count(extent, pitch, asset.inner, weight_cap)
        score(pose, asset, count)
rank -> top 2 catalogue + 1 custom (§6)
```

`n = floor((inner - extent) / pitch) + 1` per axis: the first part needs its full
extent, every one after it needs only the pitch. That single formula reproduces
both shipped proposals exactly from their own decks' numbers. There is exactly
one copy of it in the codebase (`app/nesting.py`) and `tests/test_nesting.py`
pins it to 40 and 48.

- **We did not use `jagua-rs` or NFP + bottom-left-fill,** both named here
  before. Reasons in order: (1) both are 2D and cannot see the vertical
  interleave above; (2) a raster needs no clean outline, and these are open
  surface models — the IBJ splits into 48,960 disconnected faces, so
  polygonising for an NFP is a project on its own; (3) `jagua-rs` is a Rust
  crate and there is no cargo on the dev machine, NFP needs shapely. The voxel
  raster needs numpy and trimesh, both already installed. **Zero new deps.**
- **Exact fits are the normal case, not the edge case.** 4 x 155 in 750-285 is
  exact; so is 3 x 376.6 in 1150. `lattice_count` carries an `EPS` of 1e-6 so
  float noise cannot lose a whole row, and a test pins it. It does **not**
  absorb a typo: a pitch typed `186.7` instead of `560/3` is 0.03mm wrong and
  gives 30, not 40 (audit, 2026-09-10) — ground-truth pitches are stored as
  exact fractions for that reason.
- Angle ladder, in order of payoff: in-plane rotation θ → **mirrored/interlocked
  pairs** (alternating 180°, where the stab-bar gain lives) → tilted poses.
  **Stop before continuous 3D nesting.**
- The catalogue is small and fixed. Brute force it. We do not need to be clever
  because we can enumerate — that is a gift, not a limitation.

### Tiers
`part → box → pallet → truck`. 30 of 49 assets carry a pallet spec; v1 went
box → truck and skipped the pallet tier entirely.

---

## 5. Catalogue (49 assets) — cleanup required before it drives anything

| # | Issue | Action |
|---|---|---|
| 1 | `Can Be Palletized` true for only 7/49; PLS12801 and FLC12101 have full pallet specs but read `0` | Ignore the column. Derive from pallet spec. |
| 2 | `PP Box_650x650x135`: inner 210×210×140 inside outer 650×650×160 | Impossible. Resolve — celled box or typo. |
| 3 | `PLS-1200x1000x755` = 32kg vs near-twin at 600kg; `PLS_1200x1000x1100` = 6000kg | Trailing-zero errors. Verify. |
| 4 | Racks, bare pallets and stacking frames sit alongside containers | **Add a `type` column** (container/pallet/rack/accessory). Without it the optimiser packs parts into a warehouse rack. |
| 5 | `Unit Dimension`, `Weight Dimension`, `Folded Volumetric Weight` empty in all 49 | Drop or populate. |
| 6 | CRT6434 and CRT6435 both 550×360×340 | **Already deduplicated** in the 15 seeded rows — only CRT6435 is there, at 550×360×335. Applies to the 34 unseeded assets. |
| 7 | TRW deck's PLS1280 is internal 1150×750×**1000**, tare 35kg; the raw export's PLS12803 says **1100** / 33kg at the same external size | **Resolved 2026-09-09 — 1000mm.** `app/seed_data.py` already carries PLS12803 as 1150×750×1000, agreeing with the deck. The 1100 figure is an export defect. PLS12803 *is* the deck's "PLS1280". |

The 49-asset catalogue is **global**, not per-customer. No multi-tenancy.
(The `company17` export filename suggested otherwise; confirmed global.)

---

## 6. Custom box synthesis — core, not a fallback

**Every run returns the top 2 catalogue solutions plus one custom design**, side
by side. The engineer is making that comparison anyway; the tool should show both
halves of it rather than making them ask.

Consequence: synthesis is on the hot path. It must be fast enough to run on every
job and constrained enough to always produce something sensible — not an
occasional escape hatch.

Solve *for* box dimensions rather than picking them. The constraint set is already visible in our own proposals:

- Must land on a standard footprint (1200×1000 or 1200×800)
- PP flute 1200 GSM; EVA at 100 / 150 / 180 kg/m³
- Sheet thicknesses in use: 3mm, 5mm, 35mm
- Divider wall 8–15mm, part clearance 5–10mm/side

Bounded design space, not a blank sheet.

---

## 7. Output

The insert BOM is already structured in the decks (element, size, foam density /
GSM, qty per kit) — generatable, not free text. Mubea has six elements: bottom
separator, top centre bar, top side bar, side separator, top separator, MS rod.

**The tool produces the starting design. Physical trials still decide** — the
Mubea deck shows a revision after trial 2 (15mm → 35mm side separator, slot
replacing hole). Do not present output as final.

Also: the TRW deck states 46 kits by weight where 155kg/kit against a 9,000kg
payload gives 58, and calls 48 "the minimum of" 48 and 46. The final answer is
right by volume; two numbers on the way there are not. Arithmetic consistency is
a feature.

---

## 8. Phases (ordered by risk — riskiest first)

**Phase 0 — Ground-truth harness.** Encode Mubea (40) and TRW (48) as regression
fixtures. Any engine must reproduce them. *Nothing else starts until this exists* —
without a known-good answer we cannot tell a better nester from a disagreeing one,
and a tool the team doesn't trust is a tool they close.

**Phase 1 — Input v2.** IGES reader, open-shell handling, largest-body filter,
SLDASM hard fail. Verified against all five real files.

**Phase 2 — Nesting engine.** 2D nest + layer stack, scored against Phase 0.
Includes custom-box synthesis — the result set is always top-2 catalogue + 1 custom.

**Phase 3 — Catalogue + tiering.** Cleaned catalogue, `type` column, multi-tenant,
box → pallet → truck.

**Phase 4 — Proposal export.** BOM + drawings + load plan.

**Phase 5 — UI.** Evolve the existing shell. Direction (revised 2026-09-15
after the Pack Studio benchmark, §10 F13–F16): **one picture, one list, no
prose.** A large 3D view of the packed box rendered from the real mesh in
cardboard on white; beside it a two-column key/value list; the ranked
candidates as cards with three numbers each; the binding constraint as one
sentence. No stat chips, badges, captions or footnotes. Fira Sans / Fira
Code, `#1E40AF` primary with `#D97706` accent, 8px rhythm stay. The earlier
"Data-Dense Dashboard" wording is withdrawn — density was the complaint.

Phase 5 is last only because the engine defines what there is to show. The shell
already exists and stays usable throughout.

---

## 9. Resolved

1. **Custom vs catalogue** — not a share. Always show top 2 catalogue + 1 custom.
   Synthesis is core (§6).
2. **Formats** — customers send STEP or IGES. Two readers. `.SLDASM` fails loudly
   with a request for STEP/IGES; no converter purchase, no SolidWorks seat.
3. **Catalogue is global.** No multi-tenancy.

## 10. Still open

- DOMAIN.md (clearances, wall thicknesses, foam selection rules) still needs
  to be written by the packaging engineers — no agent can invent it.

### User feedback, 2026-09-15 (three tickets — all frontend/drawing, none engine)

**F1 — Too much on one screen; the user loses track of what they are doing.**
The Packaging screen stacks hero + ranked cards + layout detail (layers,
box packing analysis, insert BOM, dunnage) + truck + "Why this design" in one
scroll. Ticket: one primary question per screen. Hero + ranked comparison is
the screen; everything below the cards moves behind a tab or a click on a
card ("Details"), collapsed by default. Acceptance: on a done run at
1440×900 the first viewport shows hero image, count, box name and the ranked
cards, and nothing else; nothing the backend computed is removed, only
hidden until asked for.

**F2 — Exploded view / GIF cannot be zoomed; not readable at hero size.**
Hero `.modal-img` is capped at 480 px and only the Insert BOM button opens
the modal. Ticket: clicking any drawing image (hero, packed, exploded,
packing-order GIF, insert drawing) opens the existing `ImageStage` modal;
the modal fits the image to the viewport (≥90vh) and lets the user zoom
(native `<img>` in an `overflow:auto` card with a fit/1:1 toggle — no
pan-zoom library). Acceptance: click hero → modal → image is at least
viewport height; Esc closes; GIF keeps animating in the modal.

**F3 — Dark ground on the exploded view makes users uncomfortable.**
the R4 dark ground (slate-900). Ticket: revert the exploded PNG to
the light card the GIF and packed PNG use (`D_*` palette collapses back to
the shared constants; dunnage alpha back to the white-ground value), and
drop the dark panel `proposal.py` pastes behind it. Acceptance: exploded
PNG background is `#FFFFFF`/`#F8FAFC`, `_check_spec_column` still passes,
GIF + packed PNG byte-identical to HEAD, proposal PDF page has no dark
panel. Design note: dark was our taste, not the users' — light ground is
the rule from here for every rendered drawing.

**F4 — Packing order as a live 3D animation, not (only) a GIF.** Rahul saw
the sample (real TRW wheel GLB in its resting pose, 48 parts dropping in
layer by layer, seekable) next to the fixed GIF and picked the animation.
Two tickets, one JSON contract between them:

- *Backend (geometry).* `insert_drawing` emits a `sequence` off the SAME
  `_place` call `build_gif` replays — never a second placement expression
  (hard rule 9). Shape, all mm, Z-up, box inner corner at (0,0,0):
  `{inner:[L,B,H], pose_matrix:4x4 (the layout's candidate rotation_matrix),
  part_extent:[l,b,h], steps:[{i, kind:"dunnage"|"parts", title, text, meta,
  cuboids:[{name, colour, alpha, origin:[x,y,z], size:[l,b,h]}],
  parts:[{origin:[x,y,z]}]}]}`. Step index, order and caption strings are
  the GIF's own (`_dun_caption`), so the two can never disagree. A part
  `origin` is the min corner of the posed part's AABB — the convention
  `_place` already uses for `ox + a*pitch`. Declared on `LayoutOut` and
  `BoxDesignOut` as `sequence: Optional[BuildSequenceOut]`, filled in
  `worker._render_drawings` next to `gif_url`, stored in the job result
  (no new column). Acceptance: `_selfcheck` asserts per-element cuboid
  count == BOM qty, parts == `count`, and the set of step indices equals the
  GIF's; TRW over real HTTP shows 8 parts steps × 6 origins and a 4×4
  `pose_matrix`; ground truth 40/48.
- *Frontend.* `PackAnimation.jsx` (Three.js, already a dep): loads
  `part.glb_url` through `glbModel` (`MM_PER_M`, `candidateMatrix` — the
  scale check must still pass), one `InstancedMesh` per GLB primitive,
  aligns the posed model's AABB min corner to each `origin`, replays
  `sequence.steps` on a deterministic time-driven timeline (Play/Pause,
  seek, 0.5/1/2×), caption card with the step's `title`/`text`/`meta`.
  Shown as an "Animation" view in the same `views` list the hero and
  Insert BOM use, so it opens in the F2 modal too; only offered when
  `layout.sequence && part.glb_url`. GIF stays as the fallback and the
  proposal PDF's still. Acceptance (tester, headless Chrome): view tab
  present on a done TRW run; canvas mounted; seeking to the end reads
  "48 of 48 parts placed"; 0 console errors; `node frontend/src/lib/scale.check.mjs` passes.
  Known gap (geometry review 2026-09-15): the sequence ships dunnage
  *solids* only; the pocket-tray GIF also punches the pocket *voids*
  through the tray so parts show. The animation relies on the shipped
  `alpha` (tray 0.30) instead. Add a `voids` list per cuboid if a tray ever
  needs to read as pockets rather than a translucent slab.

**F5 — Solve feels slow.** Measured 2026-09-15 (backend agent, cProfile +
real HTTP on 127.0.0.1, TRW wheel): upload→extraction done 6.4 s; solve
POST→`status=done` (count on screen) 6.2 s; →`render_status=done` 32 s.
The number is fast; the ~26 s tail is pictures. Function-level breakdown:

| stage | Mubea | TRW | note |
|---|---|---|---|
| engine.solve | 8.0 s | 2.4 s | 95–98 % is `nesting.occupancy` voxelisation (trimesh `subdivide_to_size` → `unique_rows`/argsort); already deduped per distinct pose |
| explode_png × 6 | 6.1 s | 6.8 s | top-5 catalogue (frontend hardcodes `topN: 5`) + custom |
| build_gif × 2 | 18.6 s | 18.5 s | catalogue[0] + custom, 0.33–0.51 s per matplotlib frame |
| truck fit, ranking | <0.05 s | <0.05 s | never the bottleneck |

Levers, in order (none touches a count; ground truth 40/48 must hold):
- *S1 — DONE 2026-09-15, and simpler than planned: the worker never builds
  the animated GIF any more.* Every solvable part has a GLB (`run_solve` and
  `run_render` load the mesh from it; manual parts 422 before solving), so
  the "GIF for no-GLB parts" branch was unreachable — a test that nulled
  `glb_path` and re-rendered proved it (render fails, no mesh). `build_gif(
  frames=False)` renders the hold frame + sequence off the same `_place`;
  the GIF code path stays alive in `insert_drawing`'s self-check only.
  Measured over HTTP: render tail 26 s → 1.1 s (TRW). Original plan:*
  GIF only when it is the only moving picture.* Once F4 ships, a
  part with a GLB gets the live animation; the GIF is then a fallback for
  manual-source parts (no GLB) and nothing else. Render `packed_url`
  straight from the hold frame (`frame(None)`, one matplotlib pass) and
  build the GIF only when `part.glb_url` is null. Saves ~18 s per solve on
  every CAD part. Acceptance: TRW `render_status=done` in <14 s over HTTP;
  packed PNG byte-identical to before; Mubea/TRW self-check still builds
  and asserts the GIF (the GIF code path stays tested).
- *S2 — Hero first, the rest progressively.* `_render_drawings` renders
  6 layouts serially and publishes all at once. Render catalogue[0] +
  packed first and write the result so the hero appears, then the rest;
  ranks 3–5 PNGs are for cards nobody opens — render on demand when the
  card is opened, or last. Acceptance: hero drawing visible within ~3 s of
  `status=done`; no count changes.
- *S3 — Parallel renders.* The 6 `render()` calls are pure per-layout
  functions; a small process pool (or `worker-render` concurrency >1 in
  compose) runs them together. Acceptance: total render wall clock ≤ the
  slowest single layout + 1 s.
- *Deferred (geometry ticket, needs a failing test first):* 46 % of
  Mubea's solve is `numpy.argsort` inside trimesh's voxeliser; a faster
  occupancy backend is real but touches `nesting.occupancy`. `VOXEL_MM`
  and `max_candidates` are NOT knobs — the 40/48 depend on them.
- *Comments to correct while there:* `nesting.py` attributes the ~2.4 s
  per pose to `min_pitch`; the profile says it is `occupancy`.
  `catalogue.py` says 15 seeded rows; `seed_data.PACKAGING` has 17.

**Gemini UX audit, 2026-09-15** (`tools/ux_audit.py`, gemini-pro-latest, 10
screens of the non-NDA sample part; report `docs/ux-audit-2026-09-15.md`).
Triage — accepted where the screenshot backs it, rejected where it does not:

- *Accepted → F1 (shape).* The first viewport at 1440×900 is the hero and
  its right-hand details grid (calculated count, upper bound, pattern,
  layers, pitch, clearance, limited-by, weight cap, WHY); the ranked cards
  start below the fold. F1's concrete shape is now: hero = count + box +
  pose + viewer only; the grid's rows move into Box Packing Analysis (they
  are already there, minus "geometric upper bound" — add it, drop the
  grid); ranked cards sit directly under the hero. Acceptance unchanged.
- *Accepted → F1.* "↻ Re-run with these parameters" sits at the very
  bottom of the Packaging scroll, ~1500 px from the inputs it applies.
  Move it into the left rail under "+ Custom box"; the rail's helper text
  then says what the button does. S.
- *Accepted → F1.* "Boxes to rank" collapses to "N selected", hiding which.
  Show the codes joined with ", " up to four, "N selected" beyond. S.
- *Accepted, done.* Load calculator said "1 boxes". Fixed in
  `LoadCalculator.jsx`.
- *Accepted → F6 (below).* Top-level Truck tab shows the Packaging hero.
- *Rejected: "inputs are massive textareas" (03-overview).* They are
  40 px single-line inputs; wide because the card is a two-column grid.
- *Rejected: "recommended run lacks a badge" (07-runs).* The badge exists
  (`ProjectPage.jsx` renders `recommended` in place of the button) — the
  audit run simply had not marked one.
- *Rejected: "remove the Truck load sub-tab".* The sub-tab is per selected
  box card, so a user can compare truck fit across boxes without leaving
  the card; the top-level tab is the hero box only. Keep both, make the
  top-level one correct (F6).
- *Rejected: duplicate "Generate proposal" inside the empty state.* The
  button is on the same screen, top right, 130 px away.

**F6 — DONE 2026-09-15. Truck tab renders the Packaging screen.** `ProjectPage.jsx`
renders the same `PackagingTruckTab` for both tabs and only steers the
sub-tab (`resultTab='truck'`) inside `LayoutDetail`, which is below the
fold — so the Truck tab's first viewport is byte-identical to Packaging's
(screenshot 06-truck). Ticket: when the project tab is Truck, the stage
opens with the truck plan: `TruckLoadIso` of the hero box + chips (boxes
loaded, parts per truck, vehicle, tare), the hero packing image is not
shown, ranked cards stay below (they carry truck_boxes already). No
re-solve, no re-mount of `PackingResults` (the F4 animation lives there).
Acceptance: at 1440×900 the Truck tab's first viewport shows the truck
drawing and no "PACKAGING FIT" heading; switching Packaging ↔ Truck does
not refetch `/solve-jobs`. S.

**F7 — DONE 2026-09-15 (4db3277). Turned-footprint layouts are drawn and animated un-turned.** Found
2026-09-15 on a Maruti floor side cover (NDA, local only): resting pose
875 x 327 x 72, winner PLS12103 in footprint order (1,0,2) — extent
(332, 876, 76), pitch (177, 833, 72), grid (5,1,13), 65/box. `Layout` never
records which of `Pose.footprint_orders()` won, so `worker._render_drawings`
poses the voxels and ships `sequence.pose_matrix` with the raw resting
rotation. The drawing stamps 875-mm voxels at 177-mm steps into an 1150
volume (all five overlap, clipped at the far wall — the yellow smear on one
side), logs `parts clipped by the volume` and carries on; the 3D animation
lays the same 875-mm parts across each other and out of the box. TRW/Mubea
never showed it: both win in order (0,1,2). Fix: `Layout.turned`; the worker
composes Rz(90°) into ONE rotation used for both `pose_voxels` and
`pose_matrix`; the second silhouette becomes `rot90`, not the transpose (a
transpose is a mirror). Check: turned layout's posed AABB == turned extent;
no clipped-cells warning on the cover; 40/48 hold.

**F8 — DONE 2026-09-15 (779813b). Animation camera crops the box in the
portrait modal.** Radius was `diag * 2.05` regardless of aspect. Now a
damped fit on the eight projected corners; code review caught the first
two-pass version oscillating (camera height is fixed while r moves, so the
response is not linear) — cropped first frames on 11 of 17 catalogue boxes.
Hero box 289 → 363 px wide at 900 x 320; modal uncropped.

**F9 — OPEN, re-judged after F7. Packed/exploded drawing still illegible on
a 13-layer stack.** With the parts in the right pose the SX4 cover drawing
is now correct (5 across x 13 high, nothing clipped) but 65 voxel parts and
13 translucent trays still read as one block; you cannot tell one part from
the next. Spec: draw the top layer's parts in full and the layers below as
ghost slabs with a "x 13" callout; the packed view likewise. Rahul's taste
memory: one clear picture beats a complete one.

**F10 — DONE 2026-09-15 (941d67d). Browser-derived insert tray deleted.**
The "parts interleave … slotted comb … not built yet" note meant nothing to
Rahul, and the tray it replaced re-derived geometry client-side (hard rule
9). Insert tab = the worker's DunnageBom only.

**F11 — DONE 2026-09-15 (8d7fc9a). Interleaved pose gets a pocket BOM with pockets narrower than the
part.** Landed as the `layer_sheets` archetype plus `Bom.layer_step_mm`: sheets the parts do not nest into are charged per layer, the engine solves layers on that step, and the drawing stacks at it (reviewer's 300/150/1003 input: 420 → 378 parts, fits). SX4 65, Mubea 40, TRW 48 unchanged.
 SX4 cover: pitch 177 < extent 332 in plane, yet the BOM says
"5 x 1 pockets, 177 x 833 x 69 mm each". A 177-mm pocket cannot hold a
332-mm part; it is the comb problem F10 deleted from the browser, now in
`dunnage._pocket_tray`. When pitch < extent on an in-plane axis the
archetype must be a slotted comb (slot = part thickness + clearance, count =
grid) or plain layer sheets — not pockets. Check: no BOM element whose cell
is smaller than the part extent on an in-plane axis; sweep asserts it.
Review follow-ups (2026-09-15, not blockers): **F11a** — `bom()` now
raises on the invariant, and `engine.solve` calls it in a plain list
comprehension, so a dunnage-only defect would fail the whole solve and lose
every count; near-unreachable today, but it should degrade the BOM, not the
answer. **F11b** — `_interleaved_in_plane` has no noise floor: at clearance
0 a 2-mm overlap (below `archetype_of`'s 4-mm floor) strips a tray to
sheets. Require the overlap to exceed the sheet thickness, or state why not.

**F12 — Drawing clips 1.2 % of cells on a 35-column lattice.** Sweep:
Y2V_YK9_Rack, PLS12803, pitch 33 mm x 35 columns = 8.25 cells per step;
`_place` rounds each column start, so the drift reaches one cell at the far
wall and 2 940 of 246 120 cells fall outside. Accumulate positions in mm
and round once per column, or round the pitch to whole cells before
`lattice_count` so the count and the picture use the same lattice. S.

**E2 — Cuboid baseline is unfair to the engine on small parts.** Sweep
BELOW_CUBOID on 4 of 25 files (TVS tray 12 vs 15, headstock collar 1575 vs
1640, YK9 IBJ 1368 vs 1386, P125 intercooler). `engine.cuboid_count` packs
raw CAD dims with no clearance and no voxel rounding; the engine packs 4-mm
voxels plus 5 mm clearance. The UI then shows "engine < boxes", the one
number that destroys trust in a nesting tool. Either the baseline takes the
same clearance (honest comparison) or `cuboid_count` is labelled "no
clearance". The sweep exits 1 on this until it is settled. S.

**S2 — Sweep is part of the process.** `python backend/tests/sweep.py`
(≈ 25 min on this Mac; P118 radiator alone is 8 min in the engine) runs
after any engine or drawing change, before the commit. Rahul: "I randomly
picked this and see the bug" — the sweep exists so he never has to. Follow-
up: `worker.rotation_for` and the sweep both compose `IN_PLANE_TURN @
rotation`; lift it into `nesting` so there is one expression.

### UI benchmark, 2026-09-15 — Pack Studio demo (Rahul: "very good and understandable unlike ours")

Twenty-four stills in `~/Downloads/Pack_Studio_UI_UX_Workflow_Analysis.docx`
(memory `pack-studio-competitor-analysis`). Side by side with our results
page the gap is not features, it is how little they put on screen and how
real the picture looks. Four tickets, in the order they pay back:

**F13 — DONE 2026-09-15 (aa4eb70). Results page = one 3D picture + a key/value list.** Their result
panel is ~15 rows, label left, value right, nothing else: outer/inner dims,
fluting, tare, capacity, crush strength, box cost, dunnage cost, num parts,
vertical clearance, pack density. Ours has stat chips, MEASURED/DERIVED
badges, "needs deck: spec_density", a "Starting design, not a final BOM"
paragraph, a "Poses searched: …" footnote and three sub-tabs. Delete the
chips, badges, captions and footnote; render `layout` + `dunnage` as one
two-column list next to the 3D view; the constraint, when there is one, is a
single info line ("Weight limit cuts this box from 70 to 65 parts"), not a
WHY disclosure. Acceptance: at 1440×900 the Packaging screen shows the 3D
view, the list and the ranked candidates with no paragraph of prose
anywhere. Candidate cards show three numbers: box, parts/box, parts/truck.

**F13a — Backend emits the binding-constraint sentence per layout.** The
results screen showed `warnings[0]` under the selected card and it was about
the custom box. Rule 9: the sentence is computed, so the backend owns it.
Add `Layout.constraint: str | None` (from `limited_by` + the two counts it
already has: "Weight cap cuts PLS12803 from 70 to 65 parts", "Inner height
allows 8 layers, a 9th needs 1033 mm") and `LayoutOut.constraint` in the same
edit; frontend shows it as the one info line; nothing else on the screen
explains anything. Check: the sentence is null for a geometry-limited layout
and names the number for a weight/height-limited one, over real HTTP.

**F14 — DONE 2026-09-15 (115bcd0). Packed view drawn from the real mesh in cardboard, on white.** Their
viewport shows shaded solid parts sitting in thin brown partitions and
layer sheets on a white ground; you can count parts per row. Ours shows
voxel blobs in translucent blue slabs on dark blue. We already have the GLB
and the composed `pose_matrix` (F7) in `PackAnimation`: render the packed
box there at t = end as the hero (white ground, cardboard-coloured dunnage
as thin boxes from the BOM, parts from the GLB), with a layer slider that
scrubs the stack and a Hide-dunnage toggle. The matplotlib voxel PNGs stay
for the PDF only. Acceptance: SX4 cover hero shows 5 distinct parts per
layer and 13 countable layers; TRW/Mubea still correct.

**F15 — DONE 2026-09-15 (343c4e1). Report drawings are orthographic Front / Side / Top, not iso.** Their
pack report shows the packed box as three flat views with parts as
silhouettes, then the pallet the same way, then the truck. That is the
legible version of what F9 is trying to fix in iso. Replace the exploded iso
in the proposal PDF with three orthographic views from the placed
silhouettes (`silhouette_runs` already carries the plan view; the side views
are the pitch lattice × part extent). F9 closes into this.

**F17 — DONE 2026-09-16 (f2a5660 backend, 179d23a ui). The insert manufacturing drawing is the deliverable, and we did not
have one.** Landed as `insert_sheets_png`: one dimensioned sheet per BOM element (plan, section, title block), on the Insert BOM rows and in the PDF. Open domain question for Rahul: interleaved poses (SX4 cover, 8 of 25 swept parts) ship separator sheets only — whether they need a slotted comb is E1. Rahul, 2026-09-15, on the Insert tab: "What is the use of this
whole process if I am not able to tell the person what insert to get
manufactured — he won't get the dimensions by looking at the animation."
Correct. The Insert tab shows a BOM table (element, size, qty) and the packed
voxel picture; nothing a tray supplier can cut from. F10 deleted the
browser-drawn tray because it re-derived geometry; the backend never grew a
replacement. Build, in `insert_drawing.py` from `layout.dunnage` + the
lattice (one expression with the count, hard rule 9): **one sheet per
distinct dunnage element** — plan view of the tray outline with every pocket
/ slot drawn and dimensioned (outer L x B, pocket L x B, pitch, wall, edge
margin), a section showing pocket depth, sheet thickness and the part
sitting in it, material + spec + qty per box in a title block. White, black
lines, dimension arrows, nothing decorative. Shown on the Insert tab in place
of the packed picture, and in the proposal PDF. Depends on **F11**: for
interleaved poses (pitch < part extent in plane, the SX4 cover) the element
must be a slotted comb or plain sheets before it can be drawn — a 177-mm
pocket for a 332-mm part cannot be dimensioned because it cannot exist.
Acceptance: TRW wheel → a pocket-tray sheet a supplier could quote from,
every number traceable to `dunnage`; SX4 cover → comb or sheets, never a
pocket narrower than the part; Mubea bar → its separator/bar elements.

**F16 — Cost and pallet layer (functional gap, after F13–F15, F17).** They rank by
landed cost: box unit cost + dunnage cost + delivery (fixed + per km) →
cost per part / box / pallet / shipment; and a pallet unit load sits
between box and truck. We have neither. Needs box and dunnage prices in the
catalogue (proposal decks have them) and a pallet asset type. Not before the
drawings are right — a cost column on an unreadable picture helps nobody.

**E1 — Engine cannot see shell spooning.** Same cover standing on edge
("Alternative 1", extent 876 x 76 x 332) measures pitch 77 along the 76-mm
axis: zero nesting. Real trim panels are packed on edge, face-to-face,
nested to thickness + rib height, which is where the count would come from
(950 / ~25 mm x 3 rows is 100+ vs 65 flat). The voxel pitch search shifts a
whole occupancy grid along one axis and finds the arch of one shell
colliding with the arch of the next — correct for these voxels at 4 mm, but
a shell 2–3 mm thick with 4 mm cells is solid. Needs a finer voxel on the
thin axis or a surface-offset test. Not a drawing bug; a count ceiling.
