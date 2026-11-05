# HƯỚNG DẪN SỬ DỤNG SALE-MANAGER

*Phần mềm bán gas, kho, thu – chi và công nợ cho Cty TNHH Cung Ứng Khí Hóa Lỏng Hà Nội · cập nhật 05/11/2026*

Tài liệu này dành cho người dùng hằng ngày (kế toán, nhân viên bán hàng). Phần kỹ thuật cho lập trình viên nằm ở
`backend/README.md`, cách đưa lên máy chủ Internet ở `docs/DEPLOY-LINUX.md`.

---

## Mục lục

1. [Phần mềm làm được gì](#1-phần-mềm-làm-được-gì)
2. [Cài đặt và mở phần mềm](#2-cài-đặt-và-mở-phần-mềm)
3. [Làm quen màn hình](#3-làm-quen-màn-hình)
4. [Thiết lập lần đầu](#4-thiết-lập-lần-đầu)
5. [Quy trình A – Bán gas theo phiếu → Bảng kê tháng](#5-quy-trình-a--bán-gas-theo-phiếu--bảng-kê-tháng)
6. [Quy trình B – Đơn hàng → Biên bản giao nhận → Đối chiếu công nợ](#6-quy-trình-b--đơn-hàng--biên-bản-giao-nhận--đối-chiếu-công-nợ)
7. [Thu – Chi và sổ quỹ](#7-thu--chi-và-sổ-quỹ)
8. [Khách hàng – xem tổng quan từng khách](#8-khách-hàng--xem-tổng-quan-từng-khách)
9. [Kho](#9-kho)
10. [Nhập dữ liệu (Excel cũ, XML hóa đơn điện tử)](#10-nhập-dữ-liệu)
11. [In và xuất Excel](#11-in-và-xuất-excel)
12. [Sao lưu dữ liệu](#12-sao-lưu-dữ-liệu)
13. [Dùng qua Internet (VPS)](#13-dùng-qua-internet-vps)
14. [Câu hỏi thường gặp & xử lý lỗi](#14-câu-hỏi-thường-gặp--xử-lý-lỗi)
15. [Bảng tra nhanh](#15-bảng-tra-nhanh)

---

## 1. Phần mềm làm được gì

Phần mềm có **hai cách bán hàng**, dùng song song được:

| | Quy trình A – Bán gas theo phiếu | Quy trình B – Đơn hàng có biên bản |
|---|---|---|
| Dùng khi | Giao gas hằng ngày cho bếp ăn, có phiếu "Hóa đơn bán lẻ" viết tay | Khách đặt hàng trước, giao nhiều đợt, cần biên bản ký nhận |
| Chứng từ | Phiếu giao hàng (nhập tay hoặc AI đọc ảnh) | Đơn đặt hàng (SO) → Biên bản giao nhận (BBGN) |
| Cuối tháng | **Bảng kê chi tiết** công nợ, xuất Excel đúng mẫu đang dùng | **Biên bản đối chiếu công nợ** (BBĐCCN), in / xuất Excel |
| Công nợ tăng khi | Ghi sổ phiếu | Kế toán xác nhận BBGN (theo số lượng **thực nhận**) |

Ngoài ra: theo dõi vỏ bình cho mượn và thiết bị đầu tư theo từng bếp, phiếu thu/chi, sổ quỹ, kho Nhập – Xuất – Tồn,
kiểm kê, nhập hóa đơn điện tử XML, và trang tổng quan cho từng khách hàng.

**Cách tính trên bảng kê** (giống file Excel của chị):
- Thực giao (kg) = Σ (số bình × kg/bình) − gas dư
- Thành tiền = Thực giao × đơn giá/kg (đơn giá đã gồm VAT 8%). Một số khách tính theo bình cũng được hỗ trợ.
- Còn phải trả = Nợ các tháng trước + Phát sinh tháng − Đã thanh toán. Tiền khách trả tự trừ vào **tháng nợ cũ nhất trước**.

---

## 2. Cài đặt và mở phần mềm

### Cài lần đầu (Windows, làm 1 lần)

1. Tải **Python 3.11 trở lên** tại python.org. Khi cài, **tick ô "Add python.exe to PATH"**.
2. Mở thư mục `sale-manager`, nháy đúp **`cai-dat.bat`**. Cửa sổ đen sẽ tự cài thư viện và chạy kiểm tra
   (cần Internet, mất vài phút). Thấy dòng *"Cai dat xong"* là được.
3. Nếu muốn dùng AI đọc ảnh phiếu: mở file `backend\.env` bằng Notepad, điền khóa vào dòng `DEEPSEEK_API_KEY=` rồi lưu.

### Mở phần mềm hằng ngày

- Nháy đúp **`chay.bat`** → trình duyệt tự mở địa chỉ **http://127.0.0.1:8000**.
- **Giữ nguyên cửa sổ đen** trong lúc làm việc. Đóng cửa sổ đó = tắt phần mềm.
- Máy khác cùng mạng (máy tính, điện thoại) mở: `http://<địa chỉ IP máy chạy phần mềm>:8000`
  (xem IP bằng lệnh `ipconfig`, dòng *IPv4 Address*, ví dụ `http://192.168.1.20:8000`).

> Chỉ chạy `chay.bat` trên **một máy** (máy chủ). Các máy khác chỉ mở trình duyệt vào địa chỉ trên, không chạy thêm.

---

## 3. Làm quen màn hình

Menu bên trái chia thành 4 nhóm:

| Nhóm | Mục |
|---|---|
| **Chung** | Tổng quan |
| **Đơn hàng → Công nợ** | Đơn đặt hàng · Biên bản giao nhận · Đối chiếu công nợ |
| **Bán gas theo phiếu** | Lập phiếu gas (F2) · Danh sách phiếu · Đọc phiếu bằng AI · Bảng kê tháng |
| **Danh mục & sổ sách** | Khách hàng & NCC · Thu – Chi · Kho · Nhập dữ liệu · Cài đặt |

- **Tổng quan**: 4 chỉ số trong tháng, công nợ phải thu theo khách, hàng sắp hết, và ô **Việc cần xử lý**
  (đơn chờ duyệt, BBGN đang giao chưa xác nhận, biên bản đối chiếu chưa chốt / có chênh lệch).
- Góc dưới menu: tên người dùng đang làm việc và nút đổi **chế độ sáng / tối**.
- Dùng được trên điện thoại (menu thu gọn).

---

## 4. Thiết lập lần đầu

Làm theo thứ tự sau:

### 4.1 Cài đặt
- **Thông tin công ty**: tên, MST, địa chỉ… – in lên bảng kê và biên bản. Bấm **Lưu**.
- **Người dùng trên máy này**: gõ tên và chọn vai trò (xem [bảng vai trò](#vai-trò-người-dùng)) → **Lưu** → tải lại trang.
  Chỉ *Quản lý / Kế toán trưởng / Quản trị* được duyệt đơn vượt hạn mức hoặc có nợ quá hạn.
- Xem trạng thái khóa AI (đã điền khóa nào).

### 4.2 Nhập lịch sử từ các file Bảng kê Excel cũ
Vào **Nhập dữ liệu → File Bảng kê Excel**, chọn từng file của khách (Stargate, Korea Food, Hyundai…).
Phần mềm hiện bảng đối chiếu từng sheet: tổng tiền trong file và tổng phần mềm tính ra phải khớp.
Cách khác: kéo-thả nhiều file Excel vào biểu tượng **`nhap-lich-su.bat`**.
Nhập lại cùng một file không bị trùng dữ liệu.

### 4.3 Khách hàng & NCC
Mở từng khách → tab **Thông tin** → **Sửa**, điền:
- Mã, tên, MST, địa chỉ, SĐT, email, người đại diện + chức vụ (in trên biên bản đối chiếu).
- **Hạn mức công nợ** và **Số ngày được nợ** – để trống hoặc 0 = không giới hạn.
- **Đường dẫn file bảng kê của khách** (ví dụ `D:\BangKe\STG.xlsx`) – để cuối tháng phần mềm thêm sheet mới vào đúng file.

Tab **Bếp & tên gọi khác**: thêm các bếp/điểm giao; thêm tên viết tắt hay viết tay (alias) để AI nhận đúng bếp.
Tab **Bảng giá**: nhập đơn giá theo mặt hàng và ngày áp dụng. Khi lập phiếu, giá tự lấy theo bảng này.

### 4.4 Kho – tồn đầu kỳ
Vào **Kho → Tồn đầu kỳ**, nhập số bình đang có. (Dữ liệu lịch sử nhập từ Excel **không** trừ kho.)

---

## 5. Quy trình A – Bán gas theo phiếu → Bảng kê tháng

### 5.1 Lập phiếu bằng tay – *Lập phiếu gas (F2)*
1. Chọn **khách** và **bếp**, ngày, số seri phiếu giấy, số hóa đơn GTGT (nếu có).
2. Thêm dòng hàng (**F4**): loại bình, số bình. Đơn giá tự điền theo bảng giá; nhập **gas dư** (kg) nếu có.
   Phần mềm tự tính kg, thực giao, thành tiền.
3. Nếu có cho mượn / thu hồi vỏ, thiết bị đầu tư, quà tặng → **+ Thêm vỏ/thiết bị**.
4. Khách trả tiền ngay → điền ô **trả ngay (đ)** (phần mềm tự tạo phiếu thu).
5. Bấm **Ghi sổ (F9)** – hoặc **Lưu nháp (F8)** nếu chưa chắc. **F2** = phiếu mới.

Phần mềm chặn trùng số seri phiếu.

### 5.2 Đọc phiếu viết tay bằng AI – *Đọc phiếu bằng AI*
1. Chụp ảnh phiếu "Hóa đơn bán lẻ" rõ nét, thẳng, đủ 4 góc. Tải lên (được nhiều ảnh một lúc).
2. Mở từng phiếu: **ảnh bên trái** (phóng to bằng nút −/+), **đề xuất của AI bên phải**.
3. Kiểm tra các ô có **cảnh báo** (sai phép nhân, tên bếp lạ, nghi trùng phiếu…), sửa nếu cần.
   Khi chị sửa tên bếp viết tay, phần mềm **nhớ** tên đó cho lần sau.
4. Bấm **Ghi sổ phiếu**, hoặc **Từ chối** nếu ảnh không dùng được.

> AI chỉ **đề xuất**, chưa ghi gì vào sổ cho tới khi chị bấm Ghi sổ.

### 5.3 Danh sách phiếu
Lọc theo khách / tháng / trạng thái. Với từng phiếu: **Sửa**, **Duyệt** (phiếu nháp), **Hủy** (bắt buộc ghi lý do).
Sửa phiếu đã ghi sổ: phần mềm tự đảo bút toán cũ và ghi lại, kho và công nợ luôn khớp.

### 5.4 Khách trả tiền
Vào **Thu – Chi** lập phiếu thu cho khách (xem [mục 7](#7-thu--chi-và-sổ-quỹ)). Tiền tự trừ vào tháng nợ cũ nhất;
bảng kê tự ghi *"TT xong CN tháng 07/2026"* hoặc *"TT 1 phần CN tháng 09/2026"*.

### 5.5 Cuối tháng – *Bảng kê tháng*
1. Chọn **khách** và **tháng** → xem trước bảng kê giống hệt file Excel.
2. Xuất ra Excel, chọn một trong hai:
   - **Tải file Excel** – file riêng 1 sheet, dùng để gửi khách.
   - **Thêm sheet vào file bảng kê của khách** – thêm sheet `T11.2026` vào file khai báo ở mục 4.3.
     Phần mềm **tự sao lưu file cũ** (`<tên>.bak-ngày-giờ.xlsx`) và ẩn các sheet tháng trước.
     **Đóng file đó trong Excel trước khi bấm**, nếu không sẽ báo lỗi.
3. Theo dõi trạng thái: **Lưu bảng kê** (nháp) → **Đã gửi khách** → **Khách xác nhận (khóa sổ)**.
4. Khi đã khóa sổ, không thêm/sửa/hủy phiếu và phiếu thu của khách trong tháng đó. Cần sửa thì bấm **Mở khóa**.

File Excel xuất ra giữ công thức như file gốc (`=D19*48-E19`, `=F19*G19`, `=SUM`…) – sửa số trong Excel thì tổng
và dòng **Bằng chữ** tự đổi theo.

---

## 6. Quy trình B – Đơn hàng → Biên bản giao nhận → Đối chiếu công nợ

**Nguyên tắc:** đơn hàng **chưa** tạo công nợ. Công nợ chỉ tăng khi kế toán **xác nhận BBGN có ảnh ký nhận**,
tính theo **số lượng khách thực nhận × đơn giá đã duyệt**.

### 6.1 Đơn đặt hàng
1. **Đơn đặt hàng → + Đơn mới**: chọn khách, ngày đặt, ngày giao dự kiến, địa điểm giao, điều khoản thanh toán,
   các dòng hàng (SL, đơn giá, chiết khấu %, thuế %). Tổng tiền và tiền bằng chữ hiện ngay.
2. Huy hiệu **hạn mức** hiện trực tiếp:
   🟢 *Trong hạn mức* · 🟡 *Gần chạm hạn mức* · 🔴 *Vượt hạn mức / nợ quá hạn*.
3. Bấm **Gửi duyệt**:
   - Trong hạn mức, không nợ quá hạn → **Đã duyệt** ngay.
   - Vượt hạn mức hoặc có nợ quá hạn → **Chờ duyệt**; người có vai trò Quản lý / Kế toán trưởng bấm **Duyệt đơn**.
4. Sau khi duyệt, đơn giá / chiết khấu / thuế / số lượng bị **khóa**. Vẫn sửa được thông tin giao hàng.
5. Màn hình chi tiết đơn có: thanh **tiến độ giao hàng**, tab *Chi tiết hàng hóa*, *Lịch sử Giao nhận & Công nợ*,
   *Hạn mức công nợ*; nút **In đơn**, **Hủy đơn** (ghi lý do), **Đóng phần còn lại** (khi khách không lấy nữa).

Đơn mua hàng từ nhà cung cấp (PO) làm tương tự.

### 6.2 Biên bản giao nhận (BBGN)
1. Ở đơn đã duyệt, bấm **Tạo Biên bản giao nhận** – số lượng còn lại tự điền sẵn (giao nhiều đợt được, không cho giao vượt).
2. **Đang chuẩn bị**: kiểm số lượng xuất kho, người giao → **Xuất kho & đi giao**. In BBGN mang theo
   (**In BBGN**, hoặc **In cho kho (ẩn giá)**).
3. **Đang giao** – khi xe về:
   - Tải **ảnh BBGN có chữ ký người nhận** (kéo-thả, hoặc chụp bằng điện thoại).
   - Nhập **SL thực nhận** và **SL lỗi / trả lại** từng dòng. Thực nhận + Lỗi phải bằng Xuất kho.
   - Dòng **vàng** = nhận ít hơn xuất; dòng **đỏ** = có hàng lỗi. Có chênh lệch thì bắt buộc ghi tình trạng và lý do.
4. Kế toán bấm **Kế toán Xác nhận & Ghi nhận Công nợ** → công nợ khách tăng, kho và tiến độ đơn cập nhật cùng lúc.

### 6.3 Ghi nhận khách thanh toán
Trong chi tiết đơn hoặc biên bản đối chiếu, bấm **Ghi nhận khách thanh toán**: ngày, số tiền, chuyển khoản / tiền mặt
→ **Ghi sổ** (tự sinh phiếu thu).

### 6.4 Đối chiếu công nợ
1. Vào **Đối chiếu công nợ**, chọn kỳ: *Tháng này / Tháng trước / Quý này / Tùy chỉnh*. Tick
   *Chỉ đối tác có phát sinh trong kỳ* để danh sách gọn.
2. Bấm **Lập biên bản đối chiếu** cho khách. Số dư đầu kỳ lấy từ biên bản đã chốt gần nhất.
   4 thẻ số liệu: đầu kỳ, phát sinh tăng, phát sinh giảm, cuối kỳ.
3. Bấm vào **Mã ĐĐH** hoặc **Mã BBGN** trên từng dòng để xem nhanh chứng từ kèm ảnh ký.
4. **In biên bản** / **Xuất Excel** gửi khách → bấm **Đã gửi đối tác**.
5. Khách báo sai dòng nào → **Báo chênh lệch** dòng đó (biên bản chuyển *Có chênh lệch*). Xử lý (điều chỉnh, ghi thanh toán…)
   → **Làm mới** → bấm **Đã xử lý** trên dòng đó.
6. Khớp hết → **Xác nhận khớp công nợ & Khóa sổ**, ghi tên người xác nhận phía khách.
   Toàn bộ đơn, BBGN, phiếu thu trong kỳ bị khóa; điều chỉnh phát sinh sau đó hạch toán vào kỳ tiếp theo.

---

## 7. Thu – Chi và sổ quỹ

1. Chọn **Phiếu thu** hoặc **Phiếu chi**, ngày, quỹ (**Ngân hàng / Tiền mặt**), số tiền.
2. Chọn khách hàng / nhà cung cấp. **Bỏ trống** = thu chi nội bộ, khi đó ghi **Khoản mục** (ví dụ: xăng xe, lương).
3. Ghi diễn giải → **Lưu phiếu**.
4. Phía dưới là **sổ quỹ** tiền mặt / ngân hàng: tồn đầu kỳ + thu − chi = tồn cuối kỳ.

---

## 8. Khách hàng – xem tổng quan từng khách

Mở một khách trong **Khách hàng & NCC**:

- Tab **Tổng quan**: 12 tháng gần nhất (kg, tiền, đã trả, nợ cuối tháng), nợ theo từng tháng, theo từng bếp,
  **vỏ bình đang mượn**. Ô *Đến tháng* để xem mốc khác.
- Tab **Bếp & tên gọi khác**, **Bảng giá**, **Thông tin** – như mục 4.3.
- Nút nhanh: **Đối chiếu công nợ →**, **Bảng kê tháng này →**.

---

## 9. Kho

- **Nhập – Xuất – Tồn** theo kỳ cho từng loại bình. Giá vốn tính bình quân gia quyền.
- **Kiểm kê**: nhập số đếm thực tế → **Lưu kiểm kê hôm nay** → phần mềm tự sinh phiếu cân bằng kho cho phần chênh.
- **Tồn đầu kỳ**: khai báo số tồn ban đầu.

---

## 10. Nhập dữ liệu

| Loại | Cách làm |
|---|---|
| **File Bảng kê Excel cũ** | Chọn file → xem bảng đối chiếu từng sheet → kiểm tra cột khớp. |
| **XML hóa đơn điện tử** (TT78) | Chọn file `.xml`. Hóa đơn **mua vào** → phiếu nhập kho + công nợ NCC. Hóa đơn **bán ra** → lưu số HĐ GTGT, nối với phiếu giao có cùng số hóa đơn. |

Nếu XML có mã hàng lạ của nhà cung cấp, phần mềm hiện danh sách để chị **chọn mã hàng tương ứng** →
**Nhập lại với mã đã chọn**. Chọn một lần, lần sau tự nhớ.

---

## 11. In và xuất Excel

| Chứng từ | Ở đâu | In (A4) | Excel |
|---|---|---|---|
| Bảng kê chi tiết công nợ | Bảng kê tháng | – | Đúng mẫu file của chị |
| Đơn đặt hàng | Chi tiết đơn → **In đơn** | ✓ | – |
| Biên bản giao nhận | Chi tiết BBGN → **In BBGN** / **In cho kho (ẩn giá)** | ✓ có mã QR | – |
| Biên bản đối chiếu công nợ | Chi tiết biên bản → **In biên bản** / **Xuất Excel** | ✓ có Quốc hiệu, phụ lục | ✓ |

Màn hình in mở ở tab mới: bấm **Ctrl + P**, chọn máy in, hoặc chọn *Lưu dưới dạng PDF* để gửi qua Zalo/email.

---

## 12. Sao lưu dữ liệu

- Toàn bộ dữ liệu nằm trong file **`data\sale.db`**.
- Mỗi ngày, lần đầu mở phần mềm, sẽ **tự sao lưu** vào `data\backups\` (giữ 30 bản gần nhất).
- Sao lưu ngay: **Cài đặt → Sao lưu ngay**, hoặc nháy đúp **`sao-luu.bat`**.
- **Nên chép cả thư mục `data` ra USB / Google Drive mỗi tuần.**
- Khi thêm sheet vào file bảng kê của khách, file cũ luôn được giữ bản `.bak-…xlsx` bên cạnh.

Khôi phục: tắt phần mềm → chép một file trong `data\backups\` đè lên `data\sale.db` (đổi tên thành `sale.db`) → mở lại.

---

## 13. Dùng qua Internet (VPS)

Khi phần mềm được đưa lên máy chủ thuê (VPS) bằng `docs/DEPLOY-LINUX.md`:
- Mở bằng địa chỉ tên miền (ví dụ `https://ten-mien.vn`), trình duyệt sẽ hỏi **tên đăng nhập và mật khẩu**.
- Vai trò lấy theo tài khoản (người quản trị khai báo), không chọn ở mục Cài đặt nữa.
- Trên VPS, dùng nút **Tải file Excel** thay cho *Thêm sheet vào file bảng kê của khách* (vì đường dẫn `D:\...` là ổ đĩa máy chị,
  máy chủ không thấy).

---

## 14a. Khách tính thuế GTGT riêng, mẫu bảng kê Welstory, xóa khách / xóa phiếu (cập nhật 05/11/2026)

**Khách yêu cầu tách thuế GTGT (VD Welstory)**
1. *Cài đặt → Đơn vị bán hàng khác → + Thêm* nếu khách được xuất bảng kê dưới tên công ty khác (VD Cty CP Metro Toàn Cầu).
2. *Khách hàng → chọn khách → Thông tin → Sửa*, phần **Thuế GTGT & bảng kê**:
   - Cách tính thuế: **Giá chưa thuế – cộng thuế GTGT riêng**
   - Mẫu bảng kê: **Mẫu tách thuế GTGT (Welstory)** (tự chọn khi đổi cách tính thuế)
   - Ngày chốt kỳ: **25** nếu bảng kê tháng 10 tính từ 26/09 đến 25/10 (để trống = theo tháng)
   - Đơn vị bán hàng in trên bảng kê: chọn đơn vị đã thêm ở bước 1.
3. *Bảng giá*: nhập **giá CHƯA thuế** (VD 34.682 đ/kg).
4. Lập phiếu như bình thường: màn hình hiện Tiền chưa thuế, Thuế GTGT 8%, Tổng cộng thanh toán. Có thể đổi cách tính thuế / thuế suất riêng cho từng phiếu.
5. Bảng kê tháng → Tải Excel: đúng mẫu file Welstory (Ngày | Loại bình | Gas dư | Thực giao | Đơn giá chưa thuế | Thành tiền chưa thuế | Tiền thuế GTGT 8% | Tổng cộng thanh toán | Ghi chú), công thức giữ số lẻ như file gốc.
   Tổng tiền của kỳ được làm tròn 1 lần nên công nợ trong phần mềm khớp đúng ô "Số tiền bên mua phải thanh toán" trên Excel (thành tiền từng phiếu có thể chênh ±1 đ).

**Xóa khách hàng / nhà cung cấp** – nút *Xóa* ở danh sách hoặc trong Thông tin khách:
- Chưa có chứng từ → xóa hẳn (kèm bếp, tên gọi khác, bảng giá riêng).
- Đã có phiếu / thu chi / bảng kê → không xóa được, chỉ **Ngừng theo dõi** (ẩn khỏi danh sách). Tick "Hiện cả đối tác đã ngừng theo dõi" rồi bấm *Theo dõi lại* để bật lại.

**Xóa phiếu** – nút *Xóa* ở Danh sách phiếu: phiếu nháp / đã hủy xóa ngay; phiếu đã ghi sổ được tự hoàn kho, vỏ mượn, công nợ rồi xóa.
Không xóa được khi kỳ đã khóa sổ (mở khóa bảng kê trước) hoặc phiếu có phiếu thu đi kèm (hủy phiếu thu trước). Muốn giữ dấu vết thì dùng *Hủy* thay vì *Xóa*.

## 14. Câu hỏi thường gặp & xử lý lỗi

| Hiện tượng | Cách xử lý |
|---|---|
| Nháy `chay.bat` báo *"Chua cai dat"* | Chạy `cai-dat.bat` trước. |
| `cai-dat.bat` báo *"Chua co Python"* | Cài lại Python, nhớ tick **Add python.exe to PATH**. |
| Trình duyệt báo không kết nối được | Kiểm tra cửa sổ đen của `chay.bat` còn mở không. |
| Máy khác trong mạng không vào được | Dùng đúng IP máy chủ; cho phép Python qua Windows Firewall khi được hỏi. |
| Thêm sheet vào file khách báo lỗi file đang mở | Đóng file đó trong Excel rồi bấm lại. |
| Báo sheet tháng đã tồn tại | Tháng đó đã xuất rồi; chọn ghi đè hoặc dùng **Tải file Excel**. |
| Không sửa được phiếu / phiếu thu | Tháng đó đã **khóa sổ**. Mở khóa ở Bảng kê tháng (Quy trình A); kỳ đối chiếu đã khớp (Quy trình B) thì ghi điều chỉnh sang kỳ sau. |
| Đơn hàng cứ nằm ở *Chờ duyệt* | Khách vượt hạn mức hoặc có nợ quá hạn – người có vai trò Quản lý / Kế toán trưởng phải duyệt. |
| Nút *Kế toán Xác nhận* bị mờ | Chưa tải ảnh ký nhận, hoặc Thực nhận + Lỗi ≠ Xuất kho, hoặc thiếu lý do chênh lệch. |
| AI đọc phiếu báo lỗi khóa | Chưa điền `DEEPSEEK_API_KEY` trong `backend\.env`, hoặc khóa hết tiền. Sửa xong tắt / mở lại `chay.bat`. |
| AI đọc sai | Chụp lại ảnh rõ hơn; sửa trực tiếp trên màn hình duyệt trước khi ghi sổ. |
| File Excel cũ bị mất hình / biểu đồ sau khi thêm sheet | Mở bản `.bak-…xlsx` bên cạnh – luôn còn nguyên. |

---

## 15. Bảng tra nhanh

### Phím tắt (màn Lập phiếu gas)
| Phím | Việc |
|---|---|
| F2 | Phiếu mới |
| F4 | Thêm dòng hàng |
| F8 | Lưu nháp |
| F9 | Ghi sổ |

### Vai trò người dùng
| Vai trò | Được làm |
|---|---|
| Quản trị (ADMIN) | Tất cả |
| Quản lý (MANAGER) | Duyệt đơn vượt hạn mức / có nợ quá hạn |
| Kế toán trưởng (CHIEF_ACCOUNTANT) | Duyệt đơn vượt hạn mức / có nợ quá hạn |
| Kế toán (ACCOUNTANT) | Xác nhận BBGN, đối chiếu công nợ, hủy / đóng đơn |
| Nhân viên (STAFF) | Lập đơn hàng, lập BBGN |

### Trạng thái chứng từ
| Chứng từ | Trạng thái |
|---|---|
| Phiếu gas | Nháp → Đã ghi sổ · Đã hủy |
| Bảng kê tháng | Nháp → Đã gửi khách → Khách xác nhận (khóa sổ) |
| Đơn đặt hàng | Nháp → Chờ duyệt → Đã duyệt → Giao từng phần → Hoàn tất · Đã hủy |
| Biên bản giao nhận | Đang chuẩn bị → Đang giao → Đã xác nhận · Đã hủy |
| Biên bản đối chiếu | Nháp → Đã gửi → (Có chênh lệch) → Đã khớp – khóa sổ |

### Mã chứng từ
`SO-YYYYMM-xxxx` đơn bán · `PO-…` đơn mua · `DN-…` biên bản giao nhận · `DR-…` biên bản đối chiếu ·
`CN-…` trả hàng · `DC-…` điều chỉnh.

---

## Việc chị cần làm để bắt đầu dùng thật

- [ ] Điền hạn mức nợ và số ngày được nợ cho từng khách (để trống = không giới hạn).
- [ ] Đặt tên người dùng / vai trò trên mỗi máy (Cài đặt).
- [ ] Điền khóa DeepSeek vào `backend\.env` để dùng AI đọc phiếu.
- [ ] Nhập 3 file bảng kê lịch sử, điền đường dẫn file bảng kê cho từng khách.
- [ ] Xuất thử bảng kê T11/2026 và so với file chị tự làm.
- [ ] Xác nhận công nợ Hyundai & Korea Food trước T10/2026, và tên các "Bếp chưa rõ tên" năm 2025.
