# Workflow audit — part-intake (2026-09-14)

Scope: the whole pipeline (upload → PDF) and the whole delivery loop (ticket → prod).
Every line: `severity — finding — evidence — lazy-est fix`. P0 = wrong answer or
data loss reachable today; P1 = outage/hang/silent failure a user cannot diagnose;
P2 = growth, hygiene, or debt. File refs are relative to the repo root.

Reference numbers on the prod VM (e2-standard-2, api+worker+db+redis+caddy on 2 vCPU):
STEP extract ~10 s, solve without pictures ~25 s (`PROGRESS.md:1876-1879`), one GIF
~40 s vs 5 s on a Mac (`backend/app/worker.py:32-34`), a full solve was ~5 min before
the GIF cut (`PROGRESS.md:2256-2258`), proposal PDF ~1.3 s (`PROGRESS.md:2227`).
Radiator-class STEP takes 41-564 s on the dev Mac (`PROGRESS.md:1170`, `:1579`).

---

## A. Product / system workflow

### A1. Upload → ExtractionJob

- P1 — `.delay()` against a live redis with no live worker succeeds; the job sits `pending` forever with `error: null`. The fallback only fires when the broker connection is *refused* — CLAUDE.md landmine 6 (fallback masks a dead worker) and landmine 2 (stale worker eats the task) are the same root cause: the API cannot see the worker — `backend/app/main.py:139-148`, `backend/app/worker.py:56-59` (silent `return` on "Job not found"), `PROGRESS.md:1976-1979` — add `GET /api/health` that runs `celery_app.control.ping(timeout=1)` and have the UI show "no worker" from it; drop the thread fallback in prod (`if settings.dev_fallback`).
- P1 — extraction poll in the UI is uncapped: a job stuck at `pending` shows "Extracting dimensions…" until the tab is closed — `frontend/src/components/NewProject.jsx:93-107` (setInterval, no counter) — reuse `MAX_POLLS_PENDING` from `frontend/src/lib/solve.js:14` and surface the same "no worker" message.
- P1 — hard `time_limit=300` on `extract_step` SIGKILLs the child; nothing catches that, so the row stays `processing` forever (soft limit is caught by the broad `except`, hard limit is not) — `backend/app/worker.py:47`, `:63-76` — set `soft_time_limit` 30 s under the hard one on every task (proposal has none: `worker.py:579`) and add a sweeper that marks `processing` rows older than the hard limit `failed`.
- P2 — uploaded CAD is written to disk *before* the DB row; an exception between `:129` and `:132` leaves a file with no row — `backend/app/main.py:119-132` — write the row first, or wrap both in one try that unlinks on failure.
- P2 — every upload persists the raw CAD (13-50 MB) + GLB forever, confirmed or not; nothing deletes an abandoned job — `backend/app/main.py:109-149`, no `unlink`/DELETE anywhere in `backend/app/*.py` (grep) — nightly `find /data/files -mtime +30 -name '*.stp' -o -name '*.igs'` for jobs with no PartProfile referencing them; or one `DELETE /api/jobs/{id}`.
- P2 — `/api/files/{name}` serves the whole storage dir, including customer CAD under NDA, to anyone past basic auth — `backend/app/main.py:168-177`, `deploy/Caddyfile:19-21` — acceptable for one team; restrict the suffix set to `.glb/.png/.gif/.pdf` when a second team shares the login.
- P2 — `storage_backend="s3"`, `s3_bucket`, `aws_region` are dead config on a GCP deployment — `backend/app/config.py:11-14` — delete.

### A2. Confirm → PartProfile

- P1 — a project can never replace its CAD: second `POST /api/parts` for the same project is a 409 "not supported yet". Real customers re-export after a revision (Bharat Forge R5, `PROGRESS.md:1223-1226`) — `backend/app/main.py:197-205` — allow the POST when the existing part has no `done` runs, else require a new project; document either.
- P2 — no DELETE endpoint for parts/projects/jobs, so the stray prod part from the libGL test cannot be removed "from the UI" as the log instructs — `PROGRESS.md:2283-2284`, grep `@app.delete` = 0 — add `DELETE /api/projects/{id}` (cascades manually: parts, solve_jobs, proposals, files).
- P2 — `project_id`, `part_id`, `run_id`, `recommended_run_id` are bare `Integer`/`String` columns, no FK, no cascade — `backend/app/models.py:89-94`, `:127`, `:160`, `:179-184` — fine on SQLite dev, but Postgres would enforce for free: add `ForeignKey(...)` on the next new table (cannot ALTER existing without a migration).

