"""Biên bản đối chiếu công nợ (BBĐCCN) DR-YYYYMM-XXXX theo kỳ [period_from, period_to].

Công thức (skill debt-reconciliation-engine):
* opening_balance = closing_balance của BBĐCCN RECONCILED gần nhất kết thúc trước period_from
                    + phát sinh giữa hai kỳ;  nếu chưa từng đối chiếu: Σ(debit − credit) mọi bút toán < period_from
* total_increase  = Σ debit trong kỳ  (DELIVERY_INCREASE từ BBGN CONFIRMED, ADJUSTMENT tăng, OPENING)
* total_decrease  = Σ credit trong kỳ (PAYMENT_DECREASE, RETURN_DECREASE, ADJUSTMENT giảm)
* closing_balance = opening + increase − decrease

Trạng thái: DRAFT -> SENT -> (DISPUTED <-> SENT) -> RECONCILED.
Khi RECONCILED: khóa (is_locked) mọi BBGN, bút toán, phiếu thu/chi và đơn hàng của đối tác thuộc kỳ.
"""
from __future__ import annotations

from datetime import date, datetime

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
    DebtReconciliationLine,
    DebtSide,
    DeliveryNote,
    DeliveryNoteStatus,
    LedgerRefType,
    Order,
    OrderStatus,
    Partner,
    ReconciliationStatus,
)
from ..money import ZERO, D
from . import ledger
from .codes import next_code

S = ReconciliationStatus


def get_reconciliation(session: Session, rec_id: int) -> DebtReconciliation:
    rec = session.get(DebtReconciliation, rec_id)
    if rec is None:
        raise NotFoundError("biên bản đối chiếu công nợ", rec_id)
    return rec


def compute(session: Session, partner_id: int, side: str, period_from: date, period_to: date) -> dict:
    last = session.scalar(
        select(DebtReconciliation).where(
            DebtReconciliation.partner_id == partner_id, DebtReconciliation.side == side,
            DebtReconciliation.status == S.RECONCILED, DebtReconciliation.period_to < period_from)
        .order_by(DebtReconciliation.period_to.desc())
    )
    if last is not None:
        between = session.execute(select(DebtLedgerEntry.debit_amount, DebtLedgerEntry.credit_amount).where(
            DebtLedgerEntry.partner_id == partner_id, DebtLedgerEntry.side == side,
            DebtLedgerEntry.transaction_date > last.period_to,
            DebtLedgerEntry.transaction_date < period_from)).all()
        opening = D(last.closing_balance) + sum((D(d) - D(c) for d, c in between), ZERO)
    else:
        opening = ledger.balance_before(session, partner_id, side, period_from)
    entries = ledger.list_entries(session, partner_id=partner_id, side=side, date_from=period_from,
                                  date_to=period_to)
    running = opening
    inc = dec = ZERO
    lines = []
    for e in entries:
        running += D(e.debit_amount) - D(e.credit_amount)
        inc += D(e.debit_amount)
        dec += D(e.credit_amount)
        lines.append({"entry": e, "balance": running})
    return {"opening": opening, "increase": inc, "decrease": dec, "closing": opening + inc - dec, "lines": lines,
            "previous": last}


def _fill(session: Session, rec: DebtReconciliation) -> None:
    disputes = {ln.ledger_entry_id: ln.dispute_comment for ln in rec.lines if ln.is_disputed}
    data = compute(session, rec.partner_id, rec.side, rec.period_from, rec.period_to)
    rec.lines.clear()
    session.flush()
    rec.opening_balance, rec.total_increase = data["opening"], data["increase"]
    rec.total_decrease, rec.closing_balance = data["decrease"], data["closing"]
    for idx, item in enumerate(data["lines"], start=1):
        e = item["entry"]
        rec.lines.append(DebtReconciliationLine(
            line_no=idx, ledger_entry_id=e.id, transaction_date=e.transaction_date, entry_type=e.entry_type,
            order_id=e.order_id, order_code=e.order_code, delivery_note_id=e.delivery_note_id,
            document_code=e.reference_code, description=e.description,
            increase_amount=e.debit_amount, decrease_amount=e.credit_amount, line_balance=item["balance"],
            is_disputed=e.id in disputes, dispute_comment=disputes.get(e.id)))
    session.flush()


