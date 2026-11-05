"""Quy trình Đơn đặt hàng -> BBGN -> Sổ cái -> Đối chiếu công nợ.

Ca kiểm thử bắt buộc: giao hàng 1 phần, giao thiếu / hàng lỗi, vượt hạn mức công nợ, khóa sổ đối chiếu.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import get_session, transaction
from app.errors import BusinessError, LockedError, PermissionDeniedError
from app.main import app
from app.models import (
    DeliveryNoteStatus,
    LedgerEntryType,
    OrderStatus,
    Partner,
    ReconciliationStatus,
)
from app.services import cashbook, ledger, masterdata, orders, reconciliations
from app.services import delivery_notes as dn

D = Decimal
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def _tmp_upload_dir(tmp_path):
    """Ảnh ký nhận ghi vào thư mục tạm, không đụng data/uploads thật."""
    from app.config import settings

    old = settings.upload_dir
    object.__setattr__(settings, "upload_dir", tmp_path)
    yield
    object.__setattr__(settings, "upload_dir", old)


@pytest.fixture()
def base(session):
    with transaction(session):
        cust = masterdata.create_partner(session, code="KH01", name="Công ty TNHH Bếp Ăn Sạch", tax_code="0101234567",
                                         credit_limit=D("100000000"), payment_term_days=30)
        sup = masterdata.create_partner(session, code="NCC01", name="Tổng kho Gas", partner_type="SUPPLIER")
        p45 = masterdata.create_product(session, sku="PC45", name="Gas Petrolimex 45kg", capacity_kg="45",
                                        sale_price="1000000", vat_rate="0.08")
        stove = masterdata.create_product(session, sku="BEP2", name="Bếp công nghiệp 2 họng", kind="ACCESSORY",
                                          unit="cái", sale_price="2500000", vat_rate="0.1")
    return {"cust": cust, "sup": sup, "p45": p45, "stove": stove}


def _order(session, base, *, qty45="10", qty_stove="2", day=date(2026, 11, 2), submit=True, **kw):
    data = orders.OrderInput(partner_id=base["cust"].id, order_date=day, items=[
        orders.OrderItemInput(product_id=base["p45"].id, ordered_qty=D(qty45), unit_price=D("1000000"),
                              discount_rate=D("5"), tax_rate=D("8")),
        orders.OrderItemInput(product_id=base["stove"].id, ordered_qty=D(qty_stove), unit_price=D("2500000"),
                              tax_rate=D("10")),
    ], **kw)
    return orders.create_order(session, data, submit=submit)


def _deliver(session, order, lines, *, day=date(2026, 11, 5), accepted=None, reason=None):
    note = dn.create_from_order(session, order.id, delivery_date=day,
                                lines=[dn.NoteLineInput(order_item_id=i, dispatched_qty=D(q)) for i, q in lines])
    dn.dispatch(session, note.id)
    dn.save_signature(session, note.id, PNG, "ky-nhan.png")
    return dn.confirm(session, note.id, lines=accepted, discrepancy_reason=reason)


# ---------------------------------------------------------------- tính tiền & mã chứng từ
def test_order_amounts_and_code(session, base):
    order = _order(session, base)
    assert order.order_code == "SO-202611-0001"
    # PC45: 10 × 1.000.000 = 10.000.000, CK 5% = 500.000, thuế 8% của 9.500.000 = 760.000 -> 10.260.000
    # Bếp: 2 × 2.500.000 = 5.000.000, thuế 10% = 500.000 -> 5.500.000
    assert order.subtotal_amount == D("15000000")
    assert order.discount_amount == D("500000")
    assert order.tax_amount == D("1260000")
    assert order.total_amount == D("15760000")
    assert order.status == OrderStatus.APPROVED
    assert _order(session, base).order_code == "SO-202611-0002"


def test_no_debt_from_order_itself(session, base):
    _order(session, base)
    session.refresh(base["cust"])
    assert base["cust"].current_debt == 0
    assert ledger.list_entries(session, partner_id=base["cust"].id) == []


# ---------------------------------------------------------------- giao hàng từng phần
def test_partial_delivery(session, base):
    order = _order(session, base)
    i45, istove = order.items
    preview = dn.preview_for_order(session, order.id)
    assert [r["remaining_qty"] for r in preview] == [D(10), D(2)]

    note1 = _deliver(session, order, [(i45.id, "6")])
    assert note1.delivery_code == "DN-202611-0001"
    assert note1.status == DeliveryNoteStatus.CONFIRMED
    session.refresh(order)
    assert order.status == OrderStatus.PARTIALLY_DELIVERED
    assert i45.delivered_qty == D(6) and istove.delivered_qty == 0
    # 6 × 1.000.000 × 95% × 108% = 6.156.000
    assert note1.total_accepted_amount == D("6156000")
    entries = ledger.list_entries(session, partner_id=base["cust"].id)
    assert len(entries) == 1
    e = entries[0]
    assert (e.entry_type, e.debit_amount, e.order_id, e.delivery_note_id) == (
        LedgerEntryType.DELIVERY_INCREASE, D("6156000"), order.id, note1.id)
    assert e.due_date == date(2026, 12, 5)
    session.refresh(base["cust"])
    assert base["cust"].current_debt == D("6156000")

    # chặn xuất vượt SL còn lại (còn 4)
    with pytest.raises(BusinessError, match="vượt SL còn lại"):
        dn.create_from_order(session, order.id, delivery_date=date(2026, 11, 6),
                             lines=[dn.NoteLineInput(order_item_id=i45.id, dispatched_qty=D(5))])
    # BBGN đang chuẩn bị cũng giữ chỗ số lượng
    pending = dn.create_from_order(session, order.id, delivery_date=date(2026, 11, 6),
                                   lines=[dn.NoteLineInput(order_item_id=i45.id, dispatched_qty=D(3))])
    with pytest.raises(BusinessError, match="vượt SL còn lại"):
        dn.create_from_order(session, order.id, delivery_date=date(2026, 11, 6),
                             lines=[dn.NoteLineInput(order_item_id=i45.id, dispatched_qty=D(2))])
    dn.cancel(session, pending.id, "Lập nhầm")

    # giao nốt toàn bộ phần còn lại (không truyền lines = điền sẵn SL còn lại)
    note2 = dn.create_from_order(session, order.id, delivery_date=date(2026, 11, 8))
    assert {(it.product_code, it.dispatched_qty) for it in note2.items} == {("PC45", D(4)), ("BEP2", D(2))}
    dn.dispatch(session, note2.id)
    dn.save_signature(session, note2.id, PNG, "bbgn.jpg")
    dn.confirm(session, note2.id)
    session.refresh(order)
    assert order.status == OrderStatus.COMPLETED
    session.refresh(base["cust"])
    assert base["cust"].current_debt == order.total_amount == D("15760000")
    with pytest.raises(BusinessError):
        dn.create_from_order(session, order.id, delivery_date=date(2026, 11, 9))


# ---------------------------------------------------------------- giao thiếu / hàng lỗi
def test_short_and_damaged_delivery(session, base):
    order = _order(session, base)
    i45 = order.items[0]
    note = dn.create_from_order(session, order.id, delivery_date=date(2026, 11, 5),
                                lines=[dn.NoteLineInput(order_item_id=i45.id, dispatched_qty=D(10))])
    with pytest.raises(BusinessError, match="xuất kho"):
        dn.confirm(session, note.id)                       # chưa xuất kho
    dn.dispatch(session, note.id)
    with pytest.raises(LockedError):
        dn.update_note(session, note.id, lines=[dn.NoteLineInput(order_item_id=i45.id, dispatched_qty=D(9))])
    bad = [dn.NoteLineInput(order_item_id=i45.id, accepted_qty=D(8), rejected_qty=D(1))]
    with pytest.raises(BusinessError, match="ảnh BBGN"):
        dn.confirm(session, note.id, lines=bad)            # chưa có ảnh ký nhận
    dn.save_signature(session, note.id, PNG, "bbgn.png")
    with pytest.raises(BusinessError, match="phải bằng SL xuất kho"):
        dn.confirm(session, note.id, lines=bad)            # 8 + 1 != 10
    ok_qty = [dn.NoteLineInput(order_item_id=i45.id, accepted_qty=D(8), rejected_qty=D(2))]
    with pytest.raises(BusinessError, match="tình trạng"):
        dn.confirm(session, note.id, lines=ok_qty)         # thiếu ghi chú tình trạng
    ok_qty[0].item_condition_note = "2 bình móp van, khách trả lại"
    with pytest.raises(BusinessError, match="lý do chênh lệch"):
        dn.confirm(session, note.id, lines=ok_qty)         # thiếu lý do chênh lệch
    note = dn.confirm(session, note.id, lines=ok_qty, discrepancy_reason="Hàng lỗi van khi giao")
    assert note.status == DeliveryNoteStatus.CONFIRMED
    # công nợ tính theo SL THỰC NHẬN (8), không theo SL đặt/xuất: 8 × 1.000.000 × 0,95 × 1,08
    assert note.total_accepted_amount == D("8208000")
    session.refresh(order)
    assert order.items[0].delivered_qty == D(8)
    assert order.status == OrderStatus.PARTIALLY_DELIVERED
    assert ledger.list_entries(session, partner_id=base["cust"].id)[0].debit_amount == D("8208000")
    # tồn kho chỉ trừ số khách thực nhận
    session.refresh(base["p45"])
    assert base["p45"].current_stock == D(-8)
    # 2 bình lỗi được giao bù ở BBGN sau
    preview = dn.preview_for_order(session, order.id)
    assert preview[0]["remaining_qty"] == D(2)
    with pytest.raises(LockedError):
        dn.update_note(session, note.id, header={"notes": "sửa sau xác nhận"})


# ---------------------------------------------------------------- hạn mức công nợ
def test_credit_limit_exceeded_goes_pending(session, base):
    first = _order(session, base, qty45="50", qty_stove="0.0001")   # ~51,3 triệu -> trong hạn mức 100 triệu
    assert first.status == OrderStatus.APPROVED
    _deliver(session, first, [(first.items[0].id, "50")])
    session.refresh(base["cust"])
    assert base["cust"].current_debt == D("51300000")
    # 51,3 triệu + đơn mới ~61,5 triệu > 100 triệu -> chờ duyệt
    second = _order(session, base, qty45="60", qty_stove="0.0001")
    assert second.status == OrderStatus.PENDING_APPROVAL
    assert "Vượt hạn mức" in second.approval_reason
    with pytest.raises(BusinessError):
        dn.create_from_order(session, second.id, delivery_date=date(2026, 11, 6))
    with pytest.raises(PermissionDeniedError):
        orders.approve_order(session, second.id, role="STAFF")
    with pytest.raises(PermissionDeniedError):
        orders.update_order(session, second.id, orders.OrderInput(partner_id=base["cust"].id,
                                                                  order_date=date(2026, 11, 2), items=[
            orders.OrderItemInput(product_id=base["p45"].id, ordered_qty=D(1))]), role="STAFF")
    approved = orders.approve_order(session, second.id, role="CHIEF_ACCOUNTANT", user="ketoantruong")
    assert approved.status == OrderStatus.APPROVED and approved.approved_by == "ketoantruong"


def test_overdue_debt_goes_pending(session, base):
    with transaction(session):
        base["cust"].credit_limit = None                # không giới hạn hạn mức
    old = _order(session, base, qty45="1", qty_stove="0.0001", day=date(2026, 9, 1))
    _deliver(session, old, [(old.items[0].id, "1")], day=date(2026, 9, 2))   # hạn 02/10/2026
    st = ledger.credit_status(session, base["cust"].id, on_date=date(2026, 11, 2))
    assert st.overdue and st.level == "red"
    new = _order(session, base, qty45="1", qty_stove="0.0001")
    assert new.status == OrderStatus.PENDING_APPROVAL and "quá hạn" in new.approval_reason
    # khách trả hết -> hết quá hạn -> đơn mới tự duyệt
    ledger.record_payment(session, partner_id=base["cust"].id, payment_date=date(2026, 11, 2),
                          amount=D("1026000"))
    assert _order(session, base, qty45="1", qty_stove="0.0001").status == OrderStatus.APPROVED


def test_price_locked_after_approval(session, base):
    order = _order(session, base)
    changed = orders.OrderInput(partner_id=base["cust"].id, order_date=order.order_date, items=[
        orders.OrderItemInput(product_id=base["p45"].id, ordered_qty=D(10), unit_price=D("900000"),
                              discount_rate=D("5"), tax_rate=D("8")),
        orders.OrderItemInput(product_id=base["stove"].id, ordered_qty=D(2), unit_price=D("2500000"),
                              tax_rate=D("10"))])
    with pytest.raises(LockedError, match="khóa đơn giá"):
        orders.update_order(session, order.id, changed)
    changed.items[0].unit_price = D("1000000")
    changed.notes = "Giao trước 9h sáng"
    assert orders.update_order(session, order.id, changed).notes == "Giao trước 9h sáng"


# ---------------------------------------------------------------- đối chiếu & khóa sổ
def test_reconciliation_and_hard_lock(session, base):
    cust = base["cust"]
    order = _order(session, base)
    note1 = _deliver(session, order, [(order.items[0].id, "10")], day=date(2026, 11, 5))   # 10.260.000
    pay = ledger.record_payment(session, partner_id=cust.id, payment_date=date(2026, 11, 20), amount=D("5000000"),
                                order_id=order.id)
    assert pay.reference_code.startswith("PT-")
    ledger.record_return(session, partner_id=cust.id, return_date=date(2026, 11, 25), amount=D("260000"),
                         description="Giảm giá bình móp")

    rec = reconciliations.create(session, partner_id=cust.id, period_from=date(2026, 11, 1),
                                 period_to=date(2026, 11, 30))
    assert rec.reconciliation_code == "DR-202611-0001"
    assert (rec.opening_balance, rec.total_increase, rec.total_decrease, rec.closing_balance) == (
        D(0), D("10260000"), D("5260000"), D("5000000"))
    assert [ln.document_code for ln in rec.lines][0] == note1.delivery_code
    assert rec.lines[0].order_code == order.order_code
    assert rec.lines[1].order_code == order.order_code          # phiếu thu gắn đơn hàng
    assert [ln.line_balance for ln in rec.lines] == [D("10260000"), D("5260000"), D("5000000")]

    reconciliations.mark_sent(session, rec.id)
    rec = reconciliations.dispute_line(session, rec.id, rec.lines[0].id, "Khách báo thiếu 1 bình")
    assert rec.status == ReconciliationStatus.DISPUTED
    with pytest.raises(BusinessError, match="chênh lệch"):
        reconciliations.reconcile(session, rec.id)
    rec = reconciliations.resolve_line(session, rec.id, rec.lines[0].id, note="Đối chiếu ảnh ký nhận: đủ 10 bình")
    assert rec.status == ReconciliationStatus.SENT

    # phát sinh mới trong kỳ sau khi lập biên bản -> phải làm mới trước khi chốt
    ledger.record_adjustment(session, partner_id=cust.id, adjust_date=date(2026, 11, 28), amount=D("-100000"),
                             reason="Thưởng doanh số")
    with pytest.raises(BusinessError, match="Làm mới"):
        reconciliations.reconcile(session, rec.id)
    rec = reconciliations.refresh(session, rec.id)
    assert rec.closing_balance == D("4900000") and rec.status == ReconciliationStatus.DRAFT
    rec = reconciliations.reconcile(session, rec.id, partner_confirmed_by="Nguyễn Văn A – Kế toán")
    assert rec.status == ReconciliationStatus.RECONCILED

    session.refresh(note1)
    assert note1.is_locked
    assert all(e.is_locked and e.reconciliation_id == rec.id
               for e in ledger.list_entries(session, partner_id=cust.id))
    session.refresh(order)
    assert order.is_locked
    voucher = cashbook.get_voucher(session, pay.reference_id)
    assert voucher.is_locked
    # chặn mọi thao tác sửa / xóa chứng từ đã khóa
    with pytest.raises(LockedError):
        cashbook.cancel_voucher(session, voucher.id, "nhầm")
    with pytest.raises(LockedError):
        dn.cancel(session, note1.id, "nhầm")
    with pytest.raises(LockedError):
        orders.cancel_order(session, order.id, "nhầm")
    with pytest.raises(LockedError):
        ledger.record_payment(session, partner_id=cust.id, payment_date=date(2026, 11, 29), amount=D(1000))
    with pytest.raises(LockedError):
        reconciliations.create(session, partner_id=cust.id, period_from=date(2026, 11, 15),
                               period_to=date(2026, 12, 15))
    # phần còn lại của đơn vẫn giao được ở kỳ sau; điều chỉnh hạch toán kỳ sau
    _deliver(session, order, [(order.items[1].id, "2")], day=date(2026, 12, 3))
    ledger.record_adjustment(session, partner_id=cust.id, adjust_date=date(2026, 12, 1), amount=D("50000"),
                             reason="Điều chỉnh sau đối chiếu T11")
    dec = reconciliations.create(session, partner_id=cust.id, period_from=date(2026, 12, 1),
                                 period_to=date(2026, 12, 31))
    assert dec.opening_balance == D("4900000")                 # = closing của kỳ đã chốt
    assert dec.closing_balance == D("4900000") + D("50000") + D("5500000")
    session.refresh(cust)
    assert cust.current_debt == dec.closing_balance


def test_cancel_payment_reverses_ledger(session, base):
    order = _order(session, base)
    _deliver(session, order, [(order.items[0].id, "10")])
    pay = ledger.record_payment(session, partner_id=base["cust"].id, payment_date=date(2026, 11, 10),
                                amount=D("1000000"))
    cashbook.cancel_voucher(session, pay.reference_id, "Ngân hàng trả lại")
    session.refresh(base["cust"])
    assert base["cust"].current_debt == D("10260000")
    types = [e.entry_type for e in ledger.list_entries(session, partner_id=base["cust"].id)]
    assert types == ["DELIVERY_INCREASE", "PAYMENT_DECREASE", "ADJUSTMENT"]


def test_purchase_order_creates_payable_and_stock(session, base):
    data = orders.OrderInput(partner_id=base["sup"].id, order_date=date(2026, 11, 1), order_type="PURCHASE", items=[
        orders.OrderItemInput(product_id=base["p45"].id, ordered_qty=D(20), unit_price=D("800000"),
                              tax_rate=D("8"))])
    po = orders.create_order(session, data, submit=True)
    assert po.order_code.startswith("PO-") and po.status == OrderStatus.APPROVED
    _deliver(session, po, [(po.items[0].id, "20")])
    session.refresh(base["sup"])
    session.refresh(base["p45"])
    assert base["sup"].current_payable == D("17280000") and base["sup"].current_debt == 0
    assert base["p45"].current_stock == D(20) and base["p45"].cost_price == D("800000")


# ---------------------------------------------------------------- HTTP API
def _client(engine):
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_http_flow(engine, session, base):
    c = _client(engine)
    cust_id = base["cust"].id
    body = {"partner_id": cust_id, "order_date": "2026-11-02", "submit": True,
            "items": [{"sku": "PC45", "ordered_qty": "4", "unit_price": "1000000", "tax_rate": "8"}]}
    r = c.post("/api/orders", json=body)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "APPROVED" and order["total_amount"] == "4320000"
    assert order["can_create_delivery"] is True and order["price_locked"] is True

    r = c.post("/api/delivery-notes", json={"order_id": order["id"], "delivery_date": "2026-11-03",
                                            "lines": [{"order_item_id": order["items"][0]["id"],
                                                       "dispatched_qty": "5"}]})
    assert r.status_code == 400 and "vượt" in r.json()["detail"]
    note = c.post(f"/api/orders/{order['id']}/delivery-notes",
                  json={"order_id": order["id"], "delivery_date": "2026-11-03"}).json()
    nid = note["id"]
    assert c.post(f"/api/delivery-notes/{nid}/dispatch").json()["status"] == "IN_TRANSIT"
    r = c.post(f"/api/delivery-notes/{nid}/signature", files={"file": ("ky.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["signature_endpoint"]
    assert c.get(f"/api/delivery-notes/{nid}/signature").content == PNG
    r = c.post(f"/api/delivery-notes/{nid}/confirm", json={"receiver_name": "Chị Lan – bếp trưởng"})
    assert r.status_code == 200, r.text
    detail = c.get(f"/api/orders/{order['id']}").json()
    assert detail["status"] == "COMPLETED" and detail["progress"]["percent"] == "100"
    assert detail["delivery_notes"][0]["has_signature"] is True
    assert detail["credit"]["ledger_debt"] == "4320000"
    assert c.get(f"/api/partners/{cust_id}/credit").json()["level"] in ("green", "yellow")

    r = c.post("/api/reconciliations", json={"partner_id": cust_id, "period_from": "2026-11-01",
                                             "period_to": "2026-11-30"})
    assert r.status_code == 201
    rec = r.json()
    assert rec["closing_balance"] == "4320000" and rec["lines"][0]["order_code"] == order["order_code"]
    r = c.post(f"/api/reconciliations/{rec['id']}/lines/{rec['lines'][0]['id']}/dispute", json={"comment": "lệch"})
    assert r.json()["status"] == "DISPUTED" and r.json()["can_reconcile"] is False
    assert c.post(f"/api/reconciliations/{rec['id']}/reconcile").status_code == 400
    c.post(f"/api/reconciliations/{rec['id']}/lines/{rec['lines'][0]['id']}/resolve", json={})
    assert c.post(f"/api/reconciliations/{rec['id']}/reconcile",
                  json={"partner_confirmed_by": "Bên B"}).json()["status"] == "RECONCILED"
    r = c.post(f"/api/delivery-notes/{nid}/cancel", json={"reason": "x"})
    assert r.status_code == 409
    r = c.post("/api/ledger/payments", json={"partner_id": cust_id, "payment_date": "2026-11-15", "amount": "1"})
    assert r.status_code == 409 and r.json()["code"] == "locked"
    assert c.get("/api/reconciliations/active-partners",
                 params={"date_from": "2026-11-01", "date_to": "2026-11-30"}).json()[0]["partner_id"] == cust_id
    app.dependency_overrides.clear()


def test_http_role_header(engine, session, base):
    c = _client(engine)
    with transaction(session):
        session.get(Partner, base["cust"].id).credit_limit = D(1000)
    body = {"partner_id": base["cust"].id, "order_date": "2026-11-02", "submit": True,
            "items": [{"sku": "PC45", "ordered_qty": "1"}]}
    order = c.post("/api/orders", json=body).json()
    assert order["status"] == "PENDING_APPROVAL" and order["warnings"]
    r = c.post(f"/api/orders/{order['id']}/approve", json={}, headers={"X-Role": "STAFF"})
    assert r.status_code == 403
    r = c.post(f"/api/orders/{order['id']}/approve", json={"note": "Khách quen"},
               headers={"X-Role": "MANAGER", "X-User": "quanly"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["approved_by"] == "quanly"
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- mẫu in & Excel đối chiếu
def test_print_payload_and_reconciliation_excel(engine, session, base):
    import io
    import shutil
    import subprocess

    from openpyxl import load_workbook

    from app.exporters.reconciliation_excel import build_reconciliation_xlsx
    from app.services import print_docs

    with transaction(session):
        masterdata.upsert_company(session, name="CTY TNHH CUNG ỨNG KHÍ HÓA LỎNG HÀ NỘI", tax_code="0107776927")
    order = _order(session, base)
    note = _deliver(session, order, [(order.items[0].id, "10"), (order.items[1].id, "1")])
    ledger.record_payment(session, partner_id=base["cust"].id, payment_date=date(2026, 11, 20), amount=D("5000000"))
    rec = reconciliations.create(session, partner_id=base["cust"].id, period_from=date(2026, 11, 1),
                                 period_to=date(2026, 11, 30))
    p = print_docs.print_payload(session, "dr", rec.id)
    assert p["code"] == rec.reconciliation_code and p["company"]["tax_code"] == "0107776927"
    assert [a["delivery_code"] for a in p["doc"]["appendix"]] == [note.delivery_code] * 2
    assert p["doc"]["amount_words"] == "Tám triệu không trăm mười nghìn đồng chẵn"   # 13.010.000 − 5.000.000
    assert print_docs.print_payload(session, "dn", note.id)["doc"]["items"][0]["accepted_qty"] == D(10)
    assert print_docs.qr_svg(session, "order", order.id).lstrip().startswith(b"<svg")

    data = build_reconciliation_xlsx(session, rec)
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Doi chieu"]
    formulas = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert any(f.startswith("=SUM(F") for f in formulas) and any(f.startswith("=SUM(G") for f in formulas)
    assert ws["A1"].font.name == "Times New Roman"
    assert "Phu luc giao nhan" in wb.sheetnames
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:   # tính lại công thức bằng LibreOffice: số dư cuối kỳ khớp
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "dr.xlsx"
            src.write_bytes(data)
            subprocess.run([soffice, "--headless", "--convert-to", "xlsx", "--outdir", str(Path(tmp) / "out"), str(src)],
                           check=True, capture_output=True, timeout=120)
            ws2 = load_workbook(Path(tmp) / "out" / "dr.xlsx", data_only=True)["Doi chieu"]
            values = [c.value for row in ws2.iter_rows() for c in row]
            assert 8010000 in values and 13010000 in values

    c = _client(engine)
    r = c.get(f"/api/reconciliations/{rec.id}/export.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert c.get(f"/api/print/dn/{note.id}").json()["doc"]["delivery_code"] == note.delivery_code
    assert c.get(f"/api/print/order/{order.id}/qr.svg").headers["content-type"].startswith("image/svg")
    assert c.get("/api/print/xx/1").status_code == 400
    app.dependency_overrides.clear()
