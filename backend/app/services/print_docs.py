"""Dữ liệu cho 3 mẫu in A4: Đơn đặt hàng (PO/SO), Biên bản giao nhận (BBGN), Biên bản đối chiếu công nợ (BBĐCCN).

Trang ``frontend/print.html`` gọi ``GET /api/print/{order|dn|dr}/{id}`` rồi dựng mẫu in (in trực tiếp / Lưu PDF
từ trình duyệt). Mã QR (``/qr.svg``) chứa mã chứng từ + đường dẫn mở chứng từ trong phần mềm.
"""
from __future__ import annotations

import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessError
from ..models import CompanyProfile
from ..schemas import row_dict
from ..utils.so_thanh_chu import so_thanh_chu
from . import delivery_notes, orders, reconciliations

DOC_TYPES = {"order", "dn", "dr"}


def _company(session: Session) -> dict:
    c = session.scalar(select(CompanyProfile).limit(1))
    return row_dict(c) if c else {"name": "(Chưa khai báo tên công ty – vào Cài đặt)", "tax_code": None,
                                  "address": None, "phones": None, "sign_city": "Hà Nội"}


def _code_and_link(session: Session, doc_type: str, doc_id: int) -> tuple[str, str]:
    if doc_type == "order":
        return orders.get_order(session, doc_id).order_code, f"#/orders?id={doc_id}"
    if doc_type == "dn":
        return delivery_notes.get_note(session, doc_id).delivery_code, f"#/delivery?id={doc_id}"
    if doc_type == "dr":
        return reconciliations.get_reconciliation(session, doc_id).reconciliation_code, f"#/reconcile?id={doc_id}"
    raise BusinessError(f"Loại chứng từ in không hợp lệ: {doc_type}")


def print_payload(session: Session, doc_type: str, doc_id: int) -> dict:
    if doc_type not in DOC_TYPES:
        raise BusinessError(f"Loại chứng từ in không hợp lệ: {doc_type}")
    payload: dict = {"type": doc_type, "company": _company(session), "printed_at": datetime.now()}
    code, link = _code_and_link(session, doc_type, doc_id)
    payload["code"], payload["link"] = code, link
    if doc_type == "order":
        o = orders.get_order(session, doc_id)
        d = orders.order_dict(o)
        p = o.partner
        d["partner"] = row_dict(p)
        d["amount_words"] = so_thanh_chu(o.total_amount, "standard")
        payload["doc"] = d
    elif doc_type == "dn":
        n = delivery_notes.get_note(session, doc_id)
        d = delivery_notes.note_dict(n)
        d["partner"] = row_dict(n.partner)
        d["amount_words"] = so_thanh_chu(n.total_accepted_amount, "standard")
        payload["doc"] = d
    else:
        r = reconciliations.get_reconciliation(session, doc_id)
        d = reconciliations.rec_dict(r)
        d["partner"] = row_dict(r.partner)
        d["amount_words"] = so_thanh_chu(abs(r.closing_balance), "standard")
        # phụ lục chi tiết từng lần giao nhận: Số ĐĐH + Số BBGN + Mặt hàng + SL thực nhận + Đơn giá + Thành tiền
        appendix = []
        for ln in r.lines:
            if not ln.delivery_note_id:
                continue
            note = delivery_notes.get_note(session, ln.delivery_note_id)
            for it in note.items:
                if it.accepted_qty == 0:
                    continue
                appendix.append({"date": note.delivery_date, "order_code": note.order.order_code,
                                 "delivery_code": note.delivery_code, "product_code": it.product_code,
                                 "product_name": it.product_name, "unit": it.unit, "accepted_qty": it.accepted_qty,
                                 "unit_price": it.unit_price, "discount_rate": it.discount_rate,
                                 "tax_rate": it.tax_rate, "amount": it.accepted_line_total})
            if note.shipping_fee:
                appendix.append({"date": note.delivery_date, "order_code": note.order.order_code,
                                 "delivery_code": note.delivery_code, "product_code": "", "unit": "",
                                 "product_name": "Phí vận chuyển", "accepted_qty": None, "unit_price": None,
                                 "discount_rate": None, "tax_rate": None, "amount": note.shipping_fee})
        d["appendix"] = appendix
        payload["doc"] = d
    return payload


def qr_svg(session: Session, doc_type: str, doc_id: int) -> bytes:
    import segno

    code, link = _code_and_link(session, doc_type, doc_id)
    qr = segno.make(f"{code}|{link}", error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=4, border=1, xmldecl=False, svgns=True, dark="#000")
    return buf.getvalue()