def create(session: Session, *, partner_id: int, period_from: date, period_to: date, side: str | None = None,
           user: str | None = None) -> DebtReconciliation:
    session.info["user"] = user or acting_user()
    if period_to < period_from:
        raise BusinessError("Ngày kết thúc kỳ đối chiếu phải sau ngày bắt đầu")
    with transaction(session):
        partner = session.get(Partner, partner_id)
        if partner is None:
            raise NotFoundError("đối tác", partner_id)
        side = ledger.side_of_partner(partner, side)
        overlap = session.scalar(select(DebtReconciliation).where(
            DebtReconciliation.partner_id == partner_id, DebtReconciliation.side == side,
            DebtReconciliation.period_from <= period_to, DebtReconciliation.period_to >= period_from))
        if overlap is not None:
            if overlap.status == S.RECONCILED:
                raise LockedError(f"Kỳ này trùng với biên bản đã chốt {overlap.reconciliation_code}")
            raise BusinessError(f"Đã có biên bản {overlap.reconciliation_code} ({overlap.status}) cho kỳ trùng – "
                                "mở biên bản đó để làm mới số liệu")
        rec = DebtReconciliation(reconciliation_code=next_code(session, "DR", period_to), partner_id=partner_id,
                                 side=side, period_from=period_from, period_to=period_to, status=S.DRAFT,
                                 created_by=session.info["user"])
        session.add(rec)
        session.flush()
        _fill(session, rec)
    return rec


def refresh(session: Session, rec_id: int, *, user: str | None = None) -> DebtReconciliation:
    """Làm mới số liệu (bổ sung BBGN / phiếu thu còn sót). Giữ nguyên các dòng đang đánh dấu chênh lệch."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        rec = get_reconciliation(session, rec_id)
        if rec.status == S.RECONCILED:
            raise LockedError("Biên bản đã chốt – không làm mới được")
        _fill(session, rec)
        if rec.status == S.SENT:
            rec.status = S.DRAFT   # số liệu thay đổi sau khi gửi -> phải gửi lại
    return rec


def mark_sent(session: Session, rec_id: int, *, user: str | None = None) -> DebtReconciliation:
    session.info["user"] = user or acting_user()
    with transaction(session):
        rec = get_reconciliation(session, rec_id)
        if rec.status not in (S.DRAFT, S.SENT):
            raise BusinessError(f"Biên bản đang {rec.status} – không gửi được")
        rec.status = S.SENT
        rec.sent_at = datetime.now()
    return rec


def _line(rec: DebtReconciliation, line_id: int) -> DebtReconciliationLine:
    for ln in rec.lines:
        if ln.id == line_id:
            return ln
    raise NotFoundError("dòng đối chiếu", line_id)


def dispute_line(session: Session, rec_id: int, line_id: int, comment: str, *, user: str | None = None
                 ) -> DebtReconciliation:
    """Báo chênh lệch trên 1 dòng -> biên bản tự chuyển DISPUTED, khóa nút chốt cho tới khi xử lý xong."""
    if not (comment or "").strip():
        raise BusinessError("Ghi rõ nội dung chênh lệch (VD: khách báo đợt giao 12/11 thiếu 2 bình)")
    session.info["user"] = user or acting_user()
    with transaction(session):
        rec = get_reconciliation(session, rec_id)
        if rec.status == S.RECONCILED:
            raise LockedError("Biên bản đã chốt")
        ln = _line(rec, line_id)
        ln.is_disputed = True
        ln.dispute_comment = comment.strip()
        rec.status = S.DISPUTED
        notes = [f"[{x.document_code}] {x.dispute_comment}" for x in rec.lines if x.is_disputed]
        rec.dispute_notes = "\n".join(notes)
    return rec


def resolve_line(session: Session, rec_id: int, line_id: int, *, note: str | None = None, user: str | None = None
                 ) -> DebtReconciliation:
    session.info["user"] = user or acting_user()
    with transaction(session):
        rec = get_reconciliation(session, rec_id)
        if rec.status == S.RECONCILED:
            raise LockedError("Biên bản đã chốt")
        ln = _line(rec, line_id)
        ln.is_disputed = False
        ln.dispute_comment = f"Đã xử lý: {note.strip()}" if note and note.strip() else None
        remaining = [x for x in rec.lines if x.is_disputed]
        rec.dispute_notes = "\n".join(f"[{x.document_code}] {x.dispute_comment}" for x in remaining) or None
        if not remaining and rec.status == S.DISPUTED:
            rec.status = S.SENT if rec.sent_at else S.DRAFT
    return rec


def reconcile(session: Session, rec_id: int, *, partner_confirmed_by: str | None = None,
              signed_document_url: str | None = None, user: str | None = None) -> DebtReconciliation:
    """Xác nhận khớp công nợ & khóa sổ kỳ đối chiếu (hard-lock)."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        rec = get_reconciliation(session, rec_id)
        if rec.status == S.RECONCILED:
            raise BusinessError("Biên bản đã chốt trước đó")
        if rec.status == S.DISPUTED or any(ln.is_disputed for ln in rec.lines):
            raise BusinessError("Còn dòng đang báo chênh lệch – xử lý xong mới được chốt đối chiếu")
        data = compute(session, rec.partner_id, rec.side, rec.period_from, rec.period_to)
        current_ids = [item["entry"].id for item in data["lines"]]
        if (current_ids != [ln.ledger_entry_id for ln in rec.lines] or data["closing"] != D(rec.closing_balance)
                or data["opening"] != D(rec.opening_balance)):
            raise BusinessError("Số liệu trong kỳ đã thay đổi kể từ khi lập biên bản – bấm 'Làm mới' rồi gửi lại "
                                "đối tác trước khi chốt")
        rec.status = S.RECONCILED
        rec.reconciled_at = datetime.now()
        rec.partner_confirmed_by = partner_confirmed_by or rec.partner_confirmed_by
        rec.partner_confirmed_at = datetime.now()
        if signed_document_url:
            rec.signed_document_url = signed_document_url
        _lock_period(session, rec)
    return rec


