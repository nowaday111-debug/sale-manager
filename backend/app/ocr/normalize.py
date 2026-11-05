"""Chuẩn hóa kết quả AI đọc phiếu + kiểm chéo toán học.

AI chỉ chép NGUYÊN VĂN số trên giấy; phần này tự quyết định:
- số viết tắt nghìn đồng ("24024" = 24.024.000 đ),
- đơn giá theo kg hay theo bình,
- gas dư đúng là bao nhiêu kg (VD viết "5,6" nhưng tiền trừ khớp 56 kg),
rồi so lại với thành tiền / tổng cộng viết trên phiếu. Chỗ nào lệch thì gắn cờ để người duyệt kiểm tra.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..models import AssetDirection, AssetType
from ..money import ZERO, D, round_money

CAPACITY = {"PC45": Decimal(45), "PC48": Decimal(48), "PC12": Decimal(12), "MINI": None}
KG_PRICE_RANGE = (Decimal(10_000), Decimal(150_000))
UNIT_PRICE_RANGE = (Decimal(120_000), Decimal(8_000_000))
_EMPTY = {"", "x", "-", "--", "null", "none", "/", "o"}


@dataclass
class Flag:
    level: str      # "info" | "warning"
    field: str
    message: str


@dataclass
class GasLine:
    sku: str | None
    qty: Decimal | None
    unit_price: Decimal | None          # VNĐ
    price_basis: str | None             # KG | UNIT
    amount_written: Decimal | None      # VNĐ
    amount_calc: Decimal | None
    text: str = ""


@dataclass
class ExtraLine:
    name: str
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    text: str = ""


@dataclass
class AssetLine:
    asset_type: str
    direction: str
    qty: Decimal | None
    sku: str | None
    text: str


@dataclass
class NormalizedSlip:
    is_slip: bool
    slip_no: str | None = None
    customer_text: str = ""
    address_text: str = ""
    slip_date: date | None = None
    gas_lines: list[GasLine] = field(default_factory=list)
    extra_lines: list[ExtraLine] = field(default_factory=list)
    residual_kg: Decimal = ZERO
    residual_kg_written: Decimal | None = None
    residual_amount_written: Decimal | None = None
    deduction: Decimal = ZERO
    deduction_note: str | None = None
    total_written: Decimal | None = None
    total_calc: Decimal | None = None
    assets: list[AssetLine] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    staff: list[str] = field(default_factory=list)
    customer_signed: bool = False
    customer_note: str | None = None
    confidence: float = 0.0
    flags: list[Flag] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.8 or any(f.level == "warning" for f in self.flags)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["needs_review"] = self.needs_review
        return d


# ---------------------------------------------------------------- đọc số
def parse_number(raw) -> Decimal | None:
    """'38,500' -> 38500 ; '5,6' -> 5.6 ; '24.024' -> 24024 ; '-625' -> -625 ; 'x' -> None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        return D(raw)
    text = str(raw).strip().lower()
    if text in _EMPTY:
        return None
    text = text.replace("kg", "").replace("đ", "").replace("vnd", "").replace(" ", "")
    text = text.rstrip("k")
    negative = text.startswith("-") or text.startswith("(")
    text = re.sub(r"[^0-9.,]", "", text)
    if not re.search(r"\d", text):
        return None
    parts = re.split(r"[.,]", text)
    if len(parts) == 1:
        value = D(parts[0])
    elif len(parts[-1]) == 3:
        value = D("".join(parts))                          # dấu phân cách nghìn
    else:
        value = D("".join(parts[:-1]) + "." + parts[-1])   # dấu thập phân
    return -value if negative else value


def _close(a: Decimal, b: Decimal, rel: Decimal = Decimal("0.02"), abs_tol: Decimal = Decimal(1000)) -> bool:
    return abs(a - b) <= max(abs_tol, abs(b) * rel)


