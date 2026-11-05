# Sale-manager – Phần mềm bán gas, kho, công nợ (Bước 1–7)

Phần mềm quản lý bán gas theo bình cho doanh nghiệp: phiếu giao hàng, kho, vỏ bình cho mượn,
thu chi, công nợ theo tháng và **Bảng kê chi tiết** đúng như file Excel đang dùng.

## Hướng dẫn sử dụng (cho người dùng)

Xem **[`../HUONG-DAN-SU-DUNG.md`](../HUONG-DAN-SU-DUNG.md)** – cài đặt, thiết lập lần đầu, quy trình bán gas theo phiếu → bảng kê,
quy trình Đơn hàng → BBGN → Đối chiếu công nợ, thu chi, kho, in/xuất Excel, sao lưu và xử lý lỗi thường gặp.

Tóm tắt: cài Python 3.11+ (tick *Add python.exe to PATH*) → nháy đúp `cai-dat.bat` (1 lần) → `chay.bat` → http://127.0.0.1:8000.

## Chạy cho lập trình viên (Windows / macOS / Linux, Python 3.11+)

```bash
cd sale-manager/backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m pytest -q               # 70 bài kiểm thử (2 test OCR cần ảnh benchmark)
python -m scripts.seed_demo       # tạo data/demo.db + in bảng kê Stargate T10/2026 (khớp 1.829.047.800 đ)
uvicorn app.main:app --reload     # giao diện http://127.0.0.1:8000  ·  API http://127.0.0.1:8000/docs
```

Dùng file dữ liệu khác: đặt biến môi trường `SALE_DB_URL=sqlite:///D:/duong/dan/sale.db`
(hoặc `postgresql+psycopg://...`). Chặn xuất kho âm: `SALE_ALLOW_NEGATIVE_STOCK=0`.

## Cấu trúc

```
app/
  config.py            cấu hình (đường dẫn DB, cho phép tồn âm, kiểu đọc số tiền)
  db.py                engine, DecimalType (lưu Decimal chính xác trên SQLite), transaction()
  models.py            toàn bộ bảng: partners, sites, products, price_lists, delivery_slips/items,
                       asset_movements, inventory_transactions, purchases, stocktakes, vat_invoices,
                       cash_vouchers, payment_allocations, opening_balances, statements, audit_log
  audit.py             tự ghi nhật ký mọi INSERT/UPDATE/DELETE trong cùng transaction
  money.py             hàm Decimal: làm tròn tiền, kg, tách VAT
  services/
    masterdata.py      khách hàng, bếp + tên viết tắt (alias), hàng hóa, bảng giá theo khách & ngày
    sales.py           phiếu giao / hóa đơn bán lẻ, khách trả hàng, nháp OCR -> duyệt, sửa, hủy
    purchases.py       phiếu nhập mua, trả hàng NCC (giá vốn bình quân gia quyền)
    inventory.py       thẻ kho, kiểm kê + cân bằng kho, báo cáo Nhập–Xuất–Tồn
    assets.py          vỏ bình cho mượn, thiết bị đầu tư, quà tặng theo bếp
    cashbook.py        phiếu thu/chi, phân bổ trả nợ theo tháng (FIFO hoặc chỉ định), sổ quỹ
    debts.py           công nợ phải thu/phải trả theo tháng, tổng hợp, chi tiết, hạn mức
    statements.py      dựng Bảng kê tháng (dữ liệu cho bước xuất Excel), chốt sổ / khóa kỳ
    reports.py         dashboard 4 chỉ số, tổng quan từng khách hàng
  routers/             REST API (FastAPI)
  utils/so_thanh_chu.py  đọc số tiền thành chữ – khớp 58/58 số với công thức trong file Excel
  demo/stargate_t10.py   dữ liệu tháng 10/2026 của Stargate để kiểm thử
tests/                 pytest
scripts/seed_demo.py   tạo DB chạy thử
```

## Quy tắc tính (lấy từ file mẫu)

- Thực giao (kg) = Σ(số bình × kg/bình) − gas dư; Thành tiền = Thực giao × đơn giá/kg (giá đã gồm VAT 8%).
  Làm tròn tới đồng một lần ở cuối, giống Excel.
