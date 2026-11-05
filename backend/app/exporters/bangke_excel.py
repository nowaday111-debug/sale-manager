"""Bước 4 – Xuất "BẢNG KÊ CHI TIẾT" ra Excel đúng mẫu file Stargate.

Bố cục (bắt đầu cột B, cột A làm lề – như file gốc):
    B Ngày tháng | C Số hóa đơn | D.. Loại bình (mỗi loại 1 cột: PC48, PC45, PC12, Mini...) |
    Gas dư | Thực giao | Đơn giá | Thành tiền | Ghi chú | Tên bếp + số vỏ mượn (gộp dọc theo khối)

- Mỗi bếp = 1 khối: [dòng lịch sử đầu tư] + các phiếu + 1 dòng trống để ghi tay + dòng "Tổng" (=SUM).
- Thực giao / Thành tiền ghi bằng công thức giống file gốc (=D19*48-F19, =G19*H19) khi công thức cho ra
  đúng số tiền đã lưu; trường hợp đặc biệt (giá theo bình, có vật tư, trừ tiền) ghi giá trị.
- "Tổng cộng" = cộng các dòng Tổng; "Tổng công nợ" = SUM; "Còn phải thanh toán" = Tổng công nợ − Đã thanh toán.
- "Bằng chữ" dùng lại đúng khối công thức đọc số của file gốc (đặt ở vùng ẩn dưới trang in), nên chị sửa số
  trong Excel thì chữ tự đổi theo.
- Thêm sheet "T{m}.{yyyy}" vào workbook sẵn có của khách (sao lưu file trước khi ghi), hoặc xuất file riêng.
"""
from __future__ import annotations

import shutil
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import BusinessError
from ..models import PriceBasis, Product
from ..money import ZERO, D
from ..services import masterdata, statements

FONT_NAME = "Times New Roman"
FONT_SIZE = 12
THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FILL_HEAD = PatternFill("solid", fgColor="FFC0C0C0")
FILL_TOTAL = PatternFill("solid", fgColor="FFD9D9D9")
FILL_DEBT = PatternFill("solid", fgColor="FFFFF2CC")
NUM = "#,##0"
KG = "#,##0.0"
DATE_FMT = "dd/mm/yyyy"
FIRST_COL = 2  # cột B


def _font(bold=False, italic=False, size=FONT_SIZE):
    return Font(name=FONT_NAME, size=size, bold=bold, italic=italic)


def _num(v: Decimal | None):
    """Decimal -> int/float để Excel hiểu là số (giữ đúng giá trị hiển thị)."""
    if v is None:
        return None
    v = D(v)
    if v == v.to_integral_value():
        return int(v)
    return float(v)


def _invoice_value(no: str | None):
    if not no:
        return None
    return int(no) if no.isdigit() and not no.startswith("0") else no


class Layout:
    """Vị trí các cột theo số loại bình trong tháng."""

    def __init__(self, cylinder_skus: list[str]):
        self.skus = cylinder_skus or ["PC48"]
        c = FIRST_COL
        self.date, self.inv = c, c + 1
        self.cyl = {sku: c + 2 + i for i, sku in enumerate(self.skus)}
        nxt = c + 2 + len(self.skus)
        self.residual, self.net, self.price, self.amount, self.note, self.site = range(nxt, nxt + 6)
        self.last = self.site

    def L(self, col: int) -> str:  # noqa: N802 – viết tắt cho dễ đọc công thức
        return get_column_letter(col)

    @property
    def first_cyl(self) -> int:
        return self.cyl[self.skus[0]]

    @property
    def last_cyl(self) -> int:
        return self.cyl[self.skus[-1]]


