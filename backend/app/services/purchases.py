"""Phiếu nhập mua hàng và trả hàng nhà cung cấp (cùng transaction với kho và công nợ phải trả)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..auth import acting_user
from ..db import transaction
from ..errors import BusinessError, DuplicateError, NotFoundError
from ..models import (
    DocStatus,
    FundType,
    InvTransType,
    PartnerType,
    Purchase,
    PurchaseItem,
    PurchaseType,
    SourceType,
    VoucherType,
)
from ..money import ZERO, D, round_money
from . import cashbook, inventory, masterdata
from .locks import ensure_period_open
from .sequences import next_number

REF_TYPE = "PURCHASE"


@dataclass
class PurchaseItemInput:
    qty: object
    unit_price: object                 # đơn giá TRƯỚC thuế
    product_id: int | None = None
    sku: str | None = None
    vat_rate: object | None = None     # None -> theo danh mục
    description: str | None = None


@dataclass
class PurchaseInput:
    supplier_id: int
    doc_date: date
    items: list[PurchaseItemInput] = field(default_factory=list)
    doc_type: str = PurchaseType.PURCHASE
    invoice_symbol: str | None = None
    invoice_no: str | None = None
    note: str | None = None
    source_type: str = SourceType.MANUAL
    paid_amount: object = 0
    paid_fund: str = FundType.BANK


def create_purchase(session: Session, data: PurchaseInput, *, user: str | None = None) -> tuple[Purchase, list[str]]:
    session.info["user"] = user or acting_user()
    with transaction(session):
        if data.doc_type not in PurchaseType.ALL:
            raise BusinessError(f"Loại phiếu không hợp lệ: {data.doc_type}")
        supplier = masterdata.get_partner(session, data.supplier_id)
        if supplier.partner_type == PartnerType.CUSTOMER:
            raise BusinessError(f"{supplier.name} là khách hàng, không lập phiếu mua được")
        if not data.items:
            raise BusinessError("Phiếu nhập phải có ít nhất một dòng hàng")
        ensure_period_open(session, supplier.id, data.doc_date)
        if data.invoice_no:
            dup = session.scalar(select(Purchase).where(
                Purchase.supplier_id == supplier.id, Purchase.invoice_symbol == data.invoice_symbol,
                Purchase.invoice_no == str(data.invoice_no), Purchase.doc_type == data.doc_type,
                Purchase.status != DocStatus.CANCELLED))
            if dup:
                raise DuplicateError(f"Hóa đơn {data.invoice_symbol or ''} số {data.invoice_no} của "
                                     f"{supplier.name} đã nhập ({dup.doc_no})")
        prefix = "PN" if data.doc_type == PurchaseType.PURCHASE else "PX"
        doc = Purchase(doc_type=data.doc_type, doc_no=next_number(session, prefix, data.doc_date),
                       supplier_id=supplier.id, invoice_symbol=data.invoice_symbol,
                       invoice_no=str(data.invoice_no) if data.invoice_no else None, doc_date=data.doc_date,
                       note=data.note, source_type=data.source_type, status=DocStatus.POSTED)
        subtotal = vat = ZERO
        for idx, it in enumerate(data.items, start=1):
            product = (masterdata.get_product(session, it.product_id) if it.product_id
                       else masterdata.get_product_by_sku(session, it.sku or ""))
            qty, price = D(it.qty), D(it.unit_price)
            if qty <= 0 or price < 0:
                raise BusinessError(f"Dòng {idx}: số lượng phải > 0 và đơn giá không âm")
            rate = D(it.vat_rate) if it.vat_rate is not None else D(product.vat_rate)
            line = round_money(qty * price)
            line_vat = round_money(line * rate)
            subtotal += line
            vat += line_vat
            doc.items.append(PurchaseItem(line_no=idx, product_id=product.id, description=it.description or product.name,
                                          qty=qty, unit_price=price, vat_rate=rate, line_total=line, vat_amount=line_vat))
        doc.subtotal, doc.vat_amount, doc.total = subtotal, vat, subtotal + vat
        session.add(doc)
        session.flush()
        for row in doc.items:
            product = masterdata.get_product(session, row.product_id)
            if not product.track_stock:
                continue
            if doc.doc_type == PurchaseType.PURCHASE:
                inventory.apply_movement(session, product, qty_change=D(row.qty), trans_type=InvTransType.IN_PURCHASE,
                                         trans_date=doc.doc_date, ref_type=REF_TYPE, ref_id=doc.id,
                                         unit_cost=row.unit_price, note=f"Nhập mua {doc.doc_no}")
            else:
                inventory.apply_movement(session, product, qty_change=-D(row.qty), trans_type=InvTransType.OUT_RETURN,
                                         trans_date=doc.doc_date, ref_type=REF_TYPE, ref_id=doc.id,
                                         note=f"Trả NCC {doc.doc_no}")
        paid = round_money(data.paid_amount)
        if paid > 0:
            if doc.doc_type != PurchaseType.PURCHASE:
                raise BusinessError("Phiếu trả hàng NCC không kèm chi tiền")
            if paid > doc.total:
                raise BusinessError("Số tiền trả lớn hơn tổng hóa đơn")
            cashbook._add_voucher(session, voucher_type=VoucherType.PAYMENT, voucher_date=doc.doc_date, amount=paid,
                                  fund_type=data.paid_fund, partner_id=supplier.id, category=None,
                                  description=f"Trả tiền {doc.doc_no}", payer_payee=None, ref_slip_id=None,
                                  ref_purchase_id=doc.id, allocations=[(doc.period, paid)], affects_debt=True)
        warnings = inventory.pop_warnings(session)
    return doc, warnings


def get_purchase(session: Session, purchase_id: int) -> Purchase:
    doc = session.get(Purchase, purchase_id)
    if doc is None:
        raise NotFoundError("phiếu nhập", purchase_id)
    return doc


def cancel_purchase(session: Session, purchase_id: int, reason: str) -> tuple[Purchase, list[str]]:
    with transaction(session):
        doc = get_purchase(session, purchase_id)
        if doc.status == DocStatus.CANCELLED:
            raise BusinessError("Phiếu đã hủy trước đó")
        ensure_period_open(session, doc.supplier_id, doc.doc_date)
        inventory.reverse_ref(session, ref_type=REF_TYPE, ref_id=doc.id, trans_date=doc.doc_date,
                              note=f"Hủy {doc.doc_no}: {reason}")
        doc.status = DocStatus.CANCELLED
        doc.note = f"{doc.note or ''} [HỦY: {reason}]".strip()
        warnings = inventory.pop_warnings(session)
    return doc, warnings


def list_purchases(session: Session, *, supplier_id: int | None = None, date_from: date | None = None,
                   date_to: date | None = None) -> list[Purchase]:
    stmt = select(Purchase).order_by(Purchase.doc_date, Purchase.id)
    if supplier_id:
        stmt = stmt.where(Purchase.supplier_id == supplier_id)
    if date_from:
        stmt = stmt.where(Purchase.doc_date >= date_from)
    if date_to:
        stmt = stmt.where(Purchase.doc_date <= date_to)
    return list(session.scalars(stmt))
