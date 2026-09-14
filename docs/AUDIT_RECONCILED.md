# Reconciled audit — ours vs external, 2026-09-14

Inputs: `docs/WORKFLOW_AUDIT.md` (ours), the external agency's response
(round 1, documents only, no code), and code checks done today. Rahul's own
list of defects found in live use is still to be merged — see the last section.

## Where the external audit is right and we change our ranking

- **Product trust outranks operability.** Accepted. Our top-5 was
  infrastructure-first because the libGL outage was that morning's wound. The
  core risk is an engineer reading a number that belongs to a different run
  than the drawing beside it, and a polished PDF making that look authoritative.
- **Run identity is not propagated** — Overview ignores `?run=`, Packaging and
  Truck honour it, Proposal ignores it. Verified. P0.
- **Proposal generation picks its own run** — `main.py:553 proposal_run_for`
  (recommended, else newest done) instead of the run on screen. Verified. P0.
  Lazy fix: `POST /api/projects/{id}/proposal` takes `run_id`; the button
  passes the run it is looking at; 422 if that run is not done.
- **Count coupled to rendering** — agreed P0 (was our #3). Verified: one
  `solve_part` task does nesting + PNGs + GIFs; `--concurrency=2` on a 2-vCPU
  VM (`deploy/docker-compose.prod.yml:27`); soft limit 570 s vs 564 s real.
- **Two counts on screen** — `LayoutOut.count` and `count_upper`
  (`schemas.py:214,240`) reach the UI with no label saying which is the
  recommendation. P0 as labelling, not as engine work.
- **CI gate P0 → P1, NDA fixtures P0 → P1.** Accepted on the strict P0
  definition. Stays in the first batch anyway: it is a two-hour change and the
  libGL incident shows a P1 process gap produces P0 outcomes.
- **Manual entry is a dead path** (422 on every solve). Accepted P1. Lazy fix:
  hide the segment until it solves.
- **Calculation certificate.** Strongly accepted. Most fields already exist on
  `RunOut` (pose, pitch, pattern, clearance, weight cap, cuboid, reasons); this
  is the "Why this design" card rebuilt as a verifiable chain, not new engine
  work.

## Where we push back

- **"Save is not transactional" (P1)** — stale. Fixed 2026-09-11: a blocked
  part save leaves exactly one project and the retry attaches to it
  (`NewProject.jsx:47`, PROGRESS.md). Remaining issue is that the recovery
  state is not explained on screen. P2.
- **"Business metrics change after the solve" (P0)** — deliberate design (F3):
  the *count* never moves; only gain-vs-customer and trips/year are computed at
  read time from project inputs so the customer's pack can be entered after the
  solve. Agree it needs a label ("from current project inputs, not stored with
  the run"). P1, one line of UI.
- **"Weight needs extraction-grade validation" (P1)** — weight cannot come from
  the CAD: customer models are open surfaces, hard rule 4 forbids mass. A
  provenance label ("entered by user") is right; extraction is impossible. P2.
- **"/api/files exposes the storage directory" (P0 for team two)** — names are
  job UUIDs behind basic auth, no listing, no traversal (`main.py:170`). Not
  exploitable today; becomes P0 the day two teams share the login. Keep on the
  team-two gate, not the fix-now list.

## Combined ranked fix list

Order = what we build next. One ticket each; acceptance check stated.

| # | Sev | Fix | Check |
|---|---|---|---|
| 1 | P0 | **Run identity everywhere.** One `runId` in the URL drives Packaging, Truck, Overview, Proposal. Every headline number and drawing shows `Run #id · time`. Proposal route takes `run_id`. | Open `?run=<older>`: all four tabs show that run; generated PDF names it on the cover. |
| 2 | P0 | **Count before pictures.** `solve_part` stores the count and marks `done`; chains `render_pictures`. `--concurrency=1`. Solve limit 1200 s on the VM. | Mubea on the VM: count visible < 60 s; drawings arrive later; a killed render leaves the count intact with a `warnings` entry. |
| 3 | P0 | **Label the two counts + certificate.** "Calculated count" vs "geometric upper bound" named; the Why card becomes the certificate: pose confirmed/winning, box, clearance, raster, pattern, limited-by, weight, truck chain. | An engineer reconciles 40 → layer × layers → box → truck on screen without the PDF. |
| 4 | P1 | **Bounded async + health.** Cap every poll (extraction/run/proposal) with a terminal "stuck — retry" state; map known exception classes to user text; `GET /api/health` pings the worker; compose healthcheck. | Stop the worker; upload: UI shows "worker unavailable" within 60 s, not a spinner. |
| 5 | P1 | **Deploy gate.** Cloud Build step runs the tests inside the built backend image (fixtures from a private GCS bucket, `INTAKE_SKIP_NDA=1` until then); post-deploy `curl /api/health`. | Break an import; push; build fails; VM unchanged. |
| 6 | P1 | **Small dead ends.** Hide Manual entry; draft-box message says the real action or a status toggle ships; remove `route_km` input until it has a consumer; label read-time metrics. | No message on screen names an action that does not exist. |
| 7 | P1 | **CAD revision inside a project.** Replace part → new part version, mark old runs `superseded`, pose picker reachable after save. | Upload corrected CAD to an existing project; old runs visibly stale; new solve works. |
| 8 | P1 | **Team-two gate.** Per-user credentials + attribution; project scoping; files scoped to project; `DELETE /api/projects/{id}` with file cleanup + retention; one rehearsed restore; log rotation. | Second login sees only its projects; deleted project frees its files; restore drill logged in PROGRESS. |

## Round-2 questions for the agency

1. You marked nothing "needs code" — we can grant repo read access. Which of
   your findings would change with it?
2. Certificate: which fields are the minimum an engineer needs to *verify* a
   count in under a minute, versus nice-to-have? We want the smallest card.
3. Read-time metrics (gain, trips/year): label as "from current project
   inputs", or snapshot them into the run at solve time and force a re-solve to
   change them? You argued P0; which way?
4. Team-two gate: if we had two weeks, which of your eight items would you
   cut to ship, and what is the residual risk of each cut?
5. `count` vs `count_upper`: would you show the band (40–44) or the single
   calculated count with the bound in the certificate only?

## Still to merge

Rahul's own list of defects from live use. Add them here with a step number
from `docs/USER_JOURNEY.md` and they get slotted into the table above.