class SheetWriter:
    def __init__(self, ws: Worksheet, st: dict, capacities: dict[str, Decimal]):
        self.ws, self.st, self.cap = ws, st, capacities
        self.lay = Layout(st["cylinder_columns"])
        self.row = 1

    # ------------------------------------------------------------ tiện ích ô
    def put(self, row, col, value=None, *, bold=False, italic=False, size=FONT_SIZE, fmt=None, h=None, v="center",
            wrap=False, border=False, fill=None):
        cell = self.ws.cell(row=row, column=col)
        if value is not None:
            cell.value = value
        cell.font = _font(bold, italic, size)
        cell.alignment = Alignment(horizontal=h, vertical=v, wrap_text=wrap)
        if fmt:
            cell.number_format = fmt
        if border:
            cell.border = BORDER
        if fill:
            cell.fill = fill
        return cell

    def merge(self, r1, c1, r2, c2):
        if (r1, c1) != (r2, c2):
            self.ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)

    def table_row(self, row, *, fill=None, bold=False):
        """Kẻ viền + font cho cả dòng trong bảng (B..cột bếp)."""
        for col in range(FIRST_COL, self.lay.last + 1):
            cell = self.ws.cell(row=row, column=col)
            cell.border = BORDER
            if cell.font is None or cell.font.name != FONT_NAME:
                cell.font = _font(bold)
            if fill:
                cell.fill = fill
            if cell.alignment.horizontal is None:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # ------------------------------------------------------------ các phần
    def header(self):
        s, lay = self.st["seller"], self.lay
        lines = [
            (s.get("parent_name"), True),
            (s.get("name", "").title().replace("Tnhh", "TNHH") + "." if s.get("name") else None, False),
            (s.get("phones"), False),
            (f"Số Tài Khoản:  {s['bank_account']}." if s.get("bank_account") else None, False),
            (s.get("bank_name"), False),
        ]
        for i, (text, bold) in enumerate(lines, start=1):
            if text:
                self.put(i, FIRST_COL, text, bold=bold, h="left")
        self.put(7, FIRST_COL, "BẢNG KÊ CHI TIẾT", bold=True, size=16, h="center")
        self.merge(7, FIRST_COL, 7, lay.last)
        self.ws.row_dimensions[7].height = 23.25
        self.put(8, FIRST_COL, self.st["period_text"], bold=True, italic=True, h="center")
        self.merge(8, FIRST_COL, 8, lay.last)

        b = self.st["buyer"]
        info = [
            (9, "Đơn vị bán hàng:", s.get("name"), True),
            (10, "Địa chỉ:", s.get("address"), False),
            (11, "Mã số thuế:", s.get("tax_code"), False),
            (12, "Đơn vị mua hàng:", b.get("name"), True),
            (13, "Địa chỉ:", b.get("address"), False),
            (14, "Mã số thuế:", b.get("tax_code"), False),
            (15, "Điện thoại", self._buyer_phone(b), False),
        ]
        val_col = FIRST_COL + 2  # cột D như file gốc
        for r, label, value, bold in info:
            self.put(r, FIRST_COL, label, h="left")
            self.put(r, val_col, value or "", bold=bold, h="left", wrap=False)
            self.merge(r, val_col, r, lay.last)
        self.row = 16

    @staticmethod
    def _buyer_phone(b: dict) -> str:
        phone, name = b.get("phone") or "", b.get("contact_name") or ""
        if name and phone and name not in phone:
            return f"{name}: {phone}"
        return b.get("phone") or b.get("contact_name") or ""

    def column_titles(self):
        lay, r = self.lay, self.row
        all_kg = all(row.get("price_basis") in (None, PriceBasis.KG)
                     for blk in self.st["blocks"] for row in blk["rows"] if row["kind"] == "slip")
        titles = [
            (lay.date, "Ngày tháng"), (lay.inv, "Số hóa đơn"), (lay.residual, "Gas dư"), (lay.net, "Thực giao"),
            (lay.price, "Đơn giá  (đồng/kg)" if all_kg else "Đơn giá  (đồng/bình)"), (lay.amount, "Thành tiền"),
            (lay.note, "Ghi chú"),
        ]
        for col, text in titles:
            self.put(r, col, text, bold=True, h="center", wrap=True, border=True, fill=FILL_HEAD)
            self.merge(r, col, r + 1, col)
        self.put(r, lay.first_cyl, "Loại Bình", bold=True, h="center", border=True, fill=FILL_HEAD)
        self.merge(r, lay.first_cyl, r, lay.last_cyl)
        for sku, col in lay.cyl.items():
            self.put(r + 1, col, sku, bold=True, h="center", border=True, fill=FILL_HEAD)
        self.put(r, lay.site, None, border=True, fill=FILL_HEAD)
        self.merge(r, lay.site, r + 1, lay.site)
        self.table_row(r, fill=FILL_HEAD, bold=True)
        self.table_row(r + 1, fill=FILL_HEAD, bold=True)
        self.ws.row_dimensions[r].height = 18.0
        self.ws.row_dimensions[r + 1].height = 18.0
        # dòng tổng vỏ đầu kỳ
        r += 2
        self.table_row(r, fill=FILL_TOTAL, bold=True)
        self.merge(r, FIRST_COL, r, lay.amount)
        self.put(r, lay.note, self.st.get("opening_loans_text") or None, bold=True, h="center", wrap=True,
                 border=True, fill=FILL_TOTAL)
        self.merge(r, lay.note, r, lay.site)
        self.ws.print_title_rows = f"{self.row}:{self.row + 1}"
        self.row = r + 1

    def _net_formula(self, r: int, row: dict) -> str | None:
        lay, parts = self.lay, []
        expected = ZERO
        for sku, q in row["qty_by_sku"].items():
            cap = self.cap.get(sku)
            if not cap or not q:
                continue
            parts.append(f"{lay.L(lay.cyl[sku])}{r}*{_num(cap)}")
            expected += D(q) * D(cap)
        if not parts:
            return None
        expected -= D(row["gas_residual_kg"] or ZERO)
        if row["net_kg"] is None or expected != D(row["net_kg"]):
            return None
        return "=" + "+".join(parts) + f"-{lay.L(lay.residual)}{r}"

    def slip_row(self, r: int, row: dict):
        lay = self.lay
        self.table_row(r)
        self.put(r, lay.date, row["date"], fmt=DATE_FMT, h="center", border=True, wrap=True)
        self.put(r, lay.inv, _invoice_value(row.get("vat_invoice_no")), bold=True, fmt="0", h="center", border=True)
        for sku, q in row["qty_by_sku"].items():
            if sku in lay.cyl:
                self.put(r, lay.cyl[sku], _num(q), h="center", border=True)
        notes = [row.get("note")]
        if row["qty_by_sku"]:
            self.put(r, lay.residual, _num(row["gas_residual_kg"]), h="center", border=True)
            net_formula = self._net_formula(r, row)
            self.put(r, lay.net, net_formula or _num(row["net_kg"]), fmt="0.0", h="center", border=True)
            if row.get("description"):
                notes.append(row["description"])
        elif row.get("description"):
            # phiếu chỉ có vật tư (lắp bếp, súng mồi, cược vỏ...): mô tả ở vùng cột loại bình
            self.put(r, lay.first_cyl, row["description"], h="center", wrap=True, border=True)
            self.merge(r, lay.first_cyl, r, lay.net)
        price = row.get("unit_price")
        self.put(r, lay.price, _num(price), fmt=NUM, h="center", border=True)
        amount = D(row["amount"])
        formula = None
        if price is not None and row["qty_by_sku"] and row.get("price_basis") == PriceBasis.KG \
                and row["net_kg"] is not None and (D(row["net_kg"]) * D(price)) == amount:
            formula = f"={lay.L(lay.net)}{r}*{lay.L(lay.price)}{r}"
        elif price is not None and row["qty_by_sku"] and row.get("price_basis") == PriceBasis.UNIT \
                and not row["gas_residual_kg"] and len(row["qty_by_sku"]) == 1:
            sku, q = next(iter(row["qty_by_sku"].items()))
            if D(q) * D(price) == amount and sku in lay.cyl:
                formula = f"={lay.L(lay.cyl[sku])}{r}*{lay.L(lay.price)}{r}"
        self.put(r, lay.amount, formula or _num(amount), fmt=NUM, h="right", border=True)
        text = "; ".join(n for n in notes if n)
        self.put(r, lay.note, text or None, h="center", wrap=True, border=True)
        self.ws.row_dimensions[r].height = 18.75

    def note_row(self, r: int, row: dict):
        lay = self.lay
        self.table_row(r)
        self.put(r, lay.first_cyl, row["text"], h="center", wrap=True, border=True)
        self.merge(r, lay.first_cyl, r, lay.amount)

    def block(self, blk: dict) -> int:
        """Ghi một khối bếp, trả về số dòng "Tổng" của khối."""
        lay = self.lay
        start = self.row
        if blk.get("investment_note"):
            self.table_row(start)
            self.put(start, FIRST_COL, blk["investment_note"], bold=True, h="center", wrap=True, border=True)
            self.merge(start, FIRST_COL, start, lay.note)
            lines = blk["investment_note"].count("\n") + 1
            self.ws.row_dimensions[start].height = max(15.75, 15.75 * lines + (15.75 if len(blk["investment_note"]) > 110 else 0))
            self.row += 1
        data_start = self.row
        for row in blk["rows"]:
            if row["kind"] == "slip":
                self.slip_row(self.row, row)
            else:
                self.note_row(self.row, row)
            self.row += 1
        # 1 dòng trống để ghi tay (nằm trong vùng SUM)
        self.table_row(self.row)
        for col in (lay.date,):
            self.ws.cell(self.row, col).number_format = DATE_FMT
        self.row += 1
        data_end = self.row - 1
        total = self.row
        self.table_row(total, fill=FILL_TOTAL, bold=True)
        self.put(total, lay.date, "Tổng", bold=True, h="center", border=True, fill=FILL_TOTAL)
        for col in [*lay.cyl.values(), lay.residual, lay.net, lay.amount]:
            fmt = KG if col == lay.net else ("General" if col == lay.residual else NUM)
            self.put(total, col, f"=SUM({lay.L(col)}{data_start}:{lay.L(col)}{data_end})", bold=True, fmt=fmt,
                     h="right" if col == lay.amount else "center", border=True, fill=FILL_TOTAL)
        self.put(total, lay.price, "X", bold=True, h="center", border=True, fill=FILL_TOTAL)
        # tên bếp + vỏ mượn gộp dọc cả khối
        self.put(start, lay.site, blk["site_label"], bold=True, h="center", wrap=True, border=True)
        self.merge(start, lay.site, total - 1, lay.site)
        self.row += 1
        return total

    def totals_and_debts(self, total_rows: list[int]):
        lay, st = self.lay, self.st
        r = self.row
        grand = r
        self.table_row(r, fill=FILL_TOTAL, bold=True)
        self.put(r, lay.date, "Tổng cộng", bold=True, h="center", border=True, fill=FILL_TOTAL)
        for col in [*lay.cyl.values(), lay.residual, lay.net, lay.amount]:
            refs = "+".join(f"{lay.L(col)}{t}" for t in total_rows) or "0"
            self.put(r, col, f"=+{refs}", bold=True,
                     fmt=KG if col == lay.net else ("General" if col == lay.residual else NUM),
                     h="right" if col == lay.amount else "center", border=True, fill=FILL_TOTAL)
        self.put(r, lay.price, "X", bold=True, h="center", border=True, fill=FILL_TOTAL)
        self.put(r, lay.note, st.get("closing_loans_text") or None, bold=True, h="center", wrap=True, border=True,
                 fill=FILL_TOTAL)
        self.merge(r, lay.note, r, lay.site)
        r += 1

        def money_row(row, label, value, *, fill=None, note=None):
            self.table_row(row, fill=fill, bold=True)
            self.put(row, FIRST_COL, label, bold=True, h="center", wrap=True, border=True, fill=fill)
            self.merge(row, FIRST_COL, row, lay.price)
            self.put(row, lay.amount, value, bold=True, fmt=NUM, h="right", border=True, fill=fill)
            self.put(row, lay.note, note, bold=True, h="center", wrap=True, border=True, fill=fill)

        for d in st["prior_debts"]:
            money_row(r, d["label"], _num(d["amount"]))
            r += 1
        A = lay.L(lay.amount)  # noqa: N806
        total_debt = r
        money_row(r, "Tổng công nợ", f"=SUM({A}{grand}:{A}{r - 1})", fill=FILL_DEBT)
        r += 1
        pay_start = r
        for p in st["payments"]:
            money_row(r, p["label"], _num(p["amount"]), note=p.get("note"))
            r += 1
        paid = r
        formula = f"=SUM({A}{pay_start}:{A}{r - 1})" if st["payments"] else 0
        money_row(r, "Tổng đã thanh toán", formula, fill=FILL_DEBT)
        r += 1
        closing = r
        money_row(r, "Công nợ còn lại phải thanh toán là: ", f"=+{A}{total_debt}-{A}{paid}")
        self.row = r + 2
        return closing

    def footer(self, closing_row: int) -> int:
        lay, st = self.lay, self.st
        A = lay.L(lay.amount)  # noqa: N806
        r = self.row
        words_row = r
        self.put(r, FIRST_COL, "Bằng chữ: ", bold=True, h="left")
        self.put(r, FIRST_COL + 2, st["amount_in_words"], bold=True, h="left", wrap=True)
        self.merge(r, FIRST_COL + 2, r, lay.last)
        self.ws.row_dimensions[r].height = 31.5
        r += 1
        self.put(r, FIRST_COL, "- Số tiền bên mua phải thanh toán:", h="left")
        self.put(r, lay.net, f"={A}{closing_row}", bold=True, fmt=NUM, h="center")
        self.merge(r, lay.net, r, lay.price)
        self.put(r, lay.amount, "đồng", bold=True, h="right")
        r += 1
        contact = st["seller"].get("contact_line")
        lines = [
            "- Bên mua kiểm tra, đối chiếu, xác nhận công nợ cho bên bán. Sau 04 ngày kể từ khi nhận được bản này "
            "bên mua không có phản hồi thì số liệu trên coi là đúng.",
            f"- Mọi ý kiến của quý khách liên hệ trực tiếp {contact} để được giải đáp" if contact else None,
            "- Trân trọng cảm ơn.",
        ]
        for i, text in enumerate(t for t in lines if t):
            self.put(r, FIRST_COL, text, h="left", wrap=(i == 0))
            if i == 0:
                self.merge(r, FIRST_COL, r, lay.last)
                self.ws.row_dimensions[r].height = 31.5
            r += 1
        self.put(r, lay.amount, st["sign_text"], h="center")
        self.merge(r, lay.amount, r, lay.last)
        r += 1
        self.put(r, FIRST_COL, "Bên mua hàng", bold=True, h="center")
        self.merge(r, FIRST_COL, r, FIRST_COL + 2)
        self.put(r, lay.amount, "Bên bán hàng", bold=True, h="center")
        self.merge(r, lay.amount, r, lay.last)
        r += 1
        self.put(r, FIRST_COL, "(Ký, ghi rõ họ tên)", italic=True, h="center")
        self.merge(r, FIRST_COL, r, FIRST_COL + 2)
        self.put(r, lay.amount, "(Ký, ghi rõ họ tên)", italic=True, h="center")
        self.merge(r, lay.amount, r, lay.last)
        last_print = r + 4  # chừa chỗ ký
        self.words_formula(words_row, f"{lay.L(lay.net)}{words_row + 1}", last_print + 8)
        return last_print

    def words_formula(self, words_row: int, amount_ref: str, base: int):
        """Chép khối công thức đọc số thành chữ của file gốc vào vùng ẩn (dòng base..base+5)."""
        ws = self.ws
        r0, r1, r2, r3, r4, r5 = range(base, base + 6)
        cols = ["E", "H", "I", "K", "L", "N", "O", "P", "Q", "R", "S", "T"]
        ws[f"B{r0}"] = f"={amount_ref}"
        ws[f"D{r0}"] = f'=RIGHT("000000000000"&ROUND(B{r0},0),12)'
        for i, c in enumerate(cols, start=1):
            ws[f"{c}{r0}"] = i
            ws[f"{c}{r1}"] = f"=VALUE(MID(D{r0},{c}{r0},1))"
        groups = [("E", "H", "I"), ("K", "L", "N"), ("O", "P", "Q"), ("R", "S", "T")]
        for g in groups:
            a, b, c = g
            ws[f"{a}{r2}"] = f"=SUM({a}{r1})"
            ws[f"{b}{r2}"] = f"=SUM({a}{r1}:{b}{r1})"
            ws[f"{c}{r2}"] = f"=SUM({a}{r1}:{c}{r1})"
            digits = '"một","hai","ba","bốn","năm","sáu","bảy","tám","chín"'
            ws[f"{a}{r3}"] = f'=IF({a}{r1}=0,"",CHOOSE({a}{r1},{digits}))'
            ws[f"{b}{r3}"] = (f'=IF({b}{r1}=0,IF(AND({a}{r1}<>0,{c}{r1}<>0),"lẻ",""),'
                              f'CHOOSE({b}{r1},"mười","hai","ba","bốn","năm","sáu","bảy","tám","chín"))')
            ws[f"{c}{r3}"] = (f'=IF({c}{r1}=0,"",CHOOSE({c}{r1},IF({b}{r1}>1,"mốt","một"),"hai","ba","bốn",'
                              f'IF({b}{r1}=0,"năm","lăm"),"sáu","bảy","tám","chín"))')
            ws[f"{a}{r4}"] = f'=IF({a}{r1}=0,"","trăm")'
            ws[f"{b}{r4}"] = f'=IF({b}{r1}=0,"",IF({b}{r1}=1,"","mươi"))'
        ws[f"I{r4}"] = f'=IF(AND(I{r1}=0,I{r2}=0),"",IF(SUM(K{r1}:T{r1})=0,"tỷ","tỷ,"))'
        ws[f"N{r4}"] = (f'=IF(AND(N{r1}=0,N{r2}=0),"",IF(AND(O{r1}=0,P{r1}=0,Q{r1}=0,R{r1}=0,S{r1}=0,T{r1}=0),'
                        f'"triệu","triệu,"))')
        ws[f"Q{r4}"] = f'=IF(AND(Q{r1}=0,Q{r2}=0),"",IF(SUM(R{r1}:T{r1})=0,"ngàn","ngàn,"))'
        ws[f"T{r4}"] = "đồng chẵn."
        parts = "&\" \"&".join(f"{c}{r3}&\" \"&{c}{r4}" for c in cols)
        inner = f'TRIM(IF(B{r0}=0,"không đồng.",{parts}))'
        ws[f"D{r5}"] = f"=UPPER(LEFT({inner},1))&RIGHT({inner},LEN({inner})-1)"
        for r in range(r0, r5 + 1):
            ws.row_dimensions[r].hidden = True
        # ô "Bằng chữ" tham chiếu khối công thức – giống file gốc (=D125)
        ws.cell(words_row, FIRST_COL + 2).value = f"=D{r5}"

    def page_setup(self, last_row: int):
        ws, lay = self.ws, self.lay
        ws.column_dimensions["A"].width = 3.0
        widths = {lay.date: 12.5, lay.inv: 11.0, lay.residual: 10.0, lay.net: 13.86, lay.price: 14.86,
                  lay.amount: 19.29, lay.note: 38.29, lay.site: 31.43}
        for col in lay.cyl.values():
            widths[col] = 10.5 if len(lay.cyl) == 1 else 8.5
        for col, w in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = w
        ws.column_dimensions[get_column_letter(lay.last + 1)].width = 4.43
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = ws.page_margins.right = 0.3
        ws.page_margins.top = ws.page_margins.bottom = 0.5
        ws.print_options.horizontalCentered = True
        ws.print_area = f"A1:{get_column_letter(lay.last + 1)}{last_row}"
        ws.sheet_view.zoomScale = 85

    def write(self):
        self.header()
        self.column_titles()
        totals = [self.block(b) for b in self.st["blocks"]]
        closing = self.totals_and_debts(totals)
        last = self.footer(closing)
        self.page_setup(last)
        # font mặc định cho các ô còn thiếu trong vùng in
        for row in self.ws.iter_rows(min_row=1, max_row=last, min_col=1, max_col=self.lay.last + 1):
            for c in row:
                if c.font is None or c.font.name != FONT_NAME:
                    c.font = _font(bool(c.font and c.font.b), bool(c.font and c.font.i))
        return {"closing_row": closing, "last_row": last}