- Giá theo bình cũng hỗ trợ (`price_basis = UNIT`); gas dư khi đó trừ theo giá/bình ÷ kg/bình.
- Mỗi lần lưu phiếu = 1 transaction: phiếu + thẻ kho + vỏ mượn + phiếu thu (nếu trả ngay). Lỗi ở bất kỳ bước nào -> rollback toàn bộ.
- Tiền khách trả được phân bổ vào nợ tháng cũ nhất trước; bảng kê tự ghi "TT xong CN tháng 07/2026", "TT 1 phần CN tháng 09/2026".
- Bảng kê đã CONFIRMED thì khóa kỳ: không thêm/sửa/hủy phiếu, phiếu thu của khách trong tháng đó.

## Giả định đang dùng (đã được chị đồng ý ở Bước 1)

1. Cột "Số hóa đơn" trên bảng kê = số hóa đơn điện tử GTGT (`vat_invoice_no`); số seri đỏ trên phiếu giấy lưu ở `slip_no`.
2. Một bố cục bảng kê chung (bắt đầu cột A như file Stargate) cho mọi khách – áp dụng ở Bước 4.
3. Mỗi tháng thêm sheet "T{tháng}.{năm}" vào workbook sẵn có của khách (`partners.workbook_path`).
4. Giá mặc định theo kg; có hỗ trợ theo bình.
5. Vỏ bình và thiết bị đầu tư được quản lý bằng số lượng thật theo từng bếp.
6. Web app nội bộ; API key Claude cho OCR khai báo ở Bước 3.

## Bước 3 – Nhập dữ liệu

### 1. Nhập lịch sử từ các file Bảng kê Excel cũ

```bash
python -m scripts.import_excel "D:/chị Hiền nhờ viết app/Sale-manage/CN T3_25 _ stargate ....xlsx" \
       "D:/.../CN. Gầu bò CT4 - Mỹ Đình + 47TT4 Mỹ Đình.xlsx" "D:/.../T2. CN. CT4 -  Hyundai CT4 Mỹ Đình.xlsx"
```
(hoặc API `POST /api/import/excel`). Đọc theo nhãn cột nên chịu được file lệch cột, có/không cột Số HĐ, PC45/PC48/PC12/Mini,
giá theo kg hoặc theo bình (=E19/45*F19), dòng vật tư (Lắp bếp, súng mồi, cược vỏ), dòng thu hồi gas dư (tiền âm).
Kết quả chạy thử trên 3 file của chị: **63/63 sheet khớp tổng tiền**; Stargate khớp "Công nợ còn lại" từ T7 đến T11/2026.
Nhập lại cùng file không bị trùng. Lịch sử không trừ kho (khai báo tồn kho thật bằng kiểm kê).

### 2. XML hóa đơn điện tử (TT78)
`POST /api/import/xml` – hóa đơn mua vào -> phiếu nhập kho + công nợ NCC; hóa đơn bán ra -> lưu HĐ GTGT và nối với
phiếu giao có cùng "Số hóa đơn". Mã hàng NCC lạ -> trả về `NEEDS_MAPPING`, gửi lại kèm `mapping` một lần, phần mềm nhớ.

### 3. AI đọc phiếu "Hóa đơn bán lẻ" viết tay
1. Sao chép `.env.example` thành `.env`, điền `DEEPSEEK_API_KEY=...` (mặc định dùng DeepSeek V4.1 Flash `deepseek-flash`).
   Đổi hãng: `OCR_PROVIDER=gemini | qwen | openai | anthropic` + khóa tương ứng.
2. `POST /api/ocr/jobs` (gửi ảnh) -> AI đọc -> phần mềm tự sửa số viết tắt nghìn đồng, nhận giá theo kg/bình, kiểm chéo
   số bình × kg × giá − gas dư = thành tiền, khớp tên bếp viết tay, cảnh báo phiếu trùng -> **đề xuất**, chưa ghi sổ.
3. Người duyệt sửa nếu cần rồi `POST /api/ocr/jobs/{id}/apply` -> tạo phiếu (hoặc chỉ ghi vỏ/thiết bị đầu tư).
4. Chấm điểm các hãng trên 18 ảnh thật: `python -m scripts.ocr_benchmark` (đáp án ở `benchmark/ground_truth.json`).


## Bước 4 – Xuất Bảng kê ra Excel đúng mẫu

