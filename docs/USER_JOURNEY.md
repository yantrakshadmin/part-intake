# User journey — packit.trakkia.com

Every step from opening the site to holding a proposal PDF. Traced from the
code, not the docs; each claim cites `file:line`. Paths are repo-relative.

Poll intervals and caps referenced below: extraction 1.5 s (`frontend/src/components/NewProject.jsx:106`);
solve 1.5 s, 90 s cap while `pending`, 660 s cap while `processing` (`frontend/src/lib/solve.js:8-15`);
stored-run reload 3 s (`frontend/src/components/PackingRecommendation.jsx:307`); proposal 2 s (`frontend/src/components/ProjectPage.jsx:493`).

---

## 1. Login

1. **User** opens `https://packit.trakkia.com`. Browser shows its native basic-auth dialog.
   **System**: Caddy `basic_auth` on the whole site, including `/api/*` (`deploy/Caddyfile:18-31`). Credentials in Secret Manager `part-intake-basic-auth`, user `yantra` (`deploy/README.md:12,68`).
   **Sees**: the app shell — left nav Projects / New project / Assets / Tools → Load calculator (`frontend/src/App.jsx:8-12,45-51`). Default route `#/projects` (`frontend/src/lib/router.js:25`).
   **Gotcha**: no logout, no per-user identity; one shared credential. Wrong password = browser re-prompts forever, no app error.

## 2. Projects list

2. **User** lands on Projects (`#/projects`). Types in the search box.
   **System**: `GET /api/projects` or `GET /api/projects?q=…` debounced 250 ms (`ProjectsList.jsx:19-28`); server filters customer/part_number/part_name ILIKE, orders by `updated_at desc` (`backend/app/main.py:468-480`).
   **Sees**: table Customer · Part · Status pill · Best (`best_count · asset`, "Custom box" in accent when custom wins) · vs cuboid (`gain_vs_cuboid×`, `+x.x%` vs customer) · Runs · Updated (`ProjectsList.jsx:53-96`). Numbers come from `_project_summary` → recommended run, else newest done run (`main.py:428-455`). Empty state "No projects yet" (`ProjectsList.jsx:43-51`).
3. **User** clicks a row → `#/projects/{id}/overview` (`ProjectsList.jsx:63-64`), or **New project** → `#/projects/new`.

## 3. New project — upload CAD

4. **User** on New project (`#/projects/new`), rail shows segmented **STEP file | Manual entry** (`NewProject.jsx:209-214`). Drops or browses a file (`accept=".stp,.step,.igs,.iges"`, `NewProject.jsx:245`).
   **System**: `POST /api/parts/upload-step` multipart (`NewProject.jsx:9-15`). Server checks suffix against `SUPPORTED_SUFFIXES` (`backend/app/geometry.py:31-33`) → **400** "Only .stp / .step / .igs / .iges files are accepted." (`main.py:111-114`); streams to `INTAKE_LOCAL_STORAGE_DIR/{job_id}{suffix}`, **413** over 200 MB (`main.py:121-128`, `backend/app/config.py:16`; Caddy body cap 220 MB, `deploy/Caddyfile:24`). Persists `ExtractionJob(status=pending)`, enqueues Celery `extract_step`; falls back to an in-process thread if the broker is down (`main.py:130-149`).
   **Sees**: dropzone spinner "Uploading…" then "Extracting dimensions…", hint "Large assemblies can take up to ~30 s" (`NewProject.jsx:223-228`). A 400/413 message appears as red status text under the form (`NewProject.jsx:117,295`).
   **Gotcha**: `.SLDASM`/`.SLDPRT` are rejected at the 400 by suffix; the loud "no open-source reader… request STEP/IGES" message in `geometry.py:320-326` only fires if a bad suffix reaches the worker.