# ---------------------------------------------------------------- API công khai
def _capacities(session: Session, skus: list[str]) -> dict[str, Decimal]:
    if not skus:
        return {}
    return {p.sku: D(p.capacity_kg) for p in session.scalars(select(Product).where(Product.sku.in_(skus)))
            if p.capacity_kg}


def render_sheet(ws: Worksheet, st: dict, capacities: dict[str, Decimal]) -> dict:
    return SheetWriter(ws, st, capacities).write()


def _unique_sheet_title(wb, title: str) -> str:
    if title not in wb.sheetnames:
        return title
    i = 2
    while f"{title} ({i})" in wb.sheetnames:
        i += 1
    return f"{title} ({i})"


def _is_month_sheet(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"T\d{1,2}\.\d{4}( \(\d+\))?", name.strip()))


def build_workbook_bytes(session: Session, partner_id: int, period: str, *, sign_date: date | None = None) -> tuple[bytes, str]:
    """File Excel riêng chỉ có sheet bảng kê của tháng (để tải về / gửi khách)."""
    st = statements.build_statement(session, partner_id, period, sign_date=sign_date)
    wb = Workbook()
    ws = wb.active
    ws.title = st["sheet_name"]
    render_sheet(ws, st, _capacities(session, st["cylinder_columns"]))
    buf = BytesIO()
    wb.save(buf)
    partner = masterdata.get_partner(session, partner_id)
    safe = "".join(ch for ch in partner.code if ch.isalnum() or ch in "-_") or str(partner_id)
    return buf.getvalue(), f"BangKe_{safe}_{st['sheet_name']}.xlsx"


