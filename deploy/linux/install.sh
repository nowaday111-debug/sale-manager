#!/usr/bin/env bash
# Cài Sale-manager lên VPS Ubuntu 22.04/24.04 hoặc Debian 12 (chạy bằng root):
#   git clone https://github.com/<tai-khoan>/sale-manager.git /opt/sale-manager
#   sudo bash /opt/sale-manager/deploy/linux/install.sh ten-mien.vn   [--postgres]
# Sau khi chạy: tạo tài khoản đăng nhập (in ra ở cuối), trỏ tên miền rồi chạy certbot để có HTTPS.
set -euo pipefail
DOMAIN="${1:-_}"
USE_PG="${2:-}"
APP=/opt/sale-manager
SVC_USER=salemgr

[ "$(id -u)" = 0 ] || { echo "Chạy bằng sudo/root"; exit 1; }
[ -d "$APP/backend" ] || { echo "Không thấy $APP/backend – clone mã nguồn vào $APP trước"; exit 1; }

echo "== 1. Cài gói hệ thống"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx sqlite3 ca-certificates
[ "$USE_PG" = "--postgres" ] && apt-get install -y postgresql postgresql-client
command -v certbot >/dev/null || apt-get install -y certbot python3-certbot-nginx || true

echo "== 2. Tài khoản chạy dịch vụ & thư mục dữ liệu"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP/data/backups" "$APP/data/uploads" "$APP/data/exports"

echo "== 3. Môi trường Python"
cd "$APP/backend"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
if [ "$USE_PG" = "--postgres" ]; then .venv/bin/pip install -r requirements-postgres.txt; else .venv/bin/pip install -r requirements.txt; fi

echo "== 4. File cấu hình backend/.env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "SALE_DB_URL=sqlite:///$APP/data/sale.db" >> .env
  if [ "$USE_PG" = "--postgres" ]; then
    PGPASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='sale'" | grep -q 1 || \
      sudo -u postgres psql -c "CREATE ROLE sale LOGIN PASSWORD '$PGPASS'"
    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='sale'" | grep -q 1 || \
      sudo -u postgres createdb -O sale -E UTF8 -T template0 sale
    sed -i "s#^SALE_DB_URL=.*#SALE_DB_URL=postgresql+psycopg://sale:$PGPASS@127.0.0.1:5432/sale#" .env
  fi
  ADMIN_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(10))")
  HASH=$(.venv/bin/python -m app.auth hash "$ADMIN_PW")
  echo "SALE_AUTH_USERS=admin:$HASH:ADMIN" >> .env
  echo "$ADMIN_PW" > "$APP/data/.admin-password"; chmod 600 "$APP/data/.admin-password"
fi
chmod 600 .env
chown -R "$SVC_USER:$SVC_USER" "$APP/data" "$APP/backend"

echo "== 5. Khởi tạo / nâng cấp cơ sở dữ liệu (migration)"
sudo -u "$SVC_USER" .venv/bin/python -m app.migrations

echo "== 6. Dịch vụ systemd"
cp "$APP/deploy/linux/sale-manager.service" /etc/systemd/system/sale-manager.service
systemctl daemon-reload
systemctl enable --now sale-manager
sleep 2
systemctl --no-pager --lines=5 status sale-manager || true

echo "== 7. Nginx"
sed "s/server_name ten-mien.vn;/server_name $DOMAIN;/" "$APP/deploy/linux/nginx-sale-manager.conf" > /etc/nginx/sites-available/sale-manager
ln -sf /etc/nginx/sites-available/sale-manager /etc/nginx/sites-enabled/sale-manager
[ "$DOMAIN" != "_" ] && rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "== 8. Sao lưu hằng đêm"
chmod +x "$APP/deploy/linux/backup.sh"
( crontab -l 2>/dev/null | grep -v sale-manager/deploy/linux/backup.sh; echo "15 2 * * * $APP/deploy/linux/backup.sh >/dev/null 2>&1" ) | crontab -

command -v ufw >/dev/null && ufw allow 'Nginx Full' >/dev/null 2>&1 || true
echo
echo "XONG. Mở http://$DOMAIN"
[ -f "$APP/data/.admin-password" ] && echo "Đăng nhập: admin / $(cat "$APP/data/.admin-password")  (đổi mật khẩu: sửa SALE_AUTH_USERS trong $APP/backend/.env)"
[ "$DOMAIN" != "_" ] && echo "Bật HTTPS:  certbot --nginx -d $DOMAIN"
