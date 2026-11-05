"""Đơn đặt hàng bán (SO) / mua (PO): lập, kiểm soát hạn mức công nợ, duyệt, khóa giá, hủy, đóng phần dư.

Máy trạng thái:
    DRAFT --submit--> APPROVED            (trong hạn mức, không nợ quá hạn)
    DRAFT --submit--> PENDING_APPROVAL    (vượt hạn mức / có nợ quá hạn) --approve (MANAGER/CHIEF_ACCOUNTANT)--> APPROVED
    APPROVED --BBGN CONFIRMED--> PARTIALLY_DELIVERED --...--> COMPLETED   (tự động, xem delivery_notes.py)
    PARTIALLY_DELIVERED --close (đóng phần dư)--> COMPLETED
    DRAFT / PENDING_APPROVAL / APPROVED (chưa có BBGN xác nhận) --cancel--> CANCELLED

Quy tắc khóa: từ APPROVED trở đi KHÔNG được sửa đơn giá, chiết khấu, thuế, số lượng, khách hàng; chỉ sửa được
thông tin phụ (ngày giao dự kiến, địa chỉ giao, ghi chú). Đơn bị khóa bởi BBĐCCN (is_locked) thì không sửa/hủy.
Đơn hàng KHÔNG bao giờ sinh công nợ – công nợ chỉ sinh khi BBGN được xác nhận.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..auth import acting_user
from ..db import transaction
from ..errors import BusinessError, LockedError, NotFoundError, PermissionDeniedError
from ..models import (
    DeliveryNoteStatus,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    Partner,
    PartnerType,
    Product,
    Site,
)
from ..money import ZERO, D, round_money
from . import ledger
from .codes import ROLES_APPROVE, next_code, warn

HUNDRED = Decimal(100)


@dataclass
class OrderItemInput:
    product_id: int | None = None
    sku: str | None = None
    ordered_qty: Decimal = ZERO
    unit_price: Decimal | None = None          # trước thuế; None = lấy giá bán/giá nhập mặc định
    discount_rate: Decimal = ZERO              # %
    tax_rate: Decimal | None = None            # %; None = theo mặt hàng (VAT)
    product_name: str | None = None
    unit: str | None = None


@dataclass
class OrderInput:
    partner_id: int
    order_date: date
    order_type: str = OrderType.SALES
    site_id: int | None = None
    expected_delivery_date: date | None = None
    delivery_address: str | None = None
    shipping_fee: Decimal = ZERO
    payment_terms: str | None = None
    notes: str | None = None
    items: list[OrderItemInput] = field(default_factory=list)


def line_amounts(qty, price, discount_rate, tax_rate) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(tiền hàng, chiết khấu, thuế, thành tiền) – làm tròn tới đồng theo từng dòng."""
    base = round_money(D(qty) * D(price))
    disc = round_money(base * D(discount_rate) / HUNDRED)
    tax = round_money((base - disc) * D(tax_rate) / HUNDRED)
    return base, disc, tax, base - disc + tax


def _role(role: str | None) -> str:
    return (role or settings.default_role or "STAFF").strip().upper()


def get_order(session: Session, order_id: int) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise NotFoundError("đơn hàng", order_id)
    return order


def _resolve_product(session: Session, item: OrderItemInput) -> Product:
    product = None
    if item.product_id:
        product = session.get(Product, item.product_id)
    elif item.sku:
        product = session.scalar(select(Product).where(Product.sku == item.sku.strip().upper()))
    if product is None:
        raise NotFoundError("mặt hàng", item.product_id or item.sku)
    return product


