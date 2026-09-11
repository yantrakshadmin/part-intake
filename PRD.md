# PRD — Part Intake as a product

**Status:** draft for the next goal. Written 2026-09-10 after cross-checking
`Green_Packaging_Optimization_PRD.md` (Fitsol-derived, in Rahul's Downloads)
against what this repository actually does.
**Owner:** Rahul + Claude (PMs). Implementation stays delegated per `CLAUDE.md`.
**Plan of record is still `PLANNING.md`.** This document says what we wrap
around the engine so the app reads as a product; it does not reopen the engine.

---

## 0. The one-paragraph verdict on the reference PRD

The Fitsol PRD is right about the *shape* of a product and wrong about the
*substance* of ours. Right: a persistent project with a status, a baseline the
result is measured against, a "why this answer" panel, alternatives side by
side, a report you can hand to a customer, and calculations demoted to a detail
view. Wrong, for us: its part is a form field with three dimensions, its insert
is a "4 × 6 matrix with cell dimensions", its optimiser weighs cost and CO₂
with sliders, and its numbers (₹18.4L, 11 %, 91 %) come from nowhere. That is
the cuboid calculator `PLANNING.md §1` exists to replace, with a business
layer painted over it. **We adopt the shape and keep our substance.** Every
number on every screen still comes from one expression in the backend
(hard rule 9), and nothing is auto-filled (hard rule 2).

---

## 1. Where we are (cross-check, honest)

| Fitsol PRD asks for | We have today | Gap |
|---|---|---|
| Part input | STEP/IGES → OBB → resting poses → human confirm; bare dims fall back to "cuboid only" | none, and ours is stronger |
| Packaging optimisation | Voxel lattice nester, top-2 catalogue + 1 custom, ground truth 40 / 48 | none |
| Insert design | Generated BOM per archetype, exploded PNG, packing-sequence GIF | none (theirs is hand-designed; reject) |
| Truck optimisation | `/api/truck-fit`: boxes, parts, floor grid, stack, limited_by, per-asset tare | trips/year, cost, CO₂ |
| Loading visualisation | `TruckLoadIso` fed by backend `floor_grid` | none |
| Asset master | 49-asset catalogue + vehicles seeded, `POST /api/packaging` | no UI to view/edit |
| Project / scenario | `PartProfile` + `SolveJob` rows, nothing groups them | **the main gap** |
| Baseline | Cuboid best-orientation count is computed as the baseline in ground truth | not shown as "current state" in the UI |
| Why this solution | `limited_by`, `count_upper`, `poses_searched`, `warnings`, `interleave` all shipped | not surfaced as an explanation |
| Compare alternatives | Ranked cards, catalogue vs custom | no saved what-ifs (clearance, pose, asset filter) |
| Report export | PNG download of drawings | no proposal deck |
| Cost / CO₂ | nothing | everything, and the inputs do not exist yet |
| Approval, versioning | nothing | status field + immutable solve results (already immutable) |
| Dashboard | nothing | list of projects is enough |
| Login / org | nothing | not needed: one internal team, global catalogue |

Two facts from the reference PRD that we already live by and should keep
saying out loud: *"a screen that only shows calculations belongs in a details
section"* and *"without a baseline, optimised has no business meaning."*

---

## 2. Users

Yantra Packs, not a SaaS market. Three hats, often the same person.

1. **Projects engineer** — receives a customer part, needs the fit, the insert
   BOM and the drawings in an hour, not a day. Primary user. Everything in
   `PLANNING.md` is for them.
2. **Proposal author** — turns the engineer's answer into the deck the customer
   sees (the Mubea and TRW decks are the template). Needs the same numbers, the
   drawings, the truck plan, and a page that says why this design.
3. **Customer-facing / management** — reads the deck: parts per box vs what the
   customer ships today, boxes per truck, trips per year. CO₂ and cost per part
   are what the customer's buyer asks about. They never see the app.

Not users: ESG analysts, fleet planners, procurement. Their needs in the
reference PRD are out of scope.

---

## 3. Product definition

> Give us the customer's part and how many they ship a year. We return the
> insert design that packs the most parts into a returnable asset, the box and
> truck plan that follows from it, the gain against what the customer does
> today, and the proposal deck that says so.

The primary loop becomes **Project → Solve runs → Recommendation → Proposal**.
The current three-step screen (intake → confirm → results) becomes the
*create* flow of a project and then stops being a wizard.

---

## 4. Principles (inherit `CLAUDE.md`, add three)

1. **Hard rules 1–9 stand.** Notably 9: any new number the UI shows (trips,
   gain %, utilisation, cost, CO₂) is a backend field on a dataclass *and* a
   response model, checked over real HTTP.
2. **No invented business numbers.** Cost per trip, route distance, emission
   factor, customer's current parts per box are all *entered* and shown as
   entered. Empty until entered; the screen says "not provided" rather than
   estimating. Hard rule 2 extended from dims to money.
3. **Rank by parts per box, then box size. Nothing else until cost exists.**
   No weighted objective sliders. When cost inputs are present, a second sort
   ("by cost per part") is offered as a column, not a slider.
4. **The engine is not touched by this PRD.** Every ticket below is a wrapper:
   persistence, aggregation of existing results, presentation, export.

---

## 5. Feature set

### P0 — makes it a product

**F1. Project entity.** `Project(id, customer, part_number, part_name,
status, owner, created_at, updated_at, notes)` owning one `PartProfile` and
many `SolveJob`s. Status ladder, hand-driven, no automation:
`draft → solved → proposal_sent → trial → approved → archived`. Column add goes
through `main._ensure_added_columns` (landmine 5). API: `POST/GET /api/projects`,
`GET/PATCH /api/projects/{id}`; a solve is `POST /api/projects/{id}/solve` and
lists under the project.
*Check:* create a project over HTTP, run two solves with different clearance,
`GET /api/projects/{id}` lists both with their result summaries and the status
string; restart uvicorn against the old dev.db and it still boots.

**F2. App shell.** Persistent left nav: Projects, Assets, New project. Project
header: customer · part number · status pill · owner · updated. Tabs:
Overview · Packaging · Truck · Runs · Proposal. Empty states explain the
product. Fira Sans / Fira Code, `#1E40AF`, `#D97706`, 8 px rhythm, unchanged.
*Check:* browser walk-through recorded with screenshots into PROGRESS; this is
the surface that has produced six defects per session when skipped.

**F3. Baseline and gain.** Two baselines, both computed server-side and shipped
on the solve result: (a) `cuboid_count` for the best orientation in the same
asset (already the ground-truth baseline), (b) `customer_count` if the customer's
current parts-per-box and box were entered on the project. The Overview shows
`40 vs 8 cuboid (5.0×)` and `40 vs 32 today (+25 %)`. Never shown when (b) is
absent.
*Check:* result JSON and response model carry both fields; Mubea reads
40 / 8 / 5.0× over HTTP.

**F4. "Why this design" panel.** Rendered from fields that already exist:
`pose_label`, `limited_by` (which axis or the weight cap bound the count),
`count_upper` band, `interleave`, `poses_searched`, `warnings`, `clearance_mm`,
and the BOM `fits` flag. Wording is the backend's: add a `reasons: list[str]`
to the layout dataclass and model so the frontend prints, never composes.
*Check:* every ranked card has ≥ 3 reasons over HTTP; the wheel lists
"135 mm wheel sits 15 mm into its tray: 120 mm pitch" or equivalent computed
text.

**F5. Runs as scenarios.** Every `SolveJob` under a project is a row: date,
clearance, confirmed pose, asset filter, best count, custom count, truck boxes.
Any row opens the existing results view. Marking one run **recommended** is a
project field. No new solver path; a what-if is a new run with changed inputs.
*Check:* two runs, mark the second recommended, project GET returns
`recommended_run_id`, Overview shows that run.

**F6. Demand → trips.** Project fields `annual_volume`, `route_km`,
`vehicle` (from the existing vehicles table). Backend computes and ships
`trips_per_year = ceil(annual_volume / parts_per_truck)` on the truck block,
plus `parts_per_truck` it already knows. Nothing else until F9.
*Check:* truck block over HTTP carries `trips_per_year`; null when
`annual_volume` is null.

**F7. Proposal export.** One `POST /api/projects/{id}/proposal` renders a PDF
in the worker from the recommended run: cover, part and pose, ranked
comparison table, insert BOM, exploded PNG, first/middle/last GIF frames as a
packing-sequence strip, truck load drawing, baseline and gain, assumptions
(clearance, weight cap, "starting design, trials decide" caveat from
`PLANNING §7`). Uses matplotlib + PIL already installed; no new deps.
*Check:* PDF opens, page count ≥ 6, every number on it matches the run's JSON
(the test reads both).

### P1 — makes it credible to a customer

**F8. Assets UI.** Read/edit the catalogue and vehicles with the `type` column
visible; add a custom box as `draft` (exists on the API already).

**F9. Cost and CO₂ per part.** Project fields `cost_per_trip`,
`emission_factor_kg_per_km` (defaults come from the vehicle row, shown and
editable). Backend ships `cost_per_part`, `co2_kg_per_part`, `co2_kg_per_year`
on the truck block. A second sort by cost per part on the comparison table.
Factors are versioned by storing them on the run, so an old run never changes.

**F10. Progress while solving.** The worker already has stages (extract,
voxelise, nest, synthesise, dunnage, render PNG, render GIF). Publish a stage
string on the job row; the UI shows the ladder instead of a spinner. Solve is
~40–50 s with the GIF, so this matters.

**F11. Activity.** Append-only log rows on the project: status change, run
created, recommended run changed, proposal generated. Shown on the Overview.

### P2 — later

Customer login for read-only proposals; portfolio view across projects with
summed gain; bulk solve of a customer's part list; AI explanation on top of
`reasons`. None start before P0 and P1 are in the browser.

### Rejected from the reference PRD, with reasons

| Item | Why not |
|---|---|
| Weighted objective sliders (§10) | False precision. Parts per box is the objective; cost becomes a column when the inputs exist. |
| Hand-designed insert matrix (§17) | Contradicts the engine. The insert is an output. |
| OR-Tools / CP-SAT (§41) | The catalogue is enumerable; brute force is the design (`PLANNING §4`). |
| Login, organisations, roles (§39, §47) | One internal team, global catalogue (`PLANNING §9`). Revisit for customer read-only links. |
| Dashboard KPIs in ₹ (§22) | Would be fabricated until F9 exists and is populated. |
| Natural-language project creation (§42) | Zero saved time for a part that arrives as CAD. |
| Network / portfolio optimisation (§53) | Not this year. |
| "Optimising: 12,480 configurations evaluated" | We evaluate a few dozen poses × 49 assets. Show the real stages (F10), not a big number. |

---

## 6. Data model delta

```
Project           new  (F1)   id, customer, part_number, part_name, status, owner,
                              notes, annual_volume, route_km, vehicle_id,
                              customer_count, customer_box, cost_per_trip,
                              emission_factor_kg_per_km, recommended_run_id,
                              created_at, updated_at
PartProfile       +project_id
SolveJob          +project_id, +stage (F10), +inputs_json (clearance, pose, assets, factors)
Activity          new  (F11)  project_id, at, kind, text
```

No Alembic: every new column lands in `main._ensure_added_columns` in the same
commit as the model (landmine 5).

---

## 7. Screens

**Projects list** — table: customer, part, status, best count vs baseline,
updated. Search by part number or customer. "New project" primary button.

**New project** — the existing intake → confirm flow, with customer, part
number, annual volume and the customer's current pack as optional fields on the
first step. Ends by creating the project and running the first solve.

**Project · Overview** — hero: recommended run's best count, asset, gain vs
cuboid and vs customer. Below: packaging card, truck card, "Why this design",
activity. Buttons: Re-run with changes, Generate proposal, status change.

**Project · Packaging** — today's `PackingRecommendation` results view.

**Project · Truck** — today's Truck Load tab plus trips per year.

**Project · Runs** — F5 table.

**Project · Proposal** — generated PDFs by date, download.

**Assets** — F8.

---

## 8. Delivery order for the next goal

Riskiest and most visible first, one ticket each, `tester` after each,
`/code-review` before landing, ground truth 40 / 48 after every backend change.

1. F1 project entity + API (backend)
2. F2 shell + project header, wired to F1 (frontend) — browser check mandatory
3. F3 baseline fields (backend) then Overview hero (frontend)
4. F4 reasons (backend) then panel (frontend)
5. F5 runs table (frontend; API from F1)
6. F6 demand → trips (backend then frontend)
7. F7 proposal PDF (backend, worker)
8. F10 stage ladder, F8 assets UI, F9 cost/CO₂, F11 activity

Definition of done for the goal: a new user creates a project from the Mubea
IGES, sees 40 in PLS12801 against 8 cuboid on the Overview with three reasons,
sees trips per year for an entered volume, downloads a proposal PDF whose
numbers match the API, and does all of it in the browser without reading code.

---

## 9. Open

- Who supplies cost per trip and emission factors per vehicle? Vehicle table
  can carry defaults, but a packaging engineer has to confirm the source.
- Customer's current pack: do decks always state it? Mubea's did (cuboid 8
  is our reconstruction, the deck's own baseline is what the customer shipped
  before). Check the two decks before building F3(b).
- Proposal template: reproduce the existing deck layout or a cleaner one?
  Default: a cleaner one that carries the same sections.