def scale_to(raw_value: Decimal | None, expected: Decimal | None) -> tuple[Decimal | None, bool]:
    """Chọn raw hoặc raw×1000 cho khớp với số kỳ vọng. Trả (giá trị VNĐ, có khớp không)."""
    if raw_value is None:
        return None, False
    v = abs(raw_value)
    if expected is not None and expected != 0:
        for cand in (v, v * 1000):
            if _close(cand, abs(expected)):
                return cand, True
    return (v * 1000 if v < 100_000 else v), False


def product_sku(text) -> str | None:
    if not text:
        return None
    t = re.sub(r"[^A-Z0-9]", "", str(text).upper())
    for sku in ("PC45", "PC48", "PC12"):
        if sku in t:
            return sku
    if "MINI" in t:
        return "MINI"
    return None


def _in(v: Decimal, rng: tuple[Decimal, Decimal]) -> bool:
    return rng[0] <= v <= rng[1]


def price_candidates(raw_price: Decimal, sku: str | None) -> list[tuple[Decimal, str]]:
    out = []
    for p in (raw_price, raw_price * 1000):
        if _in(p, KG_PRICE_RANGE) and CAPACITY.get(sku or ""):
            out.append((p, "KG"))
        if _in(p, UNIT_PRICE_RANGE):
            out.append((p, "UNIT"))
    return out


def _line_amount(qty: Decimal, sku: str | None, price: Decimal, basis: str) -> Decimal | None:
    if basis == "KG":
        cap = CAPACITY.get(sku or "")
        return qty * cap * price if cap else None
    return qty * price


def parse_date(raw: dict | None, today: date) -> tuple[date | None, str | None]:
    if not isinstance(raw, dict):
        return None, "Không đọc được ngày trên phiếu"
    try:
        day, month = int(raw.get("day")), int(raw.get("month"))
    except (TypeError, ValueError):
        return None, "Không đọc được ngày/tháng trên phiếu"
    digits = re.sub(r"\D", "", str(raw.get("year_text") or ""))
    if len(digits) >= 4:
        year = int(digits[:4])
    elif len(digits) == 1:
        year = 2020 + int(digits)
    elif digits.startswith("202") and len(digits) == 3:
        return None, "Năm trên phiếu thiếu chữ số cuối"
    elif len(digits) == 2:
        year = 2000 + int(digits)
    else:
        year = today.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None, f"Ngày không hợp lệ: {day}/{month}/{year}"
    if d > today + timedelta(days=3):
        return d, f"Ngày {d:%d/%m/%Y} nằm ở tương lai – kiểm tra lại năm"
    if d < today - timedelta(days=400):
        return d, f"Ngày {d:%d/%m/%Y} đã cũ hơn 1 năm – kiểm tra lại"
    return d, None