def _build_items(session: Session, order: Order, data: OrderInput) -> None:
    if not data.items:
        raise BusinessError("Đơn hàng phải có ít nhất 1 mặt hàng")
    order.items.clear()
    session.flush()
    subtotal = disc_total = tax_total = total = ZERO
    for idx, it in enumerate(data.items, start=1):
        product = _resolve_product(session, it)
        qty = D(it.ordered_qty)
        if qty <= 0:
            raise BusinessError(f"Dòng {idx}: số lượng đặt phải lớn hơn 0")
        if it.unit_price is None:
            price = D(product.sale_price if data.order_type == OrderType.SALES else product.cost_price)
        else:
            price = D(it.unit_price)
        if price < 0:
            raise BusinessError(f"Dòng {idx}: đơn giá không được âm")
        rate = D(it.discount_rate or ZERO)
        if not ZERO <= rate <= HUNDRED:
            raise BusinessError(f"Dòng {idx}: chiết khấu phải trong khoảng 0–100%")
        tax_rate = D(it.tax_rate) if it.tax_rate is not None else D(product.vat_rate or ZERO) * HUNDRED
        base, disc, tax, line_total = line_amounts(qty, price, rate, tax_rate)
        order.items.append(OrderItem(
            line_no=idx, product_id=product.id, product_code=product.sku,
            product_name=(it.product_name or product.name).strip(), unit=(it.unit or product.unit),
            ordered_qty=qty, delivered_qty=ZERO, unit_price=price, discount_rate=rate,
            tax_rate=tax_rate.normalize() if tax_rate == tax_rate.to_integral_value() else tax_rate,
            line_total=line_total))
        subtotal += base
        disc_total += disc
        tax_total += tax
        total += line_total
    fee = round_money(data.shipping_fee or ZERO)
    if fee < 0:
        raise BusinessError("Phí vận chuyển không được âm")
    order.subtotal_amount, order.discount_amount, order.tax_amount = subtotal, disc_total, tax_total
    order.shipping_fee = fee
    order.total_amount = total + fee


def _check_header(session: Session, data: OrderInput) -> Partner:
    if data.order_type not in OrderType.ALL:
        raise BusinessError(f"Loại đơn không hợp lệ: {data.order_type}")
    partner = session.get(Partner, data.partner_id)
    if partner is None:
        raise NotFoundError("đối tác", data.partner_id)
    if data.order_type == OrderType.SALES and partner.partner_type == PartnerType.SUPPLIER:
        raise BusinessError(f"{partner.name} là nhà cung cấp – không lập đơn bán được")
    if data.order_type == OrderType.PURCHASE and partner.partner_type == PartnerType.CUSTOMER:
        raise BusinessError(f"{partner.name} là khách hàng – không lập đơn mua được")
    if data.site_id is not None:
        site = session.get(Site, data.site_id)
        if site is None or site.partner_id != partner.id:
            raise BusinessError("Điểm giao không thuộc đối tác đã chọn")
    if data.expected_delivery_date and data.expected_delivery_date < data.order_date:
        raise BusinessError("Ngày giao dự kiến không được trước ngày đặt hàng")
    return partner


def _apply_credit_control(session: Session, order: Order) -> None:
    """current_debt + order.total_amount > credit_limit  HOẶC  có nợ quá hạn  -> PENDING_APPROVAL."""
    if order.order_type != OrderType.SALES:
        order.status = OrderStatus.APPROVED
        order.approval_reason = None
        order.approved_by = session.info.get("user", settings.default_user)
        order.approved_at = datetime.now()
        return
    status = ledger.credit_status(session, order.partner_id, extra=order.total_amount, on_date=order.order_date,
                                  exclude_order_id=order.id)
    reasons = []
    if status.credit_limit and status.current_debt + D(order.total_amount) > status.credit_limit:
        reasons.append(f"Vượt hạn mức công nợ: dư nợ hiện tại {status.current_debt:,.0f} + giá trị đơn "
                       f"{D(order.total_amount):,.0f} > hạn mức {status.credit_limit:,.0f}".replace(",", "."))
    if status.overdue:
        reasons.append(f"Khách có {len(status.overdue)} khoản nợ quá hạn ({status.overdue_amount:,.0f} đ)"
                       .replace(",", "."))
    if reasons:
        order.status = OrderStatus.PENDING_APPROVAL
        order.approval_reason = "; ".join(reasons)
        warn(session, "Đơn chuyển sang CHỜ DUYỆT: " + order.approval_reason)
    else:
        order.status = OrderStatus.APPROVED
        order.approval_reason = None
        order.approved_by = "system (trong hạn mức)"
        order.approved_at = datetime.now()


