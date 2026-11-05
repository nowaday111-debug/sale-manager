"""Phiếu thu / phiếu chi, phân bổ thanh toán vào công nợ theo tháng, sổ quỹ tiền mặt & ngân hàng."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import transaction
from ..errors import BusinessError, LockedError, NotFoundError
from ..models import (
    CashVoucher,
    DocStatus,
    FundOpening,
    FundType,
    Partner,
    PaymentAllocation,
    VoucherType,
)
from ..money import ZERO, D, round_money
from . import debts
from .locks import ensure_period_open, period_of
from .sequences import next_number

DEFAULT_CATEGORY = {
    VoucherType.RECEIPT: "Thu tiền khách hàng",
    VoucherType.PAYMENT: "Trả tiền nhà cung cấp",
}


def allocation_sign(voucher_type: str, side: str) -> int:
    """+1: tiền làm GIẢM nợ (khách trả / mình trả NCC). −1: hoàn tiền, làm TĂNG nợ."""
    from ..models import DebtSide

    if (voucher_type == VoucherType.RECEIPT and side == DebtSide.RECEIVABLE) or \
            (voucher_type == VoucherType.PAYMENT and side == DebtSide.PAYABLE):
        return 1
    return -1


def _check_period(period: str) -> None:
    if len(period) != 7 or period[4] != "-" or not (period[:4].isdigit() and period[5:].isdigit()) \
            or not 1 <= int(period[5:]) <= 12:
        raise BusinessError(f"Kỳ phân bổ phải dạng YYYY-MM: {period}")


def _fifo_allocate(session: Session, partner_id: int, side: str, amount: Decimal, voucher_date: date
                   ) -> list[tuple[str, Decimal]]:
    """Trả nợ tháng cũ trước. Phần thừa ghi vào tháng của phiếu (trả trước)."""
    outstanding = debts.outstanding_by_period(session, partner_id, side, as_of=voucher_date)
    remaining = amount
    result: list[tuple[str, Decimal]] = []
    for period in sorted(outstanding):
        owed = outstanding[period]
        if owed <= 0 or remaining <= 0:
            continue
        take = min(owed, remaining)
        result.append((period, take))
        remaining -= take
    if remaining > 0:
        result.append((period_of(voucher_date), remaining))
    return result


def _add_voucher(session: Session, *, voucher_type: str, voucher_date: date, amount, fund_type: str,
                 partner_id: int | None, category: str | None, description: str | None, payer_payee: str | None,
                 ref_slip_id: int | None, ref_purchase_id: int | None,
                 allocations: list[tuple[str, object]] | None, affects_debt: bool) -> CashVoucher:
    """Tạo phiếu thu/chi BÊN TRONG transaction hiện có (dùng cho lập hóa đơn thu tiền ngay)."""
    if voucher_type not in VoucherType.ALL:
        raise BusinessError(f"Loại phiếu không hợp lệ: {voucher_type}")
    if fund_type not in FundType.ALL:
        raise BusinessError(f"Loại quỹ không hợp lệ: {fund_type}")
    amount = round_money(amount)
    if amount <= 0:
        raise BusinessError("Số tiền phiếu phải lớn hơn 0")
    partner = None
    if partner_id is not None:
        partner = session.get(Partner, partner_id)
        if partner is None:
            raise NotFoundError("đối tác", partner_id)
        ensure_period_open(session, partner_id, voucher_date)
    prefix = "PT" if voucher_type == VoucherType.RECEIPT else "PC"
    voucher = CashVoucher(
        voucher_no=next_number(session, prefix, voucher_date), voucher_date=voucher_date,
        voucher_type=voucher_type, fund_type=fund_type, partner_id=partner_id,
        ref_slip_id=ref_slip_id, ref_purchase_id=ref_purchase_id,
        category=category or (DEFAULT_CATEGORY[voucher_type] if partner else "Thu chi nội bộ"),
        amount=amount, payer_payee=payer_payee or (partner.name if partner else None),
        description=description,
    )
    session.add(voucher)
    session.flush()
    if partner is not None and affects_debt:
        side = debts.side_for_partner(partner, voucher_type)
        sign = allocation_sign(voucher_type, side)
        if allocations:
            pairs = [(p, round_money(a)) for p, a in allocations]
            if sum((a for _, a in pairs), ZERO) != amount:
                raise BusinessError("Tổng tiền phân bổ phải bằng số tiền phiếu")
        elif sign > 0:
            pairs = _fifo_allocate(session, partner.id, side, amount, voucher_date)
        else:
            # hoàn tiền cho khách / NCC hoàn tiền: làm TĂNG lại công nợ của tháng phát sinh phiếu
            pairs = [(period_of(voucher_date), amount)]
        for period, amt in pairs:
            _check_period(period)
            voucher.allocations.append(PaymentAllocation(partner_id=partner.id, side=side, period=period,
                                                         amount=sign * amt))
        session.flush()
    return voucher


def create_voucher(session: Session, *, voucher_type: str, voucher_date: date, amount, fund_type: str = FundType.BANK,
                   partner_id: int | None = None, category: str | None = None, description: str | None = None,
                   payer_payee: str | None = None, ref_slip_id: int | None = None, ref_purchase_id: int | None = None,
                   allocations: list[tuple[str, object]] | None = None, affects_debt: bool = True) -> CashVoucher:
    with transaction(session):
        voucher = _add_voucher(session, voucher_type=voucher_type, voucher_date=voucher_date, amount=amount,
                               fund_type=fund_type, partner_id=partner_id, category=category,
                               description=description, payer_payee=payer_payee, ref_slip_id=ref_slip_id,
                               ref_purchase_id=ref_purchase_id, allocations=allocations, affects_debt=affects_debt)
    return voucher


def get_voucher(session: Session, voucher_id: int) -> CashVoucher:
    v = session.get(CashVoucher, voucher_id)
    if v is None:
        raise NotFoundError("phiếu thu/chi", voucher_id)
    return v


def reallocate_voucher(session: Session, voucher_id: int, allocations: list[tuple[str, object]]) -> CashVoucher:
    """Phân bổ lại thủ công (VD: khách chỉ định trả cho tháng 9 trước)."""
    with transaction(session):
        v = get_voucher(session, voucher_id)
        if v.is_locked:
            raise LockedError(f"Phiếu {v.voucher_no} đã khóa bởi biên bản đối chiếu công nợ")
        if v.status != DocStatus.POSTED or v.partner_id is None:
            raise BusinessError("Chỉ phân bổ lại phiếu đã ghi sổ có đối tác")
        ensure_period_open(session, v.partner_id, v.voucher_date)
        side = v.allocations[0].side if v.allocations else debts.side_for_partner(v.partner, v.voucher_type)
        sign = allocation_sign(v.voucher_type, side)
        pairs = [(p, round_money(a)) for p, a in allocations]
        if sum((a for _, a in pairs), ZERO) != D(v.amount):
            raise BusinessError("Tổng tiền phân bổ phải bằng số tiền phiếu")
        v.allocations.clear()
        session.flush()
        for period, amt in pairs:
            _check_period(period)
            v.allocations.append(PaymentAllocation(partner_id=v.partner_id, side=side, period=period,
                                                   amount=sign * amt))
    return v


def cancel_voucher(session: Session, voucher_id: int, reason: str) -> CashVoucher:
    with transaction(session):
        v = get_voucher(session, voucher_id)
        if v.status == DocStatus.CANCELLED:
            raise BusinessError("Phiếu đã hủy trước đó")
        if v.is_locked:
            raise LockedError(f"Phiếu {v.voucher_no} đã khóa bởi biên bản đối chiếu công nợ – "
                              "hạch toán điều chỉnh ở kỳ sau")
        ensure_period_open(session, v.partner_id, v.voucher_date)
        from .ledger import reverse_voucher_entries

        reverse_voucher_entries(session, v, reason)   # phiếu thuộc sổ cái đơn hàng -> bút toán đảo
        v.status = DocStatus.CANCELLED
        v.description = f"{v.description or ''} [HỦY: {reason}]".strip()
        v.allocations.clear()
    return v


def set_fund_opening(session: Session, fund_type: str, as_of: date, amount) -> FundOpening:
    with transaction(session):
        row = session.scalar(select(FundOpening).where(FundOpening.fund_type == fund_type))
        if row is None:
            row = FundOpening(fund_type=fund_type, as_of=as_of, amount=D(amount))
            session.add(row)
        else:
            row.as_of, row.amount = as_of, D(amount)
    return row


def cash_book(session: Session, fund_type: str, date_from: date, date_to: date) -> dict:
    """Sổ quỹ: Tồn đầu kỳ + Thu trong kỳ − Chi trong kỳ = Tồn cuối kỳ."""
    opening_row = session.scalar(select(FundOpening).where(FundOpening.fund_type == fund_type))
    base_date = opening_row.as_of if opening_row else date.min
    opening = D(opening_row.amount) if opening_row else ZERO
    if date_from < base_date:
        raise BusinessError(f"Sổ quỹ chỉ xem được từ ngày khai báo số dư đầu ({base_date:%d/%m/%Y})")
    vouchers = session.scalars(
        select(CashVoucher).where(CashVoucher.fund_type == fund_type, CashVoucher.status == DocStatus.POSTED,
                                  CashVoucher.voucher_date >= base_date, CashVoucher.voucher_date <= date_to)
        .order_by(CashVoucher.voucher_date, CashVoucher.id)
    ).all()
    for v in vouchers:
        if v.voucher_date < date_from:
            opening += D(v.amount) if v.voucher_type == VoucherType.RECEIPT else -D(v.amount)
    lines, running = [], opening
    total_in = total_out = ZERO
    for v in vouchers:
        if v.voucher_date < date_from:
            continue
        amt = D(v.amount)
        if v.voucher_type == VoucherType.RECEIPT:
            running += amt
            total_in += amt
        else:
            running -= amt
            total_out += amt
        lines.append({"date": v.voucher_date, "voucher_no": v.voucher_no, "type": v.voucher_type,
                      "partner": v.payer_payee, "category": v.category, "description": v.description,
                      "receipt": amt if v.voucher_type == VoucherType.RECEIPT else ZERO,
                      "payment": amt if v.voucher_type == VoucherType.PAYMENT else ZERO, "balance": running})
    return {"fund_type": fund_type, "date_from": date_from, "date_to": date_to, "opening": opening,
            "total_receipt": total_in, "total_payment": total_out, "closing": running, "lines": lines}


def list_vouchers(session: Session, *, date_from: date | None = None, date_to: date | None = None,
                  partner_id: int | None = None, voucher_type: str | None = None) -> list[CashVoucher]:
    stmt = select(CashVoucher).order_by(CashVoucher.voucher_date, CashVoucher.id)
    if date_from:
        stmt = stmt.where(CashVoucher.voucher_date >= date_from)
    if date_to:
        stmt = stmt.where(CashVoucher.voucher_date <= date_to)
    if partner_id:
        stmt = stmt.where(CashVoucher.partner_id == partner_id)
    if voucher_type:
        stmt = stmt.where(CashVoucher.voucher_type == voucher_type)
    return list(session.scalars(stmt))


__all__ = ["create_voucher", "reallocate_voucher", "cancel_voucher", "cash_book", "set_fund_opening",
           "list_vouchers", "get_voucher"]