5. **User** waits.
   **System**: page polls `GET /api/jobs/{job_id}` every 1.5 s (`NewProject.jsx:91-108`). Worker: `status=processing` → `extract_part` (mesh faces only → GLB → hull → OBB → up to 4 resting-pose candidates, `geometry.py:238,292-306`) → `status=done` with `result_json` + `glb_path`, or `status=failed` with `error="ExcType: msg"` (`backend/app/worker.py:52-76`). Worker hard limit 300 s (`worker.py:47`).
   **Sees on done**: stage card **Confirm resting orientation** — Isometric (Three.js GLB), Front, Top views (`NewProject.jsx:323-343`), candidate buttons labelled "Largest face down (most stable)", "Lowest profile", "Alternative n" with `L × B × H mm` (`NewProject.jsx:345-354`, `geometry.py:293-299`). Extraction warnings (declared INCH/METRE units, not watertight, N solids, unusual dims) render verbatim below (`NewProject.jsx:365-367`, `geometry.py:336-371`).
   **Sees on failed**: red "Extraction failed: {error}"; dropzone returns to idle so another file can be dropped (`NewProject.jsx:102-104`).
   **Gotcha**: on done, L/B/H fields are **pre-filled from candidate 0** (`NewProject.jsx:99-100`) — visible, editable, never hidden; this is the "user sees and confirms" step, not a silent fill. Weight is never filled — the user must type it.
   **Gotcha**: stuck at "Extracting dimensions…" with no error = a stale worker from another session ate the task (CLAUDE.md landmine 2). There is no client-side timeout on extraction polling.

6. **User** clicks a candidate to change the resting pose.
   **System**: local only — swaps `dims_lbh` into the form and re-renders the three views (`NewProject.jsx:126-130`). Nothing persisted yet.

## 4. New project — fill the form and save

7. **User** fills Customer, Part number, Part name, Weight (kg), L/B/H (`NewProject.jsx:191-199`). Optionally expands **Customer's current pack (optional)**: Annual volume, Parts per box today, Current box (`NewProject.jsx:264-287`).
8. **User** clicks **Save & calculate**.
   **System**: client validates customer / part no. + name / weight > 0 / dims > 0 (`NewProject.jsx:134-146`). Then `POST /api/projects` → `Project` row (`NewProject.jsx:153-162`, `main.py:458-465`); then `POST /api/parts` with `source: 'stp'`, `job_id`, `confirmed_orientation` = the selected candidate's 4×4 matrix, `project_id` (`NewProject.jsx:166-179`). Server stores dims sorted L ≥ B ≥ H (`backend/app/schemas.py:61-65`), copies `glb_path` off the job (`main.py:184-190`), **409** if the project already has a part (`main.py:197-205`). Navigates to `#/projects/{id}/packaging` (`NewProject.jsx:183`).
   **Sees**: "Saving…", then the project's Packaging tab.
   **Gotcha**: if `POST /api/parts` fails the project already exists; the page keeps `createdProjectId` so a retry doesn't make a second project (`NewProject.jsx:68-72,149-163`) — but only within this page session. Leaving the page orphans a part-less project (see Dead ends).
   **Gotcha**: **Manual entry** saves fine but every solve on it is a **422** "Nesting needs the CAD file…" (`main.py:242-248`). The Packaging tab still auto-fires the solve and shows that text in the red warning.

## 5. Project page — first solve (auto)

9. **User** arrives on `#/projects/{id}/packaging`.
   **System**: `GET /api/projects/{id}` (`ProjectPage.jsx:71-76`) → `ProjectOut` with `part` and `runs[]` as `RunOut` (`main.py:407-425`, `backend/app/runs.py:105-155`). Also fetches `GET /api/packaging` and `GET /api/vehicles` once (`PackingRecommendation.jsx:16-24`). `pickedRun` finds no run → `PackingResults` **solves on mount** (`ProjectPage.jsx:32-54`, `PackingRecommendation.jsx:323-331`). Rail vehicle defaults to `32_ft_sxl` (`PackingRecommendation.jsx:94-99`).
   **Sees**: header card — customer, part no., **Status** dropdown (draft/solved/proposal_sent/trial/approved/archived), **Owner** input, "updated {date}" (`ProjectPage.jsx:163-179`); tabs Overview · Packaging · Truck · Runs · Proposal (`ProjectPage.jsx:9-12`). Left rail "Ship it in": Box tare weight (kg), Vehicle, Boxes to rank (multi-select, max 20, drafts marked), Clearance (mm, "default 5"), "Search confirmed pose only" checkbox, **+ Custom box** (`PackingRecommendation.jsx:124-209`). Stage: spinner "Solving… {n}s" (`PackingRecommendation.jsx:372-381`).
   **Gotcha**: the very first solve uses whatever the rail holds at mount — i.e. defaults. Rail edits apply only on the next Re-run (`PackingRecommendation.jsx:157-160`).