def create_order(session: Session, data: OrderInput, *, submit: bool = False, user: str | None = None) -> Order:
    session.info["user"] = user or acting_user()
    with transaction(session):
        _check_header(session, data)
        prefix = "SO" if data.order_type == OrderType.SALES else "PO"
        order = Order(order_code=next_code(session, prefix, data.order_date), order_type=data.order_type,
                      partner_id=data.partner_id, site_id=data.site_id, order_date=data.order_date,
                      expected_delivery_date=data.expected_delivery_date, delivery_address=data.delivery_address,
                      payment_terms=data.payment_terms, notes=data.notes, status=OrderStatus.DRAFT,
                      created_by=session.info["user"])
        session.add(order)
        _build_items(session, order, data)
        session.flush()
        if submit:
            _apply_credit_control(session, order)
    return order


def _same_items(session: Session, order: Order, items: list[OrderItemInput]) -> bool:
    if len(items) != len(order.items):
        return False
    for it, cur in zip(items, order.items):
        product = _resolve_product(session, it)
        if product.id != cur.product_id or D(it.ordered_qty) != D(cur.ordered_qty):
            return False
        if it.unit_price is not None and D(it.unit_price) != D(cur.unit_price):
            return False
        if D(it.discount_rate or ZERO) != D(cur.discount_rate):
            return False
        if it.tax_rate is not None and D(it.tax_rate) != D(cur.tax_rate):
            return False
    return True


def update_order(session: Session, order_id: int, data: OrderInput, *, role: str | None = None,
                 user: str | None = None) -> Order:
    session.info["user"] = user or acting_user()
    role = _role(role)
    with transaction(session):
        order = get_order(session, order_id)
        if order.is_locked:
            raise LockedError(f"Đơn {order.order_code} đã khóa bởi biên bản đối chiếu công nợ")
        if order.status in (OrderStatus.CANCELLED, OrderStatus.COMPLETED):
            raise LockedError(f"Đơn {order.order_code} đã {order.status} – không sửa được")
        if order.status == OrderStatus.PENDING_APPROVAL and role not in ROLES_APPROVE:
            raise PermissionDeniedError("Đơn đang chờ duyệt – chỉ Quản lý / Kế toán trưởng được sửa")
        if order.status in OrderStatus.DELIVERABLE:
            # đã duyệt: chỉ sửa thông tin phụ. Mọi thay đổi giá/CK/thuế/SL/khách phải lập phụ lục (đơn mới)
            if data.partner_id != order.partner_id or data.order_type != order.order_type:
                raise LockedError("Đơn đã duyệt – không đổi khách hàng / loại đơn")
            if data.items and not _same_items(session, order, data.items):
                raise LockedError(f"Đơn {order.order_code} đã duyệt: khóa đơn giá, chiết khấu, thuế và số lượng. "
                                  "Muốn thay đổi hãy lập phụ lục / đơn bổ sung.")
            if round_money(data.shipping_fee or ZERO) != D(order.shipping_fee):
                raise LockedError("Đơn đã duyệt – không sửa phí vận chuyển")
            order.expected_delivery_date = data.expected_delivery_date
            order.delivery_address = data.delivery_address
            order.notes = data.notes
            order.payment_terms = data.payment_terms
            session.flush()
            return order
        _check_header(session, data)
        order.partner_id, order.site_id, order.order_date = data.partner_id, data.site_id, data.order_date
        order.order_type = data.order_type
        order.expected_delivery_date, order.delivery_address = data.expected_delivery_date, data.delivery_address
        order.payment_terms, order.notes = data.payment_terms, data.notes
        _build_items(session, order, data)
        session.flush()
    return order


