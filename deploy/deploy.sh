#!/usr/bin/env bash
# Ship part-intake to the GCP VM by hand. Pushing to `main` does the same thing
# automatically via the Cloud Build trigger `part-intake-main` (see README.md).
#
#   deploy/deploy.sh              Cloud Build builds the images AND deploys them
#                                 (the deploy step lives in deploy/cloudbuild.yaml)
#   deploy/deploy.sh --no-build   skip the build; just re-sync compose/Caddyfile
#                                 to the VM and `docker compose pull && up -d`
#
# Needs: gcloud authenticated with access to project yantra-part-intake
# (`gcloud config configurations activate part-intake`). No local Docker needed.
set -euo pipefail

PROJECT=yantra-part-intake
ZONE=asia-south1-a
VM=part-intake-vm
REMOTE_DIR=/opt/part-intake

cd "$(dirname "$0")/.."

if [[ "${1:-}" != "--no-build" ]]; then
  TAG=$(date -u +%Y%m%d-%H%M%S)
  echo ">> Cloud Build + deploy: tag $TAG"
  gcloud builds submit --project "$PROJECT" --config deploy/cloudbuild.yaml \
    --substitutions="_TAG=$TAG" .
else
  echo ">> Sync compose + Caddyfile (.env is managed on the VM, never overwritten)"
  gcloud compute scp --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap \
    deploy/docker-compose.prod.yml deploy/Caddyfile "$VM:/tmp/"
  gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap --command "
    set -e
    sudo mv /tmp/docker-compose.prod.yml $REMOTE_DIR/docker-compose.yml
    sudo mv /tmp/Caddyfile $REMOTE_DIR/Caddyfile
    cd $REMOTE_DIR
    sudo docker compose pull -q
    sudo docker compose up -d --remove-orphans
    sudo docker image prune -f >/dev/null
    sudo docker compose ps
  "
fi
echo ">> Done. Rollback: set IMAGE_TAG=<older tag> in $REMOTE_DIR/.env on the VM and rerun with --no-build"
