"""Biên bản giao nhận (BBGN) & đối khớp 3 chiều (Order -> DeliveryNote -> DebtLedgerEntry).

    PREPARING  : tạo từ đơn APPROVED/PARTIALLY_DELIVERED, điền sẵn SL còn lại; được sửa SL xuất (<= SL còn lại)
    IN_TRANSIT : đã xuất kho đi giao – khóa mặt hàng & SL xuất; nhập SL thực nhận / lỗi
    CONFIRMED  : kế toán xác nhận (bắt buộc có ảnh BBGN ký nhận) -> trong 1 transaction:
                 (a) BBGN CONFIRMED  (b) cộng accepted_qty vào order_items.delivered_qty + cập nhật trạng thái đơn
                 (c) bút toán DELIVERY_INCREASE = Σ accepted_qty × đơn giá đã duyệt (sau CK, gồm thuế)
                 (d) cập nhật partners.current_debt (khóa dòng)   (e) ghi thẻ kho theo SL thực nhận
    CANCELLED  : hủy khi chưa xác nhận
Đơn giá / chiết khấu / thuế luôn kế thừa từ dòng đơn hàng – không nhận từ người dùng.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..auth import acting_user
from ..db import transaction
from ..errors import BusinessError, LockedError, NotFoundError
from ..models import (
    DebtSide,
    DeliveryNote,
    DeliveryNoteItem,
    DeliveryNoteStatus,
    InvTransType,
    LedgerEntryType,
    LedgerRefType,
    OrderItem,
    OrderStatus,
    OrderType,
)
from ..money import ZERO, D, round_money, round_price
from . import inventory, ledger, orders
from .codes import next_code

HUNDRED = Decimal(100)
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}


@dataclass
class NoteLineInput:
    order_item_id: int
    dispatched_qty: Decimal | None = None
    accepted_qty: Decimal | None = None
    rejected_qty: Decimal | None = None
    item_condition_note: str | None = None


def get_note(session: Session, note_id: int) -> DeliveryNote:
    note = session.get(DeliveryNote, note_id)
    if note is None:
        raise NotFoundError("biên bản giao nhận", note_id)
    return note


def reserved_qty(session: Session, order_item_id: int, *, exclude_note_id: int | None = None) -> Decimal:
    """SL đang nằm trên các BBGN chưa xác nhận (PREPARING / IN_TRANSIT) – chưa được giao lại lần nữa."""
    rows = session.execute(
        select(DeliveryNoteItem.dispatched_qty, DeliveryNote.id).join(DeliveryNote).where(
            DeliveryNoteItem.order_item_id == order_item_id,
            DeliveryNote.status.in_([DeliveryNoteStatus.PREPARING, DeliveryNoteStatus.IN_TRANSIT]))
    ).all()
    return sum((D(q) for q, nid in rows if nid != exclude_note_id), ZERO)


def available_qty(session: Session, item: OrderItem, *, exclude_note_id: int | None = None) -> Decimal:
    return D(item.ordered_qty) - D(item.delivered_qty) - reserved_qty(session, item.id,
                                                                       exclude_note_id=exclude_note_id)


def accepted_total(item: DeliveryNoteItem | OrderItem, qty) -> Decimal:
    return orders.line_amounts(qty, item.unit_price, item.discount_rate, item.tax_rate)[3]


def preview_for_order(session: Session, order_id: int) -> list[dict]:
    """Danh sách mặt hàng còn phải giao (ordered − delivered − đang giao > 0) để điền sẵn form BBGN."""
    order = orders.get_order(session, order_id)
    rows = []
    for it in order.items:
        avail = available_qty(session, it)
        rows.append({"order_item_id": it.id, "product_code": it.product_code, "product_name": it.product_name,
                     "unit": it.unit, "ordered_qty": it.ordered_qty, "delivered_qty": it.delivered_qty,
                     "reserved_qty": reserved_qty(session, it.id), "remaining_qty": max(avail, ZERO),
                     "unit_price": it.unit_price, "discount_rate": it.discount_rate, "tax_rate": it.tax_rate})
    return rows


def create_from_order(session: Session, order_id: int, *, delivery_date: date, lines: list[NoteLineInput] | None = None,
                      deliverer_name: str | None = None, deliverer_phone: str | None = None,
                      receiver_name: str | None = None, warehouse_name: str | None = None,
                      notes: str | None = None, user: str | None = None) -> DeliveryNote:
    session.info["user"] = user or acting_user()
    with transaction(session):
        order = orders.get_order(session, order_id)
        if order.status not in OrderStatus.DELIVERABLE:
            raise BusinessError(f"Chỉ lập BBGN cho đơn đã duyệt hoặc đang giao từng phần (đơn đang {order.status})")
        if delivery_date < order.order_date:
            raise BusinessError("Ngày giao không được trước ngày đặt hàng")
        wanted = {ln.order_item_id: ln for ln in lines} if lines is not None else None
        if wanted is not None:
            unknown = set(wanted) - {it.id for it in order.items}
            if unknown:
                raise BusinessError(f"Dòng hàng không thuộc đơn {order.order_code}: {sorted(unknown)}")
        note = DeliveryNote(delivery_code=next_code(session, "DN", delivery_date), order_id=order.id,
                            partner_id=order.partner_id, delivery_date=delivery_date,
                            status=DeliveryNoteStatus.PREPARING, deliverer_name=deliverer_name,
                            deliverer_phone=deliverer_phone, receiver_name=receiver_name,
                            warehouse_name=warehouse_name, notes=notes, created_by=session.info["user"])
        line_no = 0
        for it in order.items:
            avail = available_qty(session, it)
            if wanted is None:
                qty = avail
            elif it.id in wanted:
                qty = D(wanted[it.id].dispatched_qty if wanted[it.id].dispatched_qty is not None else avail)
            else:
                continue
            if qty < 0:
                raise BusinessError(f"{it.product_code}: số lượng xuất không được âm")
            if qty > avail:
                raise BusinessError(f"{it.product_code}: SL xuất {qty} vượt SL còn lại cần giao {max(avail, ZERO)} "
                                    f"(đặt {D(it.ordered_qty)}, đã nhận {D(it.delivered_qty)}, "
                                    f"đang giao {reserved_qty(session, it.id)})")
            if qty == 0:
                continue
            line_no += 1
            note.items.append(DeliveryNoteItem(
                line_no=line_no, order_item_id=it.id, product_id=it.product_id, product_code=it.product_code,
                product_name=it.product_name, unit=it.unit, ordered_qty_snapshot=it.ordered_qty,
                dispatched_qty=qty, accepted_qty=qty, rejected_qty=ZERO, unit_price=it.unit_price,
                discount_rate=it.discount_rate, tax_rate=it.tax_rate, accepted_line_total=accepted_total(it, qty)))
        if not note.items:
            raise BusinessError(f"Đơn {order.order_code} không còn mặt hàng nào cần giao")
        session.add(note)
        session.flush()
        _recompute_total(note)
    return note


def _recompute_total(note: DeliveryNote) -> None:
    total = ZERO
    for it in note.items:
        it.accepted_line_total = accepted_total(it, it.accepted_qty)
        total += D(it.accepted_line_total)
    note.total_accepted_amount = total + D(note.shipping_fee or ZERO)


def _ensure_editable(note: DeliveryNote) -> None:
    if note.is_locked:
        raise LockedError(f"BBGN {note.delivery_code} đã khóa bởi biên bản đối chiếu công nợ")
    if note.status in (DeliveryNoteStatus.CONFIRMED, DeliveryNoteStatus.CANCELLED):
        raise LockedError(f"BBGN {note.delivery_code} đã {note.status} – không sửa được")


def update_note(session: Session, note_id: int, *, lines: list[NoteLineInput] | None = None,
                header: dict | None = None, user: str | None = None) -> DeliveryNote:
    """PREPARING: sửa SL xuất (<= còn lại).  IN_TRANSIT: chỉ nhập SL thực nhận / lỗi + ghi chú tình trạng."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        note = get_note(session, note_id)
        _ensure_editable(note)
        for key, value in (header or {}).items():
            if key not in {"deliverer_name", "deliverer_phone", "receiver_name", "warehouse_name", "notes",
                           "discrepancy_reason", "delivery_date"}:
                raise BusinessError(f"Không sửa được trường {key}")
            if key == "delivery_date" and note.status != DeliveryNoteStatus.PREPARING:
                raise LockedError("Chỉ đổi ngày giao khi BBGN đang chuẩn bị")
            setattr(note, key, value)
        by_item = {it.order_item_id: it for it in note.items}
        for ln in lines or []:
            row = by_item.get(ln.order_item_id)
            if row is None:
                raise BusinessError(f"Dòng hàng {ln.order_item_id} không có trên BBGN {note.delivery_code}")
            if ln.dispatched_qty is not None and D(ln.dispatched_qty) != D(row.dispatched_qty):
                if note.status != DeliveryNoteStatus.PREPARING:
                    raise LockedError("BBGN đã xuất kho – khóa số lượng xuất")
                avail = available_qty(session, row.order_item, exclude_note_id=note.id)
                qty = D(ln.dispatched_qty)
                if qty <= 0 or qty > avail:
                    raise BusinessError(f"{row.product_code}: SL xuất phải trong khoảng (0; {avail}]")
                row.dispatched_qty = qty
                row.accepted_qty, row.rejected_qty = qty, ZERO
            if ln.accepted_qty is not None or ln.rejected_qty is not None:
                if note.status != DeliveryNoteStatus.IN_TRANSIT:
                    raise BusinessError("Chỉ nhập SL thực nhận khi BBGN đang đi giao (đã xuất kho)")
                if ln.accepted_qty is not None:
                    row.accepted_qty = D(ln.accepted_qty)
                if ln.rejected_qty is not None:
                    row.rejected_qty = D(ln.rejected_qty)
            if ln.item_condition_note is not None:
                row.item_condition_note = ln.item_condition_note
        _recompute_total(note)
    return note


