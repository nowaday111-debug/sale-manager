# Triển khai Sale-manager lên VPS Linux

Phần mềm chạy được trên cả **Windows** (các file `.bat`, như cũ) và **Linux** (Ubuntu 22.04/24.04, Debian 12).
Trên VPS có 2 cách: **cài trực tiếp** (khuyên dùng – đơn giản, dễ sao lưu) hoặc **Docker**.

> ⚠️ Khi phần mềm ra Internet, **bắt buộc bật đăng nhập** (`SALE_AUTH_USERS`) và **HTTPS**. Script cài đặt tự tạo tài
> khoản `admin` với mật khẩu ngẫu nhiên.

## Cách 1 – Cài trực tiếp (systemd + nginx)

```bash
# trên VPS, bằng tài khoản có sudo
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/<tai-khoan>/sale-manager.git /opt/sale-manager
sudo bash /opt/sale-manager/deploy/linux/install.sh ten-mien-cua-chi.vn          # SQLite (khuyên dùng)
# hoặc: sudo bash /opt/sale-manager/deploy/linux/install.sh ten-mien-cua-chi.vn --postgres
sudo certbot --nginx -d ten-mien-cua-chi.vn                                       # bật HTTPS miễn phí
```

Script sẽ: cài Python/nginx, tạo người dùng hệ thống `salemgr`, môi trường `.venv`, file `backend/.env`,
chạy migration, tạo dịch vụ `sale-manager` (tự khởi động lại khi lỗi / khởi động máy), cấu hình nginx
(cho phép tải ảnh 30 MB, chờ AI đọc ảnh 180 giây) và lịch sao lưu 2h15 sáng hằng ngày.

| Việc | Lệnh |
|---|---|
| Xem trạng thái / log | `systemctl status sale-manager` · `journalctl -u sale-manager -f` |
| Khởi động lại sau khi sửa `.env` | `sudo systemctl restart sale-manager` |
| Cập nhật phiên bản mới | `cd /opt/sale-manager && sudo git pull && sudo -u salemgr backend/.venv/bin/pip install -r backend/requirements.txt && sudo -u salemgr backend/.venv/bin/python -m app.migrations && sudo systemctl restart sale-manager` |
| Sao lưu ngay | `sudo /opt/sale-manager/deploy/linux/backup.sh` (file trong `data/backups`) |

### Tài khoản & vai trò
Trong `/opt/sale-manager/backend/.env`:
```
SALE_AUTH_USERS=admin:pbkdf2~...:ADMIN,hien:pbkdf2~...:CHIEF_ACCOUNTANT,lan:pbkdf2~...:STAFF
```
Tạo mật khẩu băm: `cd /opt/sale-manager/backend && .venv/bin/python -m app.auth hash` rồi dán vào, khởi động lại dịch vụ.
Vai trò: `ADMIN`, `MANAGER`, `CHIEF_ACCOUNTANT` (được duyệt đơn vượt hạn mức), `ACCOUNTANT` (xác nhận BBGN, đối chiếu,
hủy/đóng đơn), `STAFF` (lập đơn, BBGN). Khi đăng nhập, vai trò lấy theo tài khoản – trình duyệt không tự nâng quyền được.

## Cách 2 – Docker

```bash
git clone https://github.com/<tai-khoan>/sale-manager.git && cd sale-manager
cp backend/.env.example backend/.env       # điền SALE_AUTH_USERS, khóa AI...
docker compose up -d --build               # http://127.0.0.1:8000 – đặt nginx + HTTPS phía trước
```
Dữ liệu nằm trong volume `sale-data` (`/data` trong container). Muốn PostgreSQL: bỏ chú thích khối `db` trong
`docker-compose.yml`.

## Chuyển dữ liệu từ máy Windows lên VPS
1. Trên máy Windows: chạy `sao-luu.bat` (hoặc tắt phần mềm) rồi chép `data\sale.db` và thư mục `data\uploads`.
2. Trên VPS: `sudo systemctl stop sale-manager`, chép vào `/opt/sale-manager/data/`,
   `sudo chown -R salemgr:salemgr /opt/sale-manager/data`, `sudo systemctl start sale-manager`
   (migration tự chạy khi khởi động, nâng cấp cấu trúc bảng mà không mất dữ liệu).

## Ghi chú kỹ thuật
* Cơ sở dữ liệu: SQLite (mặc định, đủ cho doanh nghiệp nhỏ) hoặc PostgreSQL 14+ (`requirements-postgres.txt`).
  Toàn bộ 68 test tự động chạy qua cả hai: `SALE_TEST_DB_URL=postgresql+psycopg://... pytest`.
* Khóa dòng `SELECT ... FOR UPDATE` khi ghi sổ công nợ hoạt động thật trên PostgreSQL; SQLite tự tuần tự hóa ghi.
* Tính năng "thêm sheet vào file Excel bảng kê của khách" dùng đường dẫn file trên máy chủ – trên VPS hãy dùng nút
  **Tải Excel** thay vì đường dẫn `D:\...`.