### A3. Solve (Celery)

- P0 — worker soft limit 570 s vs the slowest real CAD at 564 s on the Mac and ~8x slower on the VM: any radiator-class part fails with `SoftTimeLimitExceeded` after ~10 min of waiting, while the UI budget is 660 s — `backend/app/worker.py:209`, `frontend/src/lib/solve.js:15`, `PROGRESS.md:1579` — either bump the task limit to 1800 s and the poll to match, or return the count first and render pictures in a second task (`PROGRESS.md:2269-2270` already proposes this).
- P1 — picture failures are swallowed by design and nothing reaches `warnings`, so a solve with no PNG/GIF looks identical to a solve whose pictures were never requested — `backend/app/worker.py:87-92`, `:152-154`, `:165-167` — append one line to `warnings` per failed artifact (`f"{file_stem}: drawing not rendered"`).
- P1 — `concurrency=2` on a 2-vCPU box shared with api/db/redis/caddy: two simultaneous solves each pull 40 s GIFs and starve the API — `deploy/docker-compose.prod.yml:27`, `backend/app/worker.py:32-38` — `--concurrency=1` until the picture task is split off.
- P1 — no retries anywhere (no `autoretry_for`/`max_retries`, grep), and `acks_late=True` re-delivers a task whose worker died mid-run to a new worker with no idempotency key: a worker that dies after `db.commit()` but before the ack gets the task redelivered and `run_solve` overwrites a `done` row from scratch — `backend/app/worker.py:41`, `:229-235` — guard `run_solve` with `if job.status == "done": return`.
- P1 — the stored-run poll (opening a project with a pending run) is uncapped at 3 s forever; only the *new-solve* poll has the 90 s/660 s caps — `frontend/src/components/PackingRecommendation.jsx:291-314` vs `frontend/src/lib/solve.js:14-15` — route both through `runSolve`'s loop or copy the two caps.
- P1 — a duplicate solve was enqueued in prod by leaving and returning to the tab during a pending job; fixed client-side only (`pickedRun`), nothing server-side stops two `pending` jobs for one part — `PROGRESS.md:2259-2262`, `backend/app/main.py:272-282` — in `_enqueue_solve`, return the existing `pending|processing` job for the same `part_id` + identical `inputs_json` instead of creating one.
- P2 — every re-run writes ~5 PNG + 2 GIF (~1.1 MB each) + a new `solve_jobs` row and nothing is ever deleted; 50 GB disk, no alert — `backend/app/worker.py:114-123`, `:174-201`, `deploy/README.md:7` — a `df` check in the daily snapshot cron or Cloud Monitoring disk alert (free); delete drawings of runs older than the newest N per project.
- P2 — engine answer is calibrated on N=2 shipped proposals and the headline 40 is a `VOXEL_MM=4` artefact (3 mm → 44, 5 mm → 36); this is recorded, not fixed — `PROGRESS.md:1828-1839`, `backend/app/nesting.py:46`, `:71-73` — not a workflow fix; keep `count_upper` visible on every card and in the PDF so the band ships with the number.

### A4. Drawings / GIF

- P1 — GIF is the single most expensive step (~40 s each on the VM, 2 per solve) and it blocks the count; the stated "about a minute" copy will be wrong on any part slower than the wheel — `backend/app/worker.py:32-38`, `frontend/src/components/PackingRecommendation.jsx:158` — second Celery task `render_pictures(job_id)` chained after `solve_part`; mark the job `done` before pictures, `drawing_url` nullable is already the contract.
- P2 — `_render_drawings` re-voxelises per pose; on the P118 that is ~3 min per pose on the Mac — `backend/app/worker.py:98-112`, `PROGRESS.md:1577-1579` — cache `pose_voxels` output on disk keyed by `(job_id, pose_label)` only if the split above is not enough.