`app/exporters/bangke_excel.py` đổ kết quả `statements.build_statement()` vào sheet theo bố cục file Stargate
(cột A làm lề, bảng bắt đầu cột B): Ngày tháng | Số hóa đơn | một cột cho mỗi loại bình có trong tháng (PC48, PC45, PC12,
MINI…) | Gas dư | Thực giao | Đơn giá | Thành tiền | Ghi chú | Tên bếp + vỏ mượn (gộp dọc theo khối).

- Mỗi bếp 1 khối: dòng lịch sử đầu tư (nếu có) → các phiếu → 1 dòng trống để ghi tay → dòng **Tổng** (=SUM).
- Công thức giống file gốc: `=D19*48-E19`, `=F19*G19`, Tổng cộng `=+H21+H30+…`, Tổng công nợ `=SUM`, Còn phải trả `=+H86-H93`.
  Trường hợp không biểu diễn được bằng công thức (giá theo bình có gas dư, có vật tư, có khoản trừ) thì ghi giá trị.
- "Bằng chữ" dùng lại đúng khối công thức đọc số của file gốc (đặt ở các dòng ẩn dưới vùng in) → sửa số trong Excel chữ tự đổi.
- Times New Roman 12, viền mảnh, tô xám dòng Tổng, vàng dòng Tổng công nợ / Đã thanh toán, A4 đứng, vừa 1 trang ngang,
  lặp dòng tiêu đề khi in nhiều trang.
- Đã thử: nhập 3 file thật (62 tháng) → xuất lại từng tháng → LibreOffice tính lại công thức: **62/62 tháng khớp tổng tiền,
  còn phải trả và số tiền bằng chữ**.

Dùng:
- Giao diện: **Bảng kê** → *Tải file Excel* (file riêng 1 sheet) hoặc *Thêm sheet vào file bảng kê của khách*.
- API: `GET /api/statements/export/{partner_id}/{YYYY-MM}` (tải file); `POST /api/statements/export-to-workbook`
  `{"partner_id":1,"period":"2026-10","replace":false,"hide_old_months":true}`.
- Dòng lệnh: `python -m scripts.export_bangke --partner STG --period 2026-10 [--file D:/BangKe/STG.xlsx] [--rieng] [--ghi-de]`;
  `python -m scripts.export_bangke --demo` xuất bảng kê demo Stargate T10/2026.

Khi thêm vào workbook sẵn có: sao lưu thành `<tên>.bak-YYYYmmdd-HHMMSS.xlsx` trước khi ghi; sheet trùng tên thì báo lỗi trừ khi
chọn ghi đè; báo lỗi rõ ràng nếu file đang mở trong Excel. Lưu ý: openpyxl có thể làm mất hình ảnh/biểu đồ chèn trong
workbook cũ – bản sao lưu luôn còn nguyên.

## Bước 5 – Giao diện web

Thư mục `frontend/` (React 18 + htm, **không cần cài Node/build**; thư viện để sẵn trong `frontend/vendor/` nên chạy được
khi mất Internet). FastAPI phục vụ luôn giao diện ở `/`.

| Màn hình | Việc làm |
|---|---|
| Tổng quan | 4 chỉ số tháng, công nợ phải thu theo khách, hàng sắp hết |
| Lập phiếu | chọn khách/bếp, tự lấy đơn giá theo bảng giá, tính kg/thực giao/tiền, vỏ mượn – đầu tư – quà tặng, trả ngay; phím F2/F4/F8/F9 |
| Danh sách phiếu | lọc theo khách/tháng/trạng thái; sửa, duyệt nháp, hủy (bắt buộc lý do) |
| Đọc phiếu bằng AI | tải nhiều ảnh, ảnh phóng to bên trái – đề xuất sửa được bên phải, cảnh báo, nhớ tên viết tay của bếp, ghi sổ/từ chối |
| Khách hàng | danh sách + thêm/sửa; tổng quan 12 tháng (kg, tiền, đã trả, nợ cuối tháng), nợ theo tháng, theo bếp, vỏ đang mượn; bếp & tên gọi khác; bảng giá theo khách |
| Bảng kê | xem trước giống Excel, tải Excel, thêm sheet vào file khách, lưu / gửi / khách xác nhận (khóa sổ) / mở khóa |
| Thu – Chi | phiếu thu/chi (tự trừ nợ tháng cũ nhất), sổ quỹ tiền mặt / ngân hàng |
| Kho | Nhập–Xuất–Tồn, kiểm kê (tự sinh phiếu cân bằng), tồn đầu kỳ |
| Nhập dữ liệu | file Bảng kê Excel cũ (bảng đối chiếu từng sheet), XML hóa đơn điện tử (chọn mã hàng cho dòng lạ) |
| Cài đặt | thông tin công ty in trên bảng kê, sao lưu ngay, trạng thái khóa API các hãng AI |