def export_to_workbook(session: Session, partner_id: int, period: str, *, workbook_path: str | Path | None = None,
                       sign_date: date | None = None, replace: bool = False, hide_old_months: bool = True,
                       backup: bool = True) -> dict:
    """Thêm sheet "T{m}.{yyyy}" vào workbook bảng kê của khách (partners.workbook_path).

    - Sao lưu file gốc thành "<tên>.bak-YYYYmmdd-HHMMSS.xlsx" trước khi ghi.
    - Sheet trùng tên: replace=True thì ghi đè, ngược lại báo lỗi (tránh mất số liệu chị sửa tay).
    - hide_old_months: ẩn các sheet tháng cũ, sheet mới được chọn sẵn khi mở file.
    - Chưa có workbook: tạo file mới trong data/exports/.
    """
    partner = masterdata.get_partner(session, partner_id)
    st = statements.build_statement(session, partner_id, period, sign_date=sign_date)
    path = Path(workbook_path or partner.workbook_path or "")
    created = False
    backup_path = None
    if path and path.suffix.lower() in (".xlsx", ".xlsm") and path.is_file():
        keep_vba = path.suffix.lower() == ".xlsm"
        wb = load_workbook(path, keep_vba=keep_vba)
        if backup:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
            shutil.copy2(path, backup_path)
    else:
        if not path or str(path) in (".", ""):
            settings.export_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(ch for ch in partner.code if ch.isalnum() or ch in "-_") or str(partner_id)
            path = settings.export_dir / f"BangKe_{safe}.xlsx"
            if path.is_file():
                return export_to_workbook(session, partner_id, period, workbook_path=path, sign_date=sign_date,
                                          replace=replace, hide_old_months=hide_old_months, backup=backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        wb.remove(wb.active)
        created = True

    title = st["sheet_name"]
    if title in wb.sheetnames:
        if not replace:
            raise BusinessError(f"Workbook đã có sheet {title}. Chọn 'ghi đè' nếu muốn thay sheet này.",
                                code="sheet_exists")
        idx = wb.sheetnames.index(title)
        wb.remove(wb[title])
        ws = wb.create_sheet(title, idx)
    else:
        ws = wb.create_sheet(title)
    info = render_sheet(ws, st, _capacities(session, st["cylinder_columns"]))

    for other in wb.worksheets:
        other.sheet_view.tabSelected = other is ws
        if hide_old_months and other is not ws and _is_month_sheet(other.title):
            other.sheet_state = "hidden"
    ws.sheet_state = "visible"
    wb.active = wb.worksheets.index(ws)
    try:
        wb.save(path)
    except PermissionError as exc:
        raise BusinessError(f"Không ghi được file {path} – có thể file đang mở trong Excel, đóng file rồi thử lại.",
                            code="file_locked") from exc
    if created and not partner.workbook_path:
        masterdata.update_partner(session, partner_id, workbook_path=str(path))
        session.commit()
    return {"path": str(path), "sheet": title, "backup": str(backup_path) if backup_path else None,
            "created": created, "closing_debt": st["closing_debt"], "rows": info["last_row"]}
