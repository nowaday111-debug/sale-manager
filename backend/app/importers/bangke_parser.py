"""Đọc sheet "BẢNG KÊ CHI TIẾT" theo NHÃN (không cứng tọa độ ô).

Chịu được các khác biệt giữa các file/tháng: lệch cột, có/không có cột "Số hóa đơn", PC45/PC48/PC12/Mini,
số dòng thay đổi, một hay nhiều bếp, có/không có phần công nợ – thanh toán.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from ..money import D
from ..services.masterdata import normalize_text

SKU_LABELS = {"pc45": "PC45", "pc48": "PC48", "pc12": "PC12", "mini": "MINI"}
DEFAULT_KG = {"PC45": Decimal(45), "PC48": Decimal(48), "PC12": Decimal(12), "MINI": Decimal(12)}
_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/+\s*(\d{2,4})")


@dataclass
class SlipRow:
    row: int
    slip_date: date
    vat_invoice_no: str | None
    qty_by_sku: dict[str, Decimal]
    gas_residual_kg: Decimal
    unit_price: Decimal | None
    amount_sheet: Decimal | None          # giá trị Excel đã tính (cached)
    note: str | None
    accessory: str | None = None          # dòng tính tiền dạng chữ: "02 súng mồi", "Lắp 02 bộ bếp CN"
    accessory_qty: Decimal | None = None
    amount_formula: str | None = None     # VD "=E19/45*F19" -> đơn giá theo bình 45kg
    kind: str = "gas"                     # gas | charge | residual_credit


@dataclass
class NoteRow:
    row: int
    note_date: date | None
    text: str


@dataclass
class Block:
    header_note: str | None = None
    site_label: str | None = None
    slips: list[SlipRow] = field(default_factory=list)
    notes: list[NoteRow] = field(default_factory=list)
    total_sheet: Decimal | None = None

    @property
    def site_name(self) -> str | None:
        if not self.site_label:
            return None
        return self.site_label.split("\n")[0].strip() or None

    @property
    def loan_text(self) -> str | None:
        if not self.site_label or "\n" not in self.site_label:
            return None
        return " ".join(self.site_label.split("\n")[1:]).strip() or None


@dataclass
class DebtRow:
    row: int
    label: str
    month: int
    year: int
    amount: Decimal


@dataclass
class PaymentRow:
    row: int
    pay_date: date | None
    label: str
    amount: Decimal
    note: str | None


@dataclass
class ParsedSheet:
    title: str
    period: str                     # YYYY-MM theo tên sheet (T10.2026) hoặc dòng "Từ ngày"
    date_from: date | None = None
    date_to: date | None = None
    seller: dict = field(default_factory=dict)
    buyer: dict = field(default_factory=dict)
    columns: dict = field(default_factory=dict)
    capacity: dict[str, Decimal] = field(default_factory=dict)
    unit_skus: set[str] = field(default_factory=set)   # cột không có trong công thức Thực giao -> bán theo cái/bình
    blocks: list[Block] = field(default_factory=list)
    grand_total_sheet: Decimal | None = None
    prior_debts: list[DebtRow] = field(default_factory=list)
    payments: list[PaymentRow] = field(default_factory=list)
    total_debt_sheet: Decimal | None = None
    paid_sheet: Decimal | None = None
    closing_sheet: Decimal | None = None
    payable_sheet: Decimal | None = None      # "Số tiền bên mua phải thanh toán"
    warnings: list[str] = field(default_factory=list)

    @property
    def has_debt_section(self) -> bool:
        return self.closing_sheet is not None


# ---------------------------------------------------------------- tiện ích ô
def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return D(v)
    s = str(v).strip().replace(" ", "")
    if not s or s.startswith("="):
        return None
    s = s.replace(".", "").replace(",", ".") if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s) else s.replace(",", ".")
    try:
        return D(s)
    except ValueError:
        return None


def _as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        m = _DATE_RE.match(v)
        if m:
            d, mo, y = (int(x) for x in m.groups())
            y = y + 2000 if y < 100 else y
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


def _parse_period_text(text: str) -> tuple[date | None, date | None]:
    found = re.findall(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})", text)
    out = []
    for d, m, y in found[:2]:
        d, m, y = int(d), int(m), int(y)
        for day in (d, d - 1, d - 2, d - 3):          # "31/11/2026" -> 30/11
            try:
                out.append(date(y, m, day))
                break
            except ValueError:
                continue
    return (out[0] if out else None), (out[1] if len(out) > 1 else None)


def _sheet_period(title: str, date_from: date | None) -> str | None:
    m = re.search(r"T\s*(\d{1,2})(?:\s*\+\s*T?\s*(\d{1,2}))?\s*[._ ]\s*(\d{4})", title, re.IGNORECASE)
    if m:
        month = int(m.group(2) or m.group(1))
        return f"{int(m.group(3)):04d}-{month:02d}"
    if date_from:
        return f"{date_from.year:04d}-{date_from.month:02d}"
    return None


def _capacity_from_formula(formula: str) -> dict[str, Decimal]:
    """'=C19*48+D19*12-E19' -> {'C': 48, 'D': 12}"""
    out = {}
    for col, num in re.findall(r"([A-Z]{1,3})\d+\s*\*\s*(\d+(?:\.\d+)?)", formula.upper()):
        out[col] = D(num)
    return out


def _merged_span(ws, row: int, col: int) -> tuple[int, int]:
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
            return rng.min_col, rng.max_col
    return col, col


# ---------------------------------------------------------------- phân tích sheet
def parse_sheet(ws_values, ws_formulas=None) -> ParsedSheet | None:
    """ws_values: sheet mở với data_only=True; ws_formulas: cùng sheet mở bình thường (để đọc công thức)."""
    from openpyxl.utils import get_column_letter

    ws = ws_values
    header = None
    period_text = None
    labels: dict[str, list[tuple[int, int]]] = {}
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 40)):
        for c in row:
            t = normalize_text(_text(c.value))
            if not t:
                continue
            if t.startswith("ngay thang") and header is None:
                header = (c.row, c.column)
            if t.startswith("tu ngay"):
                period_text = _text(c.value)
            for key in ("don vi ban hang", "don vi mua hang", "dia chi", "ma so thue", "dien thoai"):
                if t.startswith(key):
                    labels.setdefault(key, []).append((c.row, c.column))
    if header is None:
        return None
    date_from, date_to = _parse_period_text(period_text or "")
    period = _sheet_period(ws.title, date_from)
    if period is None:
        return None
    ps = ParsedSheet(title=ws.title, period=period, date_from=date_from, date_to=date_to)

    def value_right(r: int, c: int):
        for cc in range(c + 1, c + 8):
            v = ws.cell(r, cc).value
            if v not in (None, ""):
                return v
        return None

    hdr_row, first_col = header
    buyer_row = labels.get("don vi mua hang", [(None, None)])[0][0]
    for key, positions in labels.items():
        for r, c in positions:
            if r >= hdr_row:
                continue
            target = ps.buyer if buyer_row and r >= buyer_row else ps.seller
            name = {"don vi ban hang": "name", "don vi mua hang": "name", "dia chi": "address",
                    "ma so thue": "tax_code", "dien thoai": "phone"}[key]
            v = value_right(r, c)
            if v is not None and name not in target:
                target[name] = _text(v)
    # dòng tiêu đề người bán (A1..A5)
    top = [_text(ws.cell(r, first_col).value) for r in range(1, 6)]
    ps.seller.setdefault("parent_name", top[0] or None)

    # cột theo nhãn
    cols: dict[str, int] = {"date": first_col}
    sku_cols: dict[int, str] = {}
    note_span = None
    for c in range(first_col, first_col + 15):
        t = normalize_text(_text(ws.cell(hdr_row, c).value))
        if not t:
            continue
        if t.startswith("so hoa don"):
            cols["invoice"] = c
        elif t.startswith("loai binh"):
            lo, hi = _merged_span(ws, hdr_row, c)
            for cc in range(lo, hi + 1):
                lab = normalize_text(_text(ws.cell(hdr_row + 1, cc).value)).replace(" ", "")
                if lab in SKU_LABELS:
                    sku_cols[cc] = SKU_LABELS[lab]
            cols["qty_from"], cols["qty_to"] = lo, hi
        elif t.startswith("gas du"):
            cols["residual"] = c
        elif t.startswith("thuc giao"):
            cols["net"] = c
        elif t.startswith("don gia"):
            cols["price"] = c
        elif t.startswith("thanh tien"):
            cols["amount"] = c
        elif t.startswith("ghi chu"):
            note_span = _merged_span(ws, hdr_row, c)
            cols["note"] = note_span[0]
            if note_span[1] > note_span[0]:
                cols["site"] = note_span[1]
    missing = [k for k in ("qty_from", "residual", "price", "amount") if k not in cols]
    if missing or not sku_cols:
        ps.warnings.append(f"Sheet {ws.title}: không nhận ra cột {missing or 'loại bình'}")
        return ps
    ps.columns = {**cols, "sku_cols": {get_column_letter(k): v for k, v in sku_cols.items()}}

    def cell(r, key):
        c = cols.get(key)
        return ws.cell(r, c).value if c else None

    # sức chứa kg theo công thức cột Thực giao
    cap_by_col: dict[str, Decimal] = {}
    if ws_formulas is not None and "net" in cols:
        # mỗi khối có thể dùng công thức khác nhau (=B19*48-E19, =C27*45-E27): gộp tất cả
        for r in range(hdr_row + 2, min(ws.max_row, hdr_row + 150)):
            f = ws_formulas.cell(r, cols["net"]).value
            if isinstance(f, str) and f.startswith("="):
                for col, kg in _capacity_from_formula(f).items():
                    cap_by_col.setdefault(col, kg)
    for c, sku in sku_cols.items():
        letter = get_column_letter(c)
        ps.capacity[sku] = cap_by_col.get(letter, DEFAULT_KG.get(sku, Decimal(0)))
        if cap_by_col and letter not in cap_by_col:
            ps.unit_skus.add(sku)

    # ---- thân bảng
    END_KEYS = ("tong cong", "cong no", "bang chu", "so tien ben mua", "thanh toan thieu")
    qty_cols = list(range(cols["qty_from"], cols["qty_to"] + 1))
    block = Block()
    last_date: date | None = None
    r = hdr_row + 2
    end_row = ws.max_row
    while r <= ws.max_row:
        first = cell(r, "date")
        t = normalize_text(_text(first))
        if t and any(t.startswith(k) for k in END_KEYS):
            end_row = r
            break
        site_v = cell(r, "site")
        if isinstance(site_v, str) and site_v.strip() and not normalize_text(site_v).startswith(("tong", "tông")):
            if not block.site_label:
                block.site_label = site_v.strip()
        if t.startswith("tong") and not _as_date(first):
            block.total_sheet = _num(cell(r, "amount"))
            if block.slips or block.notes or block.site_label:
                ps.blocks.append(block)
            block = Block()
            r += 1
            continue
        d = _as_date(first)
        if isinstance(first, str) and (d or t):
            # dòng diễn giải đầu khối: "22/7/2026 - Đầu tư lắp đặt ..." hoặc "17/3//2025" + chữ ở cột bên cạnh
            rest = " ".join(_text(ws.cell(r, c).value) for c in range(first_col + 1, cols["amount"] + 1)
                            if isinstance(ws.cell(r, c).value, str) and _text(ws.cell(r, c).value))
            text = f"{first.strip()} - {rest}" if (d and rest) else first.strip()
            block.header_note = f"{block.header_note}\n{text}" if block.header_note else text
            r += 1
            continue
        qty_nums = {sku: _num(ws.cell(r, c).value) for c, sku in sku_cols.items()
                    if _num(ws.cell(r, c).value) not in (None, Decimal(0))}
        qty_text = next((_text(ws.cell(r, c).value) for c in qty_cols
                         if isinstance(ws.cell(r, c).value, str) and _text(ws.cell(r, c).value)
                         and not _text(ws.cell(r, c).value).startswith("=")), None)
        residual = _num(cell(r, "residual")) or Decimal(0)
        amount = _num(cell(r, "amount"))
        price = _num(cell(r, "price"))
        note = _text(cell(r, "note")) or None
        formula = None
        if ws_formulas is not None:
            fv = ws_formulas.cell(r, cols["amount"]).value
            formula = fv if isinstance(fv, str) and fv.startswith("=") else None
        row_date = d or last_date or ps.date_from
        inv = cell(r, "invoice")
        inv = str(int(inv)) if isinstance(inv, (int, float)) else (_text(inv) or None)
        if qty_nums:
            block.slips.append(SlipRow(r, row_date, inv, qty_nums, residual, price, amount, note,
                                       amount_formula=formula))
            last_date = row_date
        elif residual and amount and amount < 0:
            block.slips.append(SlipRow(r, row_date, inv, {}, residual, price, amount, note,
                                       amount_formula=formula, kind="residual_credit"))
        elif qty_text and amount:
            m = re.search(r"(\d+)", qty_text)
            q = D(m.group(1)) if m else Decimal(1)
            if not (price and q * price == amount):
                q, price = Decimal(1), (price if price and price == amount else amount)
            block.slips.append(SlipRow(r, row_date, inv, {}, Decimal(0), price, amount, note,
                                       accessory=qty_text, accessory_qty=q, amount_formula=formula, kind="charge"))
        elif qty_text:
            block.notes.append(NoteRow(r, row_date, qty_text))
        elif note and (d or last_date):
            block.notes.append(NoteRow(r, row_date, note))
        r += 1
    if block.slips or block.notes:
        ps.blocks.append(block)

    # ---- phần tổng cộng / công nợ / thanh toán
    sheet_year, sheet_month = (int(x) for x in ps.period.split("-"))
    for rr in range(end_row, min(ws.max_row, end_row + 40) + 1):
        raw = _text(cell(rr, "date"))
        t = normalize_text(raw)
        if not t:
            continue
        amount = _num(cell(rr, "amount"))
        if t.startswith("bang chu"):
            break
        if t.startswith("tong cong no"):
            ps.total_debt_sheet = amount
        elif t.startswith("tong cong"):
            ps.grand_total_sheet = amount
        elif t.startswith("cong no con lai"):
            ps.closing_sheet = amount
        elif t.startswith("tong da thanh toan"):
            ps.paid_sheet = amount
        elif t.startswith("thanh toan thieu") or t.startswith("cong no thang"):
            m = re.search(r"(\d{1,2})\s*/\s*(\d{4})", raw)
            if m and amount is not None:
                month, year = int(m.group(1)), int(m.group(2))
                # nhãn năm hay bị sai (copy sheet cũ): chọn năm gần nhất mà tháng đó vẫn trước kỳ của sheet
                year = sheet_year if (sheet_year * 12 + month) < (sheet_year * 12 + sheet_month) else sheet_year - 1
                ps.prior_debts.append(DebtRow(rr, raw, month, year, amount))
        elif "thanh toan" in t and t.startswith("ngay"):
            if amount is not None:
                ps.payments.append(PaymentRow(rr, _as_date(re.sub(r"(?i)ng[aà]y\s*", "", raw)), raw, amount,
                                              _text(cell(rr, "note")) or None))
        elif t.startswith("so tien ben mua"):
            for c in range(cols["date"] + 1, cols["date"] + 12):
                v = _num(ws.cell(rr, c).value)
                if v is not None:
                    ps.payable_sheet = v
                    break
    if ps.grand_total_sheet is None and len(ps.blocks) == 1:
        ps.grand_total_sheet = ps.blocks[0].total_sheet
    return ps


def parse_workbook(path) -> list[ParsedSheet]:
    import warnings

    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb_v = openpyxl.load_workbook(path, data_only=True)
        wb_f = openpyxl.load_workbook(path, data_only=False)
    out = []
    for ws in wb_v.worksheets:
        parsed = parse_sheet(ws, wb_f[ws.title])
        if parsed is not None and parsed.blocks:
            out.append(parsed)
    out.sort(key=lambda p: (p.period, p.title))
    return out
