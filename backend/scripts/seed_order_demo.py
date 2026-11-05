"""Dữ liệu chạy thử quy trình Đơn đặt hàng -> BBGN -> Đối chiếu công nợ (thêm vào DB demo Stargate).

Chạy trong thư mục backend:
    python -m scripts.seed_order_demo                  # tạo ../data/demo-orders.db
    python -m scripts.seed_order_demo --db sale.db     # thêm vào file khác (KHÔNG xóa dữ liệu cũ)
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal as D

from sqlalchemy.orm import sessionmaker

from app.config import BASE_DIR
from app.db import init_db, make_engine, transaction
from app.demo import stargate_t10
from app.services import ledger, masterdata, orders, reconciliations
from app.services import delivery_notes as dn

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="demo-orders.db", help="tên file trong thư mục data/")
    parser.add_argument("--fresh", action="store_true", help="xóa file cũ và nạp cả dữ liệu Stargate T10")
    args = parser.parse_args()
    path = BASE_DIR.parent / "data" / args.db
    fresh = args.fresh or not path.exists()
    if fresh and path.exists():
        path.unlink()
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    if fresh:
        stargate_t10.seed(s)
    with transaction(s):
        kh = masterdata.create_partner(s, code="BEPSACH", name="CÔNG TY CP SUẤT ĂN CÔNG NGHIỆP BẾP SẠCH",
                                       tax_code="0109998887", address="KCN Thăng Long, Đông Anh, Hà Nội",
                                       phone="0912 345 678", contact_name="Chị Lan – bếp trưởng",
                                       credit_limit=D("150000000"), payment_term_days=30,
                                       representative="Nguyễn Văn Bình", representative_title="Giám đốc")
        try:
            p45 = masterdata.get_product_by_sku(s, "PC45")
        except Exception:  # noqa: BLE001
            p45 = masterdata.create_product(s, sku="PC45", name="Gas Petrolimex 45kg", capacity_kg="45")
        p45.sale_price = D("1950000")
        stove = masterdata.create_product(s, sku="BEPCN2", name="Bếp công nghiệp 2 họng inox", kind="ACCESSORY",
                                          unit="cái", sale_price="3200000", vat_rate="0.1")
        p45.current_stock = D(200)
    o1 = orders.create_order(s, orders.OrderInput(
        partner_id=kh.id, order_date=date(2026, 11, 2), expected_delivery_date=date(2026, 11, 5),
        delivery_address="Bếp trung tâm – KCN Thăng Long", notes="Giao trước 9h sáng", items=[
            orders.OrderItemInput(product_id=p45.id, ordered_qty=D(30), unit_price=D("1950000"), discount_rate=D(2),
                                  tax_rate=D(8)),
            orders.OrderItemInput(product_id=stove.id, ordered_qty=D(2), unit_price=D("3200000"), tax_rate=D(10))]),
        submit=True)
    n1 = dn.create_from_order(s, o1.id, delivery_date=date(2026, 11, 5), deliverer_name="Anh Hùng",
                              lines=[dn.NoteLineInput(order_item_id=o1.items[0].id, dispatched_qty=D(20))])
    dn.dispatch(s, n1.id)
    dn.save_signature(s, n1.id, PNG, "bbgn-ky.png")
    dn.confirm(s, n1.id, receiver_name="Chị Lan", discrepancy_reason="1 bình móp van, khách trả lại",
               lines=[dn.NoteLineInput(order_item_id=o1.items[0].id, accepted_qty=D(19), rejected_qty=D(1),
                                       item_condition_note="1 bình móp van")])
    n2 = dn.create_from_order(s, o1.id, delivery_date=date(2026, 11, 12), deliverer_name="Anh Hùng")
    dn.dispatch(s, n2.id)
    ledger.record_payment(s, partner_id=kh.id, payment_date=date(2026, 11, 15), amount=D("20000000"),
                          order_id=o1.id, description="Khách chuyển khoản đợt 1")
    orders.create_order(s, orders.OrderInput(partner_id=kh.id, order_date=date(2026, 11, 16), items=[
        orders.OrderItemInput(product_id=p45.id, ordered_qty=D(80), unit_price=D("1950000"), tax_rate=D(8))]),
        submit=True)
    rec = reconciliations.create(s, partner_id=kh.id, period_from=date(2026, 11, 1), period_to=date(2026, 11, 30))
    print(f"Đã tạo dữ liệu quy trình đơn hàng trong {path}")
    print(f"  Đơn {o1.order_code}: {o1.status}; BBGN {n1.delivery_code} đã xác nhận, {n2.delivery_code} đang đi giao")
    print(f"  Biên bản đối chiếu {rec.reconciliation_code}: cuối kỳ {rec.closing_balance:,.0f}".replace(",", "."))
    print(f"Chạy giao diện với file này:  set SALE_DB_URL=sqlite:///{path.as_posix()}  rồi chạy chay.bat")


if __name__ == "__main__":
    main()