def submit_order(session: Session, order_id: int, *, user: str | None = None) -> Order:
    """Gửi duyệt: hệ thống tự kiểm tra hạn mức & nợ quá hạn rồi chuyển APPROVED hoặc PENDING_APPROVAL."""
    session.info["user"] = user or acting_user()
    with transaction(session):
        order = get_order(session, order_id)
        if order.status != OrderStatus.DRAFT:
            raise BusinessError(f"Chỉ gửi duyệt đơn nháp (đơn đang {order.status})")
        _apply_credit_control(session, order)
    return order


def approve_order(session: Session, order_id: int, *, role: str | None = None, user: str | None = None,
                  note: str | None = None) -> Order:
    session.info["user"] = user or acting_user()
    role = _role(role)
    with transaction(session):
        order = get_order(session, order_id)
        if order.status == OrderStatus.DRAFT:
            _apply_credit_control(session, order)
            if order.status == OrderStatus.APPROVED:
                return order
        if order.status != OrderStatus.PENDING_APPROVAL:
            raise BusinessError(f"Đơn {order.order_code} không ở trạng thái chờ duyệt ({order.status})")
        if role not in ROLES_APPROVE:
            raise PermissionDeniedError("Chỉ Quản lý hoặc Kế toán trưởng được duyệt đơn vượt hạn mức / có nợ quá hạn")
        order.status = OrderStatus.APPROVED
        order.approved_by = session.info["user"]
        order.approved_at = datetime.now()
        if note:
            order.approval_reason = f"{order.approval_reason or ''} | Duyệt: {note}".strip(" |")
    return order


def cancel_order(session: Session, order_id: int, reason: str, *, role: str | None = None,
                 user: str | None = None) -> Order:
    if not (reason or "").strip():
        raise BusinessError("Hủy đơn bắt buộc ghi lý do")
    session.info["user"] = user or acting_user()
    with transaction(session):
        order = get_order(session, order_id)
        if order.is_locked:
            raise LockedError(f"Đơn {order.order_code} đã khóa bởi biên bản đối chiếu công nợ")
        if order.status in (OrderStatus.CANCELLED, OrderStatus.COMPLETED, OrderStatus.PARTIALLY_DELIVERED):
            raise BusinessError(f"Không hủy được đơn ở trạng thái {order.status}"
                                + (" – hãy dùng 'Đóng phần còn lại'" if order.status == OrderStatus.PARTIALLY_DELIVERED
                                   else ""))
        if order.status == OrderStatus.APPROVED and _role(role) not in ROLES_APPROVE | {"ACCOUNTANT"}:
            raise PermissionDeniedError("Đơn đã duyệt – chỉ Kế toán / Quản lý được hủy")
        open_notes = [n for n in order.delivery_notes if n.status != DeliveryNoteStatus.CANCELLED]
        if open_notes:
            raise BusinessError("Đơn đã có biên bản giao nhận – hủy các BBGN chưa xác nhận trước: "
                                + ", ".join(n.delivery_code for n in open_notes))
        order.status = OrderStatus.CANCELLED
        order.cancel_reason = reason.strip()
    return order