10. **System (solve)**: `POST /api/projects/{id}/solve` body `{tare_kg, vehicle, top_n: 5, assets|null, confirmed_pose_only, clearance_mm|null}` (`solve.js:44-63`) → **202** `solve_job_id`; `SolveJob(status=pending, inputs_json=params)`; `project.updated_at` bumped; `solve_part.delay` or thread fallback (`main.py:229-291,517-530`). Poll `GET /api/solve-jobs/{id}` every 1.5 s (`solve.js:80-113`). Worker (`worker.py:214-527`, hard limit 600 s): loads mesh, rebuilds pose candidates from the extraction, matches the confirmed pose by matrix, ranks only `status="checked"` containers unless boxes were named (`worker.py:288-293`, `backend/app/catalogue.py:98-108`), runs `engine.solve`, renders exploded PNG per layout and a packing GIF **only for catalogue[0] and the custom design** (`worker.py:38,174-201`), scores every option to the truck with the asset's own tare (override only fills gaps) (`worker.py:315-446`), composes reasons, writes `result_json`, `status=done`.
   **Wait**: "about a minute" per the rail copy; ~40–50 s typical (`ProjectPage.jsx:375`); radiator-class parts 40–500 s (`solve.js:9-12`). GIF alone is ~40 s/layout on the prod VM (`worker.py:32-37`).
   **Error states**: `failed` → red warning with the worker's `ExcType: msg` (`PackingRecommendation.jsx:383-385`, `worker.py:523-526`). Still `pending` after 90 s → "…no Celery worker is consuming the queue." Still `processing` after 660 s → "Solve exceeded the worker's 600s limit" (`solve.js:86-96`).

## 6. Packaging tab — the ranked comparison

11. **Sees on done**: URL becomes `…/packaging?run={solve_job_id}` and the project reloads (`ProjectPage.jsx:102-105`). Heading "Packaging fit — {part no.} (L × B × H mm, kg · insert: {type})", caption "Run of {date} · {clearance} mm clearance · {vehicle}" (`PackingRecommendation.jsx:357-365`). Every solve warning verbatim, e.g. "N unverified box(es) not ranked…", "Best layout rests X but this part was confirmed resting Y…", "Best per box is A, but best per truck is B…", "No tare weight for …" (`PackingRecommendation.jsx:455-457`, `worker.py:336-469`).
   **Box cards** (up to 5 catalogue + 1 **Custom design**): code, `best` badge on rank 0, `beats catalogue` badge, `{count} parts / box`, `cuboid {n}` badge, "geometry allows up to {count_upper}" when the raster floor differs, `grid a × b × c · {pose}`, inner dims, "weight-limited", a **Why** disclosure with the backend's reasons (`PackingRecommendation.jsx:507-553`).
   **Detail for the selected card**: chips parts/box · layers · per layer · limited by · % weight used; sub-tabs **Layers | Insert design | Truck load** (Truck load only when the run has a truck fit) (`PackingRecommendation.jsx:582-617`).
   - **Layers**: table Layer / Pose / Parts / Dummy pockets / Layer pitch; "Box packing analysis" facts — container + status, inner/outer, part dims, insert type, pattern, **measured pitch**, interleave, headroom, content weight (`PackingRecommendation.jsx:619-676`).
   - **Insert design**: drawing-only knobs (clearance/wall/foam; clearance+foam hidden when a pocket-tray BOM exists) (`PackingRecommendation.jsx:687-720`); dimensioned isometric tray per insert with **⤓ PNG** and **Rotate in 3D** (`PackingRecommendation.jsx:978-1053`); **Insert BOM** table (element/size/qty/spec/basis) with height-budget line and the "physical trials still decide" caveat, plus **⛶ Explode** (PNG) and **▶ Pack** (GIF) modal buttons (`PackingRecommendation.jsx:816-883`).
   - Footer: "Poses searched: … — Clearance {n} mm in-plane, 0 mm between layers" (`PackingRecommendation.jsx:489-500`).
   **Gotchas**: interleaved poses (pitch < extent in-plane) get **no tray drawing** — a note says count/pitch are correct but the comb drawing is not built; the backend PNG/GIF still show (`PackingRecommendation.jsx:942-955`). **▶ Pack** exists only on the top catalogue card and the custom card. "No layout found" empty state when the part fits nothing (`PackingRecommendation.jsx:459-464`).

12. **User** clicks another card → local selection only, no request (`PackingRecommendation.jsx:468-479`).

## 7. Adding a custom box to rank

