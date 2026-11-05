"""Công nợ phải thu (khách) / phải trả (NCC) theo từng tháng phát sinh.

Nợ của tháng m = Σ chứng từ tháng m (bán − trả lại, hoặc mua − trả NCC) + số dư chuyển sang của tháng m
                 − Σ tiền đã phân bổ vào tháng m.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import (
    CashVoucher,
    DebtSide,
    DeliverySlip,
    DocStatus,
    OpeningBalance,
    Partner,
    PartnerType,
    PaymentAllocation,
    Purchase,
)
from ..money import ZERO, D
from .locks import period_of


def _period_start(period: str) -> date:
    y, m = period.split("-")
    return date(int(y), int(m), 1)


def charges_by_period(session: Session, partner_id: int, side: str, *, as_of: date | None = None,
                      date_from: date | None = None) -> dict[str, Decimal]:
    """Tổng phát sinh tăng nợ theo tháng (đã trừ hàng trả lại) + số dư chuyển sang."""
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if side == DebtSide.RECEIVABLE:
        stmt = select(DeliverySlip).where(DeliverySlip.partner_id == partner_id,
                                          DeliverySlip.status == DocStatus.POSTED)
        if as_of:
            stmt = stmt.where(DeliverySlip.slip_date <= as_of)
        if date_from:
            stmt = stmt.where(DeliverySlip.slip_date >= date_from)
        for slip in session.scalars(stmt):
            out[slip.period] += slip.sign * D(slip.total)
    elif side == DebtSide.PAYABLE:
        stmt = select(Purchase).where(Purchase.supplier_id == partner_id, Purchase.status == DocStatus.POSTED)
        if as_of:
            stmt = stmt.where(Purchase.doc_date <= as_of)
        if date_from:
            stmt = stmt.where(Purchase.doc_date >= date_from)
        for doc in session.scalars(stmt):
            out[doc.period] += doc.sign * D(doc.total)
    else:
        raise BusinessError(f"Loại công nợ không hợp lệ: {side}")
    for ob in session.scalars(select(OpeningBalance).where(OpeningBalance.partner_id == partner_id,
                                                           OpeningBalance.side == side)):
        start = _period_start(ob.period)
        if as_of and start > as_of:
            continue
        if date_from and start < date_from.replace(day=1):
            continue
        out[ob.period] += D(ob.amount)
    return dict(out)


def allocations_by_period(session: Session, partner_id: int, side: str, *, as_of: date | None = None,
                          before: date | None = None, exclude_voucher_id: int | None = None) -> dict[str, Decimal]:
    stmt = (select(PaymentAllocation, CashVoucher)
            .join(CashVoucher, CashVoucher.id == PaymentAllocation.voucher_id)
            .where(PaymentAllocation.partner_id == partner_id, PaymentAllocation.side == side,
                   CashVoucher.status == DocStatus.POSTED))
    if as_of:
        stmt = stmt.where(CashVoucher.voucher_date <= as_of)
    if before:
        stmt = stmt.where(CashVoucher.voucher_date < before)
    if exclude_voucher_id:
        stmt = stmt.where(CashVoucher.id != exclude_voucher_id)
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for alloc, _v in session.execute(stmt):
        out[alloc.period] += D(alloc.amount)
    return dict(out)


def outstanding_by_period(session: Session, partner_id: int, side: str, *, as_of: date | None = None,
                          payments_before: date | None = None, exclude_voucher_id: int | None = None
                          ) -> dict[str, Decimal]:
    """Nợ còn lại của từng tháng (sắp xếp tháng cũ -> mới), bỏ các tháng đã hết nợ."""
    charges = charges_by_period(session, partner_id, side, as_of=as_of)
    allocs = allocations_by_period(session, partner_id, side, as_of=as_of, before=payments_before,
                                   exclude_voucher_id=exclude_voucher_id)
    result = {}
    for period in sorted(set(charges) | set(allocs)):
        amount = charges.get(period, ZERO) - allocs.get(period, ZERO)
        if amount != 0:
            result[period] = amount
    return result


def balance(session: Session, partner_id: int, side: str = DebtSide.RECEIVABLE, *, as_of: date | None = None) -> Decimal:
    charges = charges_by_period(session, partner_id, side, as_of=as_of)
    allocs = allocations_by_period(session, partner_id, side, as_of=as_of)
    return sum(charges.values(), ZERO) - sum(allocs.values(), ZERO)


def side_for_partner(partner: Partner, voucher_type: str) -> str:
    from ..models import VoucherType

    if voucher_type == VoucherType.RECEIPT:
        return DebtSide.RECEIVABLE if partner.partner_type != PartnerType.SUPPLIER else DebtSide.PAYABLE
    return DebtSide.PAYABLE if partner.partner_type != PartnerType.CUSTOMER else DebtSide.RECEIVABLE


@dataclass
class DebtSummaryRow:
    partner_id: int
    code: str
    name: str
    opening: Decimal
    increase: Decimal
    decrease: Decimal
    closing: Decimal


def debt_summary(session: Session, side: str, date_from: date, date_to: date) -> list[DebtSummaryRow]:
    """Tổng hợp công nợ: Đầu kỳ + Phát sinh tăng − Phát sinh giảm = Cuối kỳ."""
    types = ([PartnerType.CUSTOMER, PartnerType.BOTH] if side == DebtSide.RECEIVABLE
             else [PartnerType.SUPPLIER, PartnerType.BOTH])
    rows = []
    for p in session.scalars(select(Partner).where(Partner.partner_type.in_(types)).order_by(Partner.name)):
        from datetime import timedelta

        day_before = date_from - timedelta(days=1)
        opening = balance(session, p.id, side, as_of=day_before)
        increase = decrease = ZERO
        # phát sinh trong kỳ
        if side == DebtSide.RECEIVABLE:
            docs = session.scalars(select(DeliverySlip).where(
                DeliverySlip.partner_id == p.id, DeliverySlip.status == DocStatus.POSTED,
                DeliverySlip.slip_date >= date_from, DeliverySlip.slip_date <= date_to))
            for d in docs:
                if d.sign > 0:
                    increase += D(d.total)
                else:
                    decrease += D(d.total)
        else:
            docs = session.scalars(select(Purchase).where(
                Purchase.supplier_id == p.id, Purchase.status == DocStatus.POSTED,
                Purchase.doc_date >= date_from, Purchase.doc_date <= date_to))
            for d in docs:
                if d.sign > 0:
                    increase += D(d.total)
                else:
                    decrease += D(d.total)
        for ob in session.scalars(select(OpeningBalance).where(OpeningBalance.partner_id == p.id,
                                                               OpeningBalance.side == side)):
            start = _period_start(ob.period)
            if date_from <= start <= date_to:
                increase += D(ob.amount)
        paid = allocations_by_period(session, p.id, side, as_of=date_to, before=None)
        paid_before = allocations_by_period(session, p.id, side, as_of=day_before)
        decrease += sum(paid.values(), ZERO) - sum(paid_before.values(), ZERO)
        closing = opening + increase - decrease
        if opening == 0 and increase == 0 and decrease == 0:
            continue
        rows.append(DebtSummaryRow(p.id, p.code, p.name, opening, increase, decrease, closing))
    return rows


def debt_detail(session: Session, partner_id: int, side: str, date_from: date, date_to: date) -> dict:
    """Sổ chi tiết công nợ một đối tác: từng chứng từ + số dư lũy kế."""
    from datetime import timedelta

    opening = balance(session, partner_id, side, as_of=date_from - timedelta(days=1))
    events: list[tuple[date, int, str, str, Decimal, Decimal]] = []
    if side == DebtSide.RECEIVABLE:
        for s in session.scalars(select(DeliverySlip).where(
                DeliverySlip.partner_id == partner_id, DeliverySlip.status == DocStatus.POSTED,
                DeliverySlip.slip_date >= date_from, DeliverySlip.slip_date <= date_to)):
            label = ("Trả hàng " if s.sign < 0 else "Giao hàng ") + (s.slip_no or f"#{s.id}")
            if s.site:
                label += f" – {s.site.name}"
            events.append((s.slip_date, 0, label, s.slip_no or "", D(s.total) if s.sign > 0 else ZERO,
                           D(s.total) if s.sign < 0 else ZERO))
    else:
        for d in session.scalars(select(Purchase).where(
                Purchase.supplier_id == partner_id, Purchase.status == DocStatus.POSTED,
                Purchase.doc_date >= date_from, Purchase.doc_date <= date_to)):
            label = ("Trả hàng NCC " if d.sign < 0 else "Mua hàng ") + d.doc_no
            events.append((d.doc_date, 0, label, d.invoice_no or "", D(d.total) if d.sign > 0 else ZERO,
                           D(d.total) if d.sign < 0 else ZERO))
    for ob in session.scalars(select(OpeningBalance).where(OpeningBalance.partner_id == partner_id,
                                                           OpeningBalance.side == side)):
        start = _period_start(ob.period)
        if date_from <= start <= date_to:
            events.append((start, -1, f"Số dư chuyển sang tháng {ob.period}", "", D(ob.amount), ZERO))
    for v in session.scalars(select(CashVoucher).where(
            CashVoucher.partner_id == partner_id, CashVoucher.status == DocStatus.POSTED,
            CashVoucher.voucher_date >= date_from, CashVoucher.voucher_date <= date_to)):
        amt = sum((D(a.amount) for a in v.allocations if a.side == side), ZERO)
        if amt:
            events.append((v.voucher_date, 1, v.description or v.voucher_no, v.voucher_no, ZERO, amt))
    events.sort(key=lambda e: (e[0], e[1]))
    running = opening
    lines = []
    for d, _o, label, ref, debit, credit in events:
        running += debit - credit
        lines.append({"date": d, "description": label, "ref": ref, "increase": debit, "decrease": credit,
                      "balance": running})
    return {"partner_id": partner_id, "side": side, "opening": opening, "lines": lines, "closing": running,
            "date_from": date_from, "date_to": date_to}


def check_credit_limit(session: Session, partner: Partner, extra: Decimal, on_date: date) -> str | None:
    if not partner.credit_limit:
        return None
    current = balance(session, partner.id, DebtSide.RECEIVABLE, as_of=on_date)
    if current + D(extra) > D(partner.credit_limit):
        return (f"Vượt hạn mức công nợ của {partner.name}: nợ sau phiếu {current + D(extra):,.0f} "
                f"> hạn mức {D(partner.credit_limit):,.0f}")
    return None


__all__ = [
    "charges_by_period", "allocations_by_period", "outstanding_by_period", "balance", "debt_summary",
    "debt_detail", "check_credit_limit", "side_for_partner", "period_of",
]


def set_opening_balance(session: Session, *, partner_id: int, period: str, amount, side: str = DebtSide.RECEIVABLE,
                        note: str | None = None) -> OpeningBalance:
    """Khai báo nợ cũ chưa thanh toán của một tháng (chuyển số liệu từ Excel cũ)."""
    from ..db import transaction
    from .locks import ensure_period_open

    if side not in DebtSide.ALL:
        raise BusinessError(f"Loại công nợ không hợp lệ: {side}")
    start = _period_start(period)
    with transaction(session):
        if session.get(Partner, partner_id) is None:
            raise BusinessError(f"Không có đối tác id={partner_id}")
        ensure_period_open(session, partner_id, start)
        row = session.scalar(select(OpeningBalance).where(OpeningBalance.partner_id == partner_id,
                                                          OpeningBalance.side == side,
                                                          OpeningBalance.period == period))
        if row is None:
            row = OpeningBalance(partner_id=partner_id, side=side, period=period, amount=D(amount), note=note)
            session.add(row)
        else:
            row.amount, row.note = D(amount), note
    return row