def close_order(session: Session, order_id: int, reason: str, *, role: str | None = None,
                user: str | None = None) -> Order:
    """Đóng phần còn lại chưa giao (khách không lấy nữa): PARTIALLY_DELIVERED -> COMPLETED."""
    if not (reason or "").strip():
        raise BusinessError("Đóng đơn bắt buộc ghi lý do")
    if _role(role) not in ROLES_APPROVE | {"ACCOUNTANT"}:
        raise PermissionDeniedError("Chỉ Kế toán / Quản lý được đóng phần còn lại của đơn")
    session.info["user"] = user or acting_user()
    with transaction(session):
        order = get_order(session, order_id)
        if order.status != OrderStatus.PARTIALLY_DELIVERED:
            raise BusinessError("Chỉ đóng phần còn lại cho đơn đang giao từng phần")
        pending = [n.delivery_code for n in order.delivery_notes
                   if n.status in (DeliveryNoteStatus.PREPARING, DeliveryNoteStatus.IN_TRANSIT)]
        if pending:
            raise BusinessError("Còn BBGN chưa xác nhận: " + ", ".join(pending))
        order.status = OrderStatus.COMPLETED
        order.notes = f"{order.notes or ''}\n[Đóng phần còn lại: {reason.strip()}]".strip()
    return order


def refresh_status(order: Order) -> None:
    """Gọi sau khi BBGN xác nhận: cập nhật trạng thái theo lũy kế số lượng thực nhận."""
    if order.status not in OrderStatus.DELIVERABLE:
        return
    delivered = sum((D(i.delivered_qty) for i in order.items), ZERO)
    if delivered <= 0:
        order.status = OrderStatus.APPROVED
    elif all(D(i.delivered_qty) >= D(i.ordered_qty) for i in order.items):
        order.status = OrderStatus.COMPLETED
    else:
        order.status = OrderStatus.PARTIALLY_DELIVERED


def list_orders(session: Session, *, status: str | None = None, partner_id: int | None = None,
                order_type: str | None = None, date_from: date | None = None, date_to: date | None = None
                ) -> list[Order]:
    stmt = select(Order).order_by(Order.order_date.desc(), Order.id.desc())
    if status:
        stmt = stmt.where(Order.status.in_(status.split(",")))
    if partner_id:
        stmt = stmt.where(Order.partner_id == partner_id)
    if order_type:
        stmt = stmt.where(Order.order_type == order_type)
    if date_from:
        stmt = stmt.where(Order.order_date >= date_from)
    if date_to:
        stmt = stmt.where(Order.order_date <= date_to)
    return list(session.scalars(stmt))


def progress(order: Order) -> dict:
    ordered = sum((D(i.ordered_qty) for i in order.items), ZERO)
    delivered = sum((D(i.delivered_qty) for i in order.items), ZERO)
    items_done = sum(1 for i in order.items if D(i.delivered_qty) >= D(i.ordered_qty))
    pct = (delivered * 100 / ordered).quantize(Decimal("0.1")) if ordered else ZERO
    return {"ordered_qty": ordered, "delivered_qty": delivered, "items_total": len(order.items),
            "items_completed": items_done, "percent": pct}


def order_dict(order: Order, *, with_notes: bool = True) -> dict:
    from ..schemas import row_dict

    d = row_dict(order)
    d["partner_name"] = order.partner.name if order.partner else None
    d["partner_code"] = order.partner.code if order.partner else None
    d["site_name"] = order.site.name if order.site else None
    d["items"] = []
    for it in order.items:
        x = row_dict(it)
        x["remaining_qty"] = it.remaining_qty
        d["items"].append(x)
    d["progress"] = progress(order)
    d["can_create_delivery"] = order.status in OrderStatus.DELIVERABLE and any(
        it.remaining_qty > 0 for it in order.items)
    d["price_locked"] = order.status not in (OrderStatus.DRAFT, OrderStatus.PENDING_APPROVAL)
    if with_notes:
        d["delivery_notes"] = [{
            "id": n.id, "delivery_code": n.delivery_code, "delivery_date": n.delivery_date, "status": n.status,
            "total_accepted_amount": n.total_accepted_amount, "receiver_name": n.receiver_name,
            "has_signature": bool(n.receiver_signature_url), "is_locked": n.is_locked,
            "discrepancy_reason": n.discrepancy_reason,
        } for n in order.delivery_notes]
    return d