13. **User** clicks **+ Custom box** in the rail, enters item code, max content weight, inner L×B×H, optional outer → **Add box (draft)**.
    **System**: `POST /api/packaging` (`PackingRecommendation.jsx:101-122`); outer defaults to inner + 50/50/120 mm; saved `status="draft"`, **409** on duplicate code, **400** if inner > outer (`main.py:342-357`).
    **Sees**: "Added {code} (draft)". It appears in **Boxes to rank** with a `draft` badge (`PackingRecommendation.jsx:60-68`).
    **Gotcha**: an unrestricted solve **skips drafts** and warns "mark them checked to include them" (`worker.py:363-374`) — but there is no UI or endpoint to mark a box checked (see Dead ends). Workaround: tick it in **Boxes to rank**; named boxes are ranked regardless of status, with a "ranked with unconfirmed dims" warning (`catalogue.py:52-58`, `worker.py:354-362`).

## 8. Re-run

14. **User** changes rail params (tare, vehicle, boxes, clearance, confirmed-pose-only) and clicks **↻ Re-run with these parameters** (`PackingRecommendation.jsx:394-399`).
    **System**: same POST/poll as step 10; `onStarted` reloads the project so the pending run lands in `runs[]` immediately (`PackingRecommendation.jsx:269`, `ProjectPage.jsx:111`); on done navigates to `?run={new id}` (`ProjectPage.jsx:102-105`).
    **Sees**: rail title becomes "Parameters of this run" and mirrors the shown run's inputs (`ProjectPage.jsx:388-402,412`). If the vehicle dropdown no longer matches the run, Truck load shows "⚠ This plan is for X — the vehicle dropdown has changed… Re-solve to match it." (`PackingRecommendation.jsx:764,800-805`).
    Also from Overview: **Re-run with changes** just navigates to Packaging (`ProjectPage.jsx:259-261`).

## 9. Truck tab

15. **User** clicks **Truck**.
    **System**: no new request — same mounted `PackingResults`, `resultTab='truck'` (`ProjectPage.jsx:129-137`). Numbers are the run's `truck` block from `engine.parts_per_truck`; the floor drawing is built from `truck.floor_grid`/`floor_rotated`, never re-fitted client-side (`solve.js:172-184`).
    **Sees**: "Truck loading plan — {vehicle} ({asset})", chips boxes loaded · parts/truck · kg/box · kg tare (this box) · bound; isometric load drawing with **⤓ PNG** (`PackingRecommendation.jsx:759-807`).
    **Gotcha**: **trips/year is not on this tab** — it is on Overview and in the PDF only (`ProjectPage.jsx:288-289`). Truck load may be for a different box than the best-per-box card (worker warns, `worker.py:430-436`).

## 10. Overview

16. **User** clicks **Overview**.
    **Sees** (from `pickedRun(project)` — recommended run, else newest done; **ignores `?run=`**, `ProjectPage.jsx:212`): hero chips `{best_count} parts / box` · asset ("Custom box" in accent) · clearance · vehicle; baseline lines "{best} vs {cuboid} cuboid ({gain}×)" and "{best} vs {customer_count} today (+x.x%)" — both server-computed (`ProjectPage.jsx:186-207,230-257`, `runs.py:137-142`). Buttons **Re-run with changes**, **Generate proposal**. **Project inputs** card: Annual volume, Customer's current parts per box, Customer's current box, Route (km) — each `PATCH /api/projects/{id}` on blur, "not provided" when blank (`ProjectPage.jsx:316-360`). **Why this design** — reasons verbatim, or "Reasons ship with the next solve." for old runs (`ProjectPage.jsx:300-314`). Cards Packaging (catalogue count/asset, custom count) and Truck (boxes, vehicle, parts/truck, **trips/year** or "enter annual volume") (`ProjectPage.jsx:273-292`).
    **Gotcha**: editing Annual volume / customer count updates gain and trips/year **without a re-solve** — computed at read time in `_run_out` (`runs.py:105-113,137-142,154`). Route (km) is stored but nothing reads it.
    Empty states: "No part attached" → New project (`ProjectPage.jsx:363-372`); "No solve yet" → Open Packaging (`ProjectPage.jsx:214-225`).

## 11. Runs tab — pick the recommended run

17. **User** clicks **Runs**.
    **Sees**: table Date / Status pill / Clearance / Confirmed pose / Assets ("all" or codes) / Catalogue `{count} · {asset}` / Custom (● when custom wins) / Truck boxes / **Mark recommended** button on done runs, `recommended` badge on the chosen one (`ProjectPage.jsx:424-481`). Row click → `packaging?run={id}`.
18. **User** clicks **Mark recommended**.
    **System**: `PATCH /api/projects/{id}` `{recommended_run_id}`; **422** unless it is a done run of this project (`main.py:500-508`). This pins the run the Overview, list row and proposal use (`runs.py:34-64`).

## 12. Proposal tab — generate, poll, download