### A5. Proposal PDF → download

- P1 — proposal poll is 2 s, uncapped; a stuck proposal spins forever — `frontend/src/components/ProjectPage.jsx:493`, `:535-544` — same cap as A1.
- P2 — `render_proposal` has a hard 300 s limit and no soft limit: a kill leaves `processing` forever — `backend/app/worker.py:579` — `soft_time_limit=270`.
- P2 — VM has no Fira Sans, PDF silently falls back to DejaVu — `backend/app/proposal.py:62-64`, `PROGRESS.md:2242-2243` — ship the two TTFs under `app/fonts/` and `fm.fontManager.addfont()` them next to `_FONT`; no apt guesswork.
- P2 — `proposals.run_id` is resolved at POST time (good) but a later `DELETE`/re-solve can leave a PDF whose run is invisible in the Runs tab — `backend/app/models.py:179-184`, `PROGRESS.md:2168-2170` (legacy rows already invisible) — nothing until DELETE exists; then refuse to delete a run with a proposal.

### A6. Cross-cutting: auth, schema, backups, observability

- P1 — one shared basic-auth login at Caddy; `Project.owner` is free text; no audit trail of who solved/changed status — `deploy/Caddyfile:19-21`, `backend/app/models.py:148` — for a second team: second `basic_auth` user per team in the Caddyfile (Caddy supports N users in the block) and stamp `{http.auth.user.id}` into a header the API writes to `owner`. Real SSO is not the lazy fix.
- P1 — no `/health`, no api/worker healthcheck, no alert; the libGL outage was found by Rahul ("nothing parses") — `deploy/docker-compose.prod.yml:37-41` (db only), `PROGRESS.md:2273-2275` — `GET /api/health` (db + redis ping + worker ping) + compose `healthcheck` on api + an uptime check on it in Cloud Monitoring.
- P1 — schema changes are a hand-maintained ALTER map run at import time in the *api* process only; the worker connects without `create_all` — `backend/app/main.py:56-90`, `backend/app/worker.py:44` — first boot of a fresh volume with worker up before api errors on missing tables; make the worker call `_ensure_added_columns` too, or move both into a tiny `app/db.py:init()` both import. Alembic when the fourth column lands.
- P1 — backups are one daily boot-disk snapshot, 7-day retention, restore never tested — `deploy/README.md:14`, `:74-90`, `PROGRESS.md:1882-1884` — run the README restore once into a throwaway disk and record the date; add `pg_dump | gzip` to a bucket weekly (a disk snapshot mid-write is crash-consistent, not application-consistent).
- P2 — docker json-file logs have no rotation configured; the worker logs a full traceback per failed render — `deploy/docker-compose.prod.yml` (no `logging:` block) — `logging: {driver: json-file, options: {max-size: 50m, max-file: '3'}}` on the `x-backend` anchor.
- P2 — CORS `*` on the API — `backend/app/main.py:93-98` — harmless behind Caddy (api not published, `docker-compose.prod.yml:23`); delete the middleware in prod or leave with the comment.

---

## B. Development & delivery workflow

### B1. The cycle (PM → implementer → tester → /code-review → ground truth)

- P1 — `/code-review` did not exist for the first eleven tickets; CLAUDE.md listed it as step 4 with no command behind it, and the same file claimed a contract test that did not exist — `PROGRESS.md:1257-1262`, `:1269`, `:1431-1434` — both now exist (`.claude/commands/code-review.md`, `backend/tests/test_scale_contract.py`); add a one-line "does this command/test exist" check to the ticket template.
- P1 — the cycle was followed *after* 2026-09-10 (tester walks `PROGRESS.md:2144-2157`, `:2232-2239`; review `:2061-2068`) but the whole engine lived uncommitted for two days and 23 modified source files were live in the prod image while uncommitted — `PROGRESS.md:1767-1769`, `:1886-1893`, `:2010-2012` — rule: `git commit` is step 5 of every ticket, before PROGRESS.md; deploy only via the trigger (never `deploy.sh` from a dirty tree, which uploads the working tree).
- P2 — PMs invented facts about existing code in two consecutive tickets (D1 `partsPerBox` helpers; "existing stale-params affordance") — `PROGRESS.md:824-832` — ticket template gets a "verified by grep" line for every claim about existing code.
- P2 — implementers' own checks passed while shipped defaults gave the wrong headline (36 vs 40), three sessions running — `PROGRESS.md:1066-1083`, `:806-822` — the hunting list in `.claude/commands/code-review.md:31-40` already encodes this; keep it.

