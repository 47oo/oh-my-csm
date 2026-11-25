#!/usr/bin/env bash
set -euo pipefail

# Daily PostgreSQL custom-format backup script (crontab-ready).
# Example cron (daily at 02:30):
#   30 2 * * * /opt/oh-my-csm/deploy/scripts/backup-db.sh /opt/oh-my-csm/backups 30

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DIR="${1:-${DEPLOY_DIR}/backups}"
RETENTION_DAYS="${2:-30}"

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

mkdir -p "${BACKUP_DIR}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${BACKUP_DIR}/${PG_DB}_${STAMP}.dump"

echo "==> Dumping PostgreSQL database '${PG_DB}' from container '${CONTAINER}'..."
docker exec "${CONTAINER}" pg_dump -U "${PG_USER}" -d "${PG_DB}" -Fc > "${OUT_FILE}"
chmod 600 "${OUT_FILE}"

echo "==> Pruning backups older than ${RETENTION_DAYS} days in ${BACKUP_DIR}..."
find "${BACKUP_DIR}" -type f -name "${PG_DB}_*.dump" -mtime "+${RETENTION_DAYS}" -delete

echo "Backup completed: ${OUT_FILE}"
echo "Reminder: Keep deploy/.env (CSM_SECRET_KEYS) backed up in a separate secure location."
