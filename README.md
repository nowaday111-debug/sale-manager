# Sale-manager

Phần mềm quản lý bán hàng nội bộ cho doanh nghiệp nhỏ (đại lý gas): lập phiếu, bảng kê công nợ tháng xuất Excel đúng mẫu,
AI đọc phiếu viết tay, kho, thu–chi, và quy trình **Đơn đặt hàng → Biên bản giao nhận → Đối chiếu công nợ**.

- Backend: Python 3.11+, FastAPI, SQLAlchemy (SQLite hoặc PostgreSQL), openpyxl
- Giao diện: React 18 + htm (không cần build), chạy trên trình duyệt / điện thoại

## Chạy nhanh
- **Windows:** nháy đúp `cai-dat.bat` (1 lần) rồi `chay.bat` → http://127.0.0.1:8000
- **Linux / macOS:** `./chay.sh`
- **VPS Linux:** xem [`docs/DEPLOY-LINUX.md`](docs/DEPLOY-LINUX.md) (systemd + nginx + HTTPS, hoặc Docker)

## Tài liệu
- Hướng dẫn chi tiết & kiến trúc: [`backend/README.md`](backend/README.md)
- Tiến độ dự án: [`summary.md`](summary.md)

## Kiểm thử
```
cd backend && python -m pytest -q
node frontend/tests/read_currency.test.mjs
```