def dispatch(session: Session, note_id: int, *, user: str | None = None) -> DeliveryNote:
    """Kho xuất hàng, in BBGN giao cho người vận chuyển: PREPARING -> IN_TRANSIT."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        note = get_note(session, note_id)
        _ensure_editable(note)
        if note.status != DeliveryNoteStatus.PREPARING:
            raise BusinessError(f"BBGN đang {note.status} – không xuất kho được")
        for row in note.items:
            if D(row.dispatched_qty) > available_qty(session, row.order_item, exclude_note_id=note.id):
                raise BusinessError(f"{row.product_code}: SL xuất vượt SL còn lại của đơn")
        note.status = DeliveryNoteStatus.IN_TRANSIT
        note.dispatched_at = datetime.now()
    return note


def save_signature(session: Session, note_id: int, data: bytes, filename: str, *, user: str | None = None
                   ) -> DeliveryNote:
    """Lưu ảnh chụp BBGN có chữ ký người nhận (kéo-thả hoặc chụp từ điện thoại)."""
    ext = Path(filename or "bbgn.jpg").suffix.lower() or ".jpg"
    if ext not in ALLOWED_IMAGE:
        raise BusinessError("Chỉ nhận ảnh JPG/PNG/WEBP/HEIC hoặc PDF")
    if not data:
        raise BusinessError("File rỗng")
    session.info["user"] = user or acting_user()
    with transaction(session):
        note = get_note(session, note_id)
        _ensure_editable(note)
        folder = settings.upload_dir / "delivery-notes" / f"{note.delivery_date:%Y-%m}"
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(data).hexdigest()[:12]
        path = folder / f"{note.delivery_code}-{digest}{ext}"
        path.write_bytes(data)
        note.receiver_signature_url = path.relative_to(settings.upload_dir).as_posix()
    return note


def signature_path(note: DeliveryNote) -> Path | None:
    if not note.receiver_signature_url:
        return None
    path = (settings.upload_dir / note.receiver_signature_url).resolve()
    if settings.upload_dir.resolve() not in path.parents:
        return None
    return path if path.is_file() else None


def confirm(session: Session, note_id: int, *, lines: list[NoteLineInput] | None = None,
            discrepancy_reason: str | None = None, receiver_name: str | None = None,
            user: str | None = None) -> DeliveryNote:
    """Kế toán xác nhận BBGN & ghi nhận công nợ (1 transaction)."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        note = get_note(session, note_id)
        _ensure_editable(note)
        if note.status != DeliveryNoteStatus.IN_TRANSIT:
            raise BusinessError("Chỉ xác nhận BBGN đã xuất kho (đang đi giao). Bấm 'Xuất kho & đi giao' trước.")
        by_item = {it.order_item_id: it for it in note.items}
        for ln in lines or []:
            row = by_item.get(ln.order_item_id)
            if row is None:
                raise BusinessError(f"Dòng hàng {ln.order_item_id} không có trên BBGN")
            if ln.accepted_qty is not None:
                row.accepted_qty = D(ln.accepted_qty)
            if ln.rejected_qty is not None:
                row.rejected_qty = D(ln.rejected_qty)
            if ln.item_condition_note is not None:
                row.item_condition_note = ln.item_condition_note.strip() or None
        if discrepancy_reason is not None:
            note.discrepancy_reason = discrepancy_reason.strip() or None
        if receiver_name:
            note.receiver_name = receiver_name.strip()
        if not note.receiver_signature_url:
            raise BusinessError("Bắt buộc tải ảnh BBGN có chữ ký người nhận trước khi xác nhận")
        has_discrepancy = False
        for row in note.items:
            acc, rej, disp = D(row.accepted_qty), D(row.rejected_qty), D(row.dispatched_qty)
            if acc < 0 or rej < 0:
                raise BusinessError(f"{row.product_code}: số lượng không được âm")
            if acc + rej != disp:
                raise BusinessError(f"{row.product_code}: SL thực nhận ({acc}) + SL lỗi/trả lại ({rej}) "
                                    f"phải bằng SL xuất kho ({disp})")
            if rej > 0 or acc < disp:
                has_discrepancy = True
                if not (row.item_condition_note or "").strip():
                    raise BusinessError(f"{row.product_code}: có hàng lỗi/thiếu – bắt buộc ghi tình trạng hàng")
            item = session.get(OrderItem, row.order_item_id)
            if D(item.delivered_qty) + acc > D(item.ordered_qty):
                raise BusinessError(f"{row.product_code}: tổng SL thực nhận vượt SL đặt trên đơn")
        if has_discrepancy and not (note.discrepancy_reason or "").strip():
            raise BusinessError("Có hàng lỗi / giao thiếu – bắt buộc nhập lý do chênh lệch")

        order = note.order
        side = DebtSide.RECEIVABLE if order.order_type == OrderType.SALES else DebtSide.PAYABLE
        ledger.ensure_ledger_period_open(session, note.partner_id, side, note.delivery_date)
        # phí vận chuyển của đơn: tính 1 lần ở BBGN xác nhận đầu tiên
        if D(order.shipping_fee or ZERO) > 0 and not any(
                n.status == DeliveryNoteStatus.CONFIRMED and D(n.shipping_fee or ZERO) > 0
                for n in order.delivery_notes if n.id != note.id):
            note.shipping_fee = D(order.shipping_fee)
        _recompute_total(note)

        # (a) trạng thái BBGN
        note.status = DeliveryNoteStatus.CONFIRMED
        note.confirmed_by = session.info["user"]
        note.confirmed_at = datetime.now()
        # (b) lũy kế đã giao + trạng thái đơn
        for row in note.items:
            item = session.get(OrderItem, row.order_item_id)
            item.delivered_qty = D(item.delivered_qty) + D(row.accepted_qty)
        session.flush()
        orders.refresh_status(order)
        # (e) thẻ kho theo SL thực nhận
        for row in note.items:
            product = row.order_item.product
            qty = D(row.accepted_qty)
            if qty <= 0 or not product.track_stock:
                continue
            if order.order_type == OrderType.SALES:
                inventory.apply_movement(session, product, qty_change=-qty, trans_type=InvTransType.OUT_SALE,
                                         trans_date=note.delivery_date, ref_type="DN", ref_id=note.id,
                                         note=f"{note.delivery_code} / {order.order_code}")
            else:
                cost = round_price(D(row.unit_price) * (HUNDRED - D(row.discount_rate)) / HUNDRED)
                inventory.apply_movement(session, product, qty_change=qty, trans_type=InvTransType.IN_PURCHASE,
                                         trans_date=note.delivery_date, ref_type="DN", ref_id=note.id,
                                         unit_cost=cost, note=f"{note.delivery_code} / {order.order_code}")
        # (c)+(d) bút toán ghi tăng công nợ & dư nợ đối tác (khóa dòng partners)
        amount = round_money(note.total_accepted_amount)
        if amount > 0:
            term = int(note.partner.payment_term_days or 0)
            lines_txt = "; ".join(f"{r.product_code} {D(r.accepted_qty).normalize():f} {r.unit}" for r in note.items
                                  if D(r.accepted_qty) > 0)
            ledger.post_entry(
                session, partner_id=note.partner_id, side=side, transaction_date=note.delivery_date,
                entry_type=LedgerEntryType.DELIVERY_INCREASE, reference_type=LedgerRefType.DELIVERY_NOTE,
                reference_id=note.id, reference_code=note.delivery_code, debit=amount, order_id=order.id,
                order_code=order.order_code, delivery_note_id=note.id,
                due_date=note.delivery_date + timedelta(days=term),
                description=f"Giao nhận theo {order.order_code}: {lines_txt}"
                            + (" (+ phí vận chuyển)" if D(note.shipping_fee or ZERO) > 0 else ""))
    return note


