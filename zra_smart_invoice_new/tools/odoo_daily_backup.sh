#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Odoo nightly database backup — internal checklist #21
# "Automatically download a backup copy of each client database daily at night."
#
# What it does:
#   • pg_dump (custom format) of every database listed in DATABASES
#   • optionally tars each database's filestore (attachments) alongside
#   • writes timestamped files to BACKUP_DIR and deletes backups older than
#     RETENTION_DAYS
#
# Setup:
#   1. Edit the variables below (or export them in the environment / cron).
#   2. Make executable:  chmod +x odoo_daily_backup.sh
#   3. Schedule nightly at 02:00 with cron:
#        crontab -e
#        0 2 * * * BACKUP_DIR=/var/backups/odoo DATABASES="client_db_1 client_db_2" /opt/odoo/custom-addons/zra_smart_invoice_new/tools/odoo_daily_backup.sh >> /var/log/odoo_backup.log 2>&1
#
# Restore:  pg_restore -d <new_db> <dump_file>
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
umask 077   # dumps contain full client data — create all files 600/700

BACKUP_DIR="${BACKUP_DIR:-/var/backups/odoo}"
DATABASES="${DATABASES:-}"                 # REQUIRED: space-separated, e.g. "client1 client2"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-odoo}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
FILESTORE_BASE="${FILESTORE_BASE:-$HOME/.local/share/Odoo/filestore}"   # set BACKUP_FILESTORE=1 to include
BACKUP_FILESTORE="${BACKUP_FILESTORE:-0}"

if [[ -z "$DATABASES" ]]; then
    echo "[$(date '+%F %T')] ERROR: set DATABASES (space-separated list of client DBs)" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date '+%Y%m%d_%H%M%S')"

for db in $DATABASES; do
    dump="$BACKUP_DIR/${db}_${STAMP}.dump"
    echo "[$(date '+%F %T')] Backing up database '$db' -> $dump"
    pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -Fc -f "$dump" "$db"

    if [[ "$BACKUP_FILESTORE" == "1" && -d "$FILESTORE_BASE/$db" ]]; then
        fs_archive="$BACKUP_DIR/${db}_filestore_${STAMP}.tar.gz"
        echo "[$(date '+%F %T')] Backing up filestore '$db' -> $fs_archive"
        tar -czf "$fs_archive" -C "$FILESTORE_BASE" "$db"
    fi
done

# Retention: remove backups older than RETENTION_DAYS
find "$BACKUP_DIR" -name '*.dump' -o -name '*_filestore_*.tar.gz' | while read -r f; do
    if [[ $(find "$f" -mtime +"$RETENTION_DAYS") ]]; then
        echo "[$(date '+%F %T')] Purging old backup: $f"
        rm -f "$f"
    fi
done

echo "[$(date '+%F %T')] Backup run complete."


