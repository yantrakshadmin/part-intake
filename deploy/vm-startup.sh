#!/usr/bin/env bash
# Compute Engine startup script for part-intake-vm (runs as root on every boot;
# every step is a no-op once done). Installs Docker Engine + compose plugin
# from Docker's apt repo and logs root's docker client in to Artifact Registry
# using the VM service account.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! command -v docker >/dev/null; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# Debian 12 GCE images ship gcloud; this uses the VM SA (needs roles/artifactregistry.reader).
gcloud auth configure-docker asia-south1-docker.pkg.dev --quiet

install -d -m 0755 /opt/part-intake
if [[ -f /opt/part-intake/docker-compose.yml && -f /opt/part-intake/.env ]]; then
  (cd /opt/part-intake && docker compose up -d) || true
fi
