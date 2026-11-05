"""Đọc file XML hóa đơn điện tử Việt Nam (Thông tư 78/2021/TT-BTC) và nhập vào phần mềm.

- Bỏ qua namespace/gói bọc khác nhau giữa các nhà cung cấp (Viettel, VNPT, MISA, EasyInvoice...): tìm thẻ theo tên.
- Hóa đơn MUA VÀO (người mua = công ty mình) -> phiếu nhập mua + công nợ phải trả + thẻ kho.
- Hóa đơn BÁN RA (người bán = công ty mình) -> lưu hóa đơn GTGT, nối với các phiếu giao có cùng "Số hóa đơn".
- Chống trùng theo (MST người bán, Ký hiệu, Số hóa đơn).
- Mã hàng của NCC khác mã nội bộ -> hỏi người dùng nối một lần, phần mềm ghi nhớ cho các lần sau.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import transaction
from ..errors import BusinessError, DuplicateError
from ..models import (
    DeliverySlip,
    DocStatus,
    Partner,
    PartnerType,
    Purchase,
    PurchaseType,
    SourceType,
    SupplierProductMap,
    VatInvoice,
    VatInvoiceSlip,
)
from ..money import ZERO, D
from ..services import masterdata, purchases
from ..services.masterdata import normalize_tax_code, normalize_text

LINE_NORMAL, LINE_PROMO, LINE_DISCOUNT, LINE_NOTE = "1", "2", "3", "4"


@dataclass
class XmlParty:
    name: str | None = None
    tax_code: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    buyer_person: str | None = None


@dataclass
class XmlLine:
    index: int
    nature: str              # TChat
    code: str | None
    name: str
    unit: str | None
    qty: Decimal
    unit_price: Decimal      # trước thuế
    discount_rate: Decimal
    discount_amount: Decimal
    amount: Decimal          # thành tiền trước thuế
    vat_rate_text: str | None

    @property
    def vat_rate(self) -> Decimal:
        t = (self.vat_rate_text or "").strip().upper().replace("%", "")
        if t in {"", "KCT", "KKKNT", "KHAC"}:
            return ZERO
        try:
            return D(t) / 100 if D(t) >= 1 else D(t)
        except ValueError:
            return ZERO

    @property
    def key(self) -> str:
        return (self.code or "").strip().upper() or normalize_text(self.name)


@dataclass
class EInvoice:
    form_no: str | None          # KHMSHDon
    symbol: str | None           # KHHDon
    number: str                  # SHDon
    issue_date: date
    currency: str
    exchange_rate: Decimal
    payment_method: str | None
    seller: XmlParty
    buyer: XmlParty
    lines: list[XmlLine] = field(default_factory=list)
    total_before_tax: Decimal = ZERO
    vat_amount: Decimal = ZERO
    trade_discount: Decimal = ZERO
    total: Decimal = ZERO
    total_in_words: str | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node, *path):
    """Tìm theo tên thẻ bỏ namespace, ở mọi độ sâu cho phần tử đầu tiên của path."""
    cur = node
    for i, name in enumerate(path):
        nxt = None
        it = cur.iter() if i == 0 else list(cur)
        for el in it:
            if el is not cur and _local(el.tag) == name:
                nxt = el
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _text(node, name: str) -> str | None:
    if node is None:
        return None
    for el in node:
        if _local(el.tag) == name:
            return (el.text or "").strip() or None
    return None


def _dec(node, name: str) -> Decimal:
    t = _text(node, name)
    if not t:
        return ZERO
    try:
        return D(t.replace(",", ""))
    except ValueError:
        return ZERO


def _party(node) -> XmlParty:
    return XmlParty(name=_text(node, "Ten"), tax_code=normalize_tax_code(_text(node, "MST")),
                    address=_text(node, "DChi"), phone=_text(node, "SDThoai"), email=_text(node, "DCTDTu"),
                    buyer_person=_text(node, "HVTNMHang"))


def parse_einvoice(data: bytes) -> EInvoice:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise BusinessError(f"File XML lỗi cấu trúc: {exc}") from exc
    dl = _find(root, "DLHDon")
    if dl is None:
        raise BusinessError("Không thấy thẻ <DLHDon> – không phải hóa đơn điện tử theo TT78")
    tt = _find(dl, "TTChung")
    nd = _find(dl, "NDHDon")
    if tt is None or nd is None:
        raise BusinessError("Hóa đơn thiếu <TTChung> hoặc <NDHDon>")
    number = _text(tt, "SHDon")
    issued = _text(tt, "NLap")
    if not number or not issued:
        raise BusinessError("Hóa đơn thiếu Số hóa đơn (SHDon) hoặc Ngày lập (NLap)")
    inv = EInvoice(
        form_no=_text(tt, "KHMSHDon"), symbol=_text(tt, "KHHDon"), number=number.lstrip("0") or "0",
        issue_date=date.fromisoformat(issued[:10]), currency=(_text(tt, "DVTTe") or "VND").upper(),
        exchange_rate=_dec(tt, "TGia") or Decimal(1), payment_method=_text(tt, "HTTToan"),
        seller=_party(_find(nd, "NBan")), buyer=_party(_find(nd, "NMua")),
    )
    ds = _find(nd, "DSHHDVu")
    for i, h in enumerate([el for el in (ds if ds is not None else []) if _local(el.tag) == "HHDVu"], start=1):
        inv.lines.append(XmlLine(
            index=i, nature=_text(h, "TChat") or LINE_NORMAL, code=_text(h, "MHHDVu"), name=_text(h, "THHDVu") or "",
            unit=_text(h, "DVTinh"), qty=_dec(h, "SLuong"), unit_price=_dec(h, "DGia"),
            discount_rate=_dec(h, "TLCKhau"), discount_amount=_dec(h, "STCKhau"), amount=_dec(h, "ThTien"),
            vat_rate_text=_text(h, "TSuat"),
        ))
    tto = _find(nd, "TToan")
    inv.total_before_tax = _dec(tto, "TgTCThue")
    inv.vat_amount = _dec(tto, "TgTThue")
    inv.trade_discount = _dec(tto, "TTCKTMai")
    inv.total = _dec(tto, "TgTTTBSo")
    inv.total_in_words = _text(tto, "TgTTTBChu")
    if inv.currency != "VND":
        rate = inv.exchange_rate
        for ln in inv.lines:
            ln.unit_price, ln.amount, ln.discount_amount = ln.unit_price * rate, ln.amount * rate, ln.discount_amount * rate
        inv.total_before_tax *= rate
        inv.vat_amount *= rate
        inv.total *= rate
    return inv


# ---------------------------------------------------------------- nhập vào phần mềm
@dataclass
class XmlImportResult:
    direction: str                     # PURCHASE | SALE
    invoice: EInvoice
    status: str                        # IMPORTED | NEEDS_MAPPING | DUPLICATE
    purchase_id: int | None = None
    vat_invoice_id: int | None = None
    linked_slip_ids: list[int] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _direction(session: Session, inv: EInvoice) -> str:
    company = masterdata.get_company(session)
    mine = normalize_tax_code(company.tax_code)
    if mine and inv.buyer.tax_code == mine:
        return "PURCHASE"
    if mine and inv.seller.tax_code == mine:
        return "SALE"
    raise BusinessError(f"Hóa đơn không có MST của công ty ({mine}) ở bên mua hay bên bán")


def _ensure_partner(session: Session, party: XmlParty, ptype: str) -> Partner:
    partner = masterdata.find_partner_by_tax_code(session, party.tax_code) if party.tax_code else None
    if partner:
        if partner.partner_type not in (ptype, PartnerType.BOTH):
            partner.partner_type = PartnerType.BOTH
        return partner
    code = f"NCC{party.tax_code}" if ptype == PartnerType.SUPPLIER else f"KH{party.tax_code}"
    return masterdata.create_partner(session, code=code, name=party.name or code, partner_type=ptype,
                                     tax_code=party.tax_code, address=party.address, phone=party.phone)


def _lookup_map(session: Session, supplier_id: int, line: XmlLine) -> SupplierProductMap | None:
    return session.scalar(select(SupplierProductMap).where(SupplierProductMap.supplier_id == supplier_id,
                                                           SupplierProductMap.supplier_key == line.key))


def import_einvoice(session: Session, data: bytes, *, mapping: dict[int, dict] | None = None) -> XmlImportResult:
    """mapping: {số dòng: {"sku" hoặc "product_id", "qty_factor"?}} cho các dòng NCC chưa nối mã."""
    inv = parse_einvoice(data)
    direction = _direction(session, inv)
    result = XmlImportResult(direction=direction, invoice=inv, status="IMPORTED")
    if inv.total and abs((inv.total_before_tax + inv.vat_amount - inv.trade_discount) - inv.total) > 5 \
            and inv.trade_discount == 0:
        result.warnings.append("Tổng thanh toán khác Tổng trước thuế + Thuế – kiểm tra lại hóa đơn")

    if direction == "SALE":
        with transaction(session):
            dup = session.scalar(select(VatInvoice).where(VatInvoice.invoice_symbol == inv.symbol,
                                                          VatInvoice.invoice_no == inv.number))
            if dup:
                result.status, result.vat_invoice_id = "DUPLICATE", dup.id
                return result
            partner = _ensure_partner(session, inv.buyer, PartnerType.CUSTOMER)
            vi = VatInvoice(partner_id=partner.id, invoice_symbol=inv.symbol, invoice_no=inv.number,
                            invoice_date=inv.issue_date, amount_before_tax=inv.total_before_tax,
                            vat_amount=inv.vat_amount, total=inv.total)
            session.add(vi)
            session.flush()
            slips = session.scalars(select(DeliverySlip).where(
                DeliverySlip.partner_id == partner.id, DeliverySlip.vat_invoice_no == inv.number,
                DeliverySlip.status == DocStatus.POSTED)).all()
            for s in slips:
                session.add(VatInvoiceSlip(vat_invoice_id=vi.id, slip_id=s.id))
            result.vat_invoice_id = vi.id
            result.linked_slip_ids = [s.id for s in slips]
            slip_total = sum((D(s.total) for s in slips), ZERO)
            if slips and abs(slip_total - inv.total) > 1000:
                result.warnings.append(f"Tổng các phiếu giao số HĐ {inv.number} = {slip_total:,.0f} đ, "
                                       f"hóa đơn = {inv.total:,.0f} đ")
            if not slips:
                result.warnings.append(f"Chưa có phiếu giao nào ghi Số hóa đơn {inv.number}")
        return result

    # ---- MUA VÀO
    with transaction(session):
        supplier = _ensure_partner(session, inv.seller, PartnerType.SUPPLIER)
        supplier_id = supplier.id
        mapping = mapping or {}
        for idx, m in mapping.items():
            line = next((ln for ln in inv.lines if ln.index == int(idx)), None)
            if line is None:
                raise BusinessError(f"Không có dòng {idx} trên hóa đơn")
            product = (masterdata.get_product(session, m["product_id"]) if m.get("product_id")
                       else masterdata.get_product_by_sku(session, m["sku"]))
            row = _lookup_map(session, supplier_id, line)
            if row is None:
                row = SupplierProductMap(supplier_id=supplier_id, supplier_key=line.key, supplier_name=line.name,
                                         supplier_unit=line.unit, product_id=product.id)
                session.add(row)
            row.product_id = product.id
            row.qty_factor = D(m.get("qty_factor") or 1)
        session.flush()
        items = []
        for ln in inv.lines:
            if ln.nature == LINE_NOTE:
                continue
            mp = _lookup_map(session, supplier_id, ln)
            if mp is None:
                auto = session.scalar(select(masterdata.Product).where(masterdata.Product.sku == (ln.code or "").upper()))
                if auto is None:
                    result.unmapped.append({"line": ln.index, "code": ln.code, "name": ln.name, "unit": ln.unit,
                                            "qty": ln.qty, "amount": ln.amount})
                    continue
                mp = SupplierProductMap(supplier_id=supplier_id, supplier_key=ln.key, supplier_name=ln.name,
                                        supplier_unit=ln.unit, product_id=auto.id, qty_factor=Decimal(1))
                session.add(mp)
                session.flush()
            if ln.nature == LINE_DISCOUNT:
                result.warnings.append(f"Dòng {ln.index} là chiết khấu thương mại {ln.amount:,.0f} đ – "
                                       "đã trừ vào giá nhập của dòng hàng đầu tiên")
                if items:
                    first = items[0]
                    first.unit_price = (D(first.unit_price) * D(first.qty) - abs(ln.amount)) / D(first.qty)
                continue
            qty = ln.qty * D(mp.qty_factor)
            if qty <= 0:
                continue
            line_amount = ln.amount if ln.nature != LINE_PROMO else ZERO
            unit_price = line_amount / qty if qty else ZERO
            items.append(purchases.PurchaseItemInput(qty=qty, unit_price=unit_price, product_id=mp.product_id,
                                                     vat_rate=ln.vat_rate, description=ln.name))
    if result.unmapped:
        result.status = "NEEDS_MAPPING"
        return result
    if not items:
        raise BusinessError("Hóa đơn không có dòng hàng nào để nhập kho")
    dup = session.scalar(select(Purchase).where(Purchase.supplier_id == supplier_id,
                                                Purchase.invoice_symbol == inv.symbol,
                                                Purchase.invoice_no == inv.number,
                                                Purchase.status != DocStatus.CANCELLED))
    if dup:
        result.status, result.purchase_id = "DUPLICATE", dup.id
        return result
    try:
        doc, warnings = purchases.create_purchase(session, purchases.PurchaseInput(
            supplier_id=supplier_id, doc_date=inv.issue_date, items=items, doc_type=PurchaseType.PURCHASE,
            invoice_symbol=inv.symbol, invoice_no=inv.number, source_type=SourceType.XML,
            note=f"Nhập từ HĐĐT {inv.form_no or ''}{inv.symbol or ''} số {inv.number}"))
    except DuplicateError:
        result.status = "DUPLICATE"
        return result
    result.purchase_id = doc.id
    result.warnings += warnings
    if abs(D(doc.total) - inv.total) > 5:
        result.warnings.append(f"Tổng phiếu nhập {doc.total:,.0f} đ khác tổng hóa đơn {inv.total:,.0f} đ "
                               "(do làm tròn thuế từng dòng) – kiểm tra lại")
    return result