19. **User** clicks **Proposal** (or **Generate proposal** on Overview, which lands here with `?generate=1` and fires the POST once, then strips the flag, `ProjectPage.jsx:262-264,547-554`).
    **System**: `GET /api/projects/{id}/proposals` (`ProjectPage.jsx:510-515`).
    **Sees**: "Generated from the recommended run / newest solve", **Generate proposal** button (disabled while any row is pending/processing), table Date / Run (id prefix) / Status pill / action (`ProjectPage.jsx:561-606`).
20. **User** clicks **Generate proposal**.
    **System**: `POST /api/projects/{id}/proposal` → **202** `ProposalOut(status=pending)`; run = recommended if still done, else newest done run with content (`main.py:542-577`, `runs.py:34-64`); **422** "No finished solve…" or "The selected run found no box; nothing to propose." Worker `render_proposal` (300 s limit): `build_pdf` → 9 pages — cover, part & pose, comparison, insert BOM, exploded view, packing sequence (GIF frames), truck load, gain, assumptions (`worker.py:530-581`, `backend/app/proposal.py:544-554`); writes `proposal_{id}.pdf`; `status=done`/`failed`.
    **Sees**: row with spinner "Rendering…", polled via `GET /api/proposals/{id}` every 2 s (`ProjectPage.jsx:534-545,592-596`). On `failed`: red error text. On `done`: **Download PDF** link → `/api/files/proposal_{id}.pdf`, opens in a new tab (`ProjectPage.jsx:597-600`, `main.py:168-177`).
21. **User** (optionally) sets header **Status** → `proposal_sent` etc.
    **System**: `PATCH /api/projects/{id}` `{status}` (`ProjectPage.jsx:158-161`). Status never advances by itself — `solved` is a manual choice too.

**End state**: PDF in hand; project row on the list shows Best / vs cuboid from the recommended (or newest) run.

---

## Steps that exist in the backend but have no UI

- `POST /api/parts/{id}/solve` — standalone part solve; the UI always posts via the project (`main.py:294-301`, `solve.js:48`).
- `GET /api/parts` — flat part list (`main.py:365-370`).
- `POST /api/parts` with `source: "manual"` and no `project_id` — standalone manual part; the UI always attaches to a project.
- `SolveIn.top_n` 1–5 (UI hardcodes 5, `PackingRecommendation.jsx:257`); `SolveIn.tare_kg` up to 400 (rail accepts it, fine).
- `PackagingIn.kind` container/pallet/rack/accessory — both custom-box forms omit it, so everything added is a container (`schemas.py:75`, `PackingRecommendation.jsx:107-114`, `LoadCalculator.jsx:82-88`).
- `Project.notes`, `vehicle_id`, `cost_per_trip`, `emission_factor_kg_per_km` — PATCHable, stored, no input anywhere and nothing computes with them (`schemas.py:344-362`). `route_km` has an input but no consumer.
- `HEAD /api/files/{name}` (`main.py:168`).
- `python -m app.proposal` CLI shares `proposal_run_for` (`runs.py:38-40`).

## Dead ends

- **Wrong CAD saved to a project.** `POST /api/parts` is **409** once a project has a part; the message says re-upload is not supported (`main.py:197-205`). Only exit: new project.
- **Manual-entry project.** Packaging auto-solve returns 422 every time; Overview shows "No solve yet" forever. No route to attach CAD later (same 409).
- **Draft custom box in an unrestricted solve.** Warning says "mark them checked" but Assets is read-only (`Assets.jsx:3`) and there is no PATCH `/api/packaging` (`main.py:337-357`). Only DB access flips `status`. Workaround is naming it in Boxes to rank.
- **Part-less project.** If `POST /api/parts` failed and the user navigated away, the project sits on the list with no part; Overview's "New project" button creates a second project, not a part for this one (`ProjectPage.jsx:363-372`).
- **Revisiting the confirmed pose.** The GLB viewer and candidate picker exist only on New project; after save the project page never renders `part.glb_url` and the pose cannot be changed — only searched-around via "Search confirmed pose only".
- **Failed solve on a project with no earlier done run.** Overview "No solve yet"; Runs shows the `failed` pill; Packaging shows the error and **↻ Re-run** — that button is the only exit.
- **Proposal from a specific run.** `?run=` on the Proposal tab is ignored; the only lever is Mark recommended (`main.py:549-555`).
- **Extraction hung `pending`** (stale worker on the same redis db). NewProject has no poll cap for `/api/jobs`; the spinner never ends. Reload and re-drop.
