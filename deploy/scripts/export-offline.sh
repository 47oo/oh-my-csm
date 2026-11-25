#!/usr/bin/env bash
set -euo pipefail

# Build images on an internet-connected machine and export a single air-gapped tar.gz bundle.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="${1:-v0.1.0}"
OUT_DIR="${DEPLOY_DIR}/dist-offline"
BUNDLE_NAME="oh-my-csm-offline-${VERSION}.tar.gz"

echo "==> [1/4] Building backend & frontend Docker images..."
docker compose -f "${DEPLOY_DIR}/docker-compose.yml" build

echo "==> [2/4] Pulling postgres:16-alpine..."
docker pull postgres:16-alpine

echo "==> [3/4] Saving Docker images..."
mkdir -p "${OUT_DIR}/oh-my-csm-offline/deploy/scripts"
docker save \
  postgres:16-alpine \
  oh-my-csm-backend:latest \
  oh-my-csm-frontend:latest \
  -o "${OUT_DIR}/oh-my-csm-offline/images.tar"

echo "==> [4/4] Packaging compose files & scripts into ${BUNDLE_NAME}..."
cp "${DEPLOY_DIR}/docker-compose.yml" "${OUT_DIR}/oh-my-csm-offline/deploy/"
cp "${DEPLOY_DIR}/nginx.conf" "${OUT_DIR}/oh-my-csm-offline/deploy/"
cp "${DEPLOY_DIR}/.env.example" "${OUT_DIR}/oh-my-csm-offline/deploy/"
cp "${DEPLOY_DIR}/scripts/"*.sh "${OUT_DIR}/oh-my-csm-offline/deploy/scripts/"
chmod +x "${OUT_DIR}/oh-my-csm-offline/deploy/scripts/"*.sh

tar -czf "${DEPLOY_DIR}/${BUNDLE_NAME}" -C "${OUT_DIR}" oh-my-csm-offline
rm -rf "${OUT_DIR}"

echo "Done! Offline bundle created at: ${DEPLOY_DIR}/${BUNDLE_NAME}"
