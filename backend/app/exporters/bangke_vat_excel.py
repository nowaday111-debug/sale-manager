"""Mẫu "BẢNG KÊ CHI TIẾT" tách thuế GTGT – theo file Welstory (Cty CP Metro Toàn Cầu, 11/2026).

Dùng cho khách yêu cầu ghi rõ thuế GTGT (partners.statement_template = VAT_DETAIL).

Bố cục (bắt đầu cột A như file gốc):
    A Ngày tháng | B.. Loại bình (mỗi loại 1 cột) | Gas dư | Thực giao | Đơn giá chưa thuế (đ/kg) |
    Thành tiền chưa thuế | Tiền thuế GTGT 8% | Tổng cộng thanh toán | Ghi chú

- Dòng đầu bảng: tổng vỏ đang mượn đầu kỳ (cột Ghi chú). Mỗi phiếu 1 dòng, công thức như file gốc:
  Thực giao =+B18*45-C18, Thành tiền chưa thuế =+D18*E18, Thuế =F18*8%, Tổng cộng =F18+G18 (giữ số lẻ).
- Dòng "Tổng" =SUM; "Số tiền bên mua phải thanh toán" = ô Tổng cộng thanh toán; "Bằng chữ" = khối công thức
  đọc số (dòng ẩn) – sửa số trong Excel thì chữ tự đổi.
- Kỳ có thể chốt theo ngày (VD 26/09 → 25/10) – lấy từ partners.statement_cut_day.
"""
from __future__ import annotations

from decimal import Decimal

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

from ..models import PriceBasis, VatMode
from ..money import ZERO, D
from .bangke_excel import BORDER, FILL_HEAD, FILL_TOTAL, FONT_NAME, NUM, SheetWriter, _font, _num

FIRST = 1  # cột A
KG1 = "#,##0.0"
DATE_FMT = "dd/mm/yyyy"


def _rate_text(rate: Decimal | None) -> str:
    if rate is None:
        return ""
    pct = D(rate) * 100
    pct = pct.quantize(Decimal(1)) if pct == pct.to_integral_value() else pct.normalize()
    return f"{pct}%"


class VatLayout:
    def __init__(self, skus: list[str]):
        self.skus = skus or ["PC45"]
        c = FIRST
        self.date = c
        self.cyl = {sku: c + 1 + i for i, sku in enumerate(self.skus)}
        nxt = c + 1 + len(self.skus)
        (self.residual, self.net, self.price, self.before, self.vat, self.amount, self.note) = range(nxt, nxt + 7)
        self.last = self.note

    @staticmethod
    def L(col: int) -> str:  # noqa: N802
        return get_column_letter(col)

    @property
    def first_cyl(self) -> int:
        return self.cyl[self.skus[0]]

    @property
    def last_cyl(self) -> int:
        return self.cyl[self.skus[-1]]


