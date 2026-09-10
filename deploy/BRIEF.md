# Deployment brief — part-intake on GCP

Written 2026-09-10 by the PM session. This is the complete task for a separate
Claude Code session. Read `CLAUDE.md` first. You will NOT change application
source under `backend/app` or `frontend/src`; you only add deployment files
and run gcloud. Do not git commit anything — the PM session reviews first.

## Decisions already made (do not re-open them)

- **Shape:** ONE Compute Engine VM running the existing docker-compose stack,
  with a Caddy reverse proxy in front serving the built frontend as static
  files and proxying `/api` to the API container. Images built by Cloud Build
  into Artifact Registry; the VM pulls them. Cloud Run was considered and
  rejected: Celery needs an always-on worker, Memorystore needs a VPC
  connector, and storage would need a code change. Migration path later is
  cheap because images live in Artifact Registry and config is all env vars.
- **New GCP project.** Project ID `yantra-part-intake` (if taken, append
  `-1`, `-2`). Organization parent id `629154133972`. Billing account
  `01D100-1FF34B-C4781E` (the only open one). gcloud is already authenticated
  as rahul@trakkia.com. Do NOT deploy anything into `trakkia-uat`,
  `dummy-502513` or `vertex-ai-agent-502006` — those are other products.
  Create a dedicated gcloud configuration (`gcloud config configurations
  create part-intake`) so the user's default config is untouched, and pass
  `--project` explicitly on every command anyway.
- **Region** `asia-south1` (Mumbai), zone `asia-south1-a`. Machine
  `e2-standard-2`, 50 GB pd-balanced, Debian 12. Reserve a static external IP.
- **Access:** HTTPS via Caddy's automatic Let's Encrypt using a
  `<static-ip-with-dashes>.sslip.io` hostname (no DNS to set up), plus HTTP
  basic auth in Caddy for the whole site. Generate a strong random password,
  hash it with `caddy hash-password` (inside the caddy container), store
  username+password in Secret Manager in the new project (secret
  `part-intake-basic-auth`), and report them in your final message.
  Firewall: allow tcp 80 and 443 from anywhere (Caddy needs 80 for the ACME
  challenge); SSH only via IAP (`gcloud compute ssh --tunnel-through-iap`),
  no public 22.
- **Docker is NOT installed on this Mac.** Build with `gcloud builds submit`,
  never locally.

## What the stack needs (verify by reading the repo)

- `docker-compose.yml` at repo root: services api, worker, db
  (postgres:16-alpine), redis (redis:7-alpine), volumes `pgdata` and
  `filedata`. api listens on 8000 inside the container. Env vars are
  `INTAKE_DATABASE_URL`, `INTAKE_REDIS_URL`, `INTAKE_LOCAL_STORAGE_DIR`; full
  list in `backend/app/config.py` (prefix `INTAKE_`).
- `backend/Dockerfile` exists (python:3.12-slim). `requirements.txt` includes
  `cadquery-ocp`, a very large wheel: give Cloud Build `--timeout=1800s` and
  machine type `E2_HIGHCPU_8`.
- Frontend has NO Dockerfile. Vite + React; `npm ci && npm run build` makes
  `dist/`. Write a multi-stage `frontend/Dockerfile`: node:20-alpine build
  stage, then the Caddy image serving `dist/` and proxying `/api/*` to
  `api:8000`. Caddyfile at `deploy/Caddyfile`. Caddy must: basic auth
  site-wide; request bodies up to 220 MB (API `max_upload_mb` is 200); no
  short proxy read timeout (uploads are large, solves poll a job endpoint);
  SPA fallback `try_files {path} /index.html`.
- Worker: same backend image, command
  `celery -A app.worker worker --loglevel=info --concurrency=2`. Worker and
  API MUST share the `filedata` volume (API writes uploads, worker reads
  them) — the root compose already does; keep it.
- Postgres password: generate a random one, put it in the VM's `.env` and in
  Secret Manager as `part-intake-db-password`. Do not ship `intake:intake`.
- The frontend uses relative `/api/...` URLs so no build-time API URL is
  needed. Confirm with `grep -rn "localhost\|8000" frontend/src`.

## Deliverables in the repo (new files only)

- `frontend/Dockerfile`, `deploy/Caddyfile`
- `deploy/docker-compose.prod.yml` — same services as the root compose but
  with Artifact Registry image names for api/worker/frontend instead of
  `build:`, the caddy/frontend service publishing 80 and 443, a `caddy_data`
  volume for certs, env from a `.env` file, `restart: unless-stopped` on
  everything.
- `deploy/cloudbuild.yaml` — builds and pushes backend and frontend images
  tagged `latest` plus a timestamp tag (there may be no git SHA in the
  upload).
- `deploy/deploy.sh` — idempotent: submits the Cloud Build, then over IAP ssh
  runs `docker compose pull && docker compose up -d` on the VM. This is what
  the team reruns to ship. Usage documented at the top.
- `deploy/README.md` — runbook: project, VM name, URL, redeploy, logs
  (`docker compose logs -f api worker`), rotate basic-auth password, restore
  from the daily disk snapshot.
- Daily snapshot schedule on the VM boot disk, retain 7 days. That is the
  backup story for Postgres and the uploaded files.

## VM setup

Startup script or one IAP ssh session: install Docker Engine + compose plugin
from Docker's apt repo; authenticate docker to Artifact Registry via the VM
service account (`gcloud auth configure-docker asia-south1-docker.pkg.dev`;
grant the VM SA `roles/artifactregistry.reader`); copy
`deploy/docker-compose.prod.yml`, `deploy/Caddyfile` and the generated `.env`
to `/opt/part-intake/` with `gcloud compute scp --tunnel-through-iap`; run
`docker compose up -d`.

## Verification — do all of it and paste real output

1. `curl -sk -u user:pass https://<host>/api/packaging | head -c 300` returns
   JSON.
2. `curl -sk -u user:pass https://<host>/` returns the SPA HTML.
3. Without credentials returns 401.
4. `docker compose ps` on the VM shows api, worker, db, redis, caddy all Up.
   Worker log shows `celery@... ready` and a redis connection.
5. End to end with real CAD: upload
   `backend/tests/fixtures/customer/QY2i SW 3D DATA FOR PACKAGING PURPOSE_(12-08-26).stp`
   via the upload route (read `backend/app/main.py` for the exact route and
   form field), poll the job until `done`, and confirm the WORKER log — not
   the API log — processed it. CLAUDE.md landmine 6: the API silently falls
   back to in-process extraction when the broker is unreachable, so this is
   the only proof the worker path works. Then create a part from the result,
   POST a solve, poll to done, and confirm `catalogue` contains PLS1280 at
   count **48** (ground truth). Report anything you could not verify rather
   than assuming it.

## Landmines

- Worker task time limit is 600s; nothing in the proxy path may time out
  sooner.
- `pgdata` must persist across `docker compose up -d`; never `down -v`.
- Enable only: compute, artifactregistry, cloudbuild, secretmanager, iap.
- If a gcloud command needs an interactive login or browser, stop and report
  exactly what is needed instead of guessing.

## Final message must contain

Project id; VM name and zone; the HTTPS URL; basic-auth username and
password; secret names; what was verified with the actual output lines; what
was not; estimated monthly cost; the list of files added to the repo. Then
append a dated entry to `PROGRESS.md` summarising the same.