def cancel(session: Session, note_id: int, reason: str, *, user: str | None = None) -> DeliveryNote:
    if not (reason or "").strip():
        raise BusinessError("Hủy BBGN bắt buộc ghi lý do")
    session.info["user"] = user or acting_user()
    with transaction(session):
        note = get_note(session, note_id)
        _ensure_editable(note)
        note.status = DeliveryNoteStatus.CANCELLED
        note.cancel_reason = reason.strip()
    return note


def list_notes(session: Session, *, order_id: int | None = None, partner_id: int | None = None,
               status: str | None = None, date_from: date | None = None, date_to: date | None = None
               ) -> list[DeliveryNote]:
    stmt = select(DeliveryNote).order_by(DeliveryNote.delivery_date.desc(), DeliveryNote.id.desc())
    if order_id:
        stmt = stmt.where(DeliveryNote.order_id == order_id)
    if partner_id:
        stmt = stmt.where(DeliveryNote.partner_id == partner_id)
    if status:
        stmt = stmt.where(DeliveryNote.status.in_(status.split(",")))
    if date_from:
        stmt = stmt.where(DeliveryNote.delivery_date >= date_from)
    if date_to:
        stmt = stmt.where(DeliveryNote.delivery_date <= date_to)
    return list(session.scalars(stmt))


def note_dict(note: DeliveryNote) -> dict:
    from ..schemas import row_dict

    d = row_dict(note)
    d["order_code"] = note.order.order_code
    d["order_date"] = note.order.order_date
    d["order_type"] = note.order.order_type
    d["order_status"] = note.order.status
    d["partner_name"] = note.partner.name
    d["partner_code"] = note.partner.code
    d["partner_address"] = note.partner.address
    d["partner_tax_code"] = note.partner.tax_code
    d["delivery_address"] = note.order.delivery_address or (note.order.site.address if note.order.site else None) \
        or note.partner.address
    d["signature_endpoint"] = f"/api/delivery-notes/{note.id}/signature" if note.receiver_signature_url else None
    d["items"] = []
    for it in note.items:
        x = row_dict(it)
        x["has_discrepancy"] = D(it.accepted_qty) < D(it.dispatched_qty) or D(it.rejected_qty) > 0
        d["items"].append(x)
    d["has_discrepancy"] = any(x["has_discrepancy"] for x in d["items"])
    return d
