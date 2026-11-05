"""Xuất Biên bản đối chiếu công nợ ra Excel (.xlsx) cho kế toán đối tác kiểm tra.

* Times New Roman, viền bảng rõ ràng, độ rộng cột cố định, A4 đứng vừa 1 trang ngang, lặp dòng tiêu đề.
* Dòng tổng cộng & số dư dùng CÔNG THỨC (=SUM, =đầu kỳ+tăng−giảm), số dư lũy kế từng dòng cũng là công thức
  -> đối tác sửa số trên Excel thì tổng tự nhảy.
* Sheet 2 "Phụ lục": chi tiết từng lần giao nhận (Số ĐĐH, Số BBGN, mặt hàng, SL thực nhận, đơn giá, thành tiền).
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from ..models import DebtReconciliation, DebtSide
from ..money import D
from ..services import print_docs

FONT = "Times New Roman"
THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="D9E1F2")
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
DEBT_FILL = PatternFill("solid", fgColor="FFF2CC")
MONEY = '#,##0;[Red]-#,##0;"-"'
QTY = '#,##0.###'
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")


def _f(size=12, bold=False, italic=False):
    return Font(name=FONT, size=size, bold=bold, italic=italic)


def _merge(ws: Worksheet, row: int, c1: int, c2: int, value, *, font=None, align=CENTER, height=None):
    ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
    cell = ws.cell(row=row, column=c1, value=value)
    cell.font = font or _f()
    cell.alignment = align
    if height:
        ws.row_dimensions[row].height = height
    return cell


def _box(ws: Worksheet, r1: int, r2: int, c1: int, c2: int) -> None:
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(row=r, column=c).border = BORDER


def _vn(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def build_reconciliation_xlsx(session: Session, rec: DebtReconciliation) -> bytes:
    payload = print_docs.print_payload(session, "dr", rec.id)
    company, doc = payload["company"], payload["doc"]
    partner = doc["partner"]
    seller_is_us = rec.side == DebtSide.RECEIVABLE

    wb = Workbook()
    ws = wb.active
    ws.title = "Doi chieu"
    widths = [6, 12, 17, 19, 40, 16, 16, 17]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    last = len(widths)

    r = 1
    _merge(ws, r, 1, 4, (company.get("name") or "").upper(), font=_f(11, bold=True), align=LEFT)
    _merge(ws, r, 5, last, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", font=_f(11, bold=True))
    r += 1
    _merge(ws, r, 1, 4, f"MST: {company.get('tax_code') or ''}", font=_f(10), align=LEFT)
    _merge(ws, r, 5, last, "Độc lập – Tự do – Hạnh phúc", font=_f(11, bold=True))
    r += 1
    _merge(ws, r, 1, 4, f"Số: {rec.reconciliation_code}", font=_f(10), align=LEFT)
    _merge(ws, r, 5, last, f"{company.get('sign_city') or 'Hà Nội'}, ngày {rec.period_to:%d} tháng {rec.period_to:%m} "
                           f"năm {rec.period_to:%Y}", font=_f(11, italic=True))
    r += 2
    _merge(ws, r, 1, last, "BIÊN BẢN ĐỐI CHIẾU CÔNG NỢ", font=_f(16, bold=True), height=24)
    r += 1
    _merge(ws, r, 1, last, f"Từ ngày {_vn(rec.period_from)} đến ngày {_vn(rec.period_to)}", font=_f(12, italic=True))
    r += 2
    us = [("Tên đơn vị", company.get("name")), ("Mã số thuế", company.get("tax_code")),
          ("Địa chỉ", company.get("address")), ("Điện thoại", company.get("phones"))]
    them = [("Tên đơn vị", partner.get("name")), ("Mã số thuế", partner.get("tax_code")),
            ("Địa chỉ", partner.get("address")), ("Đại diện", " – ".join(x for x in (
                partner.get("representative"), partner.get("representative_title")) if x) or "")]
    side_a, side_b = (us, them) if seller_is_us else (them, us)
    for title, rows in (("BÊN A (Bên bán / cung cấp)", side_a), ("BÊN B (Bên mua / tiêu thụ)", side_b)):
        _merge(ws, r, 1, last, title, font=_f(12, bold=True), align=LEFT)
        r += 1
        for label, value in rows:
            _merge(ws, r, 1, 2, f"{label}:", font=_f(11), align=LEFT)
            _merge(ws, r, 3, last, value or "", font=_f(11), align=LEFT)
            r += 1
    r += 1
    _merge(ws, r, 1, last, "Hai bên cùng đối chiếu công nợ trong kỳ, kết quả như sau:", font=_f(11, italic=True),
           align=LEFT)
    r += 1

    # ---------------------------------------------------------- bảng tổng hợp 4 chỉ tiêu (điền công thức sau)
    summary_top = r
    labels = ["(1) Số dư đầu kỳ", "(2) Phát sinh tăng trong kỳ (giao nhận)", "(3) Đã thanh toán / giảm trừ trong kỳ",
              "(4) Số dư cuối kỳ phải thanh toán = (1) + (2) − (3)"]
    for i, label in enumerate(labels):
        _merge(ws, r + i, 1, 6, label, font=_f(11, bold=i == 3), align=LEFT)
        ws.cell(row=r + i, column=7).number_format = MONEY
        ws.merge_cells(start_row=r + i, start_column=7, end_row=r + i, end_column=last)
        cell = ws.cell(row=r + i, column=7)
        cell.font = _f(11, bold=i == 3)
        cell.alignment = RIGHT
        if i == 3:
            for c in range(1, last + 1):
                ws.cell(row=r + i, column=c).fill = DEBT_FILL
    _box(ws, summary_top, summary_top + 3, 1, last)
    r += 5

    # ---------------------------------------------------------- bảng kê chi tiết chứng từ
    _merge(ws, r, 1, last, "BẢNG KÊ CHI TIẾT CHỨNG TỪ", font=_f(12, bold=True), align=LEFT)
    r += 1
    head = ["STT", "Ngày", "Số ĐĐH", "Số BBGN / Phiếu", "Diễn giải", "Phát sinh tăng", "Phát sinh giảm", "Số dư"]
    head_row = r
    for c, text in enumerate(head, start=1):
        cell = ws.cell(row=r, column=c, value=text)
        cell.font = _f(11, bold=True)
        cell.alignment = CENTER
        cell.fill = HEAD_FILL
    ws.row_dimensions[r].height = 30
    r += 1
    opening_row = r
    _merge(ws, r, 1, 7, "Số dư đầu kỳ", font=_f(11, bold=True), align=LEFT)
    ws.cell(row=r, column=8, value=float(D(rec.opening_balance))).number_format = MONEY
    ws.cell(row=r, column=8).font = _f(11, bold=True)
    r += 1
    first_line = r
    for ln in rec.lines:
        values = [ln.line_no, ln.transaction_date, ln.order_code or "", ln.document_code, ln.description or "",
                  float(D(ln.increase_amount)) if ln.increase_amount else None,
                  float(D(ln.decrease_amount)) if ln.decrease_amount else None]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = _f(11)
            cell.alignment = CENTER if c in (1, 2, 3, 4) else (LEFT if c == 5 else RIGHT)
        ws.cell(row=r, column=2).number_format = "dd/mm/yyyy"
        ws.cell(row=r, column=6).number_format = MONEY
        ws.cell(row=r, column=7).number_format = MONEY
        prev = f"H{r - 1}"
        ws.cell(row=r, column=8, value=f"={prev}+F{r}-G{r}").number_format = MONEY
        ws.cell(row=r, column=8).font = _f(11)
        if ln.is_disputed:
            for c in range(1, last + 1):
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor="F8CBAD")
            ws.cell(row=r, column=5).value = f"{ln.description or ''}\n[Chênh lệch: {ln.dispute_comment}]"
        r += 1
    last_line = r - 1
    total_row = r
    _merge(ws, r, 1, 5, "Cộng phát sinh trong kỳ", font=_f(11, bold=True))
    for c, col in ((6, "F"), (7, "G")):
        formula = f"=SUM({col}{first_line}:{col}{last_line})" if last_line >= first_line else 0
        cell = ws.cell(row=r, column=c, value=formula)
        cell.number_format = MONEY
        cell.font = _f(11, bold=True)
    for c in range(1, last + 1):
        ws.cell(row=r, column=c).fill = TOTAL_FILL
    r += 1
    closing_row = r
    _merge(ws, r, 1, 7, "Số dư cuối kỳ phải thanh toán", font=_f(11, bold=True))
    cell = ws.cell(row=r, column=8, value=f"=H{opening_row}+F{total_row}-G{total_row}")
    cell.number_format = MONEY
    cell.font = _f(12, bold=True)
    for c in range(1, last + 1):
        ws.cell(row=r, column=c).fill = DEBT_FILL
    _box(ws, head_row, closing_row, 1, last)

    # công thức bảng tổng hợp trỏ vào bảng chi tiết
    ws.cell(row=summary_top, column=7, value=f"=H{opening_row}")
    ws.cell(row=summary_top + 1, column=7, value=f"=F{total_row}")
    ws.cell(row=summary_top + 2, column=7, value=f"=G{total_row}")
    ws.cell(row=summary_top + 3, column=7, value=f"=G{summary_top}+G{summary_top + 1}-G{summary_top + 2}")

    r += 2
    closing = D(rec.closing_balance)
    debtor = "Bên B còn phải thanh toán cho Bên A" if closing >= 0 else "Bên A còn phải trả lại Bên B"
    _merge(ws, r, 1, last, f"Kết luận: {debtor} số tiền {abs(closing):,.0f} đồng".replace(",", "."),
           font=_f(12, bold=True), align=LEFT)
    r += 1
    _merge(ws, r, 1, last, f"Bằng chữ: {doc['amount_words']}", font=_f(12, italic=True), align=LEFT)
    r += 1
    _merge(ws, r, 1, last, "Biên bản được lập thành 02 bản có giá trị như nhau, mỗi bên giữ 01 bản. Trong vòng 04 ngày "
                           "kể từ ngày nhận biên bản, nếu Bên B không có ý kiến phản hồi thì số liệu trên được coi là "
                           "đã xác nhận.", font=_f(11, italic=True), align=LEFT, height=46)
    r += 2
    sign = [(1, 2, "KẾ TOÁN BÊN A"), (3, 4, "GIÁM ĐỐC BÊN A"), (5, 6, "KẾ TOÁN BÊN B"), (7, 8, "GIÁM ĐỐC BÊN B")]
    for c1, c2, title in sign:
        _merge(ws, r, c1, c2, title, font=_f(11, bold=True))
        _merge(ws, r + 1, c1, c2, "(Ký, ghi rõ họ tên)" if "KẾ TOÁN" in title else "(Ký, họ tên, đóng dấu)",
               font=_f(10, italic=True))
    ws.row_dimensions[r + 6].height = 18
    print_end = r + 6

    ws.print_title_rows = f"{head_row}:{head_row}"
    ws.print_area = f"A1:{get_column_letter(last)}{print_end}"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.oddFooter.center.text = "Trang &P / &N"
    ws.freeze_panes = None

    _appendix_sheet(wb, rec, doc)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _appendix_sheet(wb: Workbook, rec: DebtReconciliation, doc: dict) -> None:
    ws = wb.create_sheet("Phu luc giao nhan")
    head = ["STT", "Ngày giao", "Số ĐĐH", "Số BBGN", "Mã hàng", "Tên hàng", "ĐVT", "SL thực nhận", "Đơn giá",
            "CK %", "Thuế %", "Thành tiền"]
    widths = [6, 12, 17, 17, 10, 32, 8, 13, 14, 7, 7, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _merge(ws, 1, 1, len(head), f"PHỤ LỤC: CHI TIẾT TỪNG LẦN GIAO NHẬN – {rec.reconciliation_code}",
           font=_f(14, bold=True), height=22)
    _merge(ws, 2, 1, len(head), f"Đối tác: {doc['partner']['name']} · Kỳ {_vn(rec.period_from)} – {_vn(rec.period_to)}",
           font=_f(11, italic=True))
    r = 4
    for c, text in enumerate(head, start=1):
        cell = ws.cell(row=r, column=c, value=text)
        cell.font = _f(11, bold=True)
        cell.alignment = CENTER
        cell.fill = HEAD_FILL
    first = r + 1
    for i, a in enumerate(doc["appendix"], start=1):
        r += 1
        vals = [i, a["date"], a["order_code"], a["delivery_code"], a["product_code"], a["product_name"], a["unit"],
                float(a["accepted_qty"]) if a["accepted_qty"] is not None else None,
                float(a["unit_price"]) if a["unit_price"] is not None else None,
                float(a["discount_rate"]) if a["discount_rate"] is not None else None,
                float(a["tax_rate"]) if a["tax_rate"] is not None else None, float(a["amount"])]
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = _f(11)
            cell.alignment = LEFT if c == 6 else (RIGHT if c >= 8 else CENTER)
        ws.cell(row=r, column=2).number_format = "dd/mm/yyyy"
        ws.cell(row=r, column=8).number_format = QTY
        ws.cell(row=r, column=9).number_format = MONEY
        ws.cell(row=r, column=12).number_format = MONEY
    r += 1
    _merge(ws, r, 1, 11, "Tổng cộng", font=_f(11, bold=True))
    cell = ws.cell(row=r, column=12, value=f"=SUM(L{first}:L{r - 1})" if r - 1 >= first else 0)
    cell.number_format = MONEY
    cell.font = _f(11, bold=True)
    for c in range(1, len(head) + 1):
        ws.cell(row=r, column=c).fill = TOTAL_FILL
    _box(ws, 4, r, 1, len(head))
    ws.print_title_rows = "4:4"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
