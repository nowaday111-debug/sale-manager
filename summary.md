# Sale-manager – Tóm tắt dự án (cập nhật 05/11/2026, XONG Phase 1–7: Đơn hàng → BBGN → Đối chiếu công nợ, giao diện mới, chạy Linux/VPS)

> Dán file này vào đầu session mới để tiếp tục. Code nằm ở `D:\chị Hiền nhờ viết app\Sale-manage\sale-manager`
> (`backend/` Python, `frontend/` giao diện, các file `.bat` để chạy trên Windows). Hướng dẫn chi tiết: `backend/README.md`.

## 1. Bài toán
- Đại lý gas: **Cty TNHH Cung Ứng Khí Hóa Lỏng Hà Nội** (MST 0107776927, Gas Petrolimex), bán gas cho doanh nghiệp/bếp ăn.
- Đầu ra chính: **"BẢNG KÊ CHI TIẾT"** công nợ tháng theo khách, 1 workbook/khách, 1 sheet/tháng `T{m}.{yyyy}`.
- Khách 2 cấp: pháp nhân (MST) → nhiều **bếp/điểm giao** (mỗi bếp = 1 khối trên bảng kê).
- Công thức: Thực giao (kg) = Σ(số bình × kg/bình) − gas dư; Thành tiền = Thực giao × đơn giá/kg (đã gồm VAT 8%).
  Một số tháng cũ tính giá theo bình: `=E19/45*F19`.
- Theo dõi vỏ bình cho mượn (có/không cược), thiết bị công ty đầu tư (hệ thống dẫn gas, bình cứu hỏa, bếp CN, chế/khóa), quà tặng.
- Công nợ lũy kế: nợ tháng trước + phát sinh − các lần thanh toán (FIFO) = còn phải trả + bằng chữ.
- File mẫu: Stargate (9 bếp, PC48), Korea Food – Gầu bò CT4 + 47TT4 (PC45 + Mini), Hyundai CT4 (PC45/PC12/Mini), STG T10.2026.
- Phiếu giấy "HÓA ĐƠN BÁN LẺ" Petrolimex viết tay (seri đỏ 7 số): loại bình, SL, đơn giá viết tắt nghìn, gas dư, ghi chú mượn vỏ/đầu tư.

## 2. Quyết định đã chốt (chị Hiền "oke hết")
1. Cột "Số hóa đơn" trên bảng kê = số HĐĐT GTGT → `vat_invoice_no`; seri phiếu giấy → `slip_no`.
2. Một bố cục bảng kê chung cho mọi khách = bố cục file Stargate (cột A làm lề, bảng bắt đầu **cột B**).
3. Mỗi tháng thêm sheet mới vào workbook sẵn có của khách (`partners.workbook_path`), sao lưu file trước khi ghi, ẩn sheet tháng cũ.
4. Giá mặc định theo kg; hỗ trợ theo bình. 5. Quản lý số lượng vỏ/thiết bị thật theo bếp.
6. Web app nội bộ (FastAPI + SQLite). Giao diện React 18 + htm **không cần build/Node** (thư viện để sẵn trong `frontend/vendor`).
7. AI đọc ảnh: dùng hãng rẻ, **mặc định DeepSeek V4.1 Flash** (`deepseek-flash`, API OpenAI-compatible).
   Có sẵn Gemini 3.1 Flash-Lite, Qwen-VL-OCR, GPT-6 Luna, Claude. Codex KHÔNG dùng. Không dùng gói miễn phí Gemini.

