"""Tạo database chạy thử với dữ liệu Stargate tháng 10/2026 và in bảng kê ra màn hình.

Chạy trong thư mục backend:
    python -m scripts.seed_demo                 # tạo ../data/demo.db
    python -m scripts.seed_demo --db sale.db    # tên file khác
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.config import BASE_DIR
from app.db import init_db, make_engine
from app.demo import stargate_t10
from app.services import statements


def money(v) -> str:
    return f"{v:,.0f}".replace(",", ".")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="demo.db", help="tên file trong thư mục data/")
    args = parser.parse_args()
    path = (BASE_DIR.parent / "data" / args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    ids = stargate_t10.seed(session)
    st = statements.build_statement(session, ids["partner_id"], "2026-10")

    print(f"Đã tạo {path}")
    print(f"\nBẢNG KÊ CHI TIẾT – {st['buyer']['name']}\n{st['period_text']}\n{st['opening_loans_text']}")
    for b in st["blocks"]:
        print(f"\n  {b['site_name']}  ({b['loan_text']})")
        for r in b["rows"]:
            if r["kind"] == "slip":
                qty = ", ".join(f"{k}:{v}" for k, v in r["qty_by_sku"].items())
                print(f"    {r['date']:%d/%m}  HĐ {r['vat_invoice_no'] or '':<6} {qty:<10} dư {r['gas_residual_kg']:>7} kg"
                      f"  thực giao {r['net_kg']:>8}  x {money(r['unit_price'])} = {money(r['amount']):>14}"
                      f"  {r['note'] or ''}")
            else:
                print(f"    {r['date']:%d/%m}  {r['text']}")
        print(f"    Tổng: {money(b['totals']['amount'])}")
    print(f"\nTổng cộng: {money(st['grand_total']['amount'])}   {st['closing_loans_text']}")
    for r in st["prior_debts"]:
        print(f"{r['label']:<45} {money(r['amount']):>16}")
    print(f"{'Tổng công nợ':<45} {money(st['total_debt']):>16}")
    for p in st["payments"]:
        print(f"{p['label']:<45} {money(p['amount']):>16}  {p['note'] or ''}")
    print(f"{'Tổng đã thanh toán':<45} {money(st['total_paid']):>16}")
    print(f"{'Công nợ còn lại phải thanh toán là:':<45} {money(st['closing_debt']):>16}")
    print(f"Bằng chữ: {st['amount_in_words']}")
    print(f"{st['sign_text']}")
    ok = st["closing_debt"] == stargate_t10.EXPECTED_CLOSING
    print("\nKHỚP file Excel gốc (1.829.047.800 đ)" if ok else "\nKHÔNG KHỚP file Excel gốc!")


if __name__ == "__main__":
    main()
