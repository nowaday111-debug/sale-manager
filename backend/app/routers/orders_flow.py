"""REST API quy trình Đơn đặt hàng -> Biên bản giao nhận -> Sổ cái & Đối chiếu công nợ.

Vai trò người dùng gửi qua header ``X-Role`` (ADMIN | MANAGER | CHIEF_ACCOUNTANT | ACCOUNTANT | STAFF) và tên qua
``X-User``. Không gửi -> dùng SALE_DEFAULT_ROLE / SALE_USER trong backend/.env.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import BusinessError
from ..models import FundType
from ..services import delivery_notes as dn_service
from ..services import ledger, orders, reconciliations
from ..services.codes import pop_warnings

router = APIRouter(prefix="/api", tags=["Đơn hàng – Giao nhận – Đối chiếu công nợ"])
MAX_UPLOAD = 15 * 1024 * 1024


class _In(BaseModel):
    model_config = {"extra": "forbid"}


class OrderItemIn(_In):
    product_id: Optional[int] = None
    sku: Optional[str] = None
    ordered_qty: Decimal = Field(gt=0)
    unit_price: Optional[Decimal] = Field(default=None, ge=0)
    discount_rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    tax_rate: Optional[Decimal] = Field(default=None, ge=0, le=100)
    product_name: Optional[str] = None
    unit: Optional[str] = None


class OrderIn(_In):
    partner_id: int
    order_date: date
    order_type: str = "SALES"
    site_id: Optional[int] = None
    expected_delivery_date: Optional[date] = None
    delivery_address: Optional[str] = None
    shipping_fee: Decimal = Field(default=Decimal(0), ge=0)
    payment_terms: Optional[str] = None
    notes: Optional[str] = None
    items: list[OrderItemIn]
    submit: bool = False


class ReasonBody(_In):
    reason: str = Field(min_length=1)


class ApproveBody(_In):
    note: Optional[str] = None


class NoteLineIn(_In):
    order_item_id: int
    dispatched_qty: Optional[Decimal] = Field(default=None, ge=0)
    accepted_qty: Optional[Decimal] = Field(default=None, ge=0)
    rejected_qty: Optional[Decimal] = Field(default=None, ge=0)
    item_condition_note: Optional[str] = None


class DeliveryNoteIn(_In):
    order_id: int
    delivery_date: date
    lines: Optional[list[NoteLineIn]] = None
    deliverer_name: Optional[str] = None
    deliverer_phone: Optional[str] = None
    receiver_name: Optional[str] = None
    warehouse_name: Optional[str] = None
    notes: Optional[str] = None


class DeliveryNotePatch(_In):
    lines: Optional[list[NoteLineIn]] = None
    deliverer_name: Optional[str] = None
    deliverer_phone: Optional[str] = None
    receiver_name: Optional[str] = None
    warehouse_name: Optional[str] = None
    notes: Optional[str] = None
    discrepancy_reason: Optional[str] = None
    delivery_date: Optional[date] = None


class ConfirmIn(_In):
    lines: Optional[list[NoteLineIn]] = None
    discrepancy_reason: Optional[str] = None
    receiver_name: Optional[str] = None


class PaymentIn(_In):
    partner_id: int
    payment_date: date
    amount: Decimal = Field(gt=0)
    side: Optional[str] = None
    fund_type: str = FundType.BANK
    description: Optional[str] = None
    order_id: Optional[int] = None


class ReturnIn(_In):
    partner_id: int
    return_date: date
    amount: Decimal = Field(gt=0)
    side: Optional[str] = None
    description: Optional[str] = None
    order_id: Optional[int] = None


class AdjustmentIn(_In):
    partner_id: int
    adjust_date: date
    amount: Decimal
    reason: str = Field(min_length=1)
    side: Optional[str] = None


class ReconciliationIn(_In):
    partner_id: int
    period_from: date
    period_to: date
    side: Optional[str] = None


class DisputeIn(_In):
    comment: str = Field(min_length=1)


class ResolveIn(_In):
    note: Optional[str] = None


class ReconcileIn(_In):
    partner_confirmed_by: Optional[str] = None


def _ctx(x_user: Optional[str] = Header(default=None), x_role: Optional[str] = Header(default=None)) -> dict:
    from urllib.parse import unquote

    from ..auth import current_role, current_user

    if current_user.get():          # đã đăng nhập (SALE_AUTH_USERS): không tin header của trình duyệt
        return {"user": current_user.get(), "role": current_role.get()}
    return {"user": unquote(x_user) if x_user else None, "role": x_role}


def _with_warnings(session: Session, data: dict) -> dict:
    data["warnings"] = pop_warnings(session)
    return data


def _order_input(body: OrderIn) -> orders.OrderInput:
    data = body.model_dump(exclude={"submit", "items"})
    return orders.OrderInput(items=[orders.OrderItemInput(**it.model_dump()) for it in body.items], **data)


def _lines(lines: Optional[list[NoteLineIn]]):
    if lines is None:
        return None
    return [dn_service.NoteLineInput(**ln.model_dump()) for ln in lines]


# ================================================================ đơn hàng
@router.get("/orders")
def list_orders(status: Optional[str] = None, partner_id: Optional[int] = None, order_type: Optional[str] = None,
                date_from: Optional[date] = None, date_to: Optional[date] = None,
                session: Session = Depends(get_session)):
    return [orders.order_dict(o, with_notes=False) for o in orders.list_orders(
        session, status=status, partner_id=partner_id, order_type=order_type, date_from=date_from, date_to=date_to)]


@router.post("/orders", status_code=201)
def create_order(body: OrderIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    order = orders.create_order(session, _order_input(body), submit=body.submit, user=ctx["user"])
    return _with_warnings(session, orders.order_dict(order))


@router.get("/orders/{order_id}")
def get_order(order_id: int, session: Session = Depends(get_session)):
    order = orders.get_order(session, order_id)
    data = orders.order_dict(order)
    if order.order_type == "SALES":
        pending = order.status in ("DRAFT", "PENDING_APPROVAL")
        data["credit"] = asdict(ledger.credit_status(session, order.partner_id, exclude_order_id=order.id,
                                                     extra=order.total_amount if pending else 0))
    data["ledger"] = [ledger.entry_dict(e) for e in ledger.list_entries(session, order_id=order.id)]
    return data


@router.put("/orders/{order_id}")
def update_order(order_id: int, body: OrderIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    order = orders.update_order(session, order_id, _order_input(body), role=ctx["role"], user=ctx["user"])
    if body.submit and order.status == "DRAFT":
        order = orders.submit_order(session, order_id, user=ctx["user"])
    return _with_warnings(session, orders.order_dict(order))


@router.post("/orders/{order_id}/submit")
def submit_order(order_id: int, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return _with_warnings(session, orders.order_dict(orders.submit_order(session, order_id, user=ctx["user"])))


@router.post("/orders/{order_id}/approve")
def approve_order(order_id: int, body: ApproveBody | None = None, ctx: dict = Depends(_ctx),
                  session: Session = Depends(get_session)):
    order = orders.approve_order(session, order_id, role=ctx["role"], user=ctx["user"],
                                 note=body.note if body else None)
    return _with_warnings(session, orders.order_dict(order))


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: ReasonBody, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return orders.order_dict(orders.cancel_order(session, order_id, body.reason, role=ctx["role"], user=ctx["user"]))


@router.post("/orders/{order_id}/close")
def close_order(order_id: int, body: ReasonBody, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return orders.order_dict(orders.close_order(session, order_id, body.reason, role=ctx["role"], user=ctx["user"]))


@router.get("/orders/{order_id}/delivery-preview")
def delivery_preview(order_id: int, session: Session = Depends(get_session)):
    return dn_service.preview_for_order(session, order_id)


@router.get("/partners/{partner_id}/credit")
def partner_credit(partner_id: int, amount: Decimal = Decimal(0), session: Session = Depends(get_session)):
    return asdict(ledger.credit_status(session, partner_id, extra=amount))


# ================================================================ biên bản giao nhận
@router.get("/delivery-notes")
def list_notes(order_id: Optional[int] = None, partner_id: Optional[int] = None, status: Optional[str] = None,
               date_from: Optional[date] = None, date_to: Optional[date] = None,
               session: Session = Depends(get_session)):
    return [dn_service.note_dict(n) for n in dn_service.list_notes(
        session, order_id=order_id, partner_id=partner_id, status=status, date_from=date_from, date_to=date_to)]


@router.post("/delivery-notes", status_code=201)
def create_note(body: DeliveryNoteIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    data = body.model_dump(exclude={"order_id", "lines"})
    note = dn_service.create_from_order(session, body.order_id, lines=_lines(body.lines), user=ctx["user"], **data)
    return _with_warnings(session, dn_service.note_dict(note))


@router.post("/orders/{order_id}/delivery-notes", status_code=201)
def create_note_from_order(order_id: int, body: DeliveryNoteIn, ctx: dict = Depends(_ctx),
                           session: Session = Depends(get_session)):
    if body.order_id != order_id:
        raise BusinessError("order_id trong body khác đường dẫn")
    return create_note(body, ctx, session)


@router.get("/delivery-notes/{note_id}")
def get_note(note_id: int, session: Session = Depends(get_session)):
    return dn_service.note_dict(dn_service.get_note(session, note_id))


@router.put("/delivery-notes/{note_id}")
def update_note(note_id: int, body: DeliveryNotePatch, ctx: dict = Depends(_ctx),
                session: Session = Depends(get_session)):
    header = body.model_dump(exclude={"lines"}, exclude_unset=True)
    note = dn_service.update_note(session, note_id, lines=_lines(body.lines), header=header, user=ctx["user"])
    return dn_service.note_dict(note)


@router.post("/delivery-notes/{note_id}/dispatch")
def dispatch_note(note_id: int, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return dn_service.note_dict(dn_service.dispatch(session, note_id, user=ctx["user"]))


@router.post("/delivery-notes/{note_id}/signature")
async def upload_signature(note_id: int, file: UploadFile = File(...), ctx: dict = Depends(_ctx),
                           session: Session = Depends(get_session)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise BusinessError("Ảnh quá lớn (tối đa 15 MB)")
    note = dn_service.save_signature(session, note_id, data, file.filename or "bbgn.jpg", user=ctx["user"])
    return dn_service.note_dict(note)


@router.get("/delivery-notes/{note_id}/signature")
def get_signature(note_id: int, session: Session = Depends(get_session)):
    path = dn_service.signature_path(dn_service.get_note(session, note_id))
    if path is None:
        raise BusinessError("BBGN chưa có ảnh ký nhận")
    return FileResponse(path)


@router.post("/delivery-notes/{note_id}/confirm")
def confirm_note(note_id: int, body: ConfirmIn | None = None, ctx: dict = Depends(_ctx),
                 session: Session = Depends(get_session)):
    body = body or ConfirmIn()
    note = dn_service.confirm(session, note_id, lines=_lines(body.lines), discrepancy_reason=body.discrepancy_reason,
                              receiver_name=body.receiver_name, user=ctx["user"])
    return _with_warnings(session, dn_service.note_dict(note))


@router.post("/delivery-notes/{note_id}/cancel")
def cancel_note(note_id: int, body: ReasonBody, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return dn_service.note_dict(dn_service.cancel(session, note_id, body.reason, user=ctx["user"]))


# ================================================================ sổ cái công nợ
@router.get("/ledger")
def list_ledger(partner_id: Optional[int] = None, side: Optional[str] = None, date_from: Optional[date] = None,
                date_to: Optional[date] = None, order_id: Optional[int] = None,
                session: Session = Depends(get_session)):
    return [ledger.entry_dict(e) for e in ledger.list_entries(session, partner_id=partner_id, side=side,
                                                              date_from=date_from, date_to=date_to,
                                                              order_id=order_id)]


@router.post("/ledger/payments", status_code=201)
def ledger_payment(body: PaymentIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    e = ledger.record_payment(session, user=ctx["user"], **body.model_dump())
    return _with_warnings(session, ledger.entry_dict(e))


@router.post("/ledger/returns", status_code=201)
def ledger_return(body: ReturnIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    e = ledger.record_return(session, user=ctx["user"], **body.model_dump())
    return _with_warnings(session, ledger.entry_dict(e))


@router.post("/ledger/adjustments", status_code=201)
def ledger_adjustment(body: AdjustmentIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    e = ledger.record_adjustment(session, user=ctx["user"], **body.model_dump())
    return _with_warnings(session, ledger.entry_dict(e))


# ================================================================ đối chiếu công nợ
@router.get("/reconciliations")
def list_recs(partner_id: Optional[int] = None, status: Optional[str] = None, session: Session = Depends(get_session)):
    out = []
    for r in reconciliations.list_reconciliations(session, partner_id=partner_id, status=status):
        d = reconciliations.rec_dict(r)
        d.pop("lines")
        out.append(d)
    return out


@router.get("/reconciliations/preview")
def preview_rec(partner_id: int, period_from: date, period_to: date, side: Optional[str] = None,
                session: Session = Depends(get_session)):
    from ..models import Partner

    partner = session.get(Partner, partner_id)
    if partner is None:
        raise BusinessError("Không có đối tác")
    side = ledger.side_of_partner(partner, side)
    data = reconciliations.compute(session, partner_id, side, period_from, period_to)
    return {"partner_id": partner_id, "side": side, "opening_balance": data["opening"],
            "total_increase": data["increase"], "total_decrease": data["decrease"],
            "closing_balance": data["closing"],
            "lines": [{**ledger.entry_dict(x["entry"]), "line_balance": x["balance"]} for x in data["lines"]]}


@router.get("/reconciliations/active-partners")
def active_partners(date_from: date, date_to: date, side: Optional[str] = None,
                    session: Session = Depends(get_session)):
    return reconciliations.partners_with_activity(session, date_from, date_to, side)


@router.post("/reconciliations", status_code=201)
def create_rec(body: ReconciliationIn, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return reconciliations.rec_dict(reconciliations.create(session, user=ctx["user"], **body.model_dump()))


@router.get("/reconciliations/{rec_id}")
def get_rec(rec_id: int, session: Session = Depends(get_session)):
    return reconciliations.rec_dict(reconciliations.get_reconciliation(session, rec_id))


@router.post("/reconciliations/{rec_id}/refresh")
def refresh_rec(rec_id: int, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return reconciliations.rec_dict(reconciliations.refresh(session, rec_id, user=ctx["user"]))


@router.post("/reconciliations/{rec_id}/send")
def send_rec(rec_id: int, ctx: dict = Depends(_ctx), session: Session = Depends(get_session)):
    return reconciliations.rec_dict(reconciliations.mark_sent(session, rec_id, user=ctx["user"]))


@router.post("/reconciliations/{rec_id}/lines/{line_id}/dispute")
def dispute_line(rec_id: int, line_id: int, body: DisputeIn, ctx: dict = Depends(_ctx),
                 session: Session = Depends(get_session)):
    return reconciliations.rec_dict(reconciliations.dispute_line(session, rec_id, line_id, body.comment,
                                                                 user=ctx["user"]))


@router.post("/reconciliations/{rec_id}/lines/{line_id}/resolve")
def resolve_line(rec_id: int, line_id: int, body: ResolveIn | None = None, ctx: dict = Depends(_ctx),
                 session: Session = Depends(get_session)):
    return reconciliations.rec_dict(reconciliations.resolve_line(session, rec_id, line_id,
                                                                 note=body.note if body else None, user=ctx["user"]))


@router.post("/reconciliations/{rec_id}/reconcile")
def reconcile(rec_id: int, body: ReconcileIn | None = None, ctx: dict = Depends(_ctx),
              session: Session = Depends(get_session)):
    rec = reconciliations.reconcile(session, rec_id, partner_confirmed_by=body.partner_confirmed_by if body else None,
                                    user=ctx["user"])
    return reconciliations.rec_dict(rec)


@router.get("/reconciliations/{rec_id}/export.xlsx")
def export_rec_xlsx(rec_id: int, session: Session = Depends(get_session)):
    from ..exporters import reconciliation_excel

    rec = reconciliations.get_reconciliation(session, rec_id)
    data = reconciliation_excel.build_reconciliation_xlsx(session, rec)
    from urllib.parse import quote

    filename = f"{rec.reconciliation_code}_{rec.partner.code}.xlsx"
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.get("/print/{doc_type}/{doc_id}")
def print_data(doc_type: str, doc_id: int, session: Session = Depends(get_session)):
    """Dữ liệu cho mẫu in (đơn hàng / BBGN / BBĐCCN) kèm thông tin công ty, số tiền bằng chữ."""
    from ..services import print_docs

    return print_docs.print_payload(session, doc_type, doc_id)


@router.get("/print/{doc_type}/{doc_id}/qr.svg")
def print_qr(doc_type: str, doc_id: int, session: Session = Depends(get_session)):
    from ..services import print_docs

    svg = print_docs.qr_svg(session, doc_type, doc_id)
    return Response(content=svg, media_type="image/svg+xml")