# ---------------------------------------------------------------- chuẩn hóa chính
def normalize(raw: dict, *, today: date | None = None) -> NormalizedSlip:
    today = today or date.today()
    doc_type = str(raw.get("document_type") or "").upper()
    if doc_type and doc_type != "HOA_DON_BAN_LE":
        return NormalizedSlip(is_slip=False, confidence=float(raw.get("confidence") or 0),
                              flags=[Flag("warning", "document_type", "Ảnh không phải phiếu Hóa đơn bán lẻ")])
    ns = NormalizedSlip(is_slip=True)
    ns.confidence = float(raw.get("confidence") or 0)
    seri = re.sub(r"\D", "", str(raw.get("slip_no") or ""))
    ns.slip_no = seri.zfill(7) if 5 <= len(seri) <= 7 else (seri or None)
    if not ns.slip_no:
        ns.flags.append(Flag("warning", "slip_no", "Không đọc được số seri phiếu"))
    elif len(seri) not in (6, 7):
        ns.flags.append(Flag("warning", "slip_no", f"Số seri '{seri}' không đủ 7 chữ số"))
    ns.customer_text = str(raw.get("customer") or "").strip()
    ns.address_text = str(raw.get("address") or "").strip()
    ns.slip_date, msg = parse_date(raw.get("date"), today)
    if msg:
        ns.flags.append(Flag("warning", "date", msg))
    ns.staff = [str(s).strip() for s in (raw.get("staff") or []) if str(s).strip()]
    ns.customer_signed = bool(raw.get("customer_signed"))
    ns.customer_note = (str(raw.get("customer_note")).strip() or None) if raw.get("customer_note") else None
    for u in raw.get("unreadable") or []:
        ns.flags.append(Flag("warning", "unreadable", f"AI không đọc được: {u}"))

    lines = raw.get("lines") or []
    residual_raw = None
    residual_amount_raw = None
    deduction_raw: list[tuple[Decimal, str]] = []
    for ln in lines:
        kind = str(ln.get("kind") or "").upper()
        text = str(ln.get("text") or "")
        if kind == "GAS":
            ns.gas_lines.append(GasLine(sku=product_sku(ln.get("product") or text), qty=parse_number(ln.get("qty")),
                                        unit_price=parse_number(ln.get("unit_price_raw")), price_basis=None,
                                        amount_written=parse_number(ln.get("amount_raw")), amount_calc=None, text=text))
        elif kind == "GAS_RESIDUAL":
            residual_raw = parse_number(ln.get("residual_kg_raw") or ln.get("qty"))
            residual_amount_raw = parse_number(ln.get("amount_raw"))
        elif kind == "ACCESSORY":
            ns.extra_lines.append(ExtraLine(name=str(ln.get("product") or text), qty=parse_number(ln.get("qty")),
                                            unit_price=parse_number(ln.get("unit_price_raw")),
                                            amount=parse_number(ln.get("amount_raw")), text=text))
        elif kind == "DEDUCTION":
            amt = parse_number(ln.get("amount_raw"))
            if amt is not None:
                deduction_raw.append((amt, text))
        elif text:
            ns.notes.append(text)
    ns.notes += [str(t) for t in (raw.get("extra_text") or []) if t]

    # ---- dòng gas: đơn giá theo kg hay theo bình, viết tắt nghìn hay không
    price_per_kg: Decimal | None = None
    for g in ns.gas_lines:
        if g.sku is None:
            ns.flags.append(Flag("warning", "lines", f"Không nhận ra loại bình ở dòng '{g.text}'"))
        if g.qty is None or g.qty <= 0:
            ns.flags.append(Flag("warning", "lines", f"Thiếu số lượng ở dòng '{g.text}'"))
            continue
        if g.unit_price is None:
            g.price_basis = None
            continue
        cands = price_candidates(g.unit_price, g.sku)
        chosen = None
        if g.amount_written is not None:
            for price, basis in cands:
                exp = _line_amount(g.qty, g.sku, price, basis)
                if exp is None:
                    continue
                val, ok = scale_to(g.amount_written, exp)
                if ok:
                    chosen = (price, basis)
                    g.amount_written = val
                    break
        if chosen is None and cands:
            chosen = cands[0]
            if g.amount_written is not None:
                ns.flags.append(Flag("warning", "lines",
                                     f"Thành tiền '{g.text}' không khớp số lượng × đơn giá – kiểm tra lại"))
                g.amount_written, _ = scale_to(g.amount_written, None)
        if chosen is None:
            ns.flags.append(Flag("warning", "lines", f"Đơn giá '{g.unit_price}' không hợp lý cho {g.sku or 'bình'}"))
            g.unit_price = None
            continue
        g.unit_price, g.price_basis = chosen
        g.amount_calc = round_money(_line_amount(g.qty, g.sku, g.unit_price, g.price_basis) or ZERO)
        if price_per_kg is None:
            cap = CAPACITY.get(g.sku or "")
            price_per_kg = g.unit_price if g.price_basis == "KG" else (g.unit_price / cap if cap else None)
    if any(g.unit_price is None for g in ns.gas_lines):
        ns.flags.append(Flag("info", "unit_price", "Đơn giá để trống trên phiếu – sẽ lấy theo bảng giá của khách"))

    # ---- phụ kiện
    for e in ns.extra_lines:
        if e.unit_price is not None:
            e.unit_price, _ = scale_to(e.unit_price, None) if e.unit_price < 10_000 else (e.unit_price, True)
        if e.amount is not None:
            exp = (e.qty or Decimal(1)) * e.unit_price if e.unit_price else None
            e.amount, ok = scale_to(e.amount, exp)
            if exp is not None and not ok:
                ns.flags.append(Flag("warning", "lines", f"Thành tiền phụ kiện '{e.text}' không khớp"))

    # ---- gas dư: chọn số kg khớp với tiền trừ
    if residual_raw is not None:
        ns.residual_kg_written = abs(residual_raw)
        kg = abs(residual_raw)
        if price_per_kg and residual_amount_raw is not None:
            matched = False
            for cand in (kg, kg * 10, kg / 10):
                val, ok = scale_to(residual_amount_raw, cand * price_per_kg)
                if ok:
                    if cand != kg:
                        ns.flags.append(Flag("warning", "gas_residual_kg",
                                             f"Gas dư viết '{residual_raw}' kg nhưng tiền trừ khớp với {cand.normalize()} kg"))
                    kg, matched = cand, True
                    ns.residual_amount_written = val
                    break
            if not matched:
                ns.flags.append(Flag("warning", "gas_residual_kg", "Tiền trừ gas dư không khớp số kg × đơn giá"))
                ns.residual_amount_written, _ = scale_to(residual_amount_raw, None)
        gross = sum((g.qty * CAPACITY[g.sku] for g in ns.gas_lines
                     if g.qty and g.sku and CAPACITY.get(g.sku)), ZERO)
        if gross and kg >= gross:
            ns.flags.append(Flag("warning", "gas_residual_kg", f"Gas dư {kg} kg ≥ tổng số kg giao {gross}"))
        ns.residual_kg = kg

    # ---- khoản trừ (trừ tiền đầu tư...)
    for amt, text in deduction_raw:
        val, _ = scale_to(amt, None)
        ns.deduction += val
        ns.deduction_note = text if not ns.deduction_note else f"{ns.deduction_note}; {text}"

    # ---- tổng cộng
    if all(g.amount_calc is not None for g in ns.gas_lines) and ns.gas_lines:
        residual_amt = round_money(ns.residual_kg * price_per_kg) if price_per_kg else ZERO
        ns.total_calc = (sum((g.amount_calc for g in ns.gas_lines), ZERO)
                         + sum((e.amount or ZERO for e in ns.extra_lines), ZERO) - residual_amt - ns.deduction)
    total_raw = parse_number(raw.get("total_raw"))
    if total_raw is not None:
        ns.total_written, ok = scale_to(total_raw, ns.total_calc)
        if ns.total_calc is not None and not ok:
            ns.flags.append(Flag("warning", "total", f"Tổng cộng viết {ns.total_written:,.0f} đ khác tổng tính lại "
                                                       f"{ns.total_calc:,.0f} đ"))

    # ---- tài sản
    default_sku = next((g.sku for g in ns.gas_lines if g.sku), None)
    for a in raw.get("assets") or []:
        atype = str(a.get("asset_type") or "").upper()
        direction = str(a.get("direction") or "").upper()
        if atype not in AssetType.ALL:
            atype = AssetType.OTHER
        if direction not in AssetDirection.ALL:
            ns.flags.append(Flag("warning", "assets", f"Không rõ cho mượn / đầu tư: '{a.get('text')}'"))
            continue
        sku = product_sku(a.get("product") or a.get("text"))
        if atype == AssetType.EMPTY_CYLINDER and sku is None:
            sku = default_sku
            if sku is None:
                ns.flags.append(Flag("warning", "assets", f"Không rõ loại vỏ bình: '{a.get('text')}'"))
        qty = parse_number(a.get("qty"))
        ns.assets.append(AssetLine(atype, direction, abs(qty) if qty is not None else None, sku,
                                   str(a.get("text") or "").strip()))
    if ns.confidence < 0.8:
        ns.flags.append(Flag("warning", "confidence", f"AI tự đánh giá độ tin cậy {ns.confidence:.2f}"))
    if raw.get("ai_note"):
        ns.flags.append(Flag("info", "ai_note", str(raw["ai_note"])))
    return ns
