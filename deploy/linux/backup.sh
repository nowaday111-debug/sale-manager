#!/usr/bin/env bash
# Sao lưu dữ liệu Sale-manager (SQLite hoặc PostgreSQL) + ảnh tải lên. Giữ 30 bản.
# Cron hằng đêm (sudo crontab -e):   15 2 * * * /opt/sale-manager/deploy/linux/backup.sh >/dev/null 2>&1
set -euo pipefail
APP=/opt/sale-manager
DEST="${BACKUP_DIR:-$APP/data/backups}"
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
URL=$(grep -E '^SALE_DB_URL=' "$APP/backend/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)
URL="${URL:-sqlite:///$APP/data/sale.db}"
if [[ "$URL" == postgresql* ]]; then
  PGURL="${URL/postgresql+psycopg/postgresql}"
  pg_dump --no-owner --format=custom "$PGURL" > "$DEST/sale-$STAMP.dump"
else
  cd "$APP/backend" && ./.venv/bin/python -m app.backup
fi
tar -czf "$DEST/uploads-$STAMP.tar.gz" -C "$APP/data" uploads 2>/dev/null || true
ls -1t "$DEST"/sale-*.dump 2>/dev/null | tail -n +31 | xargs -r rm -f
ls -1t "$DEST"/uploads-*.tar.gz 2>/dev/null | tail -n +31 | xargs -r rm -f
echo "Đã sao lưu vào $DEST"