### B2. Test coverage

- P0 — the ground-truth contract cannot run in CI: `test_clearance` fails without the NDA fixtures, the fixtures are gitignored, and Cloud Build honours `.gitignore`, so the mandatory regression can only ever run on one laptop — `backend/tests/fixtures/customer/README.md:7-11`, `.gitignore:13`, `deploy/cloudbuild.yaml:5` — keep the NDA files out of git; put them in a private GCS bucket in `yantra-part-intake` and `gsutil cp` them into the build step under `INTAKE_FIXTURES_DIR`.
- P1 — nothing ever imported OCP inside the image until prod: `sample.igs` exists as a fixture (`backend/tests/fixtures/sample.igs`) and `test_geometry.py` would have caught it, but no test runs in the container — `PROGRESS.md:2272-2279`, `backend/Dockerfile:15` (guard added after) — Dockerfile `RUN python tests/test_geometry.py` after `COPY app` (copy `tests/` too, or a 30-line smoke that extracts `sample.igs`).
- P1 — tests are 17 `__main__` scripts, no runner, no single command; `tester.md` says `python -m pytest tests/ -v` but pytest is not installed and `test_geometry.py` needs a pytest fixture — `.claude/agents/tester.md:26`, `backend/requirements.txt` (no pytest), `backend/tests/test_geometry.py:12-14` — one `backend/tests/run_all.sh` that loops the 13-suite list from `PROGRESS.md:66-72` and exits non-zero on the first failure; that is what CI calls.
- P1 — route tests drive functions directly and never serialise; hard rule 9 has been broken four times because of it — `backend/tests/test_solve_api.py:7-12`, `PROGRESS.md:1271`, `:1281-1284` — add `httpx` to requirements so `fastapi.testclient` works; wrap the same tests in `TestClient(app)` (one import change per file).
- P2 — frontend has five ad-hoc `*.check.mjs` files, no `npm test`, not in the build — `frontend/package.json:6-10`, `frontend/src/lib/*.check.mjs` — `"test": "for f in src/lib/*.check.mjs; do node $f || exit 1; done"` and call it from the frontend Dockerfile before `npm run build`.
- P2 — no test exercises the Celery path itself (all tests replay `run_solve` in-process); the stale-worker and no-worker hangs are therefore untestable today — `backend/tests/test_projects_api.py:6-8` — once `/api/health` exists, the tester agent's live walk asserts it says `worker: ok` before any solve.
- P2 — every browser defect (6, then 11, then 4 console errors still open) came from a human or a headless walk, never from a check — `PROGRESS.md:870-899`, `:2157-2163`, `:2265-2266` — the CDP recipe is in memory; commit it as `frontend/smoke.mjs` and run it against the compose stack in the tester step, not in Cloud Build.

### B3. CI / CD

