#!/usr/bin/env bash
set -euo pipefail

# Restore a PostgreSQL custom-format .dump file produced by backup-db.sh.
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <path-to-backup.dump>"
  exit 1
fi

DUMP_FILE="$1"
if [[ ! -f "${DUMP_FILE}" ]]; then
  echo "Error: backup file not found: ${DUMP_FILE}"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${DEPLOY_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

PG_DB="${POSTGRES_DB:-oh_my_csm}"
PG_USER="${POSTGRES_USER:-csm}"
CONTAINER="${PG_CONTAINER:-oh-my-csm-postgres}"

read -r -p "WARNING: This will overwrite database '${PG_DB}' in '${CONTAINER}'. Type 'YES' to continue: " CONFIRM
if [[ "${CONFIRM}" != "YES" ]]; then
  echo "Aborted."
  exit 1
fi

echo "==> Restoring ${DUMP_FILE} into '${PG_DB}'..."
docker exec -i "${CONTAINER}" pg_restore \
  -U "${PG_USER}" \
  -d "${PG_DB}" \
  --clean \
  --if-exists \
  --no-owner < "${DUMP_FILE}"

echo "Restore completed successfully."