def _lock_period(session: Session, rec: DebtReconciliation) -> None:
    from ..models import OrderType

    entries = ledger.list_entries(session, partner_id=rec.partner_id, side=rec.side, date_from=rec.period_from,
                                  date_to=rec.period_to)
    voucher_ids = set()
    for e in entries:
        e.is_locked = True
        e.reconciliation_id = rec.id
        if e.reference_type == LedgerRefType.PAYMENT_VOUCHER and e.reference_id:
            voucher_ids.add(e.reference_id)
    order_type = OrderType.SALES if rec.side == DebtSide.RECEIVABLE else OrderType.PURCHASE
    for note in session.scalars(select(DeliveryNote).join(Order).where(
            DeliveryNote.partner_id == rec.partner_id, Order.order_type == order_type,
            DeliveryNote.status == DeliveryNoteStatus.CONFIRMED,
            DeliveryNote.delivery_date >= rec.period_from, DeliveryNote.delivery_date <= rec.period_to)):
        note.is_locked = True
    if voucher_ids:
        for v in session.scalars(select(CashVoucher).where(CashVoucher.id.in_(voucher_ids))):
            v.is_locked = True
    # Đơn hàng trong kỳ: khóa sửa/hủy. Đơn còn hàng chưa giao vẫn được lập BBGN cho phần còn lại (ghi vào kỳ sau).
    for order in session.scalars(select(Order).where(
            Order.partner_id == rec.partner_id, Order.order_type == order_type,
            Order.order_date >= rec.period_from, Order.order_date <= rec.period_to,
            Order.status.in_([OrderStatus.APPROVED, OrderStatus.PARTIALLY_DELIVERED, OrderStatus.COMPLETED]))):
        order.is_locked = True
    session.flush()


def list_reconciliations(session: Session, *, partner_id: int | None = None, status: str | None = None
                         ) -> list[DebtReconciliation]:
    stmt = select(DebtReconciliation).order_by(DebtReconciliation.period_to.desc(), DebtReconciliation.id.desc())
    if partner_id:
        stmt = stmt.where(DebtReconciliation.partner_id == partner_id)
    if status:
        stmt = stmt.where(DebtReconciliation.status.in_(status.split(",")))
    return list(session.scalars(stmt))


def partners_with_activity(session: Session, date_from: date, date_to: date, side: str | None = None) -> list[dict]:
    """Bộ lọc 'chỉ khách có phát sinh trong kỳ'."""
    stmt = select(DebtLedgerEntry.partner_id, DebtLedgerEntry.side).where(
        DebtLedgerEntry.transaction_date >= date_from, DebtLedgerEntry.transaction_date <= date_to).distinct()
    if side:
        stmt = stmt.where(DebtLedgerEntry.side == side)
    out = []
    for pid, sd in session.execute(stmt).all():
        p = session.get(Partner, pid)
        out.append({"partner_id": pid, "side": sd, "code": p.code, "name": p.name})
    return sorted(out, key=lambda x: x["name"])


def rec_dict(rec: DebtReconciliation) -> dict:
    from ..schemas import row_dict

    d = row_dict(rec, include=("lines",))
    p = rec.partner
    d["partner_name"], d["partner_code"] = p.name, p.code
    d["partner_tax_code"], d["partner_address"] = p.tax_code, p.address
    d["partner_representative"], d["partner_representative_title"] = p.representative, p.representative_title
    d["disputed_count"] = sum(1 for ln in rec.lines if ln.is_disputed)
    d["can_reconcile"] = rec.status in (S.DRAFT, S.SENT) and d["disputed_count"] == 0
    return d