- P0 — push to `main` builds and deploys with zero tests between: no unit, no contract, no smoke — `deploy/cloudbuild.yaml:12-45`, `deploy/README.md:23-27` — add one step between `backend` and `push`: `docker run backend:$_TAG bash tests/run_all.sh` with `INTAKE_SKIP_NDA=1` today, fixtures from the bucket (B2 P0) tomorrow.
- P1 — `deploy.sh` uploads the *working tree*, not HEAD, so a manual deploy can ship uncommitted code with a timestamp tag no commit maps to — `deploy/deploy.sh:22-25`, `PROGRESS.md:1886-1893` — `git diff --quiet || { echo dirty; exit 1; }` at the top of `deploy.sh`.
- P1 — no post-deploy check: `docker compose ps` is printed, nothing asserts 200 on `/` or `/api/packaging` — `deploy/cloudbuild.yaml:42-45` — `curl -fsS -u "$USER:$PASS" https://$SITE_HOST/api/health` as the last line; fail the build (images already pushed, old containers keep running per `README.md:29-31`).
- P2 — `latest` is retagged on every build and the compose default is `latest`, so a VM reboot after a bad build pulls the bad image even if you never ran `up` — `deploy/docker-compose.prod.yml:9`, `:48`, `deploy/vm-startup.sh:27-29` — pin `IMAGE_TAG=<sha>` in `.env` from the deploy step (`sed -i` one line) so `latest` is never what runs.

### B4. Rollback

- P1 — image rollback is documented; schema rollback is not possible (`_ensure_added_columns` is forward-only) and data written by the new code (e.g. `inputs_json`) is silently ignored by the old — `deploy/README.md:40-41`, `backend/app/main.py:70-73` — acceptable while columns are only added; write it down in the runbook, and take a manual snapshot before any deploy that touches `models.py`.
- P2 — rollback needs an IAP ssh session and a hand edit of `.env` — `deploy/README.md:40-41` — `deploy/rollback.sh <tag>` = the `--no-build` branch of `deploy.sh` with a `sed` on `IMAGE_TAG`.

### B5. Local dev landmines (CLAUDE.md 1-6) — still live

- P1 — landmines 2 and 6 are both "the API cannot tell whether a worker exists" and cost 20 min + a false bug report each time — `CLAUDE.md` landmines 2, 6, `PROGRESS.md:699-708` — fixed once by the `/api/health` worker ping (A1).
- P2 — dev compose publishes 8000, which collides with the Trakkia Django server on this machine — `docker-compose.yml:4`, CLAUDE.md landmine 1 — `ports: ["127.0.0.1:8011:8000"]`.
- P2 — `INTAKE_REDIS_URL` defaults to a docker hostname and silently breaks uploads outside compose — `backend/app/config.py:8`, landmine 3 — default to `redis://127.0.0.1:6379/0` (compose overrides it anyway).
- P2 — worker never hot-reloads (landmine 2) — `celery -A app.worker worker` in CLAUDE.md — `watchmedo auto-restart -d app -p '*.py' -- celery ...` is one line and one dev-only dep.

---

## Top 5 to do next

1. **Gate the deploy.** One Cloud Build step that runs `tests/run_all.sh` inside the built backend image (fixtures from a private bucket, `INTAKE_SKIP_NDA=1` until then) plus a post-deploy `curl /api/health`. Closes B3 P0, B2 P1 (libGL class), B2 P0 path.
2. **`GET /api/health` with a worker ping** + compose healthcheck + Cloud Monitoring uptime check. Kills landmines 2 and 6, the pending-forever class (A1), and "found by the customer" outages (A6).
3. **Count before pictures.** Split `render_pictures` into a chained task; `concurrency=1`; raise/align the solve limit. The 2-vCPU VM cannot do a radiator-class part inside today's 570 s (A3 P0, A4).
4. **Cap every poll and surface every swallowed failure.** Three uncapped polls (extraction, stored run, proposal) and silent picture failures; ~20 lines total (A1, A3, A5).
5. **Commit discipline + dirty-tree guard in `deploy.sh`**, and a tested restore. Two days of engine uncommitted and uncommitted code live in prod is the largest unforced risk in the log (B1, B3, A6).

## Verdict

Not yet fit for a second customer team. The engine and its ground-truth discipline are
the strongest part of the project; the workflow around it is a single-operator setup:
one shared login, one laptop that can run the contract tests, push-to-main with no gate,
polls that spin forever when the worker is dead, a solve budget that a real radiator
exceeds on the production VM, no delete, no cleanup, and an untested restore. None of
this is architectural — items 1-4 above are each under a day and mostly additive — but
until a failed job tells the user *why*, and a bad push cannot reach the VM, a second
team will generate the same "nothing parses" tickets the first one did, and the PM will
be the only person who can diagnose them.