Đóng gói: `cai-dat.bat` (tạo .venv, cài thư viện, chạy test), `chay.bat` (mở ở cổng 8000 cho cả mạng nội bộ),
`sao-luu.bat`, `nhap-lich-su.bat`. Tự sao lưu `data/sale.db` mỗi ngày (giữ 30 bản) – `app/backup.py`.

## Bước 6 – Quy trình Đơn đặt hàng → Biên bản giao nhận → Đối chiếu công nợ (05/11/2026)

**Nguyên tắc kế toán nội bộ** (skill `internal-accounting-core`): đơn hàng không sinh công nợ; công nợ chỉ ghi tăng khi
BBGN được kế toán **xác nhận** (có ảnh ký nhận), tính theo **SL thực nhận × đơn giá đã duyệt** (sau chiết khấu, gồm thuế);
đơn giá / CK / thuế khóa sau khi duyệt; khóa cứng chứng từ khi biên bản đối chiếu **RECONCILED**.

| Thành phần | File |
|---|---|
| Bảng mới + cột mới (`partners.payment_term_days/current_debt/...`, `cash_vouchers.is_locked`) | `app/models.py` |
| Migration có đánh số (SQLite + PostgreSQL), tự chạy khi khởi động | `app/migrations/` – `python -m app.migrations` |
| Đơn hàng: hạn mức, nợ quá hạn, duyệt theo vai trò, khóa giá, hủy, đóng phần dư | `app/services/orders.py` |
| BBGN: tạo từ đơn (điền SL còn lại, giữ chỗ SL đang giao), xuất kho, ảnh ký, xác nhận 3 chiều | `app/services/delivery_notes.py` |
| Sổ cái công nợ, thanh toán, trả hàng, điều chỉnh, quá hạn FIFO, trạng thái hạn mức | `app/services/ledger.py` |
| Biên bản đối chiếu: tính kỳ, chênh lệch từng dòng, chốt & khóa sổ | `app/services/reconciliations.py` |
| REST API (`/api/orders`, `/api/delivery-notes`, `/api/ledger`, `/api/reconciliations`, `/api/print`) | `app/routers/orders_flow.py` |
| Mẫu in A4 (ĐĐH, BBGN có QR + ẩn giá, BBĐCCN có Quốc hiệu, phụ lục) | `frontend/print.html`, `print.js`, `print.css` |
| Xuất Excel BBĐCCN (Times New Roman, viền, công thức SUM / số dư) | `app/exporters/reconciliation_excel.py` |
| Đọc số tiền bằng chữ `readVietnameseCurrency()` (mốt, tư, lăm, lẻ/linh, tỷ) | `frontend/lib.js` – test: `node frontend/tests/read_currency.test.mjs` |
| Màn hình Đơn hàng / BBGN / Đối chiếu | `frontend/flow.js` |
| Đăng nhập HTTP Basic cho VPS | `app/auth.py` (`SALE_AUTH_USERS`) |

Mã chứng từ: `SO-YYYYMM-XXXX` (bán), `PO-…` (mua), `DN-…` (BBGN), `DR-…` (đối chiếu), `CN-…` (trả hàng), `DC-…` (điều chỉnh).
Dư nợ dùng để kiểm tra hạn mức = sổ cái đơn hàng + nợ bảng kê tháng (phiếu gas cũ). `credit_limit` để trống/0 = không giới hạn.

Dữ liệu chạy thử: `python -m scripts.seed_order_demo` (tạo `data/demo-orders.db`).
Test: `tests/test_order_flow.py` (giao 1 phần, giao thiếu/hàng lỗi, vượt hạn mức, nợ quá hạn, khóa giá, khóa sổ đối chiếu,
hủy phiếu thu, đơn mua, API, mẫu in, Excel – LibreOffice tính lại khớp), `tests/test_auth.py`.

## Bước 7 – Chạy trên Linux / VPS
Xem `docs/DEPLOY-LINUX.md` (systemd + nginx + HTTPS, hoặc Docker; SQLite hoặc PostgreSQL). Chạy nhanh: `./chay.sh`.
