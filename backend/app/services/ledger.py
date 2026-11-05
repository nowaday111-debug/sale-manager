"""Sổ cái công nợ nội bộ (debt_ledger_entries) – nguồn sự thật cho dư nợ quy trình Đơn hàng -> BBGN.

Nguyên tắc:
* Mọi thay đổi dư nợ đi qua ``post_entry`` (chạy BÊN TRONG transaction của nghiệp vụ gọi nó), có khóa dòng
  ``partners`` (``SELECT ... FOR UPDATE`` trên PostgreSQL; SQLite tự tuần tự hóa ghi) để ``running_balance`` không
  bị sai khi nhiều người ghi cùng lúc.
* Bút toán KHÔNG được sửa/xóa. Sai thì ghi bút toán ADJUSTMENT.
* Kỳ đã có Biên bản đối chiếu RECONCILED -> chặn ghi bút toán có ngày trong kỳ đó (HTTP 409).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..auth import acting_user
from ..db import transaction
from ..errors import BusinessError, LockedError, NotFoundError
from ..models import (
    CashVoucher,
    DebtLedgerEntry,
    DebtReconciliation,
    DebtSide,
    FundType,
    LedgerEntryType,
    LedgerRefType,
    Partner,
    ReconciliationStatus,
    VoucherType,
)
from ..money import ZERO, D, round_money
from .codes import next_code, warn


# ---------------------------------------------------------------- khóa kỳ
def reconciled_covering(session: Session, partner_id: int, side: str, on_date: date) -> DebtReconciliation | None:
    return session.scalar(
        select(DebtReconciliation).where(
            DebtReconciliation.partner_id == partner_id, DebtReconciliation.side == side,
            DebtReconciliation.status == ReconciliationStatus.RECONCILED,
            DebtReconciliation.period_from <= on_date, DebtReconciliation.period_to >= on_date)
    )


def ensure_ledger_period_open(session: Session, partner_id: int, side: str, on_date: date) -> None:
    """Chặn ghi vào kỳ đã đối chiếu xong; cảnh báo nếu kỳ đang gửi đối chiếu (SENT/DISPUTED)."""
    locked = reconciled_covering(session, partner_id, side, on_date)
    if locked is not None:
        raise LockedError(
            f"Ngày {on_date:%d/%m/%Y} thuộc kỳ đã đối chiếu {locked.reconciliation_code} "
            f"({locked.period_from:%d/%m/%Y}–{locked.period_to:%d/%m/%Y}, RECONCILED). "
            "Mọi điều chỉnh phải hạch toán bằng bút toán ADJUSTMENT ở kỳ tiếp theo.")
    sent = session.scalar(
        select(DebtReconciliation).where(
            DebtReconciliation.partner_id == partner_id, DebtReconciliation.side == side,
            DebtReconciliation.status.in_([ReconciliationStatus.SENT, ReconciliationStatus.DISPUTED]),
            DebtReconciliation.period_from <= on_date, DebtReconciliation.period_to >= on_date)
    )
    if sent is not None:
        warn(session, f"Chứng từ ngày {on_date:%d/%m/%Y} nằm trong kỳ đang gửi đối chiếu {sent.reconciliation_code} "
                      f"({sent.status}). Hãy làm mới và gửi lại biên bản đối chiếu cho đối tác.")


# ---------------------------------------------------------------- ghi bút toán
def lock_partner(session: Session, partner_id: int) -> Partner:
    partner = session.execute(
        select(Partner).where(Partner.id == partner_id).with_for_update()
    ).scalar_one_or_none()
    if partner is None:
        raise NotFoundError("đối tác", partner_id)
    return partner


def _balance_attr(side: str) -> str:
    return "current_debt" if side == DebtSide.RECEIVABLE else "current_payable"


def post_entry(session: Session, *, partner_id: int, side: str, transaction_date: date, entry_type: str,
               reference_type: str, reference_id: int | None, reference_code: str, debit=ZERO, credit=ZERO,
               order_id: int | None = None, order_code: str | None = None, delivery_note_id: int | None = None,
               due_date: date | None = None, description: str | None = None) -> DebtLedgerEntry:
    if side not in DebtSide.ALL:
        raise BusinessError(f"Loại công nợ không hợp lệ: {side}")
    if entry_type not in LedgerEntryType.ALL:
        raise BusinessError(f"Loại bút toán không hợp lệ: {entry_type}")
    debit, credit = round_money(debit), round_money(credit)
    if debit < 0 or credit < 0 or (debit == 0 and credit == 0):
        raise BusinessError("Bút toán phải có số tiền ghi tăng hoặc ghi giảm dương")
    if debit and credit:
        raise BusinessError("Một bút toán chỉ ghi tăng HOẶC ghi giảm")
    ensure_ledger_period_open(session, partner_id, side, transaction_date)
    partner = lock_partner(session, partner_id)
    attr = _balance_attr(side)
    new_balance = D(getattr(partner, attr) or ZERO) + debit - credit
    setattr(partner, attr, new_balance)
    entry = DebtLedgerEntry(
        partner_id=partner_id, side=side, transaction_date=transaction_date, entry_type=entry_type,
        reference_type=reference_type, reference_id=reference_id, reference_code=reference_code,
        order_id=order_id, order_code=order_code, delivery_note_id=delivery_note_id,
        debit_amount=debit, credit_amount=credit, running_balance=new_balance, due_date=due_date,
        description=description, created_by=session.info.get("user", settings.default_user),
    )
    session.add(entry)
    session.flush()
    return entry


def side_of_partner(partner: Partner, side: str | None) -> str:
    if side:
        if side not in DebtSide.ALL:
            raise BusinessError(f"Loại công nợ không hợp lệ: {side}")
        return side
    return DebtSide.PAYABLE if partner.partner_type == "SUPPLIER" else DebtSide.RECEIVABLE


def record_payment(session: Session, *, partner_id: int, payment_date: date, amount, side: str | None = None,
                   fund_type: str = FundType.BANK, description: str | None = None, order_id: int | None = None,
                   user: str | None = None) -> DebtLedgerEntry:
    """Khách trả tiền (phiếu thu) / mình trả NCC (phiếu chi): sinh phiếu thu/chi + bút toán PAYMENT_DECREASE."""
    from . import cashbook
    from ..models import Order

    session.info["user"] = user or acting_user()
    with transaction(session):
        partner = session.get(Partner, partner_id)
        if partner is None:
            raise NotFoundError("đối tác", partner_id)
        side = side_of_partner(partner, side)
        amount = round_money(amount)
        if amount <= 0:
            raise BusinessError("Số tiền thanh toán phải lớn hơn 0")
        order = session.get(Order, order_id) if order_id else None
        if order_id and order is None:
            raise NotFoundError("đơn hàng", order_id)
        vtype = VoucherType.RECEIPT if side == DebtSide.RECEIVABLE else VoucherType.PAYMENT
        voucher = cashbook._add_voucher(
            session, voucher_type=vtype, voucher_date=payment_date, amount=amount, fund_type=fund_type,
            partner_id=partner_id, category=None,
            description=description or (f"Thanh toán đơn {order.order_code}" if order else "Thanh toán công nợ"),
            payer_payee=None, ref_slip_id=None, ref_purchase_id=None, allocations=None,
            affects_debt=False)  # sổ cái riêng của quy trình đơn hàng – không phân bổ vào bảng kê tháng
        entry = post_entry(
            session, partner_id=partner_id, side=side, transaction_date=payment_date,
            entry_type=LedgerEntryType.PAYMENT_DECREASE, reference_type=LedgerRefType.PAYMENT_VOUCHER,
            reference_id=voucher.id, reference_code=voucher.voucher_no, credit=amount,
            order_id=order.id if order else None, order_code=order.order_code if order else None,
            description=voucher.description)
    return entry


def record_return(session: Session, *, partner_id: int, return_date: date, amount, side: str | None = None,
                  description: str | None = None, order_id: int | None = None, user: str | None = None
                  ) -> DebtLedgerEntry:
    """Hàng bán trả lại / giảm giá sau giao: bút toán RETURN_DECREASE (chứng từ CN-YYYYMM-XXXX)."""
    from ..models import Order

    session.info["user"] = user or acting_user()
    with transaction(session):
        partner = session.get(Partner, partner_id)
        if partner is None:
            raise NotFoundError("đối tác", partner_id)
        side = side_of_partner(partner, side)
        order = session.get(Order, order_id) if order_id else None
        code = next_code(session, "CN", return_date)
        entry = post_entry(
            session, partner_id=partner_id, side=side, transaction_date=return_date,
            entry_type=LedgerEntryType.RETURN_DECREASE, reference_type=LedgerRefType.CREDIT_NOTE,
            reference_id=None, reference_code=code, credit=amount,
            order_id=order.id if order else None, order_code=order.order_code if order else None,
            description=description or "Hàng trả lại / giảm giá")
    return entry


def record_adjustment(session: Session, *, partner_id: int, adjust_date: date, amount, reason: str,
                      side: str | None = None, user: str | None = None) -> DebtLedgerEntry:
    """Bút toán điều chỉnh: amount > 0 tăng nợ, amount < 0 giảm nợ. Bắt buộc có lý do."""
    if not (reason or "").strip():
        raise BusinessError("Bút toán điều chỉnh bắt buộc ghi lý do")
    session.info["user"] = user or acting_user()
    amount = round_money(amount)
    with transaction(session):
        partner = session.get(Partner, partner_id)
        if partner is None:
            raise NotFoundError("đối tác", partner_id)
        side = side_of_partner(partner, side)
        code = next_code(session, "DC", adjust_date)
        entry = post_entry(
            session, partner_id=partner_id, side=side, transaction_date=adjust_date,
            entry_type=LedgerEntryType.ADJUSTMENT, reference_type=LedgerRefType.ADJUSTMENT, reference_id=None,
            reference_code=code, debit=amount if amount > 0 else ZERO, credit=-amount if amount < 0 else ZERO,
            description=reason.strip())
    return entry


def reverse_voucher_entries(session: Session, voucher: CashVoucher, reason: str) -> None:
    """Gọi khi hủy phiếu thu/chi: đảo bút toán PAYMENT_DECREASE bằng 1 bút toán ADJUSTMENT tăng lại."""
    entries = session.scalars(select(DebtLedgerEntry).where(
        DebtLedgerEntry.reference_type == LedgerRefType.PAYMENT_VOUCHER,
        DebtLedgerEntry.reference_id == voucher.id)).all()
    net = sum((D(e.credit_amount) - D(e.debit_amount) for e in entries), ZERO)
    if not entries or net == 0:
        return
    first = entries[0]
    if any(e.is_locked for e in entries):
        raise LockedError(f"Phiếu {voucher.voucher_no} đã nằm trong biên bản đối chiếu đã chốt – không hủy được")
    post_entry(session, partner_id=first.partner_id, side=first.side, transaction_date=voucher.voucher_date,
               entry_type=LedgerEntryType.ADJUSTMENT, reference_type=LedgerRefType.PAYMENT_VOUCHER,
               reference_id=voucher.id, reference_code=voucher.voucher_no,
               debit=net if net > 0 else ZERO, credit=-net if net < 0 else ZERO,
               order_id=first.order_id, order_code=first.order_code,
               description=f"Hủy phiếu {voucher.voucher_no}: {reason}")


# ---------------------------------------------------------------- tra cứu
def list_entries(session: Session, *, partner_id: int | None = None, side: str | None = None,
                 date_from: date | None = None, date_to: date | None = None, order_id: int | None = None
                 ) -> list[DebtLedgerEntry]:
    stmt = select(DebtLedgerEntry).order_by(DebtLedgerEntry.transaction_date, DebtLedgerEntry.id)
    if partner_id:
        stmt = stmt.where(DebtLedgerEntry.partner_id == partner_id)
    if side:
        stmt = stmt.where(DebtLedgerEntry.side == side)
    if date_from:
        stmt = stmt.where(DebtLedgerEntry.transaction_date >= date_from)
    if date_to:
        stmt = stmt.where(DebtLedgerEntry.transaction_date <= date_to)
    if order_id:
        stmt = stmt.where(DebtLedgerEntry.order_id == order_id)
    return list(session.scalars(stmt))


def balance_before(session: Session, partner_id: int, side: str, before: date) -> Decimal:
    rows = session.execute(select(DebtLedgerEntry.debit_amount, DebtLedgerEntry.credit_amount).where(
        DebtLedgerEntry.partner_id == partner_id, DebtLedgerEntry.side == side,
        DebtLedgerEntry.transaction_date < before)).all()
    return sum((D(d) - D(c) for d, c in rows), ZERO)


def ledger_balance(session: Session, partner_id: int, side: str, as_of: date | None = None) -> Decimal:
    if as_of is None:
        partner = session.get(Partner, partner_id)
        return D(getattr(partner, _balance_attr(side)) or ZERO)
    return balance_before(session, partner_id, side, as_of + timedelta(days=1))


@dataclass
class OverdueItem:
    reference_code: str
    order_code: str | None
    transaction_date: date
    due_date: date
    outstanding: Decimal
    days_overdue: int


def overdue_items(session: Session, partner_id: int, side: str = DebtSide.RECEIVABLE, on_date: date | None = None
                  ) -> list[OverdueItem]:
    """Đối trừ FIFO: tiền giảm nợ trả cho bút toán tăng cũ nhất trước; bút toán tăng còn dư sau hạn = quá hạn."""
    on_date = on_date or date.today()
    entries = list_entries(session, partner_id=partner_id, side=side, date_to=on_date)
    partner = session.get(Partner, partner_id)
    term = int(partner.payment_term_days or 0) if partner else 0
    debits: list[list] = []
    credit_pool = ZERO
    for e in entries:
        if D(e.debit_amount) > 0:
            debits.append([e, D(e.debit_amount)])
        credit_pool += D(e.credit_amount)
    for item in debits:
        take = min(item[1], credit_pool)
        item[1] -= take
        credit_pool -= take
    result = []
    for e, left in debits:
        if left <= 0:
            continue
        due = e.due_date or (e.transaction_date + timedelta(days=term))
        if due < on_date:
            result.append(OverdueItem(e.reference_code, e.order_code, e.transaction_date, due, left,
                                      (on_date - due).days))
    return result


@dataclass
class CreditStatus:
    partner_id: int
    credit_limit: Decimal | None
    payment_term_days: int
    ledger_debt: Decimal            # dư nợ trên sổ cái quy trình đơn hàng (partners.current_debt)
    statement_debt: Decimal         # dư nợ bảng kê tháng (phiếu giao gas cũ)
    current_debt: Decimal           # tổng dư nợ
    open_orders_amount: Decimal     # đơn đã duyệt chưa giao hết (phần còn lại)
    available: Decimal | None       # hạn mức còn lại
    usage_percent: Decimal | None
    level: str                      # green | yellow | red
    overdue_amount: Decimal
    overdue: list = field(default_factory=list)
    messages: list = field(default_factory=list)


def credit_status(session: Session, partner_id: int, *, extra=ZERO, on_date: date | None = None,
                  exclude_order_id: int | None = None) -> CreditStatus:
    from . import debts
    from ..models import Order, OrderStatus

    on_date = on_date or date.today()
    partner = session.get(Partner, partner_id)
    if partner is None:
        raise NotFoundError("đối tác", partner_id)
    ledger_debt = D(partner.current_debt or ZERO)
    statement_debt = debts.balance(session, partner_id, DebtSide.RECEIVABLE, as_of=on_date)
    current = ledger_debt + statement_debt
    open_amount = ZERO
    for o in session.scalars(select(Order).where(Order.partner_id == partner_id,
                                                 Order.status.in_(list(OrderStatus.DELIVERABLE)),
                                                 Order.order_type == "SALES")):
        if o.id == exclude_order_id:
            continue
        for it in o.items:
            if D(it.ordered_qty):
                open_amount += D(it.line_total) * (D(it.ordered_qty) - D(it.delivered_qty)) / D(it.ordered_qty)
    open_amount = round_money(open_amount)
    overdue = overdue_items(session, partner_id, DebtSide.RECEIVABLE, on_date)
    # nợ bảng kê tháng cũ quá hạn (tháng phát sinh + số ngày được nợ < hôm nay)
    term = int(partner.payment_term_days or 0)
    for period, owed in sorted(debts.outstanding_by_period(session, partner_id, DebtSide.RECEIVABLE,
                                                           as_of=on_date).items()):
        if owed <= 0:
            continue
        y, m = int(period[:4]), int(period[5:7])
        month_end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        due = month_end + timedelta(days=term)
        if due < on_date:
            overdue.append(OverdueItem(f"Bảng kê {m:02d}/{y}", None, month_end, due, owed, (on_date - due).days))
    overdue_amount = sum((o.outstanding for o in overdue), ZERO)
    limit = D(partner.credit_limit) if partner.credit_limit else None
    messages = []
    exposure = current + D(extra)
    available = usage = None
    level = "green"
    if limit:
        available = limit - current - open_amount
        usage = (current * 100 / limit).quantize(Decimal("0.1"))
        if exposure > limit:
            level = "red"
            messages.append(f"Vượt hạn mức công nợ: dư nợ {current:,.0f} + đơn {D(extra):,.0f} = {exposure:,.0f} "
                            f"> hạn mức {limit:,.0f}".replace(",", "."))
        elif exposure + open_amount > limit * Decimal("0.8") or current > limit * Decimal("0.8"):
            level = "yellow"
            messages.append("Dư nợ đã dùng trên 80% hạn mức")
    if overdue:
        level = "red"
        messages.append(f"Có {len(overdue)} khoản nợ quá hạn, tổng {overdue_amount:,.0f} đ".replace(",", "."))
    return CreditStatus(partner_id=partner_id, credit_limit=limit, payment_term_days=term,
                        ledger_debt=ledger_debt, statement_debt=statement_debt, current_debt=current,
                        open_orders_amount=open_amount, available=available, usage_percent=usage, level=level,
                        overdue_amount=overdue_amount, overdue=overdue, messages=messages)


def entry_dict(e: DebtLedgerEntry) -> dict:
    from ..schemas import row_dict

    d = row_dict(e)
    d["partner_name"] = e.partner.name if e.partner else None
    return d


__all__ = ["post_entry", "record_payment", "record_return", "record_adjustment", "credit_status",
           "overdue_items", "list_entries", "ensure_ledger_period_open", "balance_before", "ledger_balance"]
