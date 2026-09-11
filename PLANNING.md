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

**Phase 5 — UI.** Evolve the existing shell. Design system: Data-Dense Dashboard,
Fira Sans / Fira Code, `#1E40AF` primary with `#D97706` accent, dense 8px rhythm.
Ranked-solution comparison is the core screen. Not a generic dashboard.

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

- Nothing blocking. DOMAIN.md (clearances, wall thicknesses, foam selection rules)
  still needs to be written by the packaging engineers — no agent can invent it.
