"""Nhập lịch sử từ các file Bảng kê Excel vào database và in báo cáo đối chiếu.

Chạy trong thư mục backend:
    python -m scripts.import_excel "D:/.../CN. Gầu bò CT4 - Mỹ Đình + 47TT4 Mỹ Đình.xlsx" "D:/.../T2. CN. CT4 - Hyundai.xlsx"
    python -m scripts.import_excel file.xlsx --cutover 2026-07        # chọn tháng chuyển đổi công nợ
    python -m scripts.import_excel file.xlsx --db D:/SaleManager/data/sale.db
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.db import engine as default_engine
from app.db import init_db, make_engine
from app.importers.history import import_workbook


def money(v) -> str:
    return "" if v is None else f"{v:,.0f}".replace(",", ".")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--db", help="đường dẫn file SQLite (mặc định theo SALE_DB_URL / ../data/sale.db)")
    ap.add_argument("--cutover", help="tháng chuyển đổi công nợ YYYY-MM (mặc định: tự chọn)")
    ap.add_argument("--partner-code", help="mã khách hàng muốn gán")
    args = ap.parse_args()
    engine = make_engine(f"sqlite:///{Path(args.db).resolve().as_posix()}") if args.db else default_engine
    init_db(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    for f in args.files:
        r = import_workbook(session, f, partner_code=args.partner_code, cutover=args.cutover)
        print(f"\n===== {r.file}")
        print(f"Khách hàng: {r.partner_name} (id {r.partner_id}) – {r.sheets} sheet")
        print(f"Phiếu nhập mới: {r.slips_created}  | bỏ qua trùng: {r.slips_skipped_duplicate}")
        print(f"Thanh toán nhập mới: {r.vouchers_created} | bỏ qua trùng: {r.vouchers_skipped_duplicate}")
        if r.sites_created:
            print("Bếp mới: " + "; ".join(r.sites_created))
        print(f"Tháng chuyển đổi công nợ: {r.cutover_period}")
        for a in r.assumptions:
            print(f"  * {a}")
        print(f"{'Sheet':<12}{'Tổng trên file':>18}{'Tổng phần mềm':>18}{'Còn nợ trên file':>20}{'Còn nợ phần mềm':>20}")
        for c in r.sheet_checks:
            print(f"{c['sheet']:<12}{money(c['file_total']):>18}{money(c['app_total']):>18}"
                  f"{money(c['file_closing']):>20}{money(c['app_closing']):>20}  {'OK' if c['ok'] else 'LỆCH'}")
        print(f"=> {len(r.sheet_checks) - len(r.mismatches)}/{len(r.sheet_checks)} sheet khớp")
        for w in r.warnings:
            print(f"  ! {w}")


if __name__ == "__main__":
    main()
