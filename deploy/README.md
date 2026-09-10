# part-intake on GCP — runbook

| | |
|---|---|
| GCP project | `yantra-part-intake` (org trakkia.com, billing 01D100-1FF34B-C4781E) |
| gcloud config | `gcloud config configurations activate part-intake` |
| VM | `part-intake-vm`, zone `asia-south1-a`, e2-standard-2, 50 GB pd-balanced, Debian 12 |
| Static IP | `part-intake-ip` = 34.47.230.213 |
| URL | https://34-47-230-213.sslip.io (Let's Encrypt via Caddy, HTTP basic auth site-wide) |
| Images | `asia-south1-docker.pkg.dev/yantra-part-intake/part-intake/{backend,frontend}` |
| Secrets | `part-intake-basic-auth` (username=/password= lines), `part-intake-db-password` |
| Stack on VM | `/opt/part-intake/{docker-compose.yml,Caddyfile,.env}` — compose project `part-intake` |
| Backups | snapshot schedule `part-intake-daily`, 20:00 UTC, 7-day retention, on the boot disk |

Shape: one VM, docker compose (api, worker, db, redis, caddy). Caddy serves the
built SPA and proxies `/api/*` to `api:8000`. Postgres data (`pgdata`),
uploaded CAD + GLBs (`filedata`) and certs (`caddy_data`) are docker volumes on
the boot disk — that is why the disk snapshot is the whole backup.

## Deploy

**Push to `main` deploys.** Cloud Build trigger `part-intake-main` (region
asia-south1, GitHub connection `part-intake-github`) runs `deploy/cloudbuild.yaml`
on every push to `main`: build both images (~5 min), push as `latest` +
`<short sha>`, then over IAP ssh sync compose/Caddyfile to the VM and
`docker compose pull && up -d`. Watch it:
`gcloud builds list --region asia-south1 --limit 5` or the Cloud Build console.
A failed build leaves the previous images running; nothing on the VM changes
until the `deploy` step.

By hand (same pipeline, tag is a timestamp instead of a sha):

```bash
gcloud config configurations activate part-intake
deploy/deploy.sh              # build + deploy from your working tree
deploy/deploy.sh --no-build   # only re-sync compose/Caddyfile and restart what changed
```

Roll back: on the VM set `IMAGE_TAG=<older tag>` in `/opt/part-intake/.env`,
then `deploy.sh --no-build` (or `git revert` and push).
Tags: `gcloud artifacts docker images list asia-south1-docker.pkg.dev/yantra-part-intake/part-intake --include-tags`.

## SSH and logs

```bash
gcloud compute ssh part-intake-vm --zone asia-south1-a --tunnel-through-iap   # port 22 is IAP-only
cd /opt/part-intake
sudo docker compose ps
sudo docker compose logs -f api worker
sudo docker compose logs caddy | tail        # ACME / auth failures
```

A hung job: check the WORKER log, not the API log. The API falls back to
in-process extraction when redis is unreachable (CLAUDE.md landmine 6), so a
job that "works" with the worker dead is the failure mode to look for.

## Rotate the basic-auth password

```bash
# on the VM
cd /opt/part-intake
NEW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-24); echo "$NEW"
HASH=$(sudo docker run --rm caddy:2-alpine caddy hash-password --plaintext "$NEW")
sudo sed -i "s|^BASIC_AUTH_HASH=.*|BASIC_AUTH_HASH='$HASH'|" .env     # keep the single quotes
sudo docker compose up -d caddy
# from your laptop: update the secret
printf 'username=yantra\npassword=%s\n' "$NEW" | gcloud secrets versions add part-intake-basic-auth --data-file=-
```

Read the current credentials: `gcloud secrets versions access latest --secret part-intake-basic-auth`.

## Restore from a snapshot

```bash
gcloud compute snapshots list --filter="sourceDisk~part-intake-vm"
# 1. make a disk from the chosen snapshot
gcloud compute disks create part-intake-vm-restored --zone asia-south1-a \
  --source-snapshot <SNAPSHOT_NAME> --type pd-balanced
# 2. swap the boot disk
gcloud compute instances stop part-intake-vm --zone asia-south1-a
gcloud compute instances detach-disk part-intake-vm --zone asia-south1-a --disk part-intake-vm
gcloud compute instances attach-disk part-intake-vm --zone asia-south1-a --disk part-intake-vm-restored --boot
gcloud compute instances start part-intake-vm --zone asia-south1-a
```

The startup script (`deploy/vm-startup.sh`, in instance metadata) runs
`docker compose up -d` on boot, so the stack comes back with the restored
volumes. Re-attach the snapshot schedule to the new disk:
`gcloud compute disks add-resource-policies part-intake-vm-restored --zone asia-south1-a --resource-policies part-intake-daily`.

Never `docker compose down -v` — it deletes pgdata and filedata.

## Rebuild the VM from nothing

Firewall: `part-intake-web` (80/443 anywhere), `part-intake-iap-ssh` (22 from
35.235.240.0/20); tag `part-intake`. Create with `--scopes=cloud-platform`,
`--address=part-intake-ip`, `--metadata-from-file=startup-script=deploy/vm-startup.sh`.
The compute default SA needs `roles/artifactregistry.reader` (pulls) and
`roles/cloudbuild.builds.builder` (Cloud Build). Then write `/opt/part-intake/.env`
from the two secrets (keys: `POSTGRES_PASSWORD`, `SITE_HOST`, `BASIC_AUTH_USER`,
`BASIC_AUTH_HASH`, `IMAGE_TAG`) and run `deploy/deploy.sh`.

## Cost (approx., asia-south1)

e2-standard-2 ~$59/mo, 50 GB pd-balanced ~$6, static IP ~$4, snapshots ~$2,
Artifact Registry ~$1, Cloud Build ~$0.25 per deploy. About **$70–75/month**.