class VatSheetWriter(SheetWriter):
    def __init__(self, ws, st: dict, capacities: dict[str, Decimal]):
        super().__init__(ws, st, capacities)
        self.lay = VatLayout(st["cylinder_columns"])
        rates = st.get("vat_rates") or []
        self.rate = rates[0] if len(rates) == 1 else (rates[0] if rates else Decimal("0.08"))
        self.multi_site = len([b for b in st["blocks"] if any(r["kind"] == "slip" for r in b["rows"])]) > 1

    def table_row(self, row, *, fill=None, bold=False):
        for col in range(FIRST, self.lay.last + 1):
            cell = self.ws.cell(row=row, column=col)
            cell.border = BORDER
            if cell.font is None or cell.font.name != FONT_NAME:
                cell.font = _font(bold)
            if fill:
                cell.fill = fill
            if cell.alignment.horizontal is None:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # ------------------------------------------------------------ đầu trang
    def header(self):
        s, b, lay = self.st["seller"], self.st["buyer"], self.lay
        lines = [
            (s.get("name"), True),
            (f"Điện thoại : {s['phones']}" if s.get("phones") and "điện thoại" not in s["phones"].lower()
             and not s["phones"].lower().startswith("đt") else s.get("phones"), False),
            (f"Số Tài Khoản:  {s['bank_account']}" if s.get("bank_account") else None, False),
            (s.get("bank_name"), False),
        ]
        for i, (text, bold) in enumerate(lines, start=1):
            if text:
                self.put(i, FIRST, text, bold=bold, h="left")
        self.put(6, FIRST, "BẢNG KÊ CHI TIẾT ", bold=True, size=16, h="center")
        self.merge(6, FIRST, 6, lay.last)
        self.ws.row_dimensions[6].height = 26.25
        self.put(7, FIRST, self.st["period_text"], bold=True, italic=True, h="center")
        self.merge(7, FIRST, 7, lay.last)
        self.ws.row_dimensions[7].height = 23.25
        info = [
            (8, f"Đơn vị bán hàng : {s.get('name') or ''}"),
            (9, f"Địa chỉ: {s.get('address') or ''}"),
            (10, f"Mã số thuế: {s.get('tax_code') or ''}"),
            (11, f"Đơn vị mua hàng : {b.get('name') or ''}"),
            (12, f"Địa chỉ:  {b.get('address') or ''}"),
            (13, f"Mã số thuế: {b.get('tax_code') or ''}"),
        ]
        for r, text in info:
            self.put(r, FIRST, text, h="left")
            self.merge(r, FIRST, r, lay.last)
            self.ws.row_dimensions[r].height = 23.25 if r in (8, 11) else None
        self.row = 15

    def column_titles(self):
        lay, r = self.lay, self.row
        all_kg = all(row.get("price_basis") in (None, PriceBasis.KG)
                     for blk in self.st["blocks"] for row in blk["rows"] if row["kind"] == "slip")
        titles = [
            (lay.date, "Ngày tháng"), (lay.residual, "Gas dư"), (lay.net, "Thực giao"),
            (lay.price, "Đơn giá chưa thuế (đ/kg)" if all_kg else "Đơn giá chưa thuế (đ/bình)"),
            (lay.before, "Thành tiền chưa thuế"), (lay.vat, f"Tiền thuế GTGT {_rate_text(self.rate)}".strip()),
            (lay.amount, "Tổng cộng thanh toán"), (lay.note, "Ghi chú"),
        ]
        for col, text in titles:
            self.put(r, col, text, bold=True, h="center", wrap=True, border=True, fill=FILL_HEAD)
            self.merge(r, col, r + 1, col)
        self.put(r, lay.first_cyl, "Loại bình", bold=True, h="center", border=True, fill=FILL_HEAD)
        self.merge(r, lay.first_cyl, r, lay.last_cyl)
        for sku, col in lay.cyl.items():
            self.put(r + 1, col, _sku_label(sku), bold=True, h="center", border=True, fill=FILL_HEAD)
        self.table_row(r, fill=FILL_HEAD, bold=True)
        self.table_row(r + 1, fill=FILL_HEAD, bold=True)
        self.ws.row_dimensions[r].height = 33.0
        # dòng tổng vỏ đang mượn đầu kỳ (ô Ghi chú)
        r += 2
        self.table_row(r)
        self.put(r, lay.note, self.st.get("opening_loans_text") or None, bold=True, h="center", wrap=True,
                 border=True, fill=FILL_TOTAL)
        self.ws.row_dimensions[r].height = 18.0
        self.ws.print_title_rows = f"{self.row}:{self.row + 1}"
        self.row = r + 1

    # ------------------------------------------------------------ dòng phiếu
    def vat_slip_row(self, r: int, row: dict):
        lay = self.lay
        L = lay.L  # noqa: N806
        self.table_row(r)
        self.put(r, lay.date, row["date"], fmt=DATE_FMT, h="center", border=True)
        for sku, q in row["qty_by_sku"].items():
            if sku in lay.cyl:
                self.put(r, lay.cyl[sku], _num(q), fmt="#,##0", h="center", border=True)
        notes = [row.get("note")]
        if self.multi_site and row.get("site_name"):
            notes.insert(0, row["site_name"])
        excluded = row.get("vat_mode") == VatMode.EXCLUDED and row.get("amount_before_tax") is not None
        before = D(row["amount_before_tax"]) if row.get("amount_before_tax") is not None else None
        vat = D(row["vat_amount"]) if row.get("vat_amount") is not None else None
        net_ok = False
        if row["qty_by_sku"]:
            self.put(r, lay.residual, _num(row["gas_residual_kg"]), fmt=KG1, h="center", border=True)
            formula = self._net_formula(r, row)
            if formula:
                formula = "=+" + formula[1:]
                net_ok = True
            self.put(r, lay.net, formula or _num(row["net_kg"]), fmt=KG1, h="center", border=True)
            if row.get("description"):
                notes.append(row["description"])
        elif row.get("description"):
            self.put(r, lay.first_cyl, row["description"], h="center", wrap=True, border=True)
            self.merge(r, lay.first_cyl, r, lay.net)
        price = row.get("unit_price")
        if excluded:
            self.put(r, lay.price, _num(price), fmt=NUM, h="center", border=True)
            use_formula = (net_ok and price is not None and row.get("price_basis") == PriceBasis.KG
                           and D(row["net_kg"]) * D(price) == before)
            self.put(r, lay.before, f"=+{L(lay.net)}{r}*{L(lay.price)}{r}" if use_formula else _num(before),
                     fmt=NUM, h="center", border=True)
            self.put(r, lay.vat, f"={L(lay.before)}{r}*{_rate_text(row.get('vat_rate') or self.rate)}",
                     fmt=NUM, h="center", border=True)
            ded = self._deduction(row)
            total = f"={L(lay.before)}{r}+{L(lay.vat)}{r}" + (f"-({_num(ded)})" if ded else "")
            self.put(r, lay.amount, total, fmt=NUM, h="right", border=True)
        else:
            # phiếu tính giá đã gồm VAT: tách ngược ra tiền chưa thuế / thuế (ghi giá trị)
            amount = D(row["amount"])
            b = before if before is not None else amount
            unit_ex = (b / D(row["net_kg"])) if row.get("net_kg") else None
            self.put(r, lay.price, _num(unit_ex.quantize(Decimal(1))) if unit_ex is not None else None, fmt=NUM,
                     h="center", border=True)
            self.put(r, lay.before, _num(b), fmt=NUM, h="center", border=True)
            self.put(r, lay.vat, _num(vat if vat is not None else ZERO), fmt=NUM, h="center", border=True)
            self.put(r, lay.amount, _num(amount), fmt=NUM, h="right", border=True)
        text = "; ".join(n for n in notes if n)
        self.put(r, lay.note, text or None, h="center", wrap=True, border=True)
        self.ws.row_dimensions[r].height = 26.25

    def _deduction(self, row: dict) -> Decimal:
        """Khoản trừ khác của phiếu (thành tiền = chưa thuế + thuế − khoản trừ)."""
        if row.get("amount_before_tax") is None or row.get("vat_amount") is None:
            return ZERO
        return D(row.get("other_deduction") or ZERO)

    def vat_note_row(self, r: int, row: dict):
        self.table_row(r)
        self.put(r, self.lay.date, row["date"], fmt=DATE_FMT, h="center", border=True)
        self.put(r, self.lay.note, row["text"], h="center", wrap=True, border=True)
        self.ws.row_dimensions[r].height = 26.25

    def body(self) -> int:
        lay = self.lay
        rows = [r for b in self.st["blocks"] for r in b["rows"]]
        rows.sort(key=lambda r: (r["date"], 0 if r["kind"] == "slip" else 1))
        start = self.row - 1  # SUM từ dòng vỏ đầu kỳ như file gốc
        for row in rows:
            if row["kind"] == "slip":
                self.vat_slip_row(self.row, row)
            else:
                self.vat_note_row(self.row, row)
            self.row += 1
        # 1 dòng trống để ghi tay (nằm trong vùng SUM)
        self.table_row(self.row)
        self.ws.cell(self.row, lay.date).number_format = DATE_FMT
        self.row += 1
        end = self.row - 1
        total = self.row
        L = lay.L  # noqa: N806
        self.table_row(total, fill=FILL_TOTAL, bold=True)
        self.put(total, lay.date, "Tổng", bold=True, h="center", border=True, fill=FILL_TOTAL)
        for col in [*lay.cyl.values(), lay.residual, lay.net, lay.before, lay.vat, lay.amount]:
            fmt = KG1 if col in (lay.residual, lay.net, *lay.cyl.values()) else NUM
            self.put(total, col, f"=SUM({L(col)}{start}:{L(col)}{end})", bold=True, fmt=fmt, h="center",
                     border=True, fill=FILL_TOTAL)
        self.put(total, lay.price, "X", bold=True, h="center", border=True, fill=FILL_TOTAL)
        self.put(total, lay.note, self.st.get("closing_loans_text") or None, bold=True, h="center", wrap=True,
                 border=True, fill=FILL_TOTAL)
        self.ws.row_dimensions[total].height = 26.25
        self.row = total + 2
        return total

    def vat_footer(self, total_row: int) -> int:
        lay, st = self.lay, self.st
        L = lay.L  # noqa: N806
        r = self.row
        words_row = r
        self.put(r, FIRST, "Bằng chữ: ", bold=True, h="left")
        self.put(r, FIRST + 1, st.get("period_words") or "", bold=True, size=13, h="left")
        r += 1
        amount_cell_col = FIRST + 3 if lay.net > FIRST + 3 else lay.net
        self.put(r, FIRST, "- Số tiền bên mua phải thanh toán ", h="left")
        self.put(r, amount_cell_col, f"={L(lay.amount)}{total_row}", bold=True, fmt=NUM, h="center")
        self.merge(r, amount_cell_col, r, lay.vat)
        self.put(r, lay.amount, "đồng", bold=True, h="left")
        amount_ref = f"{L(amount_cell_col)}{r}"
        r += 1
        contact = st["seller"].get("contact_line")
        lines = [
            "- Bên mua kiểm tra, đối chiếu, xác nhận công nợ cho bên bán. Sau 04 ngày kể từ khi nhận được bản này "
            "bên mua không có phản hồi thì số liệu trên coi là đúng",
            f"- Mọi ý kiến của quý khách liên hệ trực tiếp {contact} để được giải đáp" if contact else None,
            "- Trân trọng cảm ơn.",
        ]
        for i, text in enumerate(t for t in lines if t):
            self.put(r, FIRST, text, h="left", wrap=(i == 0))
            if i == 0:
                self.merge(r, FIRST, r, lay.last)
                self.ws.row_dimensions[r].height = 31.5
            r += 1
        self.put(r, lay.amount, st["sign_text"], h="center")
        self.merge(r, lay.amount, r, lay.last)
        r += 1
        self.put(r, FIRST, "ĐẠI DIỆN BÊN MUA", bold=True, h="center")
        self.merge(r, FIRST, r, FIRST + 2)
        self.put(r, lay.amount, "ĐẠI DIỆN BÊN BÁN", bold=True, h="center")
        self.merge(r, lay.amount, r, lay.last)
        r += 1
        self.put(r, FIRST, "(Ký, ghi rõ họ tên)", italic=True, h="center")
        self.merge(r, FIRST, r, FIRST + 2)
        self.put(r, lay.amount, "(Ký, ghi rõ họ tên)", italic=True, h="center")
        self.merge(r, lay.amount, r, lay.last)
        last_print = r + 5
        self.words_formula(words_row, amount_ref, last_print + 8, target_col=FIRST + 1)
        return last_print

    def vat_page_setup(self, last_row: int):
        ws, lay = self.ws, self.lay
        widths = {lay.date: 12.14, lay.residual: 9.0, lay.net: 13.0, lay.price: 13.0, lay.before: 15.0,
                  lay.vat: 14.86, lay.amount: 15.0, lay.note: 32.43}
        for col in lay.cyl.values():
            widths[col] = 13.0 if len(lay.cyl) == 1 else 9.0
        for col, w in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = w
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = ws.page_margins.right = 0.3
        ws.page_margins.top = ws.page_margins.bottom = 0.5
        ws.print_options.horizontalCentered = True
        ws.print_area = f"A1:{get_column_letter(lay.last)}{last_row}"
        ws.sheet_view.zoomScale = 85

    def write(self):
        self.header()
        self.column_titles()
        total = self.body()
        last = self.vat_footer(total)
        self.vat_page_setup(last)
        for row in self.ws.iter_rows(min_row=1, max_row=last, min_col=1, max_col=self.lay.last):
            for c in row:
                if c.font is None or c.font.name != FONT_NAME:
                    c.font = _font(bool(c.font and c.font.b), bool(c.font and c.font.i))
        return {"closing_row": total, "last_row": last}


def _sku_label(sku: str) -> str:
    """PC45 -> 'PC 45' như file gốc."""
    import re
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", sku)
    return f"{m.group(1)} {m.group(2)}" if m else sku
