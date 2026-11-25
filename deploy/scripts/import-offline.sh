#!/usr/bin/env bash
set -euo pipefail

# Load offline Docker images on an air-gapped Linux host, bootstrap .env secrets, and start services.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
IMAGES_TAR="${BUNDLE_ROOT}/images.tar"

if [[ -f "${IMAGES_TAR}" ]]; then
  echo "==> [1/3] Loading Docker images from ${IMAGES_TAR}..."
  docker load -i "${IMAGES_TAR}"
else
  echo "==> [1/3] images.tar not found at ${IMAGES_TAR}, assuming images are already loaded."
fi

ENV_FILE="${DEPLOY_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "==> [2/3] Generating ${ENV_FILE} with random production secrets..."
  cp "${DEPLOY_DIR}/.env.example" "${ENV_FILE}"
  PG_PASS="$(openssl rand -hex 16 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
  JWT_KEY="$(openssl rand -hex 32 2>/dev/null || head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 48)"
  AES_KEY="$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)"

  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|" "${ENV_FILE}"
  sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=${JWT_KEY}|" "${ENV_FILE}"
  sed -i "s|^CSM_SECRET_KEYS=.*|CSM_SECRET_KEYS={\"1\":\"${AES_KEY}\"}|" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "IMPORTANT: Back up ${ENV_FILE} (especially CSM_SECRET_KEYS) separately from DB backups!"
else
  echo "==> [2/3] Using existing ${ENV_FILE}."
fi

echo "==> [3/3] Starting Oh-My-CSM services (without rebuild)..."
cd "${DEPLOY_DIR}"
docker compose up -d --no-build

HTTP_PORT="$(grep -E '^HTTP_PORT=' "${ENV_FILE}" | cut -d= -f2 || echo 8080)"
echo "Oh-My-CSM is starting on http://<host>:${HTTP_PORT:-8080}"
echo "Default admin credentials: admin / Admin123456 (forced password change on first login)."