## 3. Đã làm
**Bước 1** – Phân tích mẫu + thiết kế (Claude Doc: https://claude.ai/artifact/Ah8gtLg5ryTsG1mYHc3SVb).

**Bước 2 – Core backend** (`backend/app/`): db (Decimal chính xác trên SQLite), audit log, models đầy đủ; services masterdata,
sales (phiếu/trả hàng/nháp→duyệt/sửa=đảo+ghi lại/hủy), purchases (BQGQ), inventory (thẻ kho, kiểm kê, NXT), assets, cashbook
(FIFO + chỉ định + hoàn tiền), debts, statements (`build_statement`, khóa kỳ), reports; `so_thanh_chu` khớp 58/58 số Excel.
Demo Stargate T10/2026: phát sinh 1.195.917.000; còn phải trả **1.829.047.800**.

**Bước 3 – Nhập liệu & AI**: nhập lịch sử từ workbook bảng kê (63/63 sheet khớp), XML HĐĐT TT78, OCR phiếu viết tay
(preprocess → prompt → providers → normalize → đề xuất → duyệt → apply), benchmark 18 ảnh thật.

**Bước 4 – Xuất Excel đúng mẫu** (`app/exporters/bangke_excel.py`, xong 04/11/2026)
- `build_workbook_bytes()` (file riêng 1 sheet) và `export_to_workbook()` (thêm sheet vào workbook khách; tự sao lưu
  `<tên>.bak-YYYYmmdd-HHMMSS.xlsx`; sheet trùng tên → lỗi trừ khi `replace=True`; ẩn sheet `T*.yyyy` cũ; báo lỗi khi file đang mở).
- Bố cục: B Ngày | C Số HĐ | D.. 1 cột/loại bình có trong tháng | Gas dư | Thực giao | Đơn giá | Thành tiền | Ghi chú | Bếp (gộp dọc).
  Khối bếp: dòng lịch sử đầu tư → phiếu → dòng ghi chú vỏ/đầu tư (gộp ô) → 1 dòng trống → Tổng (=SUM). Tổng cộng, nợ các tháng
  trước, Tổng công nợ (=SUM), các lần thanh toán + "TT xong/TT 1 phần", Tổng đã TT, Còn phải trả (=công thức), Bằng chữ,
  lời nhắc đối chiếu 04 ngày, chữ ký. Times New Roman 12, viền, tô màu như mẫu, A4 đứng fit 1 trang ngang, lặp tiêu đề 16:17.
- Công thức như file gốc (`=D19*48-E19`, `=F19*G19`); trường hợp đặc biệt ghi giá trị. "Bằng chữ" = khối công thức đọc số
  của file gốc chép vào các dòng ẩn dưới vùng in → sửa số trong Excel chữ tự đổi.
- Kiểm chứng: 3 file thật (62 tháng) nhập → xuất lại → LibreOffice tính lại: **62/62 khớp tổng tiền, còn phải trả, bằng chữ**.
- Đơn giá ghi tiêu đề "(đồng/kg)" khi cả tháng tính theo kg (file gốc ghi nhầm "đồng/bình"); tháng có giá theo bình ghi "(đồng/bình)".
- API `GET /api/statements/export/{partner_id}/{YYYY-MM}`, `POST /api/statements/export-to-workbook`;
  CLI `python -m scripts.export_bangke --partner STG --period 2026-10 [--file ..] [--rieng] [--ghi-de] [--khong-an] | --demo`.

**Bước 5 – Giao diện + đóng gói** (xong 04/11/2026)
- `frontend/` (index.html, app.css, lib.js, app.js, vendor/ React 18.3.1 + htm 3.1.1), FastAPI mount ở `/` (API vẫn ở `/api`, `/docs`).
- 10 màn hình: Tổng quan · Lập phiếu (F2 mới, F4 thêm dòng, F8 nháp, F9 ghi sổ; tự lấy giá theo bảng giá khách; vỏ/đầu tư/quà;
  trả ngay) · Danh sách phiếu (sửa/duyệt/hủy có lý do) · Đọc phiếu bằng AI (ảnh cạnh đề xuất, cảnh báo, nhớ tên viết tay làm
  alias, ghi sổ/từ chối) · Khách hàng (thêm/sửa, tổng quan 12 tháng, nợ theo tháng, theo bếp, vỏ mượn, bếp+alias, bảng giá) ·
  Bảng kê (xem trước, tải Excel, thêm vào file khách, lưu/gửi/xác nhận khóa sổ/mở khóa) · Thu–Chi (+ sổ quỹ) · Kho (NXT,
  kiểm kê, tồn đầu) · Nhập dữ liệu (Excel lịch sử, XML + chọn mã cho dòng lạ) · Cài đặt (thông tin công ty, sao lưu, khóa AI).
- `slip_dict` trả thêm `assets` của phiếu (để sửa phiếu không mất vỏ mượn).
- `app/backup.py`: tự sao lưu `data/sale.db` 1 lần/ngày khi khởi động → `data/backups/` (giữ 30 bản); `POST /api/backup`.
- Windows: `cai-dat.bat` (venv + pip + test, tạo `.env` từ `.env.example`), `chay.bat` (cổng 8000, mở cho cả mạng LAN,
  tự mở trình duyệt), `sao-luu.bat`, `nhap-lich-su.bat` (kéo-thả file Excel). Thêm `backend/.env.example`.
- Đã thử bằng trình duyệt tự động: lập phiếu (giá tự lấy 47.500, 10 PC48 − 12,5 kg dư = 22.206.250 ✓, chặn trùng seri),
  duyệt OCR (giả lập) → ghi sổ, tải Excel bảng kê; không lỗi JS.
- **55 test pytest đều qua** (thêm `tests/test_export_excel.py`: bố cục, công thức, ghi vào workbook, API, sao lưu, phục vụ giao diện).

**Phase 1 – Schema & migration** (05/11/2026): `partners` + email, payment_term_days, current_debt, current_payable,
representative(+title); `cash_vouchers.is_locked`; bảng mới `orders`, `order_items` (delivered_qty), `delivery_notes`,
`delivery_note_items` (ordered_qty_snapshot / dispatched / accepted / rejected, unit_price kế thừa, accepted_line_total),
`debt_ledger_entries`, `debt_reconciliations`, `debt_reconciliation_lines` (order_code + document_code), đủ index
(partner_id, transaction_date) / order_id / delivery_note_id / status. Khung migration đánh số `app/migrations/`
(tự chạy khi khởi động, idempotent, SQLite + PostgreSQL). Khóa chính giữ kiểu số nguyên như các bảng cũ.

**Phase 2 – Services & API**: `orders.py` (DRAFT→APPROVED/PENDING_APPROVAL theo hạn mức + nợ quá hạn FIFO; duyệt theo
vai trò MANAGER/CHIEF_ACCOUNTANT; khóa giá/CK/thuế/SL sau duyệt; hủy; đóng phần dư), `delivery_notes.py` (tạo từ đơn điền
SL còn lại, giữ chỗ SL đang giao, chặn vượt; PREPARING→IN_TRANSIT→CONFIRMED; bắt buộc ảnh ký, accepted+rejected=dispatched,
lý do chênh lệch + tình trạng từng dòng; 1 transaction: cộng delivered_qty, trạng thái đơn, bút toán DELIVERY_INCREASE,
current_debt khóa dòng, thẻ kho), `ledger.py` (thanh toán sinh phiếu thu/chi, trả hàng, điều chỉnh, hủy phiếu = bút toán
đảo, chặn ghi vào kỳ RECONCILED, cảnh báo kỳ đang gửi), `reconciliations.py` (đầu kỳ từ BBĐCCN chốt gần nhất, chênh lệch
từng dòng → DISPUTED, làm mới, chốt RECONCILED khóa BBGN/bút toán/phiếu thu/đơn). API `app/routers/orders_flow.py`.

**Phase 3 – Giao diện** (`frontend/flow.js`): Đơn hàng (form tính tiền + badge hạn mức trực tiếp, chi tiết có thanh tiến
độ, badge xanh/vàng/đỏ, nút Tạo BBGN chỉ khi APPROVED/PARTIALLY_DELIVERED → modal điền sẵn SL còn lại, tab Lịch sử Giao
nhận & Công nợ), BBGN (bảng 4 cột SL tô vàng/đỏ, kéo-thả / chụp ảnh điện thoại, nút "Kế toán Xác nhận & Ghi nhận Công nợ"),
Đối chiếu (lọc Tháng này/Tháng trước/Quý/Tùy chỉnh + chỉ đối tác có phát sinh, 4 thẻ KPI, bấm Mã ĐĐH/BBGN mở ngăn kéo xem
nhanh kèm ảnh ký, Báo chênh lệch, Khóa sổ). Tổng quan có ô "việc cần xử lý".

**Phase 4 – In & Excel**: `readVietnameseCurrency()` (lib.js, 26 ca test node); `print.html` 3 mẫu A4 (@media print / Lưu PDF,
QR mã chứng từ qua `segno`, BBGN "Căn cứ ĐĐH số…", 4 ô ký, tùy chọn ẩn giá cho kho; BBĐCCN Quốc hiệu, Bên A/B, bảng 4 chỉ
tiêu, bảng kê + phụ lục giao nhận); Excel BBĐCCN `app/exporters/reconciliation_excel.py` (Times New Roman, viền, công
thức SUM/số dư, sheet phụ lục) – LibreOffice tính lại khớp.

**Phase 5 – Thiết kế lại giao diện** theo ui-ux-pro-max (Minimalism & Swiss, mật độ dày): font Fira Sans để sẵn offline
(`frontend/vendor/fonts`), số tabular, bảng màu slate + xanh, chế độ tối, biểu tượng SVG Lucide, menu nhóm, focus rõ,
tôn trọng reduced-motion, chạy được trên điện thoại.

**Phase 6 – Linux/VPS**: `docs/DEPLOY-LINUX.md`, `deploy/linux/install.sh` (systemd + nginx + certbot + cron sao lưu,
tùy chọn PostgreSQL), `Dockerfile`, `docker-compose.yml`, `chay.sh`, `requirements-postgres.txt`; đăng nhập HTTP Basic
`app/auth.py` (`SALE_AUTH_USERS`, mật khẩu băm PBKDF2, vai trò theo tài khoản). 68 test qua trên SQLite **và** PostgreSQL 16.

## 4. Cách chạy
Người dùng (Windows): cài Python 3.11+ → nháy đúp `cai-dat.bat` (1 lần) → `chay.bat` → http://127.0.0.1:8000
```
cd sale-manager/backend && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
python -m pytest -q                                     # 70 test (2 test OCR cần ảnh benchmark)
python -m scripts.seed_demo                             # DB demo Stargate T10
python -m scripts.seed_order_demo                       # DB demo quy trình đơn hàng (data/demo-orders.db)
python -m scripts.export_bangke --demo                  # xuất bảng kê demo -> data/exports/
python -m scripts.import_excel "<file1.xlsx>" "<file2.xlsx>"
python -m scripts.ocr_benchmark --providers deepseek
uvicorn app.main:app --reload                           # giao diện http://127.0.0.1:8000, API /docs
```

Linux/VPS: `./chay.sh` hoặc `sudo bash deploy/linux/install.sh ten-mien.vn` (xem docs/DEPLOY-LINUX.md).

## 5. Việc còn mở (cần chị Hiền)
- [ ] Khai báo hạn mức nợ + số ngày được nợ cho từng khách (Khách hàng → Thông tin → Sửa). Để trống = không giới hạn.
- [ ] Chọn người dùng/vai trò trên mỗi máy (Cài đặt) – hoặc bật `SALE_AUTH_USERS` khi lên VPS.
- [ ] Quyết định: lên VPS dùng SQLite (đơn giản) hay PostgreSQL; tên miền để bật HTTPS.
- [ ] Điền khóa DeepSeek vào `backend/.env` rồi chạy `python -m scripts.ocr_benchmark --providers deepseek` → chọn hãng chính.
      Cần API key thật (không dùng tài khoản ChatGPT/Codex email+mật khẩu).
- [ ] Xác nhận giả định công nợ Hyundai & Korea Food (đã trả đủ trước T10/2026?) và tên các "Bếp chưa rõ tên" năm 2025.
- [ ] Dùng thử thật: nhập 3 file lịch sử, điền đường dẫn workbook từng khách, xuất bảng kê T11/2026 so với file chị tự làm.
- [ ] Kiểm lại vài phiếu trong `benchmark/ground_truth.json`.

## 6. Gợi ý bước tiếp theo (nếu chị muốn)
- Màn hình quản lý tài khoản trong phần mềm (hiện khai báo trong .env); xuất PDF phía máy chủ nếu cần gửi tự động.
- Gộp công nợ sổ cái đơn hàng vào bảng 'Công nợ phải thu theo khách' ở Tổng quan (hiện bảng này chỉ tính bảng kê gas).
- In phiếu giao hàng / phiếu thu từ phần mềm; gửi bảng kê qua email/Zalo.
- Đóng gói thành 1 file .exe (PyInstaller) nếu không muốn cài Python.
- Lưu ý kỹ thuật: openpyxl có thể làm mất hình/biểu đồ chèn trong workbook cũ khi thêm sheet – bản `.bak` luôn còn nguyên.
- Model id OpenAI/Anthropic trong `ocr/providers.py` là mặc định có thể sửa bằng `OCR_MODEL`.
